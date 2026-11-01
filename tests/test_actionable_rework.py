"""Actionable rework uses the round budget, not the human queue."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import test_risk_merge as risk_tests
from factory import config, dispatch, lifecycle
_REFRESH_CONTEXT = dispatch.refresh_requeue_context


SURVIVORS = (
    "- mutation: FAIL\n\n## mutation\n```text\n"
    "factory/dispatch.py:10: NumberReplacer `1` -> `2`\n"
    "ran 1 of 2 mutants in 1s: 1 survived; 1 survived\n"
    "incomplete: 1 mutants not started and 0 cut short by the 1s deadline\n```\n"
)


def finding(verdict):
    rows = [{"severity": "important", "pass": "bug", "path": "factory/dispatch.py",
             "line": 10, "summary": "Handle the missing branch."}] if verdict == "REVISE" else []
    return "```json\n" + json.dumps({"verdict": verdict, "findings": rows}) + "\n```\nVERDICT: " + verdict


class ActionableReworkTest(unittest.TestCase):
    def setUp(self):
        risk_tests.HighPipelineBoundaryTest.setUp(self)
        self.patch(dispatch, "refresh_requeue_context", side_effect=_REFRESH_CONTEXT)

    patch = risk_tests.HighPipelineBoundaryTest.patch
    github = risk_tests.HighPipelineBoundaryTest.github
    command_result = risk_tests.HighPipelineBoundaryTest.command_result

    def work(self, *args, **kwargs):
        result = risk_tests.HighPipelineBoundaryTest.work(self, *args, **kwargs)
        path = self.wt / ".factory" / "handoff-7.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text("## Changed\nDone.\n## Still unverified\nNone.\n## Next\nNothing.\n")
        if getattr(self, "expire", False):
            self.clock = 1000
        return result

    def configure_reviews(self, verdicts, mutation=False):
        self.clock = 0
        self.patch(dispatch.time, "monotonic", side_effect=lambda: self.clock)
        self.cfg.review_rounds = 2
        self.patch(dispatch, "run_gate", return_value=(not mutation, SURVIVORS if mutation else "- mutation: PASS\n"))

        def review(_wt, n, _report, head, *, role="primary", **_kwargs):
            verdict = verdicts[role == "second"]
            row = {"ticket": n, "head": head, "actual_head": head, "accepted": True,
                   "verdict": verdict, "role": role, "model": f"{role}/model"}
            dispatch.record("review", **row)
            pending = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
            pending[n] = row
            return verdict, finding(verdict)
        self.patch(dispatch, "review", side_effect=review)

    def rework_rows(self):
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "review-rework"]

    def test_expired_actionable_findings_are_carried_and_charged_once(self):
        self.configure_reviews(("APPROVE", "REVISE"), mutation=True)
        self.expire = True
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.escalate.assert_not_called()
        self.assertEqual(self.heads, [risk_tests.HEAD])
        rows = self.rework_rows()
        self.assertEqual([row["round"] for row in rows], [1])
        self.assertTrue(rows[0]["deferred"])
        self.assertIn("Handle the missing branch", rows[0]["findings"])
        self.assertIn("NumberReplacer", rows[0]["findings"])
        self.assertIn("--remove-assignee", self.command.call_args.args[0])
        # The next pass receives the packet, and cannot reset the ticket's round budget.
        self.clock = 0
        self.expire = False
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertIn("Handle the missing branch", self.worker.call_args_list[1].args[4])
        self.assertEqual([row["round"] for row in self.rework_rows()], [1, 2])
        self.assertEqual(self.escalate.call_args.kwargs["code"], "review_revise")

    def test_expired_mutation_only_findings_are_carried(self):
        self.configure_reviews(("APPROVE", "APPROVE"), mutation=True)
        self.expire = True
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.escalate.assert_not_called()
        self.assertEqual([row["round"] for row in self.rework_rows()], [1])
        self.assertIn("NumberReplacer", self.rework_rows()[0]["findings"])

    def test_actionable_disagreement_can_use_more_than_one_round(self):
        self.configure_reviews(("APPROVE", "REVISE"))
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertEqual(len(self.heads), 3)
        self.assertEqual([row["round"] for row in self.rework_rows()], [1, 2])
        self.assertEqual(self.escalate.call_args.kwargs["code"], "review_revise")
        self.assertIn("Handle the missing branch", self.worker.call_args.args[4])

    def test_mutation_only_disagreement_uses_normal_round_budget(self):
        self.configure_reviews(("APPROVE", "APPROVE"), mutation=True)
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertEqual(len(self.heads), 3)
        self.assertEqual([row["round"] for row in self.rework_rows()], [1, 2])
        self.assertEqual(self.escalate.call_args.kwargs["code"], "review_revise")

    def test_spent_round_budget_escalates_without_worker_bounce(self):
        self.configure_reviews(("APPROVE", "REVISE"), mutation=True)
        self.cfg.review_rounds = 0
        self.expire = True
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertEqual(self.heads, [risk_tests.HEAD])
        self.assertEqual(self.rework_rows(), [])
        self.assertEqual(self.escalate.call_args.kwargs["code"], "review_revise")

    def test_real_reject_does_not_requeue(self):
        self.configure_reviews(("APPROVE", "REJECT"))
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertEqual(self.heads, [risk_tests.HEAD])
        self.assertEqual(self.rework_rows(), [])
        self.escalate.assert_called_once()

    def test_protected_pr_failure_does_not_requeue(self):
        self.configure_reviews(("APPROVE", "APPROVE"))
        self.patch(dispatch, "run_gate", return_value=(False, "- mutation: PASS\n- protected-paths: FAIL\n"))
        self.expire = True
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertEqual(self.rework_rows(), [])
        self.assertEqual(self.escalate.call_args.kwargs["code"], "protected_paths")
