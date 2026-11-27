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
        with mock.patch.object(dispatch, "gh_json", return_value=[issue]), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            manage.escalation_pass()

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


if __name__ == "__main__":
    unittest.main()
