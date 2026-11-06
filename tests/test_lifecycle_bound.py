"""#566: progress from a long-running gate never becomes lifecycle heartbeats."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import gate, lifecycle


class GateLifecycleBoundTest(unittest.TestCase):
    def test_long_running_gate_journals_transitions_not_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            os.environ.pop(lifecycle.CONTEXT_ENV, None)
            journal = Path(directory) / "events.jsonl"
            # Keep the real child alive across many progress updates. The journal bound
            # is independent of update count and elapsed time; no heartbeat is needed.
            command = [sys.executable, "-u", "-c", (
                "import time\n"
                "for i in range(100):\n"
                " print(i, flush=True)\n"
                " time.sleep(0.01)\n"
            )]
            with lifecycle.scope(journal, "gate-check", ticket=566):
                result = gate.timed(command)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.splitlines(), [str(i) for i in range(100)])
            rows = lifecycle.read_events(journal)
            self.assertLessEqual(len(rows), 6)
            starts = [row for row in rows if row["kind"] == "child_start"]
            exits = [row for row in rows if row["kind"] == "child_exit"]
            self.assertEqual(len(starts), 1)
            self.assertEqual(len(exits), 1)
            self.assertEqual(starts[0]["child_process"], exits[0]["child_process"])
            self.assertEqual(rows[-1]["outcome"], "completed")
