"""Behavioral regressions for the mutation survivors reported on issue #947."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_authorization_request as authorization_fixture
from tests import test_unchanged_round_edges as cache_fixture


class DispatchSurvivorRegressionTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def test_refresh_rounds_do_not_spend_manager_escalation_rounds(self) -> None:
        dispatch.record(
            "conflict-requeue", ticket=7, pr=70, head="a" * 40,
            verb="rebase", files=["factory/dispatch.py"], round=9,
        )

        _, round_number = dispatch.escalation_packet(7, "gate failed", None, self.root)

        self.assertEqual(round_number, 1)

    def test_first_manager_escalation_round_starts_at_one(self) -> None:
        _, round_number = dispatch.escalation_packet(7, "gate failed", None, self.root)

        self.assertEqual(round_number, 1)

    def test_plain_authorization_request_does_not_hide_refresh_context(self) -> None:
        rows = [
            {
                "event": "conflict-requeue", "ticket": 7, "pr": 70,
                "head": "a" * 40, "verb": "rebase",
                "files": ["factory/dispatch.py"], "round": 2,
            },
            {
                "event": "manager-request", "ticket": 7,
                "code": "add_only_authorization", "packet": "packet.md",
                "paths": ["tests/test_policy.py:9"], "round": 3,
                "exhausted": False,
            },
        ]

        with mock.patch.object(dispatch.lifecycle, "read_events", return_value=rows):
            context = dispatch.refresh_requeue_context(7)

        self.assertIn("Refresh conflict, round 2", context)
        self.assertIn("factory/dispatch.py", context)

    def test_gate_cache_creates_its_missing_parent_directory(self) -> None:
        fixture = cache_fixture.WorkerRoundCacheEdgesTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cache_report = dispatch.FACTORY / "gate-cache" / "7.md"
        self.assertFalse(cache_report.parent.exists())

        fixture.run_round({})

        self.assertEqual(cache_report.read_text(), "fresh gate report")


class PendingRequestSurvivorRegressionTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def request_row(self, *, round_number: int = 1) -> dict:
        return {
            "event": "manager-request", "ticket": 7,
            "code": "add_only_authorization",
            "packet": str(self.cfg.factory / "escalations" / "7.md"),
            "paths": self.paths, "log": None, "round": round_number,
            "exhausted": False, "at": f"2026-11-27T00:00:0{round_number}Z",
        }

    @staticmethod
    def recovery(event: str, request: dict) -> dict:
        return {
            "event": event, "ticket": request["ticket"],
            "packet": request["packet"], "round": request["round"],
        }

    @staticmethod
    def managed(request: dict) -> dict:
        return {
            "event": "manage", "ticket": request["ticket"],
            "code": request["code"], "packet": request["packet"],
            "round": request["round"], "decision": "AUTHORIZE",
        }

    def test_valid_request_recovery_and_management_rows_are_filtered_by_kind(self) -> None:
        request = self.request_row()
        unrelated_review = {
            "event": "review", "ticket": 7, "head": "a" * 40,
            "accepted": True, "verdict": "APPROVE",
        }

        self.assertIs(
            manage._pending_add_only_row([request, unrelated_review], 7),
            request,
        )
        self.assertIs(
            manage._pending_add_only_row([
                request, self.recovery("manager-request-failed", request),
            ], 7),
            request,
        )
        self.assertIsNone(manage._pending_add_only_row([
            request, self.recovery("manager-request-complete", request),
        ], 7))
        self.assertIsNone(manage._pending_add_only_row([
            request, self.managed(request),
        ], 7))

    def test_latest_round_remains_pending_after_a_completed_prior_round(self) -> None:
        first = self.request_row(round_number=1)
        second = self.request_row(round_number=2)
        rows = [
            first,
            self.managed(first),
            self.recovery("manager-request-complete", first),
            second,
            self.recovery("manager-request-failed", second),
        ]

        self.assertIs(manage._pending_add_only_row(rows, 7), second)

    def test_changed_fix_head_is_reviewed_even_when_its_sha_sorts_before_the_lease(self) -> None:
        wt = self.cfg.factory / "wt-7"
        wt.mkdir()
        packet = self.cfg.factory / "manager-packet.md"
        packet.write_text("Unresolved review finding")
        lease = "f" * 40
        gate_head = "a" * 40
        self.cfg.fixups = []
        dispatch.record(
            "review", ticket=7, head=lease, actual_head=lease,
            accepted=True, verdict="REVISE",
        )
        pr = {
            "state": "OPEN", "headRefName": "agent/7", "headRefOid": lease,
            "baseRefName": self.cfg.main, "reviewDecision": None,
        }

        def git(command: list[str], *args, **kwargs) -> subprocess.CompletedProcess:
            if command[:3] == ["git", "branch", "--show-current"]:
                output = "agent/7\n"
            elif command[:3] == ["git", "rev-parse", "HEAD"]:
                output = lease + "\n"
            else:
                raise AssertionError(command)
            return subprocess.CompletedProcess(command, 0, output, "")

        with mock.patch.object(dispatch, "gh_json", side_effect=[pr, {"baseRefName": self.cfg.main}]), \
                mock.patch.object(dispatch, "run", side_effect=git), \
                mock.patch.object(
                    dispatch, "worker_round",
                    return_value=(True, "gate passed", self.cfg.factory / "fix.log", gate_head),
                ), \
                mock.patch.object(dispatch, "push_agent") as push, \
                mock.patch.object(dispatch, "record_risk"), \
                mock.patch.object(
                    dispatch, "pr_gate_and_review",
                    return_value=(dispatch.PR_GATE_WAIT, "waiting", False),
                ) as review, \
                mock.patch.object(dispatch, "defer_mutation_proof"), \
                mock.patch.object(dispatch, "escalate"):
            manage.apply(7, self.issue, "FIX", "", {
                "worker": "default", "guidance": "Resolve the finding.",
            }, packet)

        review.assert_called_once_with(wt, 7, "gate passed", gate_head, {})
        push.assert_called_once_with(wt, 7, lease)


class RequestCommentSurvivorRegressionTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def request_row(self) -> dict:
        packet = self.cfg.factory / "escalations" / "7.md"
        packet.parent.mkdir()
        packet.write_text("authorization evidence")
        return {
            "event": "manager-request", "ticket": 7,
            "code": "add_only_authorization", "packet": str(packet),
            "paths": self.paths, "log": None, "round": 1,
            "exhausted": False, "at": "2026-11-27T00:00:00Z",
        }

    def assert_comment_failure_stops_before_decision(self, comment_result: object) -> None:
        request = self.request_row()

        def command(args: list[str], *pos, **kwargs):
            if args[:3] == ["gh", "issue", "comment"]:
                if isinstance(comment_result, BaseException):
                    raise comment_result
                return comment_result
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(lifecycle, "read_events", return_value=[request]), \
                mock.patch.object(dispatch, "run", side_effect=command), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(manage, "_manager_decision", return_value=None) as decide:
            manage._handle_add_only_request(7, self.issue, request)

        decide.assert_not_called()

    def test_comment_transport_error_leaves_request_queued_without_a_decision(self) -> None:
        self.assert_comment_failure_stops_before_decision(OSError("GitHub unavailable"))

    def test_positive_comment_exit_leaves_request_queued_without_a_decision(self) -> None:
        self.assert_comment_failure_stops_before_decision(subprocess.CompletedProcess(
            [], 1, "https://github.com/acme/widgets/issues/7#issuecomment-101\n", "failed",
        ))

    def test_negative_comment_exit_leaves_request_queued_without_a_decision(self) -> None:
        self.assert_comment_failure_stops_before_decision(subprocess.CompletedProcess(
            [], -9, "https://github.com/acme/widgets/issues/7#issuecomment-102\n", "killed",
        ))

    def test_release_journaled_failure_recovery_preserves_terminal_human_state(self) -> None:
        request = self.request_row()
        released = {
            "event": "manager-request-release", "ticket": 7,
            "packet": request["packet"], "round": request["round"],
        }
        failed = {
            "event": "manager-request-failed", "ticket": 7,
            "packet": request["packet"], "round": request["round"],
            "reason": "application failed",
        }
        labels = {config.LABEL_AGENT}
        edits: list[list[str]] = []

        def github(command: list[str], *args, **kwargs) -> subprocess.CompletedProcess:
            if command[:3] == ["gh", "issue", "edit"]:
                edits.append(command)
                for flag, label in zip(command[6::2], command[7::2]):
                    if flag == "--remove-label":
                        labels.discard(label)
                    elif flag == "--add-label":
                        labels.add(label)
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(
                    command, 0,
                    "https://github.com/acme/widgets/issues/7#issuecomment-103\n", "",
                )
            raise AssertionError(command)

        with mock.patch.object(
                lifecycle, "read_events", return_value=[request, released, failed],
        ), mock.patch.object(
                manage, "_ensure_request_comment", return_value=True,
        ), mock.patch.object(
                manage, "human_activity", return_value=False,
        ), mock.patch.object(dispatch, "run", side_effect=github):
            manage._handle_add_only_request(7, self.issue, request)

        self.assertEqual(len(edits), 1)
        self.assertEqual(labels, {config.LABEL_HUMAN})
        self.assertTrue(any(
            row.get("event") == "manager-request-complete"
            for row in lifecycle.read_events(dispatch.EVENTS)
        ))


class EscalationScanSurvivorRegressionTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def test_unreadable_pending_issue_does_not_stop_the_remaining_scan(self) -> None:
        first, second = list({7, 8})
        for ticket in (first, second):
            packet = self.cfg.factory / "escalations" / f"{ticket}.md"
            packet.parent.mkdir(exist_ok=True)
            packet.write_text("authorization evidence")
            dispatch.record(
                "manager-request", ticket=ticket, code="add_only_authorization",
                packet=str(packet), paths=self.paths, log=None, round=1,
                exhausted=False,
            )
        issue = {
            "number": second, "title": "Second pending request", "body": "Scope",
            "state": "OPEN", "labels": [{"name": config.LABEL_AGENT}],
        }
        logs: list[str] = []

        def github(args: list[str]):
            if args[:2] == ["issue", "list"]:
                return []
            if args[:3] == ["issue", "view", str(first)]:
                raise OSError("GitHub unavailable")
            if args[:3] == ["issue", "view", str(second)]:
                return issue
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch, "lock_held", return_value=False), \
                mock.patch.object(dispatch, "log", side_effect=logs.append):
            manage.escalation_pass(dry_run=True)

        self.assertTrue(any(
            line == f"#{second}: would manage queued add-only authorization request"
            for line in logs
        ))


if __name__ == "__main__":
    unittest.main()
