from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import config, dispatch, handoff, lifecycle, manage
from tests import test_dispatch_revocation as fixtures


class ManagerRevocationTest(unittest.TestCase):
    """The git-backed revocation fixture, borrowed without re-running its tests."""

    setUp = fixtures.DispatchRevocationTest.setUp
    gh_json = fixtures.DispatchRevocationTest.gh_json
    events = fixtures.DispatchRevocationTest.events

    def journal_escalation(self) -> None:
        self.cfg.manager = ["manager"]
        self.cfg.manager_rounds = 1
        dispatch.record(
            "escalate",
            ticket=9,
            reason="gate failed",
            round=1,
            packet=str(self.packet),
        )
        dispatch.record(
            "comment",
            ticket=9,
            kind="escalation",
            round=1,
            comment=101,
            url="https://github.com/local/revocation/issues/9#issuecomment-101",
        )

    def apply_refused_fix(self, *, reason: str, resumed: bool = False) -> tuple[mock.Mock, mock.Mock, mock.Mock]:
        self.github.issue_labels = {config.LABEL_HUMAN}
        self.journal_escalation()
        if resumed:
            with lifecycle.scope(dispatch.EVENTS, "manage", ticket=9):
                dispatch.record("manage", ticket=9, decision="FIX", round=1, packet=str(self.packet))
                dispatch.record(
                    "manage-pending",
                    ticket=9,
                    decision="FIX",
                    round=1,
                    worker="default",
                    guidance="fix",
                    packet=str(self.packet),
                    reason="worker unavailable",
                )

        claim_read = [
            "issue",
            "view",
            "9",
            "--repo",
            self.cfg.repo,
            "--json",
            "state,labels,assignees",
        ]

        def read(args):
            if args[:2] == ["issue", "list"]:
                return [self.github.issue()]
            if reason == "claim-unavailable" and args == claim_read:
                raise OSError("GitHub unavailable")
            return self.gh_json(args)

        route = {
            "status": "selected",
            "owner": "alice",
            "candidates": [],
            "source": "test",
            "reason": "implementation",
            "provenance": [],
        }

        def run(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "comment"]:
                self.github.calls.append(cmd)
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    "https://github.com/local/revocation/issues/9#issuecomment-202\n",
                    "",
                )
            return self.github.run(cmd, *args, **kwargs)

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "worker_round") as worker, \
                mock.patch.object(dispatch, "push_agent") as push, \
                mock.patch.object(dispatch, "pr_gate_and_review") as review:
            with lifecycle.scope(dispatch.EVENTS, "manage", ticket=9):
                if resumed:
                    dispatch.record("manage-resume", ticket=9, decision="FIX", round=1)
                else:
                    dispatch.record("manage", ticket=9, decision="FIX", round=1, packet=str(self.packet))
                manage.apply_fix(
                    9,
                    self.github.issue(),
                    {"worker": "default", "guidance": "fix"},
                    self.packet,
                    round=1,
                )
            with mock.patch.object(
                handoff,
                "observe",
                return_value={"route": route, "pr_url": "https://github.com/local/revocation/pull/102"},
            ), mock.patch.object(dispatch, "initiative_kind", return_value=False):
                handoff.handoff_pass()
        return worker, push, review

    def assert_terminal_handoff(self, reason: str) -> None:
        [terminal] = self.events("manage-terminal")
        self.assertEqual(
            (terminal["ticket"], terminal["decision"], terminal["round"], terminal["reason"]),
            (9, "FIX", 1, reason),
        )
        handoffs = [
            cmd
            for cmd in self.github.calls
            if cmd[1:3] == ["issue", "comment"] and "factory-handoff" in cmd[-1]
        ]
        self.assertEqual(len(handoffs), 1)
        self.assertIn("@alice — you own this decision", handoffs[0][-1])
        self.assertIn("the manager's FIX was refused", handoffs[0][-1])
        self.assertIn(f"({reason})", handoffs[0][-1])

    def test_escalation_fix_refused_after_revocation_routes_human_without_work(self) -> None:
        worker, push, review = self.apply_refused_fix(reason="ready-for-human")

        worker.assert_not_called()
        push.assert_not_called()
        review.assert_not_called()
        self.assert_terminal_handoff("ready-for-human")

    def test_claim_unavailable_while_resuming_fix_routes_human_without_work(self) -> None:
        worker, push, review = self.apply_refused_fix(reason="claim-unavailable", resumed=True)

        worker.assert_not_called()
        push.assert_not_called()
        review.assert_not_called()
        self.assertEqual(len(self.events("manage")), 1)
        self.assertEqual(len(self.events("manage-resume")), 1)
        self.assert_terminal_handoff("claim-unavailable")


if __name__ == "__main__":
    unittest.main()
