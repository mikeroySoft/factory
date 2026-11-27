"""Authorization pauses preserve unresolved review work without buying another review."""
from __future__ import annotations

import json
import subprocess
import unittest

from factory import config, dispatch, lifecycle, manage
from tests import test_actionable_rework as rework_tests
from tests import test_risk_merge as risk_tests
from tests import test_unchanged_round_edges as edge_tests


class AuthorizationReworkTest(unittest.TestCase):
    def fixture(self):
        fixture = edge_tests.ProcessTicketUnchangedEdgesTest.fixture(self)
        fixture.cfg.manager = ["manager", "{prompt}"]
        fixture.cfg.review_rounds = 2
        fixture.patch(manage, "human_activity", return_value=False)
        fixture.patch(manage, "selectable_workers", return_value={"default": []})
        original = fixture.command.side_effect

        def command(args, **kwargs):
            if args[0] == "manager":
                output = "DECISION: AUTHORIZE\n" + json.dumps({
                    "justification": "The behavior needs a contract change.",
                    "guidance": "Update only the named assertion.",
                })
                return subprocess.CompletedProcess(args, 0, output, "")
            if args[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(
                    args, 0, "https://github.com/acme/widgets/issues/7#issuecomment-123", "",
                )
            if args[:3] == ["gh", "issue", "edit"] and "--add-label" in args:
                label = args[args.index("--add-label") + 1]
                fixture.issue["labels"].append({"name": label})
            return original(args, **kwargs)

        fixture.command.side_effect = command
        return fixture

    def authorization_resume(self, findings, pr_failed, bounce_ok=True, changed=False):
        fixture = self.fixture()
        calls = []

        def work(*args, **kwargs):
            head = risk_tests.NEW_HEAD if changed and len(calls) == 2 else risk_tests.HEAD
            calls.append(args[4])
            fixture.pr["headRefOid"] = head
            handoff = fixture.wt / ".factory" / "handoff-7.md"
            handoff.parent.mkdir(exist_ok=True)
            handoff.write_text("Needs: add-only-authorization tests/test_contract.py:12\n"
                               if len(calls) == 2 else "Done.\n")
            ok = bounce_ok or len(calls) != 2
            dispatch.record(
                "attempt", ticket=7, head=head, actual_head=head,
                gate="PASS" if ok else "FAIL", clean=True,
                worker_model="worker/model", finished=True,
            )
            return ok, "- test: PASS\n" if ok else "- test: FAIL\n", fixture.root / "worker.log", head

        fixture.worker.side_effect = work
        review = fixture.patch(dispatch, "pr_gate_and_review", side_effect=[
            ("REVISE", findings, pr_failed), ("APPROVE", "approved", False),
        ])
        fixture.patch(dispatch, "_actionable_review", return_value=True)
        fixture.patch(dispatch, "approve_pr", return_value=True)
        dispatch.process_ticket(fixture.issue, 1, False)
        self.assertEqual(len(calls), 2)
        self.assertIn(config.LABEL_ADD_ONLY_OVERRIDE,
                      {label["name"] for label in fixture.issue["labels"]})
        self.assertEqual(dispatch._review_rounds_used(7), 0)
        fixture.escalate.assert_not_called()

        dispatch.process_ticket(fixture.issue, 1, False)

        self.assertEqual(len(calls), 3)
        self.assertIn(findings, calls[-1])
        return fixture, review

    def test_authorized_noop_preserves_mutation_survivors_without_pr_consumers(self):
        fixture, review = self.authorization_resume(rework_tests.SURVIVORS, True)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "mutation_survivors_unresolved")
        self.assertEqual(dispatch._review_rounds_used(7), 0)

    def test_authorized_noop_preserves_failed_pr_check_cause(self):
        fixture, review = self.authorization_resume("- required-check: FAIL\n", True)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "pr_checks_unresolved")

    def test_authorized_noop_preserves_review_findings_after_attempt_failure(self):
        findings = rework_tests.finding("REVISE")
        fixture, review = self.authorization_resume(findings, False, bounce_ok=False)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "review_findings_unresolved")

    def test_authorized_changed_head_can_reach_fresh_pr_consumers(self):
        fixture, review = self.authorization_resume("- required-check: FAIL\n", True, changed=True)
        self.assertEqual(review.call_count, 2)
        fixture.escalate.assert_not_called()

    def test_review_bounce_lifecycle_rounds_are_sequential(self):
        fixture = self.fixture()
        fixture.configure_reviews(("REVISE", "APPROVE"))
        original = fixture.work
        rounds = []

        def work(*args, **kwargs):
            rounds.append(lifecycle.current().review_round)
            return original(*args, **kwargs)

        fixture.worker.side_effect = work
        dispatch.process_ticket(fixture.issue, 1, False)
        self.assertEqual(rounds[1:], [2, 3])


if __name__ == "__main__":
    unittest.main()
