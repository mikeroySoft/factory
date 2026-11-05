"""#553 required revisions: unchecked failures fail closed; expected absence survives."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle, reads, watch


class ReadRevisionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets", workers={"default": [sys.executable, "{prompt}"]})
        self.enterContext(patch.dict(dispatch.__dict__))
        dispatch.configure(self.cfg)
        self.journal = dispatch.EVENTS
        self.probe = self.enterContext(patch.object(reads, "_quota", return_value={
            "available": True, "resources": {"core": {"remaining": 4999}},
        }))

    def rows(self, event):
        return [row for row in lifecycle.read_events(self.journal) if row.get("event") == event]

    def test_unchecked_dependency_errors_defer_instead_of_authorizing_claim(self):
        command = ["gh", "api", "repos/acme/widgets/issues/7/dependencies/blocked_by"]
        for stderr in ("HTTP 500: Internal Server Error", "HTTP 401: Bad credentials"):
            with self.subTest(stderr=stderr), patch.object(dispatch, "_run", return_value=(
                subprocess.CompletedProcess(command, 1, "", stderr)
            )), reads.pass_scope(self.journal, "dispatch") as state:
                with self.assertRaises(reads.ReadFailure) as caught:
                    dispatch.open_blockers(7, "")
                reads.skip(caught.exception, ticket=7)
                self.assertEqual((state.reads, state.failed, state.skipped), (1, 1, 1))
        self.assertEqual([row["code"] for row in self.rows("read-skip")],
                         ["gh_read_failed", "gh_read_failed"])
        self.assertEqual(self.rows("claimed") + self.rows("escalate"), [])

    def test_missing_pr_keeps_checked_callers_absence_handler(self):
        command = ["gh", "pr", "view", "agent/7"]
        with reads.pass_scope(self.journal, "manage") as state:
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                reads.run(command, lambda: subprocess.CompletedProcess(
                    command, 1, "", "no pull requests found for branch agent/7"))
            self.assertIn("no pull requests found", caught.exception.stderr)
            self.assertEqual((state.failed, state.skipped, state.stopped), (0, 0, False))
        self.probe.assert_not_called()

    def test_expected_dependency_absence_and_ci_results_remain_caller_owned(self):
        responses = [
            (["gh", "api", "repos/acme/widgets/issues/7/dependencies/blocked_by"], 1, "", "HTTP 404: Not Found"),
            (["gh", "pr", "checks", "7", "--json", "name,bucket"], 1,
             json.dumps([{"name": "test", "bucket": "fail"}]), ""),
        ]
        with reads.pass_scope(self.journal, "dispatch") as state:
            for command, code, stdout, stderr in responses:
                result = reads.run(command, lambda: subprocess.CompletedProcess(command, code, stdout, stderr), check=False)
                self.assertEqual((result.returncode, result.stdout), (code, stdout))
            self.assertEqual((state.failed, state.skipped, state.stopped), (0, 0, False))
        self.probe.assert_not_called()

    def test_failed_watch_close_does_not_abort_later_fingerprint(self):
        old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        for number in (701, 702):
            lifecycle.append(self.journal, {"event": "watch", "at": old,
                             "fingerprint": f"fp-{number}", "issue": number, "repo": self.cfg.repo})
        closed = []

        def run(command, *args, **kwargs):
            self.assertEqual(command[:2], ["gh", "issue"])
            self.assertEqual(command[2], "close")
            if command[3] == "701":
                raise subprocess.CalledProcessError(1, command, stderr="error connecting to api.github.com")
            closed.append(command[3])
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(watch, "signals", return_value=[]), \
                patch.object(dispatch, "gh_json", return_value={
                    "state": "OPEN", "assignees": [], "labels": [{"name": config.LABEL_AGENT}], "comments": [],
                }), patch.object(dispatch, "run", side_effect=run):
            watch.run_pass(self.cfg)
        self.assertEqual(closed, ["702"])
        self.assertEqual([row["issue"] for row in self.rows("watch") if row.get("closed")], [702])


if __name__ == "__main__":
    unittest.main()
