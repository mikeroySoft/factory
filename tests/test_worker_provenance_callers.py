from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, manage
from tests import test_refresh_conflict as refresh_suite
from tests import test_risk_merge as risk_suite


class WorkerProvenanceCallerTest(unittest.TestCase):
    """Caller behavior for unavailable High-risk review evidence."""

    setUp = risk_suite.WorkerModelProvenanceTest.setUp
    patched = risk_suite.WorkerModelProvenanceTest.patched
    events = risk_suite.WorkerModelProvenanceTest.events
    prepare = risk_suite.WorkerModelProvenanceTest.prepare
    work = risk_suite.WorkerModelProvenanceTest.work
    review = risk_suite.WorkerModelProvenanceTest.review

    @staticmethod
    def missing_findings(head: str) -> str:
        return (
            f"missing_worker_provenance: High-risk review at {head} requires "
            "worker_model from the worker attempt; configure an explicit worker --model "
            "and run a new attempt."
        )

    @staticmethod
    def unavailable_findings() -> str:
        return (
            "review provider offline\n\n"
            "Factory rejected reviewer evidence: review_exit:1."
        )

    def deserialize_failure_code(self):
        real = dispatch.pr_gate_and_review

        def call(*args, **kwargs):
            result = real(*args, **kwargs)
            state = dispatch._PENDING_RISK.get(args[1], {})
            if "failure_code" in state:
                original = state["failure_code"]
                state["failure_code"] = json.loads(json.dumps(original))
                self.assertEqual(state["failure_code"], original)
                self.assertIsNot(state["failure_code"], original)
            return result

        return mock.patch.object(dispatch, "pr_gate_and_review", side_effect=call)

    def unavailable_review(self, _wt, _prompt, head, *, reviewer=None, **_kwargs):
        return {
            "verdict": None,
            "findings": "review provider offline",
            "usage": {"model": config.command_model(reviewer or self.cfg.reviewer)},
            "accepted": False,
            "parsed": False,
            "reason": "reviewer_unavailable",
            "returncode": 1,
            "actual_head": head,
            "attempts": 1,
            "failure_reason": "review_exit:1",
            "counts": None,
            "outside_diff_findings": None,
            "rejection_reason": None,
        }

    def revising_review(self, *args, **kwargs):
        result = self.review(*args, **kwargs)
        result.update(
            verdict="REVISE",
            findings="VERDICT: REVISE",
            counts={"important": 1, "nit": 0, "pre-existing": 0},
        )
        return result

    def assert_published(self, comment: mock.Mock, findings: str, *, calls: int = 1) -> None:
        self.assertEqual(comment.call_count, calls)
        self.assertEqual(comment.call_args.args, (9, findings))
        self.assertEqual((dispatch.FACTORY / "review-9.md").read_text(), findings + "\n")
        self.assertTrue(any(call[1:3] == ["pr", "comment"] for call in self.github.calls))

    def assert_escalated(
        self,
        escalation: mock.Mock,
        *,
        code: str,
        reason: str,
        findings: str | None,
    ) -> None:
        escalation.assert_called_once()
        call = escalation.call_args
        self.assertEqual(call.args[:2], (9, reason))
        self.assertEqual(call.kwargs["code"], code)
        if findings is None:
            self.assertNotIn("extra", call.kwargs)
        else:
            self.assertEqual(call.kwargs["extra"], findings)
        event = self.events("escalate")[-1]
        self.assertEqual((event["code"], event["reason"]), (code, reason))
        packet = Path(event["packet"]).read_text()
        self.assertIn(f"## Reason\n\n{reason}\n", packet)
        if findings is not None:
            self.assertIn(findings, packet)

    def assert_not_approved(self, approve: mock.Mock) -> None:
        approve.assert_not_called()
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)

    def move_main(self) -> None:
        (self.origin / "later.txt").write_text("main moved\n")
        refresh_suite.git(self.origin, "add", "later.txt")
        refresh_suite.git(self.origin, "commit", "-qm", "move main")

    def test_process_ticket_initial_missing_provenance_preserves_findings(self) -> None:
        self.prepare()
        self.cfg.workers[config.LABEL_CHORE] = ["implicit-worker", "{prompt}"]
        with self.deserialize_failure_code(), \
                mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
        findings = self.missing_findings(self.github.head())
        self.reviewer.assert_not_called()
        self.assert_published(comment, findings)
        self.assert_escalated(
            escalation, code="missing_worker_provenance", reason=findings, findings=findings,
        )
        self.assert_not_approved(approve)

    def test_process_ticket_initial_reviewer_unavailable_uses_fallback_reason(self) -> None:
        self.prepare()
        self.reviewer.side_effect = self.unavailable_review
        with mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
        findings = self.unavailable_findings()
        self.assertEqual(self.reviewer.call_count, 1)
        self.assert_published(comment, findings)
        self.assert_escalated(
            escalation,
            code="reviewer_unavailable",
            reason="reviewer unavailable after retries",
            findings=findings,
        )
        self.assert_not_approved(approve)

    def test_process_ticket_bounce_missing_provenance_preserves_findings(self) -> None:
        self.prepare()

        def revise_then_drop_model(*args, **kwargs):
            result = self.revising_review(*args, **kwargs)
            if config.command_model(kwargs.get("reviewer") or self.cfg.reviewer) == "second/model":
                self.cfg.workers[config.LABEL_CHORE] = ["implicit-worker", "{prompt}"]
            return result

        self.reviewer.side_effect = revise_then_drop_model
        with self.deserialize_failure_code(), \
                mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
        findings = self.missing_findings(self.github.head())
        self.assertEqual(self.reviewer.call_count, 2)
        self.assertEqual(len(self.events("attempt")), 2)
        self.assert_published(comment, findings, calls=2)
        self.assert_escalated(
            escalation, code="missing_worker_provenance", reason=findings, findings=findings,
        )
        self.assert_not_approved(approve)

    def test_process_ticket_bounce_reviewer_unavailable_uses_fallback_reason(self) -> None:
        self.prepare()

        def revise_then_unavailable(*args, **kwargs):
            if self.reviewer.call_count > 2:
                return self.unavailable_review(*args, **kwargs)
            return self.revising_review(*args, **kwargs)

        self.reviewer.side_effect = revise_then_unavailable
        with mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
        findings = self.unavailable_findings()
        self.assertEqual(self.reviewer.call_count, 3)
        self.assertEqual(len(self.events("attempt")), 2)
        self.assert_published(comment, findings, calls=2)
        self.assert_escalated(
            escalation,
            code="reviewer_unavailable",
            reason="reviewer unavailable after retries",
            findings=findings,
        )
        self.assert_not_approved(approve)

    def test_refresh_missing_provenance_preserves_findings(self) -> None:
        self.prepare()
        self.cfg.workers[config.LABEL_CHORE] = ["implicit-worker", "{prompt}"]
        with self.deserialize_failure_code():
            dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
            self.move_main()
            self.reviewer.reset_mock()
            with mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                    mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                    mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
                self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        findings = self.missing_findings(self.github.head())
        reason = f"PR #102: {findings}; `{config.LABEL_APPROVED}` label removed"
        self.reviewer.assert_not_called()
        self.assert_published(comment, findings)
        self.assert_escalated(
            escalation, code="missing_worker_provenance", reason=reason, findings=findings,
        )
        self.assert_not_approved(approve)

    def test_refresh_reviewer_unavailable_uses_fallback_reason(self) -> None:
        self.prepare()
        dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
        self.move_main()
        self.reviewer.reset_mock()
        self.reviewer.side_effect = self.unavailable_review
        with mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        findings = self.unavailable_findings()
        reason = (
            f"PR #102: reviewer unavailable after rebase onto moved main; "
            f"`{config.LABEL_APPROVED}` label removed"
        )
        self.assertEqual(self.reviewer.call_count, 1)
        self.assert_published(comment, findings)
        self.assert_escalated(
            escalation, code="reviewer_unavailable", reason=reason, findings=findings,
        )
        self.assert_not_approved(approve)

    def test_manager_fix_missing_provenance_preserves_findings(self) -> None:
        self.prepare()
        self.cfg.workers[config.LABEL_CHORE] = ["implicit-worker", "{prompt}"]
        dispatch.ensure_worktree(9)
        packet = self.tmp / "packet.md"
        packet.write_text("Fix retained PR")
        with self.deserialize_failure_code(), \
                mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            manage.apply(
                9, self.github.issue(), "FIX", "",
                {"worker": config.LABEL_CHORE, "guidance": "Fix it"}, packet,
            )
        findings = self.missing_findings(self.github.head())
        self.reviewer.assert_not_called()
        self.assert_published(comment, findings)
        self.assert_escalated(
            escalation, code="missing_worker_provenance", reason=findings, findings=None,
        )
        self.assert_not_approved(approve)

    def test_manager_fix_reviewer_unavailable_uses_fallback_reason(self) -> None:
        self.prepare()
        dispatch.ensure_worktree(9)
        packet = self.tmp / "packet.md"
        packet.write_text("Fix retained PR")
        self.reviewer.side_effect = self.unavailable_review
        with mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve:
            manage.apply(
                9, self.github.issue(), "FIX", "",
                {"worker": config.LABEL_CHORE, "guidance": "Fix it"}, packet,
            )
        findings = self.unavailable_findings()
        self.assertEqual(self.reviewer.call_count, 1)
        self.assert_published(comment, findings)
        self.assert_escalated(
            escalation,
            code="reviewer_unavailable",
            reason="reviewer unavailable after manager FIX",
            findings=None,
        )
        self.assert_not_approved(approve)

    def test_manager_fix_already_human_logs_exact_missing_provenance(self) -> None:
        self.prepare()
        self.cfg.workers[config.LABEL_CHORE] = ["implicit-worker", "{prompt}"]
        self.github.issue_labels = {config.LABEL_HUMAN, config.LABEL_CHORE}
        dispatch.ensure_worktree(9)
        packet = self.tmp / "packet.md"
        packet.write_text("Fix retained PR")
        with self.deserialize_failure_code(), \
                mock.patch.object(dispatch, "pr_comment", wraps=dispatch.pr_comment) as comment, \
                mock.patch.object(dispatch, "escalate", wraps=dispatch.escalate) as escalation, \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approve, \
                mock.patch.object(dispatch, "log", wraps=dispatch.log) as log:
            manage.apply(
                9, self.github.issue(), "FIX", "",
                {"worker": config.LABEL_CHORE, "guidance": "Fix it"}, packet,
            )
        findings = self.missing_findings(self.github.head())
        self.reviewer.assert_not_called()
        self.assert_published(comment, findings)
        escalation.assert_not_called()
        log.assert_any_call(
            f"#9: manager FIX failed (missing_worker_provenance: {findings}); "
            "ticket is already ready-for-human"
        )
        self.assert_not_approved(approve)
        self.assertIn(config.LABEL_HUMAN, self.github.issue_labels)
