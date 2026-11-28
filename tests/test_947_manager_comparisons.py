"""Behavioral coverage for the remaining #947 manager comparisons."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import dispatch, lifecycle, manage
from tests import test_authorization_request as authorization_fixture


class ManagerRoundComparisonTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def request_row(self, round_number: int, at: str) -> dict:
        packet = self.cfg.factory / "request.md"
        packet.write_text("authorization evidence")
        return {
            "event": "manager-request",
            "ticket": 7,
            "code": "add_only_authorization",
            "packet": str(packet),
            "round": round_number,
            "paths": self.paths,
            "log": None,
            "exhausted": False,
            "at": at,
        }

    @staticmethod
    def release(request: dict) -> dict:
        return {
            "event": "manager-request-release",
            "ticket": request["ticket"],
            "packet": request["packet"],
            "round": request["round"],
        }

    @staticmethod
    def receipt(request: dict, comment: int) -> dict:
        return {
            "event": "comment",
            "ticket": request["ticket"],
            "kind": "escalation",
            "comment": comment,
            "round": request["round"],
            "at": request["at"],
        }

    def test_prior_round_releases_do_not_hide_current_comment(self) -> None:
        first = self.request_row(1, "2026-11-28T00:00:00Z")
        prior = self.request_row(2, "2026-11-28T00:00:01Z")
        current = self.request_row(3, "2026-11-28T00:00:02Z")
        events = []
        for request in (first, prior):
            events.extend([
                request, self.release(request), self.receipt(request, 100 + request["round"]),
                {"event": "manager-request-complete", "ticket": 7,
                 "packet": request["packet"], "round": request["round"]},
            ])
        events.extend([current, self.receipt(current, 103)])

        with mock.patch.object(dispatch, "run") as run:
            self.assertTrue(manage._ensure_request_comment(7, current, events))

        run.assert_not_called()

    def test_valid_followup_rows_bound_pending_scans(self) -> None:
        request = self.request_row(1, "2026-11-28T00:00:00Z")
        release = self.release(request)
        complete = {
            "event": "manager-request-complete",
            "ticket": 7,
            "packet": request["packet"],
            "round": request["round"],
        }
        managed = {
            "event": "manage",
            "ticket": 7,
            "code": request["code"],
            "packet": request["packet"],
            "round": request["round"],
            "decision": "AUTHORIZE",
        }

        self.assertIs(manage._pending_add_only_row([request, release], 7), request)
        self.assertIsNone(manage._pending_add_only_row([request, release, complete], 7))
        self.assertIsNone(manage._pending_add_only_row([request, release, managed], 7))

    def test_release_boundary_keeps_only_the_later_comment_as_takeover_marker(self) -> None:
        request = self.request_row(1, "2026-11-28T00:00:00Z")
        release = self.release(request)
        receipt = self.receipt(request, 101)
        timeline = [{
            "event": "commented",
            "id": 101,
            "created_at": request["at"],
            "updated_at": request["at"],
            "actor": {"login": "factory-bot"},
        }]
        events = [request, release, receipt]

        with mock.patch.object(dispatch, "run") as run:
            self.assertTrue(manage._ensure_request_comment(7, request, events))
        run.assert_not_called()
        with mock.patch.object(manage.handoff, "timeline", return_value=timeline):
            self.assertFalse(manage.human_activity(7, request, events))


class ManagerDecisionComparisonTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def decision_context(self) -> tuple[dict, list[dict], list[dict]]:
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
            "at": "2026-11-28T00:00:00Z",
        }
        receipt = {
            "event": "comment",
            "ticket": 7,
            "kind": "escalation",
            "comment": 101,
            "round": 1,
            "at": request["at"],
        }
        timeline = [{
            "event": "commented",
            "id": 101,
            "created_at": request["at"],
            "updated_at": request["at"],
            "actor": {"login": "factory-bot"},
        }]
        return request, [request, receipt], timeline

    def run_decision(self, output: str, *, keywords: bool = False):
        request, events, timeline = self.decision_context()
        result = subprocess.CompletedProcess(["manager"], 0, output, "")
        with mock.patch.object(dispatch, "run", return_value=result), \
                mock.patch.object(manage, "selectable_workers", return_value={}), \
                mock.patch.object(manage.handoff, "timeline", return_value=timeline):
            if keywords:
                return manage._manager_decision(
                    n=7,
                    issue=self.issue,
                    trigger=request,
                    events=events,
                    queue_failure=True,
                    code="add_only_authorization",
                )
            return manage._manager_decision(
                7, self.issue, request, events,
                queue_failure=True, code="add_only_authorization",
            )

    def test_manager_decision_supports_keyword_leading_arguments(self) -> None:
        result = self.run_decision(
            'DECISION: AUTHORIZE\n{"justification":"required","guidance":"edit the named test"}',
            keywords=True,
        )

        self.assertEqual(result[0], "AUTHORIZE")
        row = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                   if row.get("event") == "manage")
        self.assertEqual((row["decision"], row["code"]), ("AUTHORIZE", "add_only_authorization"))

    def test_valid_notes_statuses_before_written_are_reported(self) -> None:
        outputs = {
            "empty_rejected": "DECISION: HUMAN\nstop\n\n```notes\n\n```\n",
            "oversize_rejected": (
                "DECISION: HUMAN\nstop\n\n```notes\n" + "x" * (manage.NOTES_CAP + 1) + "\n```\n"
            ),
        }
        self.assertTrue(all(status < "written" for status in outputs))

        for status, output in outputs.items():
            with self.subTest(status=status), mock.patch.object(dispatch, "log") as log:
                result = self.run_decision(output)
                self.assertEqual(result[0], "HUMAN")
                log.assert_called_once_with(
                    f"#7: manager notes {status}; keeping the existing file"
                )
                row = [row for row in lifecycle.read_events(dispatch.EVENTS)
                       if row.get("event") == "manage"][-1]
                self.assertEqual(row["notes"], status)


class PendingAuthorizationCodeComparisonTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def test_escalation_scan_finds_persisted_authorization_code(self) -> None:
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
            "at": "2026-11-28T00:00:00Z",
        }
        dispatch.record(**request)
        issue = {**self.issue, "state": "OPEN"}
        logs: list[str] = []

        def github(args: list[str]):
            if args[:2] == ["issue", "list"]:
                return []
            if args[:3] == ["issue", "view", "7"]:
                return issue
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch, "lock_held", return_value=False), \
                mock.patch.object(dispatch, "log", side_effect=logs.append):
            manage.escalation_pass(dry_run=True)

        self.assertEqual(logs, ["#7: would manage queued add-only authorization request"])


if __name__ == "__main__":
    unittest.main()
