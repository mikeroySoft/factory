"""Behavioral boundaries for the reported authorization mutation survivors."""
from __future__ import annotations

import unittest
from unittest import mock

from factory import dispatch, lifecycle, manage
from tests import test_actionable_rework as rework_tests
from tests import test_authorization_request as authorization_tests


class EscalationRoundSurvivorsTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp

    def round_number(self) -> int:
        return dispatch.escalation_packet(7, "failed", None, self.root)[1]

    def test_legacy_escalations_without_rounds_are_counted(self) -> None:
        fields = {
            "ticket": 7, "code": "gate_failed", "reason": "gate failed",
            "log": None, "packet": str(self.cfg.factory / "request.md"),
        }
        dispatch.record("escalate", **fields)
        dispatch.record("escalate", **fields)

        self.assertEqual(self.round_number(), 3)

    def test_manager_failure_duplicate_does_not_advance_the_round(self) -> None:
        packet = str(self.cfg.factory / "request.md")
        dispatch.record(
            "escalate", ticket=7, code="gate_failed", reason="gate failed",
            log=None, packet=packet, round=40,
        )
        dispatch.record(
            "escalate", ticket=7, code="manager_failed",
            reason="manager_failed", log=None,
            packet=packet, round=40,
        )

        self.assertEqual(self.round_number(), 41)

    def test_round_high_water_uses_only_requests_and_nonfailure_escalations(self) -> None:
        packet = str(self.cfg.factory / "request.md")
        request = {
            "code": "add_only_authorization", "paths": self.paths, "packet": packet,
            "log": None, "exhausted": False,
        }
        dispatch.record("manager-request", ticket=7, round=1, **request)
        dispatch.record(
            "manage", ticket=7, decision="AUTHORIZE", round=1,
            packet=packet, code="add_only_authorization",
        )
        dispatch.record(
            "escalate", ticket=7, code="gate_failed", reason="gate failed",
            log=None, packet=packet, round=2,
        )
        dispatch.record("manager-request", ticket=7, round=3, **request)
        dispatch.record(
            "manage", ticket=7, decision="AUTHORIZE", round=3,
            packet=packet, code="add_only_authorization",
        )
        dispatch.record(
            "escalate", ticket=7, code="gate_failed", reason="gate failed",
            log=None, packet=packet, round=4,
        )

        self.assertEqual(self.round_number(), 5)


class PendingRequestSurvivorsTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp
    command_result = authorization_tests.ManagerAuthorizationRequestTest.command_result

    def request_row(self, **changes: object) -> dict:
        row = {
            "event": "manager-request",
            "ticket": 7,
            "code": "add_only_authorization",
            "packet": str(self.cfg.factory / "z-request.md"),
            "round": 2,
            "paths": self.paths,
            "log": None,
            "exhausted": False,
        }
        row.update(changes)
        return row

    @staticmethod
    def completion(request: dict, **changes: object) -> dict:
        row = {
            "event": "manager-request-complete",
            "ticket": 7,
            "packet": request["packet"],
            "round": request["round"],
        }
        row.update(changes)
        return row

    def test_recovery_before_an_odd_position_request_does_not_complete_it(self) -> None:
        request = self.request_row()

        self.assertIs(
            manage._pending_add_only_row([self.completion(request), request], 7),
            request,
        )

    def test_later_management_is_found_after_a_journal_prefix(self) -> None:
        request = self.request_row()
        managed = {
            "event": "manage",
            "ticket": 7,
            "code": request["code"],
            "packet": request["packet"],
            "round": request["round"],
        }
        prior = self.request_row(
            packet=str(self.cfg.factory / "prior-request.md"), round=1,
        )
        rows = [prior, self.completion(prior), request, managed]

        self.assertIsNone(manage._pending_add_only_row(rows, 7))

    def test_management_requires_the_exact_packet_when_another_path_sorts_first(self) -> None:
        request = self.request_row()
        managed = {
            "event": "manage",
            "ticket": 7,
            "code": request["code"],
            "packet": str(self.cfg.factory / "a-other-request.md"),
            "round": request["round"],
        }

        self.assertIs(manage._pending_add_only_row([request, managed], 7), request)

    def test_large_recovery_round_matches_by_value(self) -> None:
        packet = self.cfg.factory / "request.md"
        packet.write_text("authorization evidence")
        round_number = 10000
        self.cfg.manager_rounds = round_number
        dispatch.record(
            "manager-request", ticket=7, code="add_only_authorization",
            packet=str(packet), round=round_number, paths=self.paths,
            log=None, exhausted=False,
        )
        dispatch.record(
            "manager-request-failed", ticket=7, packet=str(packet),
            round=round_number, reason="application failed",
        )
        request = next(
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "manager-request"
        )

        with mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(manage, "_manager_decision", return_value=None) as decide, \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(dispatch, "escalate") as escalate:
            manage._handle_add_only_request(7, self.issue, request)

        decide.assert_not_called()
        self.assertEqual(escalate.call_args.kwargs["round_number"], round_number)
        self.assertIn("application failed", escalate.call_args.args[1])


class ManagerDecisionSurvivorsTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp
    command_result = authorization_tests.ManagerAuthorizationRequestTest.command_result

    def test_nonhuman_decision_that_sorts_after_human_is_applied(self) -> None:
        packet = self.cfg.factory / "request.md"
        packet.write_text("authorization evidence")
        request = {
            "event": "manager-request",
            "ticket": 7,
            "code": "add_only_authorization",
            "packet": str(packet),
            "round": 1,
            "paths": self.paths,
            "log": None,
            "exhausted": False,
        }

        with mock.patch.object(lifecycle, "read_events", return_value=[request]), \
                mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(
                    manage, "_manager_decision",
                    return_value=("REWRITE", "Clarified acceptance criteria", None),
                ), \
                mock.patch.object(dispatch, "gh_json", return_value={
                    "title": self.issue["title"], "body": self.issue["body"],
                }), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(dispatch, "escalate") as escalate:
            manage._handle_add_only_request(7, self.issue, request)

        body_edits = [
            args[args.index("--body") + 1] for args in self.commands
            if args[:3] == ["gh", "issue", "edit"] and "--body" in args
        ]
        self.assertEqual(body_edits, ["Clarified acceptance criteria"])
        escalate.assert_not_called()


class ReviewRoundSurvivorTest(unittest.TestCase):
    setUp = rework_tests.ActionableReworkTest.setUp
    patch = rework_tests.ActionableReworkTest.patch
    github = rework_tests.ActionableReworkTest.github
    command_result = rework_tests.ActionableReworkTest.command_result
    work = rework_tests.ActionableReworkTest.work
    configure_reviews = rework_tests.ActionableReworkTest.configure_reviews

    def test_second_rework_advances_to_the_third_review_round(self) -> None:
        self.configure_reviews(("APPROVE", "REVISE"))

        dispatch.process_ticket(self.issue, 1, False, forced=True)

        ticket_exit = next(
            row for row in reversed(lifecycle.read_events(dispatch.EVENTS))
            if row.get("event") == "lifecycle"
            and row.get("kind") == "exit"
            and row.get("stage") == "ticket"
        )
        self.assertEqual(ticket_exit["review_round"], 3)


if __name__ == "__main__":
    unittest.main()
