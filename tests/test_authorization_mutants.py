"""Behavioral regressions for add-only authorization mutation boundaries."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import dispatch, lifecycle, manage
from tests import test_authorization_request as authorization_tests


class AuthorizationMutantsTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp
    events = authorization_tests.ManagerAuthorizationRequestTest.events

    def _request_row(self, **changes):
        row = {
            "event": "manager-request",
            "ticket": 7,
            "code": "add_only_authorization",
            "packet": str(self.cfg.factory / "request.md"),
            "round": 2,
            "paths": self.paths,
            "at": "2026-01-02T00:00:00Z",
        }
        row.update(changes)
        return row

    def test_pending_request_ignores_matching_recovery_before_request(self):
        request = self._request_row()
        stale = {
            "event": "manager-request-complete",
            "ticket": 7,
            "packet": request["packet"],
            "round": request["round"],
        }
        rows = [stale, {"event": "attempt", "ticket": 7}, request]

        self.assertIs(manage._pending_add_only_row(rows, 7), request)

    def test_pending_request_recovery_requires_packet_and_round(self):
        request = self._request_row()
        complete = {
            "event": "manager-request-complete",
            "ticket": 7,
            "packet": request["packet"],
            "round": request["round"],
        }

        self.assertIsNone(manage._pending_add_only_row([request, complete], 7))
        self.assertIs(manage._pending_add_only_row(
            [request, {**complete, "packet": "another-packet"}], 7,
        ), request)
        self.assertIs(manage._pending_add_only_row(
            [request, {**complete, "round": 1}], 7,
        ), request)
        self.assertIs(manage._pending_add_only_row(
            [request, {**complete, "round": 3}], 7,
        ), request)

    def test_existing_request_comment_requires_event_round_and_time(self):
        request = self._request_row()
        receipt = {
            "event": "comment",
            "ticket": 7,
            "kind": "escalation",
            "round": 2,
            "at": request["at"],
        }
        with mock.patch.object(dispatch, "run") as run:
            self.assertTrue(manage._ensure_request_comment(7, request, [receipt]))
            run.assert_not_called()

        impostors = (
            {**receipt, "event": "review"},
            {**receipt, "round": 1},
            {**receipt, "at": "2026-01-01T23:59:59Z"},
        )
        result = subprocess.CompletedProcess(
            [], 0, "https://github.com/acme/widgets/issues/7#issuecomment-9\n", "",
        )
        for impostor in impostors:
            with self.subTest(impostor=impostor), \
                    mock.patch.object(dispatch, "run", return_value=result) as run, \
                    mock.patch.object(dispatch, "comment_receipt", return_value=True):
                self.assertTrue(manage._ensure_request_comment(7, request, [impostor]))
                run.assert_called_once()
        with mock.patch.object(dispatch, "run") as run:
            self.assertTrue(manage._ensure_request_comment(
                7, request, [{**receipt, "at": "2026-01-02T00:00:01Z"}],
            ))
            run.assert_not_called()

    def test_manager_takeover_errors_have_queue_and_escalation_semantics(self):
        packet = self.cfg.factory / "request.md"
        packet.write_text("evidence")
        request = self._request_row(packet=str(packet))
        result = subprocess.CompletedProcess([], 0, "DECISION: HUMAN\nstop", "")
        for error in (subprocess.CalledProcessError(1, ["gh"]), ValueError("bad timeline")):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(dispatch, "run", return_value=result), \
                    mock.patch.object(manage, "selectable_workers", return_value={}), \
                    mock.patch.object(manage, "human_activity", side_effect=error):
                self.assertIsNone(manage._manager_decision(
                    7, self.issue, request, [], queue_failure=True,
                ))
                with self.assertRaises(type(error)):
                    manage._manager_decision(7, self.issue, request, [])

    def test_declined_request_preserves_optional_worker_log(self):
        packet = self.cfg.factory / "request.md"
        packet.write_text("failed gate evidence")
        for logfile in (None, self.cfg.factory / "worker.log"):
            request = self._request_row(packet=str(packet), log=str(logfile) if logfile else None)
            with self.subTest(logfile=logfile), \
                    mock.patch.object(lifecycle, "read_events", return_value=[request]), \
                    mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                    mock.patch.object(manage, "human_activity", return_value=False), \
                    mock.patch.object(manage, "_manager_decision", return_value=("HUMAN", "declined", None)), \
                    mock.patch.object(dispatch, "escalate") as escalate:
                manage._handle_add_only_request(7, self.issue, request)
            self.assertEqual(escalate.call_args.args[2], logfile)
            self.assertIn("failed gate evidence", escalate.call_args.kwargs["extra"])

    def test_request_failure_preserves_exception_diagnostic_without_stderr(self):
        with lifecycle.scope(dispatch.EVENTS, "ticket", ticket=7) as execution, \
                mock.patch.object(manage, "_handle_add_only_request", side_effect=OSError("transport disconnected")):
            manage.request_add_only_authorization(7, self.issue, self.paths, self.report, None)
            self.assertEqual(execution.outcome, "mechanism_failure")
            self.assertEqual(execution.error, "transport disconnected")

    def test_notes_rejection_is_reported_but_absent_notes_are_not(self):
        packet = self.cfg.factory / "request.md"
        packet.write_text("failed gate evidence")
        request = self._request_row(packet=str(packet))
        outputs = ("DECISION: HUMAN\nstop\n```notes\n\n```\n", "DECISION: HUMAN\nstop\n")
        for output, rejected in zip(outputs, (True, False)):
            result = subprocess.CompletedProcess([], 0, output, "")
            with mock.patch.object(dispatch, "run", return_value=result), \
                    mock.patch.object(manage, "selectable_workers", return_value={}), \
                    mock.patch.object(manage, "human_activity", return_value=False), \
                    mock.patch.object(dispatch, "log") as log:
                manage._manager_decision(7, self.issue, request, [request])
            if rejected:
                log.assert_called_once_with("#7: manager notes empty_rejected; keeping the existing file")
            else:
                log.assert_not_called()

    def test_recovery_failure_must_follow_request_and_match_packet_and_round(self):
        packet = self.cfg.factory / "request.md"
        packet.write_text("evidence")
        request = self._request_row(packet=str(packet))
        matching = {
            "event": "manager-request-failed",
            "ticket": 7,
            "packet": str(packet),
            "round": 2,
            "reason": "application failed",
        }
        cases = (
            ("before", [matching, request]),
            ("packet before", [request, {**matching, "packet": "/a.md"}]),
            ("packet after", [request, {**matching, "packet": "/z.md"}]),
            ("round before", [request, {**matching, "round": 1}]),
            ("round after", [request, {**matching, "round": 3}]),
        )
        for name, rows in cases:
            with self.subTest(name=name), \
                    mock.patch.object(lifecycle, "read_events", return_value=rows), \
                    mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                    mock.patch.object(manage, "human_activity", return_value=False), \
                    mock.patch.object(manage, "_manager_decision", return_value=None) as decide, \
                    mock.patch.object(dispatch, "escalate") as escalate:
                manage._handle_add_only_request(7, self.issue, request)
                decide.assert_called_once()
                escalate.assert_not_called()

    def test_matching_recovery_escalates_without_missing_original_log(self):
        packet = self.cfg.factory / "request.md"
        packet.write_text("evidence")
        request = self._request_row(packet=str(packet))
        failure = {
            "event": "manager-request-failed",
            "ticket": 7,
            "packet": str(packet),
            "round": 2,
            "reason": "application failed",
        }
        with mock.patch.object(lifecycle, "read_events", return_value=[request, failure]), \
                mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(dispatch, "escalate") as escalate:
            manage._handle_add_only_request(7, self.issue, request)

        self.assertIsNone(escalate.call_args.args[2])
        self.assertEqual(escalate.call_args.kwargs["round_number"], 2)
        self.assertIn("evidence", escalate.call_args.kwargs["extra"])

    def test_request_round_below_limit_is_not_exhausted(self):
        packet = self.cfg.factory / "request.md"
        packet.write_text("evidence")
        self.cfg.manager_rounds = 2
        with mock.patch.object(dispatch, "escalation_packet", return_value=(packet, 1)), \
                mock.patch.object(manage, "_handle_add_only_request") as handle, \
                mock.patch.object(dispatch, "escalate") as escalate:
            manage.request_add_only_authorization(7, self.issue, self.paths, self.report, None)

        request = next(row for row in self.events() if row.get("event") == "manager-request")
        self.assertFalse(request["exhausted"])
        handle.assert_called_once()
        escalate.assert_not_called()

    def test_initiative_request_does_not_stop_later_pending_ticket(self):
        for ticket in (7, 8):
            packet = self.cfg.factory / f"{ticket}.md"
            packet.write_text("evidence")
            dispatch.record(
                "manager-request", ticket=ticket, code="add_only_authorization",
                packet=str(packet), round=1, paths=self.paths, exhausted=False,
            )
        issues = [
            {**self.issue, "number": 7},
            {**self.issue, "number": 8},
        ]
        logs = []
        with mock.patch.object(dispatch, "gh_json", return_value=issues), \
                mock.patch.object(
                    dispatch, "initiative_kind",
                    side_effect=lambda ticket: "initiative" if ticket == 7 else None,
                ), \
                mock.patch.object(dispatch, "lock_held", return_value=False), \
                mock.patch.object(dispatch, "log", side_effect=logs.append):
            manage.escalation_pass(dry_run=True)

        self.assertEqual(logs, [
            "#7: refused (initiative records are never managed)",
            "#8: would manage queued add-only authorization request",
        ])


if __name__ == "__main__":
    unittest.main()
