"""#947 manager liveness regressions for queued authorization and FIX handling."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_manager_fix_outcomes as fix_suite
from tests.test_host_claim_transitions import TimelineGitHub
from tests.test_host_claims import HostClaimRaceTest as HostHarness


class QueuedAuthorizationLivenessTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = config.Config(self.root, "acme/widgets", manager=None)
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        self.packet = self.cfg.factory / "request.md"
        self.packet.write_text("authorization evidence")
        dispatch.record(
            "manager-request", ticket=7, code="add_only_authorization",
            packet=str(self.packet), round=1, paths=["tests/test_policy.py:9"], exhausted=False,
        )
        self.issue = {"number": 7, "title": "Ticket", "body": "Scope", "state": "OPEN", "labels": []}
        self.timeline: list[dict] = []
        self.manager_calls = 0
        self.comment = 100

    def github(self, args: list[str]):
        if args[:2] == ["issue", "list"]:
            return []
        if args[:2] == ["issue", "view"]:
            return self.issue
        if args[0] == "api" and args[1].endswith("/timeline"):
            return [self.timeline]
        raise AssertionError(args)

    def command_result(self, args: list[str], *unused, **kwargs):
        if args[:3] == ["gh", "issue", "comment"]:
            self.comment += 1
            at = f"2099-01-01T00:00:{len(self.timeline):02d}Z"
            self.timeline.append({
                "event": "commented", "id": self.comment,
                "created_at": at, "updated_at": at,
                "actor": {"login": "factory-bot"},
            })
            return subprocess.CompletedProcess(
                args, 0,
                f"https://github.com/acme/widgets/issues/7#issuecomment-{self.comment}\n", "",
            )
        if args[:3] == ["gh", "issue", "edit"]:
            self.timeline.extend([
                {"event": "unassigned", "created_at": f"2099-01-01T00:00:{len(self.timeline):02d}Z",
                 "actor": {"login": "factory-bot"}},
                {"event": "labeled", "label": {"name": config.LABEL_AGENT},
                 "created_at": f"2099-01-01T00:00:{len(self.timeline) + 1:02d}Z",
                 "actor": {"login": "factory-bot"}},
            ])
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "manager":
            self.manager_calls += 1
            return subprocess.CompletedProcess(
                args, 0,
                'DECISION: AUTHORIZE\n{"justification":"required","guidance":"edit only the named test"}', "",
            )
        raise AssertionError(args)

    def test_bot_unclaim_after_request_marker_does_not_block_later_manager_pass(self) -> None:
        with mock.patch.object(dispatch, "gh_json", side_effect=self.github), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}):
            manage.escalation_pass()
            self.cfg.manager = ["manager", "{prompt}"]
            manage.escalation_pass()

        self.assertEqual(self.manager_calls, 1)
        self.assertFalse(manage.pending_add_only_request(7))

    def test_real_human_edit_after_bot_cleanup_still_blocks_manager(self) -> None:
        escalation = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                          if row.get("event") == "manager-request")
        events = [
            escalation,
            {"event": "comment", "kind": "escalation", "comment": 101,
             "round": 1, "at": "2099-01-01T00:00:00Z"},
        ]
        self.timeline[:] = [
            {"event": "commented", "id": 101, "created_at": "2099-01-01T00:00:00Z",
             "updated_at": "2099-01-01T00:00:00Z", "actor": {"login": "factory-bot"}},
            {"event": "unassigned", "created_at": "2099-01-01T00:00:01Z",
             "actor": {"login": "factory-bot"}},
            {"event": "edited", "created_at": "2099-01-01T00:00:03Z",
             "actor": {"login": "real-owner"}},
        ]
        with mock.patch.object(dispatch, "gh_json", side_effect=self.github):
            self.assertTrue(manage.human_activity(7, escalation, events))

    def test_host_requeue_marker_is_ignored_but_same_account_takeover_is_not(self) -> None:
        escalation = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                          if row.get("event") == "manager-request")
        events = [
            escalation,
            {"event": "comment", "kind": "requeue", "comment": 100,
             "at": "2099-01-01T00:00:01Z"},
            {"event": "comment", "kind": "escalation", "comment": 101,
             "round": 1, "at": "2099-01-01T00:00:02Z"},
        ]
        cleanup = {
            "event": "commented", "id": 100, "body": "Factory requeue: nonce=abc\ncontext",
            "created_at": "2099-01-01T00:00:01Z", "updated_at": "2099-01-01T00:00:01Z",
            "actor": {"login": "shared-owner"},
        }
        marker = {
            "event": "commented", "id": 101, "created_at": "2099-01-01T00:00:02Z",
            "updated_at": "2099-01-01T00:00:02Z", "actor": {"login": "shared-owner"},
        }
        with mock.patch.object(dispatch, "gh_json", return_value=[[cleanup, marker]]):
            self.assertFalse(manage.human_activity(7, escalation, events))
            self.assertTrue(manage.human_activity(
                7, escalation, [row for row in events if row.get("kind") != "requeue"],
            ))
        edited = {**cleanup, "updated_at": "2099-01-01T00:00:04Z"}
        with mock.patch.object(dispatch, "gh_json", return_value=[[edited, marker]]):
            self.assertTrue(manage.human_activity(7, escalation, events))
        takeover = {
            "event": "labeled", "label": {"name": "owner-change"},
            "created_at": "2099-01-01T00:00:03Z", "actor": {"login": "shared-owner"},
        }
        with mock.patch.object(dispatch, "gh_json", return_value=[[cleanup, marker, takeover]]):
            self.assertTrue(manage.human_activity(7, escalation, events))



class HostQueuedAuthorizationReceiptTest(unittest.TestCase):
    setUp = HostHarness.setUp
    dispatcher = HostHarness.dispatcher
    launch = HostHarness.launch

    def test_actual_host_unclaim_receipt_clears_real_takeover_check(self) -> None:
        self.github = TimelineGitHub()
        tank = self.dispatcher("tank")
        request = {
            "event": "manager-request", "ticket": 7, "code": "add_only_authorization",
            "packet": "packet", "round": 1, "paths": ["tests/x.py"],
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        tank.record(**request)
        self.github.add_claim("tank", datetime.now(UTC))
        tank.unclaim(7, "--add-label", config.LABEL_AGENT, check=True)
        tank.record("manager-request-release", ticket=7, packet="packet", round=1)
        events = lifecycle.read_events(tank.EVENTS)
        requeue = next(row for row in events if row.get("kind") == "requeue")
        self.github.timeline.insert(0, {
            "event": "commented", "id": requeue["comment"],
            "created_at": requeue["at"], "updated_at": requeue["at"],
        })
        result = tank.run([
            "gh", "issue", "comment", "7", "--repo", tank.REPO, "--body", "request",
        ])
        self.assertTrue(tank.comment_receipt(7, "escalation", result.stdout, round=1))
        events = lifecycle.read_events(tank.EVENTS)
        request = next(row for row in events if row.get("event") == "manager-request")
        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(manage.handoff, "dispatch", tank):
            self.assertFalse(manage.human_activity(7, request, events))


# Keep unittest discovery from collecting the imported harness twice.
del HostHarness

class FixReviewEvidenceTest(unittest.TestCase):
    setUp = fix_suite.ManagerFixOutcomeTest.setUp
    prepare_fix = fix_suite.ManagerFixOutcomeTest.prepare_fix
    apply_fix = fix_suite.ManagerFixOutcomeTest.apply_fix
    patched = fix_suite.ManagerFixOutcomeTest.patched
    def test_noop_fix_with_incomplete_risk_check_still_runs_pr_review(self) -> None:
        self.prepare_fix()
        head = self.github.head()
        for risk in (
            {"reason": "missing_worker_provenance", "worker_model": None, "pr_gate": "PASS"},
            {"mutation": "PASS", "pr_gate": "FAIL"},
        ):
            with self.subTest(risk=risk):
                dispatch.EVENTS.unlink(missing_ok=True)
                dispatch.record("manage", ticket=9, decision="FIX", round=1)
                dispatch.record("risk-check", ticket=9, head=head, **risk)
                with self.patched(), \
                        mock.patch.object(dispatch, "worker_round", return_value=(True, "gate passed", self.packet, head)), \
                        mock.patch.object(dispatch, "push_agent"), \
                        mock.patch.object(dispatch, "record_risk"), \
                        mock.patch.object(dispatch, "pr_gate_and_review", return_value=("REVISE", "findings", False)) as review, \
                        mock.patch.object(dispatch, "pr_comment"):
                    self.apply_fix()
                review.assert_called_once()

    def test_terminal_fix_failure_keeps_human_disposition_through_add_only_handler(self) -> None:
        self.prepare_fix()
        self.cfg.manager = ["manager"]
        self.github.issue_labels = {config.LABEL_AGENT}
        request = {
            "event": "manager-request", "ticket": 9, "code": "add_only_authorization",
            "packet": str(self.packet), "round": 1, "paths": ["tests/x.py"],
            "at": "2020-01-01T00:00:00Z",
        }
        dispatch.record(**request)
        with self.patched(), \
                mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(
                    manage, "_manager_decision",
                    return_value=("FIX", "", {"worker": "default", "guidance": "repair"}),
                ), \
                mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(False, "gate failed")):
            manage._handle_add_only_request(9, self.github.issue(), request)
        self.assertIn(config.LABEL_HUMAN, self.github.issue_labels)
        self.assertNotIn(config.LABEL_AGENT, self.github.issue_labels)


class AddOnlyFixDispositionTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.cfg = config.Config(root, "acme/widgets", manager=["manager"])
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        self.packet = self.cfg.factory / "request.md"
        self.packet.write_text("packet")
        self.request = {
            "event": "manager-request", "ticket": 7, "code": "add_only_authorization",
            "packet": str(self.packet), "round": 1, "paths": ["tests/x.py"], "at": "2020-01-01T00:00:00Z",
        }
        self.issue = {"number": 7, "title": "Ticket", "body": "", "labels": []}


    def test_worker_unavailable_fix_remains_pending_after_single_release(self) -> None:
        unclaims: list[tuple] = []
        unavailable = dispatch.WorkerUnavailable("worker unavailable")
        with mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(manage, "_manager_decision", return_value=(
                    "FIX", "", {"worker": "default", "guidance": "repair"},
                )), \
                mock.patch.object(dispatch, "unclaim", side_effect=lambda *args, **kwargs: unclaims.append(args)), \
                mock.patch.object(manage, "apply", side_effect=unavailable):
            dispatch.record(**self.request)
            with self.assertRaises(dispatch.WorkerUnavailable):
                manage._handle_add_only_request(7, self.issue, self.request)
        self.assertEqual(len(unclaims), 1)
        [pending] = [row for row in lifecycle.read_events(dispatch.EVENTS)
                     if row.get("event") == "manage-pending"]
        self.assertEqual((pending["decision"], pending["round"]), ("FIX", 1))


if __name__ == "__main__":
    unittest.main()
