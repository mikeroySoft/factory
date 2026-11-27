"""Behavioral edge coverage for queued add-only manager authorization."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage


class AuthorizationEdgesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = config.Config(self.root, "acme/widgets", manager=["manager", "{prompt}"], manager_rounds=2)
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        self.packet = self.cfg.factory / "request.md"
        self.packet.write_text("authorization evidence")
        self.issue = {"number": 7, "title": "Ticket", "body": "Scope", "labels": []}
        self.request = {
            "event": "manager-request", "ticket": 7, "code": "add_only_authorization",
            "packet": str(self.packet), "round": 1, "paths": ["tests/test_policy.py:9"],
            "at": "2020-01-02T10:00:00Z", "log": str(self.root / "worker.log"),
        }

    def test_pending_request_requires_matching_event_code_packet_and_round(self) -> None:
        managed = {
            "event": "manage", "ticket": 7, "code": "add_only_authorization",
            "packet": str(self.packet), "round": 1,
        }
        unrelated = {
            "event": "attempt", "ticket": 7, "code": "add_only_authorization",
            "packet": str(self.packet), "round": 1,
        }
        cases = {
            "unmanaged": ([self.request], True),
            "matching manage": ([self.request, managed], False),
            "unrelated following row": ([self.request, unrelated], True),
            "wrong code sorts earlier": ([self.request, {**managed, "code": "aaa"}], True),
            "management before request": ([managed, self.request], True),
            "request-like earlier event": ([{**self.request, "event": "manager-queue"}], False),
            "other event": ([{**self.request, "event": "escalate"}], False),
            "other code": ([{**self.request, "code": "manager_failed"}], False),
            "other packet": ([self.request, {**managed, "packet": "elsewhere"}], True),
            "other round": ([self.request, {**managed, "round": 2}], True),
            "exhausted": ([{**self.request, "exhausted": True}], False),
        }
        for name, (rows, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(manage._pending_add_only_row(rows, 7) is not None, expected)

    def test_comment_receipt_is_bound_to_request_round_and_time(self) -> None:
        outputs = iter((321, 322, 323))

        def post(*_args, **_kwargs):
            comment = next(outputs)
            return subprocess.CompletedProcess(
                [], 0, f"https://github.com/acme/widgets/issues/7#issuecomment-{comment}\n", "",
            )

        with mock.patch.object(dispatch, "run", side_effect=post):
            self.assertTrue(manage._ensure_request_comment(7, self.request, []))
            receipt = lifecycle.read_events(dispatch.EVENTS)[-1]
            self.assertEqual((receipt["kind"], receipt["comment"], receipt["round"]), ("escalation", 321, 1))
            self.assertGreaterEqual(receipt["at"], self.request["at"])
            self.assertTrue(manage._ensure_request_comment(
                7, self.request, [{**receipt, "at": "2020-01-01T10:00:00Z"}],
            ))
            impostors = [
                {**receipt, "round": 2},
                {**receipt, "event": "attempt"},
                {**receipt, "kind": "review"},
            ]
            self.assertTrue(manage._ensure_request_comment(7, self.request, impostors))
            equal_time = {**receipt, "at": self.request["at"]}
            self.assertTrue(manage._ensure_request_comment(7, self.request, [equal_time]))
        receipts = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "comment"]
        self.assertEqual([(row["comment"], row["round"]) for row in receipts], [(321, 1), (322, 1), (323, 1)])

    def test_invalid_comment_receipt_leaves_request_pending(self) -> None:
        failed = subprocess.CompletedProcess([], 0, "comment created without a URL", "")
        with mock.patch.object(dispatch, "run", return_value=failed):
            self.assertFalse(manage._ensure_request_comment(7, self.request, []))
        self.assertIs(manage._pending_add_only_row([self.request], 7), self.request)

    def manager_decision(self, *, result=None, error=None, human=False):
        patches = [
            mock.patch.object(manage, "selectable_workers", return_value={"default": []}),
            mock.patch.object(manage, "human_activity", return_value=human),
        ]
        runner = (mock.patch.object(dispatch, "run", side_effect=error) if error else
                  mock.patch.object(dispatch, "run", return_value=result))
        with patches[0], patches[1], runner:
            return manage._manager_decision(7, self.issue, self.request, [])

    def test_manager_unavailable_nonzero_and_human_takeover_leave_queued_request_unspent(self) -> None:
        for name, runner, human in (
            ("unavailable", OSError("missing"), False),
            ("nonzero", subprocess.CompletedProcess([], 9, "", "bad command"), False),
            ("takeover", subprocess.CompletedProcess([], 0, "DECISION: AUTHORIZE\n{}", ""), True),
        ):
            with self.subTest(name=name), \
                    mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                    mock.patch.object(manage, "human_activity", return_value=human), \
                    mock.patch.object(dispatch, "run", side_effect=runner if isinstance(runner, Exception) else None,
                                      return_value=None if isinstance(runner, Exception) else runner):
                self.assertIsNone(manage._manager_decision(
                    7, self.issue, self.request, [], queue_failure=True, code="add_only_authorization",
                ))
        self.assertFalse(any(row.get("event") == "manage" for row in lifecycle.read_events(dispatch.EVENTS)))

    def test_takeover_check_failures_leave_request_queued(self) -> None:
        failures = (
            subprocess.CalledProcessError(1, ["gh"]),
            OSError("github unavailable"),
            ValueError("invalid timeline"),
        )
        for failure in failures:
            logs: list[str] = []
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(lifecycle, "read_events", return_value=[self.request]), \
                    mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                    mock.patch.object(manage, "human_activity", side_effect=failure), \
                    mock.patch.object(dispatch, "log", side_effect=logs.append), \
                    mock.patch.object(manage, "_manager_decision", side_effect=AssertionError("must stay queued")):
                manage._handle_add_only_request(7, self.issue, self.request)
            self.assertEqual(len(logs), 1)
            self.assertIn("request remains queued", logs[0])

    def test_rejected_notes_preserve_existing_file_and_record_status(self) -> None:
        notes = self.cfg.factory / manage.NOTES_NAME
        notes.parent.mkdir(parents=True)
        notes.write_text("existing evidence\n")
        output = "DECISION: HUMAN\nNo\n\n```notes\n\n```\n"
        result = subprocess.CompletedProcess([], 0, output, "")
        decision = self.manager_decision(result=result)
        self.assertEqual(decision[0], "HUMAN")
        self.assertEqual(notes.read_text(), "existing evidence\n")
        row = next(row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "manage")
        self.assertEqual(row["notes"], "empty_rejected")

    def test_exhausted_request_escalates_with_original_log_and_packet(self) -> None:
        self.cfg.manager_rounds = 0
        logfile = self.root / "worker.log"
        logfile.write_text("failure")
        captured = {}

        def escalate(n, reason, log, **kwargs):
            captured.update(n=n, reason=reason, log=log, **kwargs)

        with mock.patch.object(dispatch, "escalate", side_effect=escalate):
            manage.request_add_only_authorization(7, self.issue, ["tests/test_policy.py:9"], "full report", logfile)
        request = next(row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "manager-request")
        self.assertTrue(request["exhausted"])
        self.assertEqual(captured["log"], logfile)
        self.assertEqual(captured["round_number"], request["round"])
        self.assertEqual(captured["code"], "add_only_authorization")
        self.assertIn("full report", captured["extra"])
        self.assertIn(f"`{logfile}`", Path(request["packet"]).read_text())


class EscalationPassAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = config.Config(self.root, "acme/widgets", manager=["manager"])
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        self.logs: list[str] = []

    def add_request(self, ticket: int) -> None:
        packet = self.cfg.factory / f"{ticket}.md"
        packet.write_text("packet")
        dispatch.record("manager-request", ticket=ticket, code="add_only_authorization",
                        packet=str(packet), round=1, paths=["tests/x.py"], exhausted=False)

    def test_dry_run_lists_each_open_pending_ticket_without_running_manager(self) -> None:
        self.add_request(7)
        self.add_request(8)

        def github(args):
            if args[:2] == ["issue", "list"]:
                return [{"number": 7, "title": "Seven", "body": "", "labels": []}]
            if args[:3] == ["issue", "view", "8"]:
                return {"number": 8, "title": "Eight", "body": "", "state": "OPEN", "labels": []}
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch, "lock_held", return_value=False), \
                mock.patch.object(dispatch, "log", side_effect=self.logs.append), \
                mock.patch.object(manage, "_handle_add_only_request", side_effect=AssertionError("dry run")):
            manage.escalation_pass(dry_run=True)
        self.assertEqual(self.logs, [
            "#7: would manage queued add-only authorization request",
            "#8: would manage queued add-only authorization request",
        ])

    def test_dry_run_skips_initiative_and_closed_unlisted_ticket(self) -> None:
        self.add_request(7)
        self.add_request(8)

        def github(args):
            if args[:2] == ["issue", "list"]:
                return [{"number": 7, "title": "Seven", "body": "", "labels": []}]
            if args[:3] == ["issue", "view", "8"]:
                return {"number": 8, "title": "Eight", "body": "", "state": "CLOSED", "labels": []}
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "initiative_kind", side_effect=lambda n: "initiative" if n == 7 else None), \
                mock.patch.object(dispatch, "lock_held", return_value=False), \
                mock.patch.object(dispatch, "log", side_effect=self.logs.append), \
                mock.patch.object(manage, "_handle_add_only_request", side_effect=AssertionError("must not run")):
            manage.escalation_pass(dry_run=True)
        self.assertEqual(self.logs, ["#7: refused (initiative records are never managed)"])


if __name__ == "__main__":
    unittest.main()
