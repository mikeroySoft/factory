"""Clean handoff supervision and post-finish measurements (#508)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle

COMPLETE = "Changed the worker.\nUnverified: deployment.\nNext: deploy.\n<!-- factory:handoff-complete -->\n"


class WorkerFinishTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(lifecycle.CONTEXT_ENV, None)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.wt = self.root / ".factory" / "wt-7"
        self.wt.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.wt / ".gitignore").write_text(".factory/\n.factory-prompt.md\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "initial")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.wt / ".factory").mkdir()
        self.handoff = self.wt / ".factory" / "handoff-7.md"
        dispatch.configure(config.Config(root=self.root, repo="acme/widgets", idle_timeout=0, exit_grace=0.8))
        dispatch.LOGS.mkdir()
        self.enterContext(mock.patch.object(dispatch, "WATCH_POLL", 0.02))
        self.enterContext(mock.patch.object(dispatch, "CLEAN_HANDOFF_QUIET", 0.15, create=True))

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.wt), *args], check=True, capture_output=True, text=True).stdout

    def attempt(self, text=COMPLETE, *, dirty=False, natural=False, no_done=False, deadline=float("inf")):
        script = "from pathlib import Path\nimport time\n"
        if not no_done:
            script += f"Path('.factory/handoff-7.md').write_text({text!r})\n"
        if dirty:
            script += "Path('work.txt').write_text('uncommitted')\n"
        script += "time.sleep(0.1)\n" if natural else "time.sleep(60)\n"
        dispatch.cfg.workers = {"default": [sys.executable, "-c", script]}
        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
            dispatch.worker_round(7, self.wt, set(), "ticket", "", 1, deadline)
        return next(row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "attempt")

    def test_complete_clean_quiet_worker_finishes_normally(self):
        row = self.attempt()
        self.assertNotIn("timeout", row)
        self.assertNotEqual(row.get("reason"), "hung_after_done")
        self.assertEqual(row["worker_exit"], 0)
        self.assertEqual(row["gate"], "PASS")
        self.assertEqual(row["finish"], "clean_handoff")
        self.assertGreaterEqual(row["post_finish_idle_seconds"], 0.15)
        self.assertLess(row["post_finish_idle_seconds"], 0.8)
        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertFalse(any(e.get("kind") == "timeout" for e in events))
        worker_exit = next(e for e in events if e.get("kind") == "exit" and e.get("stage") == "worker")
        self.assertEqual(worker_exit["outcome"], "completed")

    def test_incomplete_handoff_keeps_exit_grace(self):
        row = self.attempt("Still writing the handoff")
        self.assertEqual((row["timeout"], row["reason"]), ("exit_grace", "hung_after_done"))
        self.assertGreaterEqual(row["post_finish_idle_seconds"], 0.8)

    def test_dirty_worktree_keeps_exit_grace(self):
        row = self.attempt(dirty=True)
        self.assertEqual(row["timeout"], "exit_grace")
        self.assertGreaterEqual(row["post_finish_idle_seconds"], 0.8)

    def test_natural_exit_records_idle(self):
        row = self.attempt(natural=True)
        self.assertNotIn("timeout", row)
        self.assertEqual(row["finish"], "natural")
        self.assertGreater(row["post_finish_idle_seconds"], 0)
        self.assertLess(row["post_finish_idle_seconds"], 0.8)

    def test_no_done_signal_records_null_for_forced_and_natural_exits(self):
        for natural in (False, True):
            with self.subTest(natural=natural):
                dispatch.EVENTS.unlink(missing_ok=True)
                row = self.attempt(no_done=True, natural=natural, deadline=time.monotonic() + 0.3)
                self.assertIsNone(row["post_finish_idle_seconds"])
                self.assertEqual(row.get("timeout"), None if natural else "budget_min")

    def test_stale_complete_handoff_does_not_finish_new_work(self):
        self.handoff.write_text(COMPLETE)
        row = self.attempt(no_done=True, deadline=time.monotonic() + 0.3)
        self.assertEqual(row["timeout"], "budget_min")
        self.assertIsNone(row["post_finish_idle_seconds"])

    def test_log_activity_restarts_quiet_but_not_done_measurement(self):
        self.handoff.write_text(COMPLETE)
        dispatch.cfg.exit_grace = 300
        clock = [0.0]
        start = (None, "head", "", None)

        def wait(timeout):
            clock[0] += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def activity(*_args):
            return (("output" if clock[0] >= 20 else None), "head", "", (92, 1))

        proc = mock.Mock()
        proc.wait.side_effect = wait
        with mock.patch.object(dispatch, "WATCH_POLL", 5), \
                mock.patch.object(dispatch, "CLEAN_HANDOFF_QUIET", 30), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "_activity", side_effect=activity):
            finish, done_at = dispatch._watch(proc, self.wt, self.root / "log", self.handoff, 100, start)
        self.assertEqual((finish, done_at, clock[0]), ("clean_handoff", 5, 50))

    def test_commit_without_fresh_handoff_keeps_exit_grace(self):
        self.handoff.write_text(COMPLETE)
        script = (
            "import subprocess, time; "
            "subprocess.run(['git', 'commit', '--allow-empty', '-qm', 'done'], check=True); "
            "time.sleep(60)"
        )
        metrics = {}
        code, fired = dispatch.run_worker(
            [sys.executable, "-c", script], self.wt, self.root / "commit.log",
            self.handoff, float("inf"), metrics,
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(fired, "exit_grace")
        self.assertGreaterEqual(metrics["post_finish_idle_seconds"], 0.8)

    def test_idle_timeout_attempt_has_null_idle(self):
        dispatch.cfg.idle_timeout = 0.002
        row = self.attempt(no_done=True)
        self.assertEqual(row["timeout"], "idle_timeout")
        self.assertIsNone(row["post_finish_idle_seconds"])

    def test_worker_prompt_forbids_post_finish_gate_and_mutation(self):
        issue = {"title": "change", "body": "acceptance", "comments": []}
        with mock.patch.object(dispatch, "gh_json", return_value=issue), \
                mock.patch.object(dispatch, "admit_plan", return_value=None), \
                mock.patch.object(dispatch.brief, "ensure", return_value=""):
            prompt = dispatch.build_prompt(7, self.wt, attempt=1, labels=set())
        self.assertIn("Do not re-run the full gate or the mutation check after the handoff or final commit", prompt)
        self.assertIn("dispatcher's gate is authoritative", prompt)
        self.assertIn("<!-- factory:handoff-complete -->", prompt)


if __name__ == "__main__":
    unittest.main()
