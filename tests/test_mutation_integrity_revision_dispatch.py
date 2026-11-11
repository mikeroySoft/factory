from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import test_risk_merge as risk_tests

from factory import config, dispatch, lifecycle, manage

_SYNC_STAGED = dispatch.sync_staged
_WAIT = ("WAIT", "base moved during mutation proof", False)
_APPROVE = ("APPROVE", "approved", False)


class MutationProofWaitCallerTest(unittest.TestCase):
    patch = risk_tests.HighPipelineBoundaryTest.patch
    github = risk_tests.HighPipelineBoundaryTest.github
    command_result = risk_tests.HighPipelineBoundaryTest.command_result
    work = risk_tests.HighPipelineBoundaryTest.work

    def setUp(self) -> None:
        risk_tests.HighPipelineBoundaryTest.setUp(self)
        self.comments = dispatch.pr_comment
        self.patch(dispatch, "approve_pr", return_value=True)

    def events(self, name: str) -> list[dict]:
        return [
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == name
        ]

    def assert_requeued_without_findings(self) -> None:
        self.comments.assert_not_called()
        self.escalate.assert_not_called()
        self.assertEqual(len(self.events("mutation-proof-wait")), 1)
        commands = [call.args[0] for call in self.command.call_args_list]
        self.assertTrue(any(command[:3] == ["gh", "issue", "edit"]
                            and "--remove-assignee" in command for command in commands))
        self.assertEqual(self.events("review-rework"), [])

    def resume(self, worker_calls: int) -> None:
        report = self.wt / ".factory" / "gate-report-7.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("- test: PASS\n")
        self.patch(dispatch, "sync_staged", side_effect=_SYNC_STAGED)
        self.comments.reset_mock()

        dispatch.process_ticket(self.issue, 1, False, forced=True)

        self.assertEqual(self.worker.call_count, worker_calls)
        self.assertEqual(self.comments.call_count, 1)
        self.escalate.assert_not_called()

    def test_process_ticket_waits_then_retries_review_without_another_worker_attempt(self) -> None:
        self.patch(dispatch, "pr_gate_and_review", side_effect=[_WAIT, _APPROVE])

        dispatch.process_ticket(self.issue, 1, False, forced=True)

        self.assertEqual(self.worker.call_count, 1)
        self.assert_requeued_without_findings()
        self.resume(worker_calls=1)

    def test_manager_fix_waits_then_ticket_retry_reuses_finished_worker(self) -> None:
        self.issue["labels"] = [{"name": config.LABEL_HUMAN}]
        packet = self.root / "packet.md"
        packet.write_text("fix it")
        self.patch(dispatch, "pr_gate_and_review", side_effect=[_WAIT, _APPROVE])

        manage.apply(
            7, self.issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet,
        )

        self.assertEqual(self.worker.call_count, 1)
        self.assert_requeued_without_findings()
        self.resume(worker_calls=1)

    def test_high_refresh_waits_then_ticket_retry_reuses_refreshed_gate(self) -> None:
        self.issue["labels"] = [{"name": config.LABEL_HUMAN}]
        original = self.command_result

        def refresh_command(args, **kwargs):
            if args[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(args, 0, risk_tests.HEAD + "\n", "")
            if args[:2] == ["git", "status"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "merge-base"]:
                return subprocess.CompletedProcess(args, 1, "", "")
            return original(args, **kwargs)

        self.command.side_effect = refresh_command
        self.patch(dispatch, "run_gate", return_value=(True, "- test: PASS\n"))
        self.patch(dispatch, "pr_gate_and_review", side_effect=[_WAIT, _APPROVE])

        self.assertFalse(dispatch.refresh_pr_branch(7, 70, False))

        self.assertEqual(self.worker.call_count, 0)
        self.assert_requeued_without_findings()
        [refreshed] = self.events("refreshed")
        self.assertEqual((refreshed["head"], refreshed["gate"]), (risk_tests.HEAD, "PASS"))
        self.resume(worker_calls=0)


if __name__ == "__main__":
    unittest.main()
