"""Consumer boundaries: uncertain activity and mixed legacy/lifecycle journals."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dashboard, dispatch, learn, lifecycle


class LifecycleConsumersTest(unittest.TestCase):
    def test_lifecycle_rows_do_not_change_spend_or_latest_finished_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            factory = Path(d)
            events = factory / "events.jsonl"
            rows = [
                {"event": "claimed", "ticket": 1, "at": "2026-01-01T00:00:00Z", "title": "older"},
                {"event": "attempt", "ticket": 1, "at": "2026-01-01T00:00:01Z", "seconds": 12, "cost": 0.25},
                {"event": "merged", "ticket": 1, "at": "2026-01-01T00:00:02Z"},
                {"event": "claimed", "ticket": 2, "at": "2026-01-02T00:00:00Z", "title": "newer"},
                {"event": "attempt", "ticket": 2, "at": "2026-01-02T00:00:01Z", "seconds": 8},
                {"event": "escalate", "ticket": 2, "at": "2026-01-02T00:00:02Z", "reason": "gate failed"},
                {"event": "lifecycle", "ticket": 1, "at": "2026-01-03T00:00:00Z", "outcome": "interrupted", "seconds": 100},
                {"event": "lifecycle", "ticket": None, "at": "2026-01-03T00:00:00Z"},
                {"event": "attempt", "ticket": 3, "at": "2026-01-03T00:00:00Z", "seconds": "invalid"},
                None, [], 12, {}, {"ticket": []},
            ]
            events.write_text("\n".join(json.dumps(row) for row in rows) + '\n{"event":"attempt"')
            with patch.multiple(dispatch, FACTORY=factory, EVENTS=events, create=True):
                self.assertEqual(dashboard.spend_by_ticket(), {
                    1: {"seconds": 12, "cost": 0.25, "rounds": 1},
                    2: {"seconds": 8, "cost": None, "rounds": 1},
                })
                chosen, evidence = learn.evidence(1)
                self.assertEqual(chosen, [2])
                self.assertIn("gate failed", evidence)
                self.assertNotIn("lifecycle", evidence)
                self.assertNotIn("interrupted", learn.evidence(10)[1])

    def test_phase_requires_one_authoritative_unresolved_leaf(self) -> None:
        def execution(identity: str, stage: str, parent: str | None = None, state: str = "active") -> dict:
            return {
                "execution_id": identity, "parent_execution_id": parent,
                "stage": stage, "state": state, "attempt": 2,
                "entered_at": "2026-01-01T00:00:00Z",
            }

        ticket = execution("ticket", "ticket")
        worker = execution("worker", "worker", "ticket")
        gate = execution("gate", "gate", "worker")
        self.assertEqual(dashboard.phase_of([ticket, worker, gate])["artifact"], "gate")
        gate["state"] = "completed"
        self.assertEqual(dashboard.phase_of([ticket, worker, gate])["artifact"], "worker")
        gate["state"] = "unknown"
        self.assertIsNone(dashboard.phase_of([ticket, worker, gate]))
        gate["state"] = "completed"
        review = execution("review", "review", "ticket")
        self.assertIsNone(dashboard.phase_of([ticket, worker, gate, review]))
        worker["state"] = "completed"
        self.assertEqual(dashboard.phase_of([ticket, worker, gate, review])["artifact"], "review")
        gate.update(state="unknown", ended_at="2026-01-01T00:00:01Z")
        self.assertEqual(dashboard.phase_of([ticket, worker, gate, review])["artifact"], "review")
        self.assertEqual(dashboard.phase_of([execution("merge", "merge")])["artifact"], "merge")

    def test_held_lock_and_artifacts_do_not_invent_a_phase(self) -> None:
        issue = {
            "number": 7, "title": "ticket", "state": "OPEN", "url": "issue/7",
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
            "closedAt": None,
        }
        disk = {
            "lock_held": True,
            "attempts": [{"attempt": 1, "mtime": "2026-01-02T00:00:00Z", "path": "old.log"}],
            "gate": None, "review": {"mtime": "2026-01-03T00:00:00Z"},
        }
        with patch.object(dashboard, "cfg", config.Config(Path("/unused"), "acme/widgets"), create=True):
            ticket = dashboard.build_ticket(issue, None, disk)
        self.assertEqual(ticket["stage"], "in-flight")
        self.assertIsNone(ticket["phase"])

    def test_snapshot_keeps_concurrent_and_no_ticket_executions_when_github_fails(self) -> None:
        with tempfile.TemporaryDirectory() as d, patch.dict(dashboard.__dict__), patch.dict(dispatch.__dict__), \
             patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
            root = Path(d)
            dashboard.configure(config.Config(root, "acme/widgets", lock=root / "gpu.lock"))
            executions = [
                lifecycle.Execution(dispatch.EVENTS, "dispatcher", dispatcher=True),
                lifecycle.Execution(dispatch.EVENTS, "triage"),
                lifecycle.Execution(dispatch.EVENTS, "worker", ticket=7),
                lifecycle.Execution(dispatch.EVENTS, "gate", ticket=8),
            ]
            for execution in executions:
                execution.emit("enter")
            with patch.object(dashboard, "github", side_effect=RuntimeError("offline")), \
                 patch.object(dashboard, "dispatcher", return_value={}), \
                 patch.object(dashboard, "upstream_state", return_value={}), \
                 patch.object(dashboard, "triage_llm_online", return_value=False):
                snap = dashboard.snapshot()
            self.assertEqual(snap["errors"], ["github: offline"])
            self.assertEqual(snap["tickets"], [])
            self.assertEqual(
                {(e["stage"], e["ticket"], e["state"]) for e in snap["executions"]},
                {("dispatcher", None, "active"), ("triage", None, "active"),
                 ("worker", 7, "active"), ("gate", 8, "active")},
            )
            for execution in executions:
                execution.emit("exit")


if __name__ == "__main__":
    unittest.main()
