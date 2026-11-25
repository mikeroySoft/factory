"""Admission backoff must not suspend independently owned claim maintenance."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import gh_reads, lifecycle


class HeartbeatBackoffTest(unittest.TestCase):
    def test_maintenance_revalidates_ownership_during_admission_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            lifecycle.append(journal, {"event": "gh-read-backoff", "reset": 4102444800})
            command = ["gh", "issue", "view", "7", "--json", "comments"]
            invoke = mock.Mock(return_value=subprocess.CompletedProcess(command, 0, "[]", ""))
            with gh_reads.scope(journal, "claim-heartbeat", isolated=True, backoff=False):
                gh_reads.run(command, invoke)
            invoke.assert_called_once_with()
            with gh_reads.scope(journal, "dispatch") as state:
                self.assertTrue(state.stopped)
                self.assertEqual(state.reset, 4102444800)

    def test_maintenance_failure_still_journals_and_stops_its_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            command = ["gh", "issue", "view", "7", "--json", "comments"]
            probe = subprocess.CompletedProcess([], 0, json.dumps({"resources": {
                name: {"remaining": 0, "reset": 4102444800} for name in ("core", "graphql")
            }}), "")
            with mock.patch("factory.gh_reads.subprocess.run", return_value=probe):
                with gh_reads.scope(journal, "claim-heartbeat", isolated=True, backoff=False) as state:
                    with gh_reads.item(7):
                        gh_reads.run(command, lambda: subprocess.CompletedProcess(
                            command, 1, "", "API rate limit exceeded (HTTP 403)"))
                    self.assertTrue(state.stopped)
            rows = lifecycle.read_events(journal)
            self.assertEqual([row["code"] for row in rows if row["event"] == "gh-read-skipped"],
                             ["rate_limited"])
            self.assertEqual(state.reset, 4102444800)
