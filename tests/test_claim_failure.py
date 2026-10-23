from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config

TOKEN = "ghp_" + "a1B2c3D4e5" * 3 + "xyz012"
CLAIM = ["gh", "issue", "edit", "7", "--repo", "local/claims", "--add-assignee", "@me"]


def failed(cmd, *args, **kwargs):
    raise subprocess.CalledProcessError(
        1, cmd, output="", stderr=f"boom: GraphQL error {TOKEN}\n"
    )


class ClaimFailure(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.calls = self.root / "gh-call.json"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"Path({str(self.calls)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            f"print('boom: GraphQL error {TOKEN}', file=sys.stderr)\n"
            "raise SystemExit(1)\n"
        )
        gh.chmod(0o755)
        self.enterContext(mock.patch.dict(os.environ, {
            lifecycle.CONTEXT_ENV: "",
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        }))
        self.cfg = Config(
            root=self.root,
            repo="local/claims",
            workers={"default": [sys.executable, "{prompt}"]},
        )
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(
            mock.patch.multiple(
                dispatch,
                create=True,
                cfg=self.cfg,
                ROOT=self.cfg.root,
                REPO=self.cfg.repo,
                UPSTREAM=None,
                UPSTREAM_REPO=None,
                FACTORY=self.cfg.factory,
                LOGS=self.cfg.factory / "logs",
                SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
                EVENTS=self.events,
                MAX_ACTIVE=1,
                MAX_ATTEMPTS=self.cfg.max_attempts,
            )
        )
        self.issue = {
            "number": 7,
            "title": "local ticket",
            "labels": [{"name": "ready-for-agent"}],
        }
        self.fresh = {
            **self.issue,
            "state": "OPEN",
            "assignees": [],
            "body": "",
            "comments": [],
        }

    def test_checked_run_logs_redacted_bounded_stderr_then_raises(self):
        output = io.StringIO()
        with mock.patch.object(subprocess, "run", side_effect=failed), redirect_stdout(output), \
                self.assertRaises(subprocess.CalledProcessError) as raised:
            dispatch.run(CLAIM)

        self.assertEqual(raised.exception.stderr, f"boom: GraphQL error {TOKEN}\n")
        logged = output.getvalue()
        self.assertIn("GraphQL error", logged)
        self.assertNotIn(TOKEN, logged)

    def test_stderr_tail_redacts_before_bounding(self):
        tail = dispatch.stderr_tail(TOKEN + "y" * 1990)

        self.assertLessEqual(len(tail), 2000)
        self.assertNotIn(TOKEN[-8:], tail)

    def test_claim_failure_skips_ticket_and_waits_for_next_pass(self):
        output = io.StringIO()
        with mock.patch.object(dispatch, "gh_json", return_value=self.fresh), \
                mock.patch.object(dispatch, "admit_plan", return_value=None), \
                mock.patch.object(dispatch, "ensure_worktree") as worktree, \
                redirect_stdout(output):
            dispatch.process_ticket(self.issue, 1, False)

        self.assertEqual(json.loads(self.calls.read_text()), CLAIM[1:])
        self.assertEqual(self.fresh["assignees"], [])
        self.assertEqual(self.fresh["labels"], [{"name": "ready-for-agent"}])
        worktree.assert_not_called()
        logged = output.getvalue()
        self.assertIn("claim failed", logged)
        self.assertIn("GraphQL error", logged)
        self.assertNotIn(TOKEN, logged)

        rows = lifecycle.read_events(self.events)
        failures = [row for row in rows if row.get("event") == "claim-failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual((failures[0]["ticket"], failures[0]["returncode"]), (7, 1))
        self.assertNotIn(TOKEN, str(failures[0]))
        waits = [row["wait"] for row in rows
                 if row.get("event") == "lifecycle" and row.get("kind") == "wait"]
        self.assertEqual(
            [(wait["reason"], wait["mode"]) for wait in waits],
            [("claim_failed", "retry_next_pass")],
        )
        self.assertFalse(any(row.get("event") == "claimed" for row in rows))
        self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))


if __name__ == "__main__":
    unittest.main()
