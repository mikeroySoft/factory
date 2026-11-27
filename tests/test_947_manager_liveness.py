"""#947 manager liveness regressions for queued authorization and FIX handling."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_manager_fix_outcomes as fix_suite


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

    def github(self, args: list[str]):
        if args[:2] == ["issue", "list"]:
            return []
        if args[:2] == ["issue", "view"]:
            return self.issue
        if args[0] == "api" and args[1].endswith("/timeline"):
            return [self.timeline]
        raise AssertionError(args)

    def run(self, args: list[str], *unused, **kwargs):
        if args[:3] == ["gh", "issue", "comment"]:
            self.timeline.append({
                "event": "commented", "id": 101,
                "created_at": "2099-01-01T00:00:00Z", "updated_at": "2099-01-01T00:00:00Z",
                "actor": {"login": "factory-bot"},
            })
            return subprocess.CompletedProcess(args, 0, "https://github.com/acme/widgets/issues/7#issuecomment-101\n", "")
        if args[:3] == ["gh", "issue", "edit"]:
            self.timeline.extend([
                {"event": "unassigned", "created_at": "2099-01-01T00:00:01Z", "actor": {"login": "factory-bot"}},
                {"event": "labeled", "label": {"name": config.LABEL_AGENT},
                 "created_at": "2099-01-01T00:00:02Z", "actor": {"login": "factory-bot"}},
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
                mock.patch.object(dispatch, "run", side_effect=self.run), \
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

    def test_terminal_new_or_resumed_fix_keeps_its_resulting_disposition(self) -> None:
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                events = [self.request, {"event": "comment", "kind": "escalation", "comment": 1,
                                         "round": 1, "at": "2020-01-01T00:00:01Z"}]
                pending = {"worker": "default", "guidance": "repair", "packet": str(self.packet)} if resumed else None
                unclaims: list[tuple] = []
                with mock.patch.object(lifecycle, "read_events", return_value=events), \
                        mock.patch.object(manage, "pending_add_only_request", return_value=True), \
                        mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                        mock.patch.object(manage, "human_activity", return_value=False), \
                        mock.patch.object(manage, "pending_fix", return_value=pending), \
                        mock.patch.object(dispatch, "unclaim", side_effect=lambda *args: unclaims.append(args)), \
                        mock.patch.object(
                            manage, "_manager_decision",
                            return_value=("FIX", "", {"worker": "default", "guidance": "repair"}),
                        ), \
                        mock.patch.object(manage, "resume_fix" if resumed else "apply_fix"):
                    manage._handle_add_only_request(7, self.issue, self.request)
                self.assertEqual(unclaims, [])


if __name__ == "__main__":
    unittest.main()
