"""A failed carried rework round must not lose its unresolved review on authorization."""
from __future__ import annotations

import unittest

from factory import dispatch, lifecycle
from tests import test_actionable_rework as rework_tests
from tests import test_authorization_rework as authorization_tests
from tests import test_risk_merge as risk_tests


class CarriedAuthorizationTest(unittest.TestCase):
    def test_failed_carried_round_preserves_findings_through_authorization(self):
        fixture = authorization_tests.AuthorizationReworkTest.fixture(self)
        findings = rework_tests.SURVIVORS
        carried = {"head": risk_tests.HEAD, "findings": findings, "pr_failed": True}
        dispatch.record("review-rework", ticket=7, round=1, deferred=True, **carried)
        calls = []

        def work(*args, **kwargs):
            calls.append(args[4])
            handoff = fixture.wt / ".factory" / "handoff-7.md"
            handoff.parent.mkdir(exist_ok=True)
            handoff.write_text("Needs: add-only-authorization tests/test_contract.py:12\n"
                               if len(calls) == 1 else "Done.\n")
            ok = len(calls) != 1
            dispatch.record(
                "attempt", ticket=7, head=risk_tests.HEAD, actual_head=risk_tests.HEAD,
                clean=True, gate="PASS" if ok else "FAIL", finished=True,
                worker_model="worker/model",
            )
            return ok, "- test: PASS\n" if ok else "- test: FAIL\n", fixture.root / "worker.log", risk_tests.HEAD

        fixture.worker.side_effect = work
        review = fixture.patch(dispatch, "pr_gate_and_review", return_value=("APPROVE", "approved", False))
        fixture.patch(dispatch, "approve_pr", return_value=True)

        dispatch.process_ticket(fixture.issue, 1, False)
        self.assertEqual(len(calls), 1)
        review.assert_not_called()
        fixture.escalate.assert_not_called()
        dispatch.process_ticket(fixture.issue, 1, False)

        self.assertEqual(len(calls), 2)
        self.assertIn(findings, calls[1])
        review.assert_not_called()
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "mutation_survivors_unresolved")
        self.assertEqual(dispatch._review_rounds_used(7), 1)
        request = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                       if row.get("event") == "manager-request")
        self.assertEqual(request["rework"], carried)


if __name__ == "__main__":
    unittest.main()
