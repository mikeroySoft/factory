"""#606: worktree setup is cached by head; environment failures spend no attempts."""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, manage
from tests import test_refresh_conflict as fixture


class DispatchSetupTest(unittest.TestCase):
    setUp = fixture.RefreshConflictTest.setUp
    patched = fixture.RefreshConflictTest.patched
    events = fixture.RefreshConflictTest.events

    def configure_setup(self, script: str) -> None:
        command = [sys.executable, "-c", script]
        (self.root / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n[dispatch]\nsetup = ' + json.dumps(command) + '\n'
        )
        with mock.patch.object(config, "host_config", return_value={}):
            self.cfg = config.load(self.root)
        self.cfg.workers = {"default": [sys.executable, "-c", ""]}
        dispatch.configure(self.cfg)

    def worker_round(self, wt):
        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")), \
                mock.patch("factory.results.source_metadata", return_value={}):
            return dispatch.worker_round(9, wt, set(), "change", "", 1, time.monotonic() + 30)

    def test_setup_runs_once_per_worktree_and_head_before_worker(self):
        runs = self.tmp / "setup-runs"
        self.configure_setup(
            "from pathlib import Path\n"
            f"with Path({str(runs)!r}).open('a') as f: f.write(str(Path.cwd()) + '\\n')\n"
            "Path('.factory').mkdir(exist_ok=True)\n"
            "Path('.factory/ready').touch()\n"
            "print('environment ready')\n"
        )
        self.cfg.workers["default"] = [
            sys.executable, "-c", "from pathlib import Path; assert Path('.factory/ready').is_file()"
        ]
        wt = dispatch.ensure_worktree(9)
        dispatch.LOGS.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            self.assertTrue(self.worker_round(wt)[0])
        self.assertTrue(runs.is_file(), "setup did not run before the worker")
        self.assertEqual(runs.read_text().splitlines(), [str(wt)])
        [setup] = self.events("setup")
        self.assertEqual((setup["ticket"], setup["head"], setup["exit_status"]),
                         (9, fixture.git(wt, "rev-parse", "HEAD"), 0))
        self.assertGreaterEqual(setup["seconds"], 0)
        self.assertIn("environment ready", Path(setup["log"]).read_text())
        self.assertEqual([a["worker_exit"] for a in self.events("attempt")], [0, 0])

        fixture.git(wt, "commit", "--allow-empty", "-qm", "new head")
        self.worker_round(wt)
        other = dispatch.ensure_worktree(10)
        fixture.git(other, "checkout", "--detach", fixture.git(wt, "rev-parse", "HEAD"))
        self.worker_round(other)
        self.assertEqual(runs.read_text().splitlines(), [str(wt), str(wt), str(other)])

    def test_failing_setup_is_env_without_attempt_or_strike_and_can_retry(self):
        fixed = self.tmp / "fixed"
        self.configure_setup(
            "from pathlib import Path\n"
            f"raise SystemExit(0 if Path({str(fixed)!r}).exists() else 23)\n"
        )
        self.github.assignees = []
        dispatch.record("attempt", ticket=9, attempt=1, gate="FAIL")
        before = self.events("attempt")
        with self.patched(), mock.patch.object(dispatch, "run_worker", side_effect=AssertionError("worker launched before setup succeeded")) as worker, \
                mock.patch.object(dispatch, "run_gate") as gate:
            result = dispatch.process_ticket(self.github.issue(), 1, False)
        self.assertIs(result, False)
        worker.assert_not_called()
        gate.assert_not_called()
        self.assertEqual(self.events("attempt"), before)
        self.assertEqual(self.events("escalate"), [])
        self.assertEqual(self.github.assignees, [])
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        [setup] = self.events("setup")
        self.assertEqual(setup["exit_status"], 23)
        self.assertGreaterEqual(setup["seconds"], 0)
        [infra] = self.events("setup-infra")
        self.assertEqual((infra["classification"], infra["reason"]), ("env", "setup_failed"))
        exits = [r for r in self.events("lifecycle") if r.get("stage") == "ticket" and r.get("kind") == "exit"]
        self.assertTrue(any(r.get("outcome") == "mechanism_failure" for r in exits))
        fixed.touch()
        wt = dispatch.FACTORY / "wt-9"
        self.assertTrue(self.worker_round(wt)[0])
        self.assertEqual([e["exit_status"] for e in self.events("setup")], [23, 0])

    def test_setup_argv_is_validated(self):
        for value in ('"echo hello"', '[]', '["", "arg"]', '["echo", 1]'):
            with self.subTest(value=value):
                (self.root / ".factory.toml").write_text(
                    '[repo]\nslug = "acme/widgets"\n[dispatch]\nsetup = ' + value + '\n'
                )
                with mock.patch.object(config, "host_config", return_value={}), \
                        self.assertRaisesRegex(config.ConfigError, "dispatch.setup"):
                    config.load(self.root)

    def test_setup_launch_failure_and_timeout_are_env_and_leave_no_marker(self):
        self.configure_setup("raise SystemExit(0)")
        wt = dispatch.ensure_worktree(9)
        for command, seconds, status in (
            (["factory-606-nonexistent-setup-executable"], 10, 127),
            ([sys.executable, "-c", "import time; time.sleep(30)"], 0.02, 124),
        ):
            with self.subTest(status=status):
                self.cfg.setup = command
                with self.assertRaises(dispatch.WorktreeSetupFailed):
                    dispatch.setup_worktree(9, wt, time.monotonic() + seconds)
                self.assertEqual(self.events("setup")[-1]["exit_status"], status)
                self.assertEqual(self.events("setup-infra")[-1]["classification"], "env")
        self.cfg.setup = [sys.executable, "-c", ""]
        dispatch.setup_worktree(9, wt, time.monotonic() + 10)
        self.assertEqual([e["exit_status"] for e in self.events("setup")], [127, 124, 0])
        self.assertEqual(self.events("attempt"), [])

    def manager_setup_recovery(self, frontier: bool):
        from datetime import UTC, datetime

        fixed = self.tmp / "fixed"
        self.configure_setup(
            "from pathlib import Path\n"
            f"raise SystemExit(0 if Path({str(fixed)!r}).exists() else 23)\n"
        )
        self.cfg.workers["fixer"] = self.cfg.workers["default"]
        self.cfg.manager = [sys.executable, "-c", 'print(\'DECISION: FIX\\n{"worker":"fixer","guidance":"fix the boundary"}\')']
        self.cfg.manager_rounds = 1
        self.cfg.manager_review = "all"
        dispatch.VERIFIED_WORKERS.add("fixer")
        wt = dispatch.ensure_worktree(9)
        head = fixture.git(wt, "rev-parse", "HEAD")
        dispatch.record("claimed", ticket=9)
        run_pass = manage.frontier_pass if frontier else manage.escalation_pass
        github = self.github.gh_json

        def gh_json(args):
            if args[:2] == ["issue", "list"]:
                return [self.github.issue()]
            if args[:2] == ["pr", "list"]:
                return [{**self.github.pr(), "isCrossRepository": False,
                         "updatedAt": datetime.now(UTC).isoformat()}]
            return github(args)

        with self.patched(), mock.patch.object(dispatch, "gh_json", side_effect=gh_json), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(manage, "observe_pr", return_value={"owner": {"relation": "factory_issue"}}), \
                mock.patch.object(manage, "undelivered", return_value=[]), \
                mock.patch.object(dispatch, "build_prompt", return_value="fix the boundary"), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")), \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "approved", False)), \
                mock.patch.object(dispatch, "approve_pr", return_value=True) as approve, \
                mock.patch("factory.results.source_metadata", return_value={}):
            self.github.pr_labels.clear()
            if frontier:
                dispatch.record("attempt", ticket=9, gate="PASS", head=head, actual_head=head)
                dispatch.record("review", ticket=9, verdict="APPROVE", accepted=True, head=head, actual_head=head)
            else:
                self.github.issue_labels = {config.LABEL_HUMAN}
                dispatch.escalate(9, "gate failed", None, code="gate_failed")
            before = self.events("attempt")
            escalations = self.events("escalate")
            for _ in range(2):
                run_pass()
                self.assertIsNotNone(manage.pending_fix(
                    dispatch.lifecycle.read_events(dispatch.EVENTS),
                    **({"pr": 102, "head": head} if frontier else {"round": 1, "pr": None}),
                ))
                self.assertEqual(self.events("attempt"), before)
                self.assertEqual(self.events("escalate"), escalations)
                self.assertEqual(self.events("setup-infra")[-1]["classification"], "env")
                approve.assert_not_called()
            fixed.touch()
            run_pass()
            approve.assert_called_once_with(9, head)
            self.assertEqual(len(self.events("attempt")), len(before) + 1)
            run_pass()
        self.assertEqual(len(self.events("manage")), 1)
        self.assertEqual(self.events("manage")[0]["round"], 1)
        self.assertEqual(len(self.events("attempt")), len(before) + 1)
        self.assertEqual(self.events("escalate"), escalations)
        self.assertEqual([row["exit_status"] for row in self.events("setup")], [23, 23, 0])

    def test_frontier_fix_setup_failure_resumes_without_another_decision(self):
        self.manager_setup_recovery(frontier=True)

    def test_escalation_fix_setup_failure_resumes_without_another_round(self):
        self.manager_setup_recovery(frontier=False)

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "requires Linux process state")
    def test_setup_timeout_stops_children_before_returning_without_an_attempt(self):
        import os
        import signal

        dispatch.record("attempt", ticket=9, attempt=1, gate="FAIL")
        before = self.events("attempt")
        for ignore_term in (False, True):
            with self.subTest(ignore_term=ignore_term):
                ready = self.tmp / f"child-{ignore_term}"
                parent = self.tmp / f"parent-{ignore_term}"
                child = (
                    "import os, signal, time\nfrom pathlib import Path\n"
                    + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
                    + f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
                    + "time.sleep(60)\n"
                )
                self.configure_setup(
                    "import os, subprocess, sys, time\nfrom pathlib import Path\n"
                    f"Path({str(parent)!r}).write_text(str(os.getpid()))\n"
                    f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                    "time.sleep(60)\n"
                )
                wt = dispatch.ensure_worktree(9)
                git_dir = Path(fixture.git(wt, "rev-parse", "--absolute-git-dir"))
                marker = git_dir / "factory-setup" / fixture.git(wt, "rev-parse", "HEAD")
                try:
                    with self.assertRaises(dispatch.WorktreeSetupFailed):
                        dispatch.worker_round(9, wt, set(), "change", "", 2, time.monotonic() + 2)
                    self.assertTrue(ready.exists(), "child did not reach its signal handler")
                    for pid_file in (parent, ready):
                        pid = int(pid_file.read_text())
                        stat = Path(f"/proc/{pid}/stat")
                        until = time.monotonic() + 1
                        while stat.exists() and time.monotonic() < until:
                            if stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                                break
                            time.sleep(0.01)
                        if stat.exists():
                            self.assertEqual(stat.read_text().rsplit(")", 1)[1].split()[0], "Z",
                                             f"setup process {pid} is still running")
                    self.assertFalse(marker.exists())
                    self.assertEqual(self.events("setup")[-1]["exit_status"], 124)
                    infra = self.events("setup-infra")[-1]
                    self.assertEqual((infra["classification"], infra["reason"]), ("env", "setup_failed"))
                    self.assertEqual(self.events("attempt"), before)
                    self.assertEqual(self.events("escalate"), [])
                finally:
                    for pid_file in (parent, ready):
                        if pid_file.exists():
                            try:
                                os.kill(int(pid_file.read_text()), signal.SIGKILL)
                            except ProcessLookupError:
                                pass

    def test_review_bounce_setup_failure_releases_claim_once_and_defers_findings(self):
        self.configure_setup("raise SystemExit(23)")
        self.github.assignees = []
        wt = dispatch.ensure_worktree(9)
        head = fixture.git(wt, "rev-parse", "HEAD")
        findings = "Fix the setup boundary."
        with self.patched(), \
                mock.patch.object(dispatch, "worker_round", return_value=(True, "gate ok", None, head)), \
                mock.patch.object(dispatch, "push_and_pr", return_value=True), \
                mock.patch.object(dispatch, "record_risk"), \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=("REVISE", findings, False)), \
                mock.patch.object(dispatch, "unclaim", wraps=dispatch.unclaim) as unclaim:
            dispatch.process_ticket(self.github.issue(), 1, False)
        self.assertEqual(unclaim.call_count, 1)
        self.assertEqual(self.github.assignees, [])
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        [rework] = self.events("review-rework")
        self.assertEqual((rework["head"], rework["findings"], rework["deferred"]),
                         (head, findings, True))
        self.assertEqual(self.events("attempt"), [])
        self.assertEqual(self.events("escalate"), [])
