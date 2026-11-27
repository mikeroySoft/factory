"""#535: a completed failed FIX is resolved even when the issue is already human-owned."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, dispatch, lifecycle, manage, stats, trace
from tests import test_refresh_conflict as refresh_suite


class ManagerFixOutcomeTest(unittest.TestCase):
    setUp = refresh_suite.RefreshConflictTest.setUp
    patched = refresh_suite.RefreshConflictTest.patched
    events = refresh_suite.RefreshConflictTest.events

    def prepare_fix(self) -> None:
        self.cfg.fixups = False
        self.cfg.workers = {
            "default": [sys.executable, "-c", "print('worker ran')", "--model", "fixture/pinned"],
        }
        self.github.issue_labels = {config.LABEL_HUMAN}
        self.wt = self.cfg.factory / "wt-9"
        self.cfg.factory.mkdir(exist_ok=True)
        dispatch.LOGS.mkdir(exist_ok=True)
        refresh_suite.git(self.root, "fetch", "-q", "origin")
        refresh_suite.git(self.root, "worktree", "add", "-b", "agent/9", str(self.wt), "origin/agent/9")
        self.packet = self.cfg.factory / "packet.md"
        self.packet.write_text("Manager evidence")
        dispatch.record("manage", ticket=9, decision="FIX", round=1)

    def apply_fix(self) -> None:
        manage.apply(9, self.github.issue(), "FIX", "", {
            "worker": "default", "guidance": "Repair the failing gate",
        }, self.packet)

    def assert_failed_cohort(self) -> None:
        now = datetime.now(UTC)
        result = stats.post_fix_metrics(lifecycle.read_events(dispatch.EVENTS), now - timedelta(days=1), now)
        self.assertEqual(result["by_worker_model"], {
            "fixture/pinned": {
                "merged": 0, "re_escalated": 1, "pending": 0, "resolved": 1, "merge_rate": 0.0,
            },
        })
        self.assertEqual((result["resolved"], result["pending"], result["merge_rate"]), (1, 0, 0.0))
        # No repeated escalation, new manager round, or duplicate claim to measure the failure.
        self.assertEqual(self.events("escalate"), [])
        self.assertEqual(len(self.events("manage")), 1)
        self.assertEqual(self.events("claimed"), [])
        self.assertFalse(any(cmd[1:3] == ["issue", "edit"] for cmd in self.github.calls))

    def test_pre_worker_head_and_lease_failures_resolve_without_model_attribution(self) -> None:
        self.prepare_fix()
        (self.wt / "local.txt").write_text("retained local commit")
        refresh_suite.git(self.wt, "add", "local.txt")
        refresh_suite.git(self.wt, "commit", "-qm", "retained worker change")
        head = refresh_suite.git(self.wt, "rev-parse", "HEAD")
        for own, code in ((False, "refresh_head"), (True, "lease_refused")):
            with self.subTest(code=code):
                dispatch.EVENTS.unlink()
                if own:
                    dispatch.record("attempt", ticket=9, head=head)
                dispatch.record("manage", ticket=9, decision="FIX", round=1)
                with self.patched(), mock.patch.object(dispatch, "push_agent", return_value=None):
                    self.apply_fix()
                now = datetime.now(UTC)
                result = stats.post_fix_metrics(lifecycle.read_events(dispatch.EVENTS), now - timedelta(days=1), now)
                self.assertEqual(result["by_worker_model"], {
                    stats.NO_WORKER_CLAIM: {
                        "merged": 0, "re_escalated": 1, "pending": 0, "resolved": 1, "merge_rate": 0.0,
                    },
                })
                receipt = self.events("decision")[-1]
                self.assertEqual((receipt["stage"], receipt["chosen"]), ("manager", "FIX_FAILED"))
                self.assertTrue(receipt["rationale"].startswith(code + ":"))
                self.assertEqual(self.events("escalate"), [])

    def test_failed_retry_and_unrelated_decisions_are_not_terminal_fix_outcomes(self) -> None:
        self.prepare_fix()
        dispatch.record("attempt", ticket=9, gate="FAIL", worker_model="fixture/pinned")
        for stage, chosen in (("gate", "FIX_FAILED"), ("manager", "FIX")):
            dispatch.record("decision", **trace.decision(
                ticket=9, stage=stage, chosen=chosen,
                inputs=[], options=[{"option": chosen, "eligible": True, "reason": "retry"}],
                rationale="retry", causes=[], cost=None,
                actor={"kind": "deterministic", "name": "fixture"},
            ))
        dispatch.record("llm-usage", ticket=9, stage="manager", chosen="FIX_FAILED")
        now = datetime.now(UTC)
        result = stats.post_fix_metrics(lifecycle.read_events(dispatch.EVENTS), now - timedelta(days=1), now)
        self.assertEqual((result["pending"], result["re_escalated"], result["resolved"]), (1, 0, 0))

    def test_failed_direct_manager_gate_is_resolved_without_re_escalating_the_issue(self) -> None:
        self.prepare_fix()
        with self.patched(), \
                mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(False, "gate failed")):
            self.apply_fix()
        [attempt] = self.events("attempt")
        self.assertEqual((attempt["gate"], attempt["worker_model"]), ("FAIL", "fixture/pinned"))
        self.assert_failed_cohort()

    def test_post_worker_failures_resolve_but_mutation_wait_and_approval_remain_pending(self) -> None:
        self.prepare_fix()
        for verdict, pr_failed, approval, failed in (
            ("REVISE", True, True, True),
            (None, False, True, True),
            ("REVISE", False, True, True),
            ("APPROVE", False, False, True),
            (dispatch.PR_GATE_WAIT, False, True, False),
            ("APPROVE", False, True, False),
        ):
            with self.subTest(verdict=verdict, pr_failed=pr_failed, approval=approval):
                dispatch.EVENTS.unlink()
                dispatch.record("manage", ticket=9, decision="FIX", round=1)
                with self.patched(), \
                        mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                        mock.patch.object(dispatch, "commit_leftovers"), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "gate passed")), \
                        mock.patch.object(dispatch, "push_agent"), \
                        mock.patch.object(dispatch, "record_risk"), \
                        mock.patch.object(dispatch, "pr_comment"), \
                        mock.patch.object(dispatch, "defer_mutation_proof"), \
                        mock.patch.object(dispatch, "pr_gate_and_review", return_value=(verdict, "findings", pr_failed)), \
                        mock.patch.object(dispatch, "approve_pr", return_value=approval):
                    self.apply_fix()
                if failed:
                    self.assert_failed_cohort()
                else:
                    now = datetime.now(UTC)
                    result = stats.post_fix_metrics(lifecycle.read_events(dispatch.EVENTS), now - timedelta(days=1), now)
                    self.assertEqual((result["pending"], result["resolved"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
