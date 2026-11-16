"""Behavioral checks for per-pipeline lifecycle lanes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import lifecycle

ROOT = Path(__file__).resolve().parents[1]


class LifecycleLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop(lifecycle.CONTEXT_ENV, None)

    def test_lane_inherits_through_nested_and_subprocess_scopes_and_audits(self) -> None:
        with lifecycle.scope(self.path, "dispatcher", dispatcher=True, lane=4) as dispatcher:
            lifecycle.append(self.path, {"event": "attempt", "ticket": 17})
            with lifecycle.scope(self.path, "ticket", ticket=17) as ticket:
                ticket.emit("result")
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        """
import sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path('/unused/events.jsonl'), 'worker'):
    lifecycle.append(Path(sys.argv[1]), {'event': 'worker-audit', 'ticket': 17})
""",
                        str(self.path),
                    ],
                    cwd=ROOT,
                    env=ticket.env(),
                    check=True,
                )

        self.assertEqual((dispatcher.lane, ticket.lane), (4, 4))
        rows = lifecycle.read_events(self.path)
        self.assertTrue(all(row.get("lane") == 4 for row in rows))
        states = {state["stage"]: state for state in lifecycle.observe(self.path)}
        self.assertEqual(states["worker"]["lane"], 4)
        self.assertEqual(states["worker"]["parent_execution_id"], ticket.execution_id)

    def test_reconciled_interruption_retains_lane_in_terminal_and_projection(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os, sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'worker', lane=6):
    os._exit(0)
""",
                str(self.path),
            ],
            cwd=ROOT,
            check=True,
        )

        state = lifecycle.observe(self.path)[0]
        self.assertEqual((state["state"], state["lane"]), ("interrupted", 6))
        self.assertEqual(state["events"][-1]["lane"], 6)
        self.assertTrue(state["events"][-1]["reconciled"])

    def test_old_rows_without_lane_remain_valid_and_project_as_unlaned(self) -> None:
        with lifecycle.scope(self.path, "worker") as execution:
            lifecycle.append(self.path, {"event": "attempt", "ticket": 3})
        rows = lifecycle.read_events(self.path)
        for row in rows:
            row.pop("lane", None)
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))

        lifecycle_rows = [row for row in lifecycle.read_events(self.path) if row.get("event") == "lifecycle"]
        self.assertTrue(all(lifecycle._lifecycle(row) for row in lifecycle_rows))
        self.assertNotIn("lane", next(row for row in rows if row.get("event") == "attempt"))
        self.assertIsNone(lifecycle.observe(self.path)[0]["lane"])
        self.assertIsNone(execution.lane)

    def test_lane_must_be_a_positive_integer(self) -> None:
        for lane in (0, -1, True, "1"):
            with self.subTest(lane=lane), self.assertRaises(ValueError):
                lifecycle.Execution(self.path, "worker", lane=lane)


if __name__ == "__main__":
    unittest.main()
