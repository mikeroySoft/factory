"""Expected gh responses count as completed reads, not outage evidence."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import lifecycle, reads


class ReadAccountingTest(unittest.TestCase):
    def test_expected_ci_verdicts_prevent_false_majority_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            checks = ["gh", "pr", "checks", "7", "--json", "name,bucket"]
            view = ["gh", "issue", "view", "7"]
            failures = []
            with patch.object(reads, "_quota", return_value={"available": False}), \
                    reads.pass_scope(journal, "dispatch") as state:
                for _ in range(4):
                    reads.run(checks, lambda: subprocess.CompletedProcess(
                        checks, 1, '[{"name":"test","bucket":"fail"}]', ""), check=False)
                for ticket in range(3):
                    with reads.item(ticket=ticket):
                        reads.run(view, lambda: failures.append(7) or subprocess.CompletedProcess(
                            view, 1, "", "HTTP 500: Internal Server Error"))
                with reads.item(ticket=8):
                    result = reads.run(view, lambda: subprocess.CompletedProcess(view, 0, "OPEN", ""))
                    self.assertEqual(result.stdout, "OPEN")
                self.assertFalse(state.stopped, "three failures among eight reads are not a majority")
                self.assertEqual((state.reads, state.failed, state.skipped), (8, 3, 3))
            self.assertEqual(failures, [7, 7, 7])
            self.assertEqual([row for row in lifecycle.read_events(journal)
                              if row.get("event") == "read-stop"], [])


if __name__ == "__main__":
    unittest.main()
