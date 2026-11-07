"""Child completion evidence retains exit status without leaking command secrets."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import dispatch, gate, lifecycle, triage
from factory.config import Config


class ChildExitTest(unittest.TestCase):
    def test_dispatch_child_failure_records_redacted_command_and_returncode(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            secret = "ghp_" + "a" * 32
            command = [sys.executable, "-c", "raise SystemExit(19)", "--token", "private-value", secret]
            with lifecycle.scope(events, "ticket", ticket=7):
                result = dispatch.run(command, check=False)
            self.assertEqual(result.returncode, 19)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 19)
            self.assertEqual(exit_row["command"][:3], command[:3])
            self.assertNotIn(secret, events.read_text())
            self.assertNotIn("private-value", events.read_text())
            self.assertIn("[REDACTED]", exit_row["command"])

    def test_timeout_records_killed_child_status(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            with lifecycle.scope(events, "ticket", ticket=7), self.assertRaises(subprocess.TimeoutExpired):
                dispatch.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertLess(exit_row["returncode"], 0)
            self.assertEqual(exit_row["command"][0], sys.executable)

    def test_gate_child_records_real_status(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            with lifecycle.scope(events, "gate", ticket=7):
                result = gate.timed([sys.executable, "-c", "raise SystemExit(23)"])
            self.assertEqual(result.returncode, 23)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 23)
            self.assertEqual(exit_row["command"][0], sys.executable)

    def test_triage_child_records_status(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text("#!/bin/sh\nexit 17\n")
            gh.chmod(0o755)
            events = root / "events.jsonl"
            with mock.patch.object(triage, "cfg", Config(root=root, repo="local/safety"), create=True), \
                    mock.patch.dict("os.environ", {"PATH": str(root)}), \
                    lifecycle.scope(events, "triage") as execution, self.assertRaises(SystemExit):
                triage.gh("issue", "list", execution=execution)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 17)
            self.assertEqual(exit_row["command"][:3], ["gh", "issue", "list"])

    def test_api_comment_body_is_redacted_in_child_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text("#!/bin/sh\nexit 17\n")
            gh.chmod(0o755)
            events = root / "events.jsonl"
            body = "Factory requeue: nonce=private-context\nWorker handoff details"
            command = [str(gh), "api", "repos/local/safety/issues/7/comments",
                       "--method", "POST", "-f", f"body={body}"]
            with lifecycle.scope(events, "ticket", ticket=7):
                result = dispatch.run(command, check=False)
            self.assertEqual(result.returncode, 17)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 17)
            self.assertEqual(exit_row["command"][:-1], command[:-1])
            self.assertEqual(exit_row["command"][-1], "body=[REDACTED]")
            self.assertNotIn("private-context", events.read_text())
            self.assertNotIn("Worker handoff details", events.read_text())
