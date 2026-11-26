"""#535: causal terminal receipts and exact post-FIX outcome boundaries."""
from __future__ import annotations

import unittest
from datetime import timedelta
from unittest import mock

from factory import dispatch, stats, trace
from tests import test_manager_fix_outcomes as manager_suite
from tests.test_post_fix_boundaries import journal
from tests.test_post_fix_models import NOW, START, at


class ManagerFixCausalityTest(unittest.TestCase):
    setUp = manager_suite.ManagerFixOutcomeTest.setUp
    patched = manager_suite.ManagerFixOutcomeTest.patched
    events = manager_suite.ManagerFixOutcomeTest.events
    prepare_fix = manager_suite.ManagerFixOutcomeTest.prepare_fix
    apply_fix = manager_suite.ManagerFixOutcomeTest.apply_fix

    def test_terminal_receipt_follows_manager_decision_only_when_one_exists(self) -> None:
        self.prepare_fix()
        for has_parent in (True, False):
            with self.subTest(has_parent=has_parent):
                dispatch.EVENTS.unlink()
                dispatch.record("manage", ticket=9, decision="FIX", round=1)
                causes = []
                if has_parent:
                    parent = trace.decision(
                        ticket=9, stage="manager", chosen="FIX", inputs=[], options=[],
                        rationale="Repair the gate", causes=[], cost=None,
                        actor={"kind": "model", "name": "manager", "model": "fixture/manager"},
                    )
                    dispatch.record("decision", **parent)
                    causes = [{"decision_id": parent["decision_id"], "relation": "follows"}]
                with self.patched(), \
                        mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                        mock.patch.object(dispatch, "commit_leftovers"), \
                        mock.patch.object(dispatch, "run_gate", return_value=(False, "gate failed")):
                    self.apply_fix()

                [receipt] = [row for row in self.events("decision") if row["chosen"] == "FIX_FAILED"]
                self.assertEqual(receipt["stage"], "manager")
                self.assertEqual(receipt["causes"], causes)
                [attempt] = self.events("attempt")
                self.assertEqual((attempt["gate"], attempt["worker_model"]), ("FAIL", "fixture/pinned"))
                self.assertEqual(self.events("escalate"), [])


class PostFixDecisionBoundaryTest(unittest.TestCase):
    def test_only_exact_manager_fix_failed_resolves_the_cohort(self) -> None:
        for stage, chosen, terminal in (
            ("gate", "FIX_FAILED", False),
            ("review", "FIX_FAILED", False),
            ("manager", "FIX", False),
            ("manager", "HUMAN", False),
            ("manager", "FIX_FAILED_EXTRA", False),
            ("manager", "FIX_FAILED", True),
        ):
            with self.subTest(stage=stage, chosen=chosen):
                rows = journal([
                    {"at": at(timedelta(hours=-3)), "event": "manage", "ticket": 9, "decision": "FIX"},
                    {"at": at(timedelta(hours=-2)), "event": "claimed", "ticket": 9,
                     "worker_model": "fixture/pinned"},
                    {"event": "decision", **trace.decision(
                        ticket=9, stage=stage, chosen=chosen, inputs=[], options=[],
                        rationale="Decision boundary", causes=[], cost=None,
                        actor={"kind": "deterministic", "name": "fixture"},
                        at=at(timedelta(hours=-1)),
                    )},
                ])

                result = stats.post_fix_metrics(rows, START, NOW)

                self.assertEqual(result["by_worker_model"], {
                    "fixture/pinned": {
                        "merged": 0, "re_escalated": int(terminal), "pending": int(not terminal),
                        "resolved": int(terminal), "merge_rate": 0.0 if terminal else None,
                    },
                })


if __name__ == "__main__":
    unittest.main()
