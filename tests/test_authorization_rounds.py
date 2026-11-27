"""Manager authorization requests and ordinary escalations share one round sequence."""
from __future__ import annotations

import unittest
from unittest import mock

from factory import config, dispatch, manage
from tests import test_authorization_request as authorization_tests


class AuthorizationRoundsTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp
    command_result = authorization_tests.ManagerAuthorizationRequestTest.command_result
    events = authorization_tests.ManagerAuthorizationRequestTest.events
    request = authorization_tests.ManagerAuthorizationRequestTest.request

    def ordinary_escalation(self, reason: str) -> None:
        with mock.patch.object(dispatch, "run", side_effect=self.command_result):
            dispatch.escalate(7, reason, None, code="gate_failed")

    def manager_pass(self) -> None:
        issue = {**self.issue, "labels": [{"name": config.LABEL_HUMAN}]}
        with mock.patch.object(dispatch, "gh_json", side_effect=lambda args: (
            [issue] if args[:2] == ["issue", "list"] else issue
        )), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            manage.escalation_pass()
        self.assertTrue(any(
            args[:3] == ["gh", "issue", "edit"] and "--body" in args
            for args in self.commands
        ))

    def test_authorization_then_rewrite_advances_to_the_exhausted_round(self) -> None:
        self.cfg.manager_rounds = 2
        self.request()
        self.manager_output = "DECISION: REWRITE\nClarify the acceptance criteria."

        self.ordinary_escalation("gate failed after authorization")
        self.manager_pass()
        self.ordinary_escalation("gate failed after rewrite")
        self.manager_pass()

        rows = self.events()
        self.assertEqual(
            [(row["decision"], row["round"]) for row in rows if row.get("event") == "manage"],
            [("AUTHORIZE", 1), ("REWRITE", 2)],
        )
        self.assertEqual(
            [row["round"] for row in rows if row.get("event") == "escalate"],
            [2, 3],
        )
        self.assertEqual(len(self.manager_calls), self.cfg.manager_rounds)

    def test_third_round_is_applied_after_authorization_and_rewrite(self) -> None:
        self.cfg.manager_rounds = 3
        self.request()
        self.manager_output = "DECISION: REWRITE\nClarify the acceptance criteria."
        self.ordinary_escalation("gate failed after authorization")
        self.manager_pass()
        self.manager_output = "DECISION: RETRY\nUse the revised acceptance criteria."
        self.ordinary_escalation("gate failed after rewrite")
        self.manager_pass()

        self.assertEqual(
            [(row["decision"], row["round"]) for row in self.events() if row.get("event") == "manage"],
            [("AUTHORIZE", 1), ("REWRITE", 2), ("RETRY", 3)],
        )
        self.assertEqual(len(self.manager_calls), 3)

    def test_pr_frontier_round_does_not_consume_an_escalation_round(self) -> None:
        dispatch.record("manage", ticket=7, pr=70, round=99, decision="RETRY")
        _, round_number = dispatch.escalation_packet(7, "gate failed", None, self.cfg.root)
        self.assertEqual(round_number, 1)


if __name__ == "__main__":
    unittest.main()
