"""Consumer-visible rework classification and authorization failure evidence."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import dispatch, lifecycle, manage
from tests import test_947_manager_liveness as liveness_tests
from tests import test_actionable_rework as rework_tests
from tests import test_risk_merge as risk_tests
from tests import test_unchanged_round_edges as edge_tests


class DeferredReviewCauseTest(unittest.TestCase):
    def test_unchanged_deferred_review_keeps_review_not_pr_failure_cause(self) -> None:
        fixture = edge_tests.ProcessTicketUnchangedEdgesTest.fixture(self)
        findings = rework_tests.finding("REVISE")
        dispatch._record_rework(7, risk_tests.HEAD, findings, deferred=True)
        review = fixture.patch(dispatch, "pr_gate_and_review")

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)

        review.assert_not_called()
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "review_findings_unresolved")
        self.assertEqual(fixture.escalate.call_args.kwargs["extra"], findings)
        self.assertEqual(dispatch._review_rounds_used(7), 1)


class AuthorizationLogEvidenceTest(unittest.TestCase):
    def test_manager_decline_keeps_worker_log_in_published_handoff(self) -> None:
        fixture = liveness_tests.QueuedAuthorizationLivenessTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Start from an ordinary worker failure, not the fixture's already-queued request.
        dispatch.EVENTS.unlink()
        fixture.cfg.manager = ["manager", "{prompt}"]
        logfile = fixture.cfg.factory / "worker.log"
        logfile.write_text("existing assertion failed\n")
        commands: list[list[str]] = []

        def command(args, *pos, **kwargs):
            commands.append(args)
            if args[0] == "manager":
                return subprocess.CompletedProcess(args, 0, "DECISION: HUMAN\nEdit declined.", "")
            return fixture.command_result(args, *pos, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=command), \
                mock.patch.object(dispatch, "gh_json", side_effect=fixture.github), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}):
            manage.request_add_only_authorization(
                n=7, issue=fixture.issue, paths=["tests/test_policy.py:9"],
                report="- test: FAIL\nFAILED tests.test_policy.Policy.test_existing_rule\n",
                logfile=logfile,
            )

        published = [args[args.index("--body") + 1] for args in commands
                     if args[:3] == ["gh", "issue", "comment"]]
        handoff = next(body for body in published if "Factory dispatcher escalating:" in body)
        self.assertIn(f"Worker logs: `{logfile}`", handoff)
        self.assertIn("manager declined add-only authorization", handoff)
        escalation = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                          if row.get("event") == "escalate")
        self.assertEqual(escalation["code"], "add_only_authorization")


if __name__ == "__main__":
    unittest.main()
