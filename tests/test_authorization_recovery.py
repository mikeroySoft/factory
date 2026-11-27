"""#947 review regressions: isolated review evidence and recoverable manager failures."""
from __future__ import annotations

import subprocess
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import config, dispatch, manage, watch
from tests import test_authorization_request as authorization_tests


class AuthorizationRecoveryTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp
    command_result = authorization_tests.ManagerAuthorizationRequestTest.command_result
    events = authorization_tests.ManagerAuthorizationRequestTest.events
    request = authorization_tests.ManagerAuthorizationRequestTest.request

    def test_failed_authorize_keeps_request_pending_and_does_not_abort_later_tickets(self):
        self.cfg.manager = None
        self.request()
        self.issue8 = {**self.issue, "number": 8}
        with mock.patch.object(dispatch, "run", side_effect=self.command_result):
            manage.request_add_only_authorization(8, self.issue8, self.paths, self.report, None)
        self.cfg.manager = ["manager", "{prompt}"]
        failed = []

        def command(args, *pos, **kwargs):
            if args[:4] == ["gh", "issue", "edit", "7"] and config.LABEL_ADD_ONLY_OVERRIDE in args:
                failed.append(args)
                raise subprocess.CalledProcessError(1, args, stderr="GitHub unavailable")
            return self.command_result(args, *pos, **kwargs)

        def github(args):
            if args[:2] == ["issue", "list"]:
                return []
            return {**(self.issue if args[2] == "7" else self.issue8), "state": "OPEN"}

        with mock.patch.object(dispatch, "run", side_effect=command), \
                mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            manage.escalation_pass()
            self.assertEqual(len(failed), 1)
            self.assertTrue(manage.pending_add_only_request(7))
            self.assertFalse(manage.pending_add_only_request(8))
            self.assertEqual(len(self.manager_calls), 2)
            exits = [r for r in self.events() if r.get("event") == "lifecycle"
                     and r.get("kind") == "exit" and r.get("ticket") == 7]
            self.assertEqual(exits[-1]["outcome"], "mechanism_failure")
            self.assertEqual(exits[-1]["reason"], "github_command_failed")
            # Recovery hands the partial application to a human, never replaying its decision.
            manage.escalation_pass()

        self.assertEqual(len(self.manager_calls), 2)
        self.assertFalse(manage.pending_add_only_request(7))
        self.assertEqual(len(failed), 1)
        escalations = [r for r in self.events() if r.get("event") == "escalate"]
        self.assertEqual([(r["ticket"], r["code"]) for r in escalations], [(7, "manager_failed")])
        self.assertTrue(any(args[:4] == ["gh", "issue", "edit", "7"]
                            and config.LABEL_HUMAN in args for args in self.commands))

    def test_same_pass_application_failure_remains_pending_if_handoff_also_fails(self):
        def command(args, *pos, **kwargs):
            if args[:3] == ["gh", "issue", "edit"]:
                raise OSError("GitHub transport failed")
            return self.command_result(args, *pos, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=command), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            manage.request_add_only_authorization(7, self.issue, self.paths, self.report, self.logfile)
            self.assertTrue(manage.pending_add_only_request(7))
            manage.request_add_only_authorization(7, self.issue, self.paths, self.report, self.logfile)
            self.assertTrue(manage.pending_add_only_request(7))
        self.assertEqual(len(self.manager_calls), 1)
        # A successful later handoff completes recovery without re-running AUTHORIZE.
        self.request()
        self.assertFalse(manage.pending_add_only_request(7))
        self.assertEqual(len(self.manager_calls), 1)

    def test_unreadable_pending_issue_does_not_block_other_manager_requests(self):
        failures = (
            subprocess.CalledProcessError(1, ["gh", "issue", "view", "7"], stderr="not found"),
            OSError("GitHub unavailable"),
            ValueError("invalid issue response"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                dispatch.EVENTS.unlink(missing_ok=True)
                self.cfg.manager = None
                self.request()
                issue8 = {**self.issue, "number": 8}
                with mock.patch.object(dispatch, "run", side_effect=self.command_result):
                    manage.request_add_only_authorization(8, issue8, self.paths, self.report, None)
                self.cfg.manager = ["manager", "{prompt}"]

                def github(args, failure=failure, issue8=issue8):
                    if args[:2] == ["issue", "list"]:
                        return []
                    if args[:3] == ["issue", "view", "7"]:
                        raise failure
                    return {**issue8, "state": "OPEN"}

                with mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                        mock.patch.object(dispatch, "gh_json", side_effect=github), \
                        mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                        mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                        mock.patch.object(manage, "human_activity", return_value=False):
                    manage.escalation_pass()
                self.assertEqual([r["ticket"] for r in self.events() if r.get("event") == "manage"], [8])
                self.assertTrue(manage.pending_add_only_request(7))
                self.assertFalse(manage.pending_add_only_request(8))

    def test_unavailable_fix_worker_stays_pending_without_an_application_failure(self):
        self.manager_output = (
            'DECISION: FIX\n{"worker":"ci-fix","guidance":"Repair the current PR."}'
        )
        with mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(manage, "selectable_workers", return_value={"ci-fix": []}), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(manage, "apply", side_effect=dispatch.WorkerUnavailable("offline")), \
                self.assertRaises(dispatch.WorkerUnavailable):
            manage.request_add_only_authorization(
                7, self.issue, self.paths, self.report, self.logfile,
            )
        rows = self.events()
        self.assertTrue(manage.pending_add_only_request(7))
        self.assertEqual([r["decision"] for r in rows if r.get("event") == "manage-pending"], ["FIX"])
        self.assertFalse(any(r.get("event") == "manager-request-failed" for r in rows))


class ManagerReviewEvidenceTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp

    def test_foreign_or_prior_generation_review_cannot_skip_this_tickets_pr_review(self):
        lease = "a" * 40
        wt = self.cfg.factory / "wt-7"
        wt.mkdir()
        packet = self.cfg.factory / "packet.md"
        packet.write_text("Review this ticket's candidate.")
        pr = {"state": "OPEN", "headRefName": "agent/7", "headRefOid": lease,
              "baseRefName": "main", "reviewDecision": None}
        for evidence in ("foreign", "old-generation", "current"):
            with self.subTest(evidence=evidence):
                dispatch.EVENTS.unlink(missing_ok=True)
                dispatch.record("review", ticket=8 if evidence == "foreign" else 7,
                                head=lease, verdict="REVISE")
                if evidence == "old-generation":
                    dispatch.record("generation", ticket=7, generation=1, intent="resume")

                def git(args, **kwargs):
                    output = "agent/7\n" if args[:3] == ["git", "branch", "--show-current"] else lease + "\n"
                    return subprocess.CompletedProcess(args, 0, output, "")

                with mock.patch.object(dispatch, "run", side_effect=git), \
                        mock.patch.object(dispatch, "gh_json", return_value=pr), \
                        mock.patch.object(dispatch, "worker_round", return_value=(True, "PASS", None, lease)), \
                        mock.patch.object(dispatch, "push_agent"), \
                        mock.patch.object(dispatch, "record_risk"), \
                        mock.patch.object(dispatch, "pr_comment"), \
                        mock.patch.object(dispatch, "approve_pr", return_value=True) as approve, \
                        mock.patch.object(dispatch, "escalate") as escalate, \
                        mock.patch.object(dispatch, "pr_gate_and_review",
                                          return_value=("APPROVE", "reviewed", False)) as review:
                    manage.apply(7, self.issue, "FIX", "", {
                        "worker": "default", "guidance": "Resolve current findings.",
                    }, packet)
                if evidence == "current":
                    review.assert_not_called()
                    self.assertEqual(escalate.call_args.kwargs["code"], "review_findings_unresolved")
                else:
                    review.assert_called_once_with(wt, 7, "PASS", lease, {})
                    approve.assert_called_once()
                    escalate.assert_not_called()


class WatchUnchangedOutcomeTest(unittest.TestCase):
    def test_repeated_ticket_outcomes_are_not_engine_defects(self):
        at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        codes = ("worker_no_progress", "review_findings_unresolved", "pr_checks_unresolved",
                 "mutation_survivors_unresolved", "add_only_authorization", "dispatcher_error")
        rows = [{"event": "escalate", "ticket": n, "code": code, "at": at}
                for code in codes for n in range(3)]
        self.assertEqual(watch.repeated_escalations(rows), {
            "dispatcher_error": [row for row in rows if row["code"] == "dispatcher_error"],
        })


if __name__ == "__main__":
    unittest.main()
