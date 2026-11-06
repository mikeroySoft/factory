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

    def test_manager_fix_setup_failure_never_launches_worker_or_escalates(self):
        self.configure_setup("raise SystemExit(23)")
        dispatch.ensure_worktree(9)
        packet = self.tmp / "packet.md"
        packet.write_text("Fix the change")
        with self.patched(), mock.patch.object(dispatch, "run_worker", side_effect=AssertionError("worker launched before setup succeeded")) as worker:
            manage.apply(9, self.github.issue(), "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
        worker.assert_not_called()
        self.assertEqual(self.events("attempt"), [])
        self.assertEqual(self.events("escalate"), [])
        self.assertEqual(self.events("setup-infra")[0]["classification"], "env")

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
