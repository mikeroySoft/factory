"""#828: widespread read failures stop work and remain visible in runtime JSON."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, gh_reads, lifecycle, runtime_events


class ReadCompletionTest(unittest.TestCase):
    def test_successful_third_read_stops_a_majority_failed_pass_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            probe = subprocess.CompletedProcess([], 0, json.dumps({"resources": {
                name: {"remaining": 5000, "reset": 4102444800} for name in ("core", "graphql")
            }}), "")
            invoke = mock.Mock()
            with mock.patch("subprocess.run", return_value=probe), gh_reads.scope(journal, "watch") as reads:
                for number in gh_reads.items(range(1, 7)):
                    with gh_reads.item(number):
                        command = ["gh", "issue", "view", str(number)]
                        gh_reads.run(command, lambda number=number, command=command: subprocess.CompletedProcess(
                            command, int(number < 3), "{}", "network unavailable" if number < 3 else "",
                        ))
                        gh_reads.run(["gh", "issue", "edit", str(number)], invoke)
                self.assertEqual(reads.reason, "gh_reads_unavailable")
                self.assertEqual((reads.reads, reads.failed_reads, reads.skipped), (3, 2, 2))
            invoke.assert_not_called()
            [summary] = [row for row in lifecycle.read_events(journal) if row.get("event") == "pass-summary"]
            self.assertEqual(summary["reason"], "gh_reads_unavailable")

    def test_runtime_projection_retains_transport_and_quota_failure_reasons(self):
        for code, stderr in (("gh_read_failed", "network unavailable"),
                             ("rate_limited", "GraphQL: API rate limit exceeded")):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                journal = Path(directory) / "events.jsonl"
                command = ["gh", "issue", "view", "7", "--json", "state"]
                probe = subprocess.CompletedProcess([], 0, json.dumps({"resources": {
                    name: {"remaining": 6, "reset": 4102444800} for name in ("core", "graphql")
                }}), "")
                with (
                    mock.patch("subprocess.run", return_value=probe),
                    mock.patch.object(dispatch, "_run", side_effect=subprocess.CalledProcessError(1, command, stderr=stderr)),
                    gh_reads.scope(journal, "dispatch"),
                    lifecycle.scope(journal, "ticket", ticket=7),
                    gh_reads.item(7),
                ):
                    dispatch.run(command)
                projected = runtime_events.project(journal)
                [execution] = projected["executions"]
                self.assertEqual((execution["outcome"], execution["reason"]), ("mechanism_failure", code))
                [terminal] = [row for row in projected["events"] if row["kind"] == "exit"]
                self.assertEqual(terminal["reason"], code)
