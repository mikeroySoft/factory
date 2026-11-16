from __future__ import annotations

import subprocess
import types
import unittest
from unittest import mock

import test_risk_merge as risk_tests

from factory import config, dispatch, generation, lifecycle, manage

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
        dispatch.record("escalate", ticket=7, code="gate_failed", reason="gate failed", round=1)
        self.patch(dispatch, "pr_gate_and_review", side_effect=[_WAIT, _APPROVE])

        manage.apply(
            7, self.issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet,
        )

        self.assertEqual(self.worker.call_count, 1)
        self.assert_requeued_without_findings()
        self.resume(worker_calls=1)

    def test_high_refresh_waits_then_ticket_retry_reuses_refreshed_gate(self) -> None:
        self.issue["labels"] = [{"name": config.LABEL_HUMAN}]
        dispatch.record("escalate", ticket=7, code="gate_failed", reason="gate failed", round=1)
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

    def _head_report(self):
        self.patch(dispatch, "sync_staged", side_effect=_SYNC_STAGED)
        report = self.wt / ".factory" / "gate-report-7.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("- test: PASS\n")
        return report

    def test_sync_staged_reuses_dispatcher_staged_head_after_proof_wait(self) -> None:
        report = self._head_report()
        dispatch.record("attempt", ticket=7, attempt=0, gate="PASS", head=risk_tests.HEAD)
        dispatch.record("mutation-proof-wait", ticket=7, head=risk_tests.HEAD, source="ticket review")
        self.assertEqual(dispatch.sync_staged(7, self.wt), (report.read_text(), risk_tests.HEAD))

    def test_sync_staged_reuses_unfinished_passing_attempt_after_proof_wait(self) -> None:
        report = self._head_report()
        dispatch.record(
            "attempt", ticket=7, attempt=1, gate="PASS", head=risk_tests.HEAD, finished=False,
        )
        dispatch.record("mutation-proof-wait", ticket=7, head=risk_tests.HEAD, source="ticket review")
        self.assertEqual(dispatch.sync_staged(7, self.wt), (report.read_text(), risk_tests.HEAD))

    def test_sync_staged_ignores_proof_wait_after_later_escalate_and_retry(self) -> None:
        self._head_report()
        dispatch.record(
            "attempt", ticket=7, attempt=1, gate="PASS", head=risk_tests.HEAD, finished=True,
        )
        dispatch.record("mutation-proof-wait", ticket=7, head=risk_tests.HEAD, source="ticket review")
        dispatch.record("escalate", ticket=7, code="review_revise", reason="REVISE verdict", round=1)
        dispatch.record("manage", ticket=7, decision="RETRY")
        self.assertIsNone(dispatch.sync_staged(7, self.wt))

    def test_sync_staged_does_not_treat_later_escalate_at_same_head_as_proof_wait(self) -> None:
        # Eq->LtE would treat "escalate" as a proof wait, because
        # "escalate" <= "mutation-proof-wait", when the escalate row carries this head.
        self._head_report()
        dispatch.record(
            "attempt", ticket=7, attempt=1, gate="PASS", head=risk_tests.HEAD, finished=True,
        )
        dispatch.record("mutation-proof-wait", ticket=7, head=risk_tests.HEAD, source="ticket review")
        dispatch.record(
            "escalate", ticket=7, code="review_revise", reason="REVISE verdict",
            round=1, head=risk_tests.HEAD,
        )
        self.assertIsNone(dispatch.sync_staged(7, self.wt))

    def test_sync_staged_does_not_treat_later_review_result_as_proof_wait(self) -> None:
        # Eq->GtE would treat "review-result" as a proof wait, because
        # "review-result" >= "mutation-proof-wait", when that row carries this head.
        self._head_report()
        dispatch.record(
            "attempt", ticket=7, attempt=1, gate="PASS", head=risk_tests.HEAD, finished=True,
        )
        dispatch.record("mutation-proof-wait", ticket=7, head=risk_tests.HEAD, source="ticket review")
        dispatch.record(
            "review-result", ticket=7, verdict="REVISE", head=risk_tests.HEAD,
        )
        self.assertIsNone(dispatch.sync_staged(7, self.wt))

    def test_sync_staged_ignores_a_proof_wait_at_a_lexicographically_different_head(self) -> None:
        # A wait at another head plus a later PASS at this head is not a deferred
        # reuse: only attempt==0 is reused when this head has no proof-wait.
        # Eq->LtE / Eq->GtE would treat a smaller or larger wait head as a match
        # and then pick up the PASS row.
        self._head_report()
        lesser, greater = "0" * 40, "f" * 40
        self.assertLess(lesser, risk_tests.HEAD)
        self.assertGreater(greater, risk_tests.HEAD)
        dispatch.record(
            "attempt", ticket=7, attempt=1, gate="PASS", head=risk_tests.HEAD, finished=True,
        )
        dispatch.record("mutation-proof-wait", ticket=7, head=lesser, source="ticket review")
        self.assertIsNone(dispatch.sync_staged(7, self.wt))
        dispatch.record("mutation-proof-wait", ticket=7, head=greater, source="ticket review")
        self.assertIsNone(dispatch.sync_staged(7, self.wt))

    def test_sync_staged_matches_a_deferred_head_by_equality(self) -> None:
        # Eq->IsNot would miss a PASS row whose head is the same string object
        # that rev-parse returned (stdout had no trailing newline, so strip is
        # identity). JSON-round-tripped events would not kill that mutant.
        head = risk_tests.HEAD
        report = self._head_report()

        def commands(args, **kwargs):
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, head, "")
            return self.command_result(args, **kwargs)

        self.command.side_effect = commands
        rows = [
            {"event": "attempt", "ticket": 7, "attempt": 1, "gate": "PASS", "head": head},
            {"event": "mutation-proof-wait", "ticket": 7, "head": head, "source": "ticket review"},
        ]
        with mock.patch.object(
            generation, "current", return_value=types.SimpleNamespace(rows=rows),
        ):
            self.assertEqual(dispatch.sync_staged(7, self.wt), (report.read_text(), head))

    def test_sync_staged_without_a_proof_wait_reuses_only_attempt_zero(self) -> None:
        report = self._head_report()
        dispatch.record(
            "attempt", ticket=7, attempt=-1, gate="PASS", head=risk_tests.HEAD,
        )
        self.assertIsNone(dispatch.sync_staged(7, self.wt))
        dispatch.record("attempt", ticket=7, attempt=0, gate="PASS", head=risk_tests.HEAD)
        self.assertEqual(dispatch.sync_staged(7, self.wt), (report.read_text(), risk_tests.HEAD))

    def test_review_bounce_defers_when_proof_base_moves_during_rereview(self) -> None:
        self.patch(dispatch, "pr_gate_and_review", side_effect=[
            ("REVISE", "address findings", False),
            _WAIT,
        ])
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertEqual(self.worker.call_count, 2)
        self.escalate.assert_not_called()
        waits = self.events("mutation-proof-wait")
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["source"], "ticket re-review")


if __name__ == "__main__":
    unittest.main()
