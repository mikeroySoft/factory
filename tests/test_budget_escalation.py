"""A budget-killed bounce is not a failed project gate."""
from __future__ import annotations

import json
import os
import unittest
from contextlib import chdir
from pathlib import Path
from unittest import mock

from factory import config, dispatch, generation, lifecycle
from tests import test_dispatch_lifecycle as fixtures


class BudgetEscalationTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config

    def test_budget_killed_review_bounce_escalates_budget(self):
        clock = [0]
        calls = []
        original_worker = dispatch.run_worker
        original_run = dispatch.run
        comments = []

        def worker(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return original_worker(*args, **kwargs)
            clock[0] = 120
            return 143, "budget_min"

        def run(cmd, **kwargs):
            if cmd[:3] == ["gh", "issue", "comment"]:
                comments.append(cmd[cmd.index("--body") + 1])
            return original_run(cmd, **kwargs)

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])):
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 1, False, forced=True)

        events = lifecycle.read_events(self.events)
        attempts = [row for row in events if row.get("event") == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["PASS", None])
        self.assertEqual(attempts[-1]["worker_exit"], 143)
        self.assertEqual(attempts[-1]["timeout"], "budget_min")
        self.assertEqual(attempts[-1]["reason"], "budget_exceeded")
        escalation, = [row for row in events if row.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "budget")
        for text in (escalation["reason"], Path(escalation["packet"]).read_text(), comments[-1]):
            self.assertIn("budget_min", text)
            self.assertIn("budget_exceeded", text)
            self.assertNotIn("gate failed after review bounce", text)
        # The ordinary owner-requeue rule can consume the classified outcome unchanged.
        self.assertEqual(generation.owner_requeue(events, 7)["generation"], 1)

    def test_only_current_budget_cut_after_passing_gate_is_reclassified(self):
        passed = {"event": "attempt", "ticket": 7, "gate": "PASS"}
        cut = {
            "event": "attempt", "ticket": 7, "gate": None, "worker_exit": 143,
            "timeout": "budget_min", "reason": "budget_exceeded", "log": "worker.log",
        }
        cases = [
            ("budget_min", [passed, cut], "budget"),
            ("budget_exceeded", [passed, {**cut, "timeout": "budget_exceeded"}], "budget"),
            ("reason_only", [passed, {**cut, "timeout": None}], "budget"),
            ("failed_gate", [{**passed, "gate": "FAIL"}, cut], "gate_failed"),
            ("missing_gate", [cut], "gate_failed"),
            ("other_ticket", [{**passed, "ticket": 8}, cut], "gate_failed"),
            ("current_gate_failed", [passed, {**cut, "gate": "FAIL"}], "gate_failed"),
            ("not_budget", [passed, {**cut, "timeout": "idle_timeout", "reason": "stuck"}], "gate_failed"),
            ("stale_log", [passed, {**cut, "log": "old.log"}], "gate_failed"),
            ("new_generation", [
                passed, {"event": "generation", "ticket": 7, "generation": 1, "intent": "resume"}, cut,
            ], "gate_failed"),
            ("pr_gate_failed", [
                passed, {"event": "decision", "ticket": 7, "stage": "gate", "chosen": "mutation"}, cut,
            ], "gate_failed"),
        ]
        for name, rows, expected in cases:
            with self.subTest(name=name), chdir(self.repo), mock.patch.dict(os.environ, self.env):
                dispatch.configure(config.load(self.repo))
                self.events.parent.mkdir(exist_ok=True)
                self.events.write_text("".join(json.dumps(row) + "\n" for row in rows))
                with mock.patch.object(dispatch, "run") as run:
                    run.return_value.returncode = 0
                    run.return_value.stdout = ""
                    dispatch.escalate(7, "gate failed after worker", Path("worker.log"), code="gate_failed")
                escalation, = [
                    row for row in lifecycle.read_events(self.events) if row.get("event") == "escalate"
                ]
                self.assertEqual(escalation["code"], expected)
                if expected == "gate_failed":
                    self.assertEqual(escalation["reason"], "gate failed after worker")


if __name__ == "__main__":
    unittest.main()
