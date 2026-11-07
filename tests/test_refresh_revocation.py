"""A revoked ticket cannot resume refresh recovery or discard its local candidate."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_refresh_conflict as fixtures


class RefreshRevocationTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.root, self.origin = fixtures.build_fork(self.tmp)
        fixtures.git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "feature.txt").write_text("agent change\n")
        fixtures.git(self.origin, "add", "feature.txt")
        fixtures.git(self.origin, "commit", "-qm", "agent change")
        self.pr_head = fixtures.git(self.origin, "rev-parse", "HEAD")
        fixtures.git(self.origin, "checkout", "-q", "main")
        (self.origin / "main.txt").write_text("main change\n")
        fixtures.git(self.origin, "add", "main.txt")
        fixtures.git(self.origin, "commit", "-qm", "main moved")
        self.cfg = config.Config(
            root=self.root,
            repo="acme/widgets",
            main="main",
            review_rounds=3,
            workers={"default": []},
        )
        dispatch.configure(self.cfg)
        self.github = fixtures.FakeGitHub(self.origin, dispatch.run)
        self.wt = dispatch.ensure_worktree(9)

    def patched(self):
        return mock.patch.multiple(dispatch, gh_json=self.github.gh_json, run=self.github.run)

    def events(self, kind: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == kind]

    def revoke(self) -> None:
        self.github.issue_labels = {config.LABEL_HUMAN}
        self.github.assignees = []

    def assert_refresh_refused(self, head: str) -> None:
        [row] = self.events("refresh-refused")
        self.assertEqual((row["ticket"], row["head"], row["reason"]), (9, head, "ready-for-human"))

    def test_refresh_entry_after_revocation_preserves_unpublished_head(self) -> None:
        fixtures.git(self.wt, "commit", "--allow-empty", "-qm", "unpublished local work")
        local_head = fixtures.git(self.wt, "rev-parse", "HEAD")
        self.revoke()

        with self.patched(), \
                mock.patch.object(dispatch, "run_gate") as gate, \
                mock.patch.object(dispatch, "push_agent") as push, \
                mock.patch.object(dispatch, "review") as review:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertEqual(fixtures.git(self.wt, "rev-parse", "HEAD"), local_head)
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.github.pr_labels, {config.LABEL_APPROVED})
        gate.assert_not_called()
        push.assert_not_called()
        review.assert_not_called()
        self.assert_refresh_refused(local_head)

    def test_revocation_during_gate_failure_keeps_refreshed_head_without_recovery(self) -> None:
        gated_heads: list[str] = []

        def gate(wt, *_args, **_kwargs):
            gated_heads.append(fixtures.git(wt, "rev-parse", "HEAD"))
            self.revoke()
            return False, "gate failed"

        with self.patched(), \
                mock.patch.object(dispatch, "run_gate", side_effect=gate) as check, \
                mock.patch.object(dispatch, "push_agent") as push, \
                mock.patch.object(dispatch, "review") as review:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertEqual(check.call_count, 1)
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("gate-requeue"), [])
        self.assertEqual(self.events("escalate"), [])
        self.assertEqual(fixtures.git(self.wt, "rev-parse", "HEAD"), gated_heads[0])
        self.assertEqual(self.github.head(), self.pr_head)
        push.assert_not_called()
        review.assert_not_called()
        self.assert_refresh_refused(gated_heads[0])

    def test_revocation_during_risk_review_blocks_requeue_and_comment(self) -> None:
        reviewed_heads: list[str] = []

        def risk_review(_wt, _n, _report, head, _flags):
            reviewed_heads.append(head)
            dispatch._PENDING_RISK[9] = {
                "head": head,
                "disagreement": True,
                "packet": "reviewers disagreed",
            }
            self.revoke()
            return "REVISE", "reviewers disagreed", False

        with self.patched(), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "gate passed")), \
                mock.patch.object(dispatch, "record_risk"), \
                mock.patch.object(dispatch, "risk_for_head", return_value=("High", [])), \
                mock.patch.object(dispatch, "pr_gate_and_review", side_effect=risk_review) as review, \
                mock.patch.object(dispatch, "_actionable_review", return_value=False), \
                mock.patch.object(dispatch, "pr_comment") as comment, \
                mock.patch.object(dispatch, "approve_pr") as approve:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        review.assert_called_once()
        comment.assert_not_called()
        approve.assert_not_called()
        self.assertEqual(self.events("risk-requeue"), [])
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(fixtures.git(self.wt, "rev-parse", "HEAD"), reviewed_heads[0])
        self.assertEqual(self.github.head(), reviewed_heads[0])
        self.assert_refresh_refused(reviewed_heads[0])

    def test_revoked_deferred_review_rework_cannot_restore_agent_queue(self) -> None:
        head = fixtures.git(self.wt, "rev-parse", "HEAD")
        self.revoke()

        with self.patched(), self.assertRaises(dispatch.TicketRunStopped):
            dispatch._record_rework(9, head, "Fix the reviewer finding", deferred=True)

        self.assertEqual(self.events("review-rework"), [])
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(fixtures.git(self.wt, "rev-parse", "HEAD"), head)
        self.assert_refresh_refused(head)


class RefreshConflictRevocationTest(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.RefreshConflictTest.setUp(self)

    def events(self, kind: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == kind]

    def test_revocation_during_refresh_conflict_cannot_resurrect_agent_queue(self) -> None:
        wt = dispatch.ensure_worktree(9)

        def run(cmd, *args, **kwargs):
            result = self.github.run(cmd, *args, **kwargs)
            if cmd[:2] == ["git", "rebase"] and result.returncode:
                self.github.issue_labels = {config.LABEL_HUMAN}
                self.github.assignees = []
            return result

        with mock.patch.multiple(dispatch, gh_json=self.github.gh_json, run=run), \
                mock.patch.object(dispatch, "run_gate") as gate, \
                mock.patch.object(dispatch, "push_agent") as push, \
                mock.patch.object(dispatch, "review") as review:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("conflict-requeue"), [])
        self.assertEqual(fixtures.git(wt, "rev-parse", "HEAD"), self.approved_head)
        gate.assert_not_called()
        push.assert_not_called()
        review.assert_not_called()
        [row] = self.events("refresh-refused")
        self.assertEqual((row["head"], row["reason"]), (self.approved_head, "ready-for-human"))


if __name__ == "__main__":
    unittest.main()
