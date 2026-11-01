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

    def test_handoff_requires_content_before_final_marker(self):
        marker = "<!-- factory:handoff-complete -->"
        for text, complete in (
            ("Summary\n" + marker, True),
            ("\nSummary\n" + marker + "\n\n", True),
            (marker, False),
            (" \n\t\n" + marker, False),
            ("Summary\n" + marker + "\nStill working", False),
            ("Summary\nNot finished", False),
        ):
            with self.subTest(text=text):
                self.handoff.write_text(text)
                self.assertEqual(dispatch._handoff_complete(self.handoff), complete)

    def test_handoff_size_boundary_and_truncated_marker(self):
        from factory import results

        suffix = b"\n<!-- factory:handoff-complete -->"
        for size in (results.ARTIFACT_CAP - 1, results.ARTIFACT_CAP, results.ARTIFACT_CAP + 1):
            with self.subTest(size=size):
                self.handoff.write_bytes(b"x" * (size - len(suffix)) + suffix)
                self.assertEqual(dispatch._handoff_complete(self.handoff), size <= results.ARTIFACT_CAP)
        # A completion marker in a truncated prefix must not hide trailing unfinished work.
        prefix_size = results.ARTIFACT_CAP >> 1
        prefix = b"x" * (prefix_size - len(suffix)) + suffix
        self.handoff.write_bytes(prefix + b"\n" * (results.ARTIFACT_CAP - prefix_size + 1))
        self.assertFalse(dispatch._handoff_complete(self.handoff))

    def test_idle_measurement_includes_shutdown_after_last_done_change(self):
        for natural in (False, True):
            with self.subTest(natural=natural):
                clock = [100.0]
                start = (None, "head", "", None)
                changed = (None, "new-head", "dirty", (10, 1))
                proc = mock.MagicMock()
                proc.returncode = 0 if natural else -15
                proc.poll.return_value = 0 if natural else None

                def wait(timeout, clock=clock, natural=natural):
                    clock[0] += timeout
                    if not natural or clock[0] < 110:
                        raise subprocess.TimeoutExpired("worker", timeout)

                def stop(_proc, clock=clock):
                    clock[0] += 2

                proc.wait.side_effect = wait
                metrics = {}
                with mock.patch.object(dispatch, "WATCH_POLL", 5), \
                        mock.patch.object(dispatch.time, "monotonic", side_effect=lambda clock=clock: clock[0]), \
                        mock.patch.object(dispatch, "_activity", side_effect=[start, changed, changed]), \
                        mock.patch.object(dispatch.subprocess, "Popen", return_value=proc), \
                        mock.patch.object(dispatch, "_stop", side_effect=stop):
                    dispatch.run_worker(
                        ["worker"], self.wt, self.root / "timing.log", self.handoff,
                        metrics=metrics, journal=False,
                    )
                self.assertEqual(metrics["post_finish_idle_seconds"], 5 if natural else 7)
                self.assertEqual(metrics["finish"], "natural" if natural else "exit_grace")

    def test_worker_prompt_forbids_post_finish_gate_and_mutation(self):
        issue = {"title": "change", "body": "acceptance", "comments": []}
        with mock.patch.object(dispatch, "gh_json", return_value=issue), \
                mock.patch.object(dispatch, "admit_plan", return_value=None), \
                mock.patch.object(dispatch.brief, "ensure", return_value=""):
            prompt = dispatch.build_prompt(7, self.wt, attempt=1, labels=set())
        self.assertIn("Do not re-run the full gate or the mutation check after the handoff or final commit", prompt)
        self.assertIn("dispatcher's gate is authoritative", prompt)
        self.assertIn("<!-- factory:handoff-complete -->", prompt)


class WorkerFinishQuietBoundaryTest(unittest.TestCase):
    def test_production_quiet_period_stops_at_thirty_seconds_not_before(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handoff = root / "handoff"
            handoff.write_text(COMPLETE)
            clock = [100.0]
            proc = mock.Mock()

            def wait(timeout):
                clock[0] += timeout
                raise subprocess.TimeoutExpired("worker", timeout)

            proc.wait.side_effect = wait
            start = (None, "head", "", None)
            current = (None, "head", "", (len(COMPLETE), 1))
            settings = config.Config(root=root, repo="acme/widgets", exit_grace=300)
            with mock.patch.object(dispatch, "cfg", settings, create=True), \
                    mock.patch.object(dispatch, "WATCH_POLL", 1), \
                    mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "_activity", return_value=current):
                finish, done_at = dispatch._watch(proc, root, root / "log", handoff, 200, start)
            self.assertEqual(finish, "clean_handoff")
            self.assertEqual(clock[0] - done_at, 30)


if __name__ == "__main__":
    unittest.main()
