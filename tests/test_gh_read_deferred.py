"""Every started item deferred by a pass-wide stop has one counted receipt."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import gh_reads, lifecycle


class DeferredItemTest(unittest.TestCase):
    def test_majority_stop_counts_newly_deferred_item_once_without_probe_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            probe_result = subprocess.CompletedProcess([], 0, json.dumps({"resources": {
                name: {"remaining": 5000, "reset": 4102444800} for name in ("core", "graphql")
            }}), "")
            write = mock.Mock()
            with mock.patch("factory.gh_reads.subprocess.run", return_value=probe_result) as probe:
                with gh_reads.scope(journal, "dispatch") as reads:
                    visited = []
                    for number in gh_reads.items(range(1, 7)):
                        visited.append(number)
                        with gh_reads.item(number):
                            command = ["gh", "issue", "view", str(number)]
                            gh_reads.run(command, lambda number=number: subprocess.CompletedProcess(
                                command, int(number < 3), "{}", "connection refused" if number < 3 else "",
                            ))
                            gh_reads.run(["gh", "issue", "edit", str(number)], write)
                    # Another stage touching the same ticket must not double-count its deferral.
                    with gh_reads.item(3):
                        gh_reads.run(["gh", "issue", "edit", "3"], write)
                    self.assertEqual((reads.reads, reads.failed_reads, reads.skipped), (3, 2, 3))
                self.assertEqual(probe.call_count, 2, "a refused operation is not another provider failure")
            self.assertEqual(visited, [1, 2, 3], "unvisited work must not be enumerated as skipped")
            write.assert_not_called()
            rows = lifecycle.read_events(journal)
            skips = [row for row in rows if row.get("event") == "gh-read-skipped"]
            self.assertEqual([(row["ticket"], row["code"]) for row in skips], [
                (1, "gh_read_failed"), (2, "gh_read_failed"), (3, "gh_reads_unavailable"),
            ])
            [summary] = [row for row in rows if row.get("event") == "pass-summary"]
            self.assertEqual((summary["reads"], summary["failed_reads"], summary["skipped"]), (3, 2, 3))
