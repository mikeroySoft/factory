"""#15: the manager PR frontier shepherds factory-owned `agent/<n>` PRs to a terminal state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, feedback, lifecycle, manage
from tests.test_feedback import H, Provider

ROOT = Path(__file__).resolve().parents[1]
H2 = "c" * 40
NOW = "2026-09-12T00:00:00Z"


def pr_row(head=H, labels=(), updated=NOW):
    return {"id": "PR_80", "number": 80, "title": "feature", "url": "https://github.com/example/project/pull/80",
            "state": "OPEN", "headRefName": "agent/79", "headRefOid": head, "baseRefName": "main",
            "isCrossRepository": False, "isDraft": False, "labels": [{"name": n} for n in labels],
            "reviewDecision": "", "updatedAt": updated}


class FrontierTest(unittest.TestCase):
    """In-process: `gh` reads are faked per call; every mutation is captured, never executed."""

    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / ".factory").mkdir()
        self.cfg = config.Config(self.repo, "example/project", manager=["manager-stub"], manager_stale_days=7)
        dispatch.configure(self.cfg)
        self.provider = Provider()
        self.linked = True
        self.issue_labels = ["ready-for-agent"]
        self.pr = pr_row()
        self.mutations: list[list[str]] = []
        self.manager_output = "DECISION: HUMAN\nnothing"
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.fake_run))
        self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[]))
        from factory import dashboard
        self.enterContext(mock.patch.object(dashboard, "github", side_effect=self.read))

    def read(self, **kw):
        # One collection pops two heads; every pass observes the listed PR head.
        if not self.provider.heads:
            self.provider.heads = [self.pr["headRefOid"]] * 2
        value = self.provider(**kw)
        if not self.linked and kw.get("query") == feedback.HEAD_QUERY:
            value["repository"]["pullRequest"]["closingIssuesReferences"] = {"nodes": [], "pageInfo": {"hasNextPage": False}}
        return value

    def gh_json(self, args):
        if args[:2] == ["pr", "list"]:
            return [self.pr]
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[:2] == ["api", "repos/example/project"]:
            return {"id": "R_1"}
        if args[:2] == ["issue", "view"]:
            return {"id": "I_79", "number": 79, "url": "https://github.com/example/project/issues/79",
                    "title": "Ticket", "body": "Scope", "state": "OPEN",
                    "labels": [{"name": n} for n in self.issue_labels]}
        raise AssertionError(args)

    def fake_run(self, cmd, *a, **kw):
        if cmd[0] == "gh":
            self.mutations.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "manager-stub":
            return subprocess.CompletedProcess(cmd, 0, self.manager_output, "")
        raise AssertionError(cmd)

    def events(self):
        return lifecycle.read_events(dispatch.EVENTS)

    def escalations(self):
        return [e for e in self.events() if e.get("event") == "escalate"]

    def test_late_feedback_is_delivered_once_then_owned_by_the_escalation_loop(self) -> None:
        dispatch.record("claimed", ticket=79)
        manage.frontier_pass()
        escalated = self.escalations()
        self.assertEqual(len(escalated), 1)
        self.assertIn("3 unresolved feedback item(s)", escalated[0]["reason"])
        packet = Path(escalated[0]["packet"]).read_text()
        self.assertIn("## Late feedback on the current head", packet)
        self.assertIn("Fix boundary", packet)
        self.assertIn("`factory/example.py:7`", packet)
        delivered = [e for e in self.events() if e.get("event") == "feedback-delivered"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(sorted(p[0].split(":")[4] for p in delivered[0]["pairs"]), ["check_run", "review", "review_comment"])
        self.assertEqual(delivered[0]["head"], H)
        self.assertTrue(any("--add-label" in cmd and "ready-for-human" in cmd for cmd in self.mutations))
        # Escalated ticket: the escalation loop owns it; the frontier does nothing more.
        self.issue_labels = ["ready-for-human"]
        manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 1)
        # Human relabelled it ready-for-agent without changing the sources: nothing new to deliver.
        self.issue_labels = ["ready-for-agent"]
        manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 1)
        self.assertEqual(len([e for e in self.events() if e.get("event") == "feedback-delivered"]), 1)
        # An edited review under the same provider ID is a new source revision: delivered once more.
        self.provider.reviews[0]["body"] = "Fix the other boundary too"
        manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 2)
        pairs = [e["pairs"] for e in self.events() if e.get("event") == "feedback-delivered"]
        first = next(p for p in pairs[0] if ":review:" in p[0])
        self.assertEqual(len(pairs[1]), 1)
        self.assertEqual(pairs[1][0][0], first[0])
        self.assertNotEqual(pairs[1][0][1], first[1])

    def test_partial_coverage_unknown_relevance_and_resolved_threads_are_not_delivered(self) -> None:
        dispatch.record("claimed", ticket=79)
        self.provider.fail = "threads"
        self.provider.reviews[0]["state"] = "DISMISSED"
        self.provider.checks[0]["head_sha"] = "d" * 40  # historical check
        manage.frontier_pass()
        self.assertEqual(self.escalations(), [])
        self.assertEqual(self.mutations, [])
        self.provider.fail = None
        self.provider.thread["isResolved"] = True
        manage.frontier_pass()
        self.assertEqual(self.escalations(), [])

    def test_human_authored_pr_never_enters_the_frontier(self) -> None:
        # Branch text `agent/79` proves nothing: ownership needs the same-repo closing link AND a retained claim.
        self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "fail"}]))
        for linked, claimed in ((True, False), (False, True)):
            with self.subTest(linked=linked, claimed=claimed):
                self.linked = linked
                if claimed and not any(e.get("event") == "claimed" for e in self.events()):
                    dispatch.record("claimed", ticket=79)
                manage.frontier_pass()
                self.assertEqual(self.escalations(), [])
                self.assertEqual(self.mutations, [])
                self.assertFalse(any(e.get("event") == "feedback-delivered" for e in self.events()))

    def test_initiative_ticket_pr_is_refused_before_any_read(self) -> None:
        self.issue_labels = ["initiative", "ready-for-agent"]
        manage.frontier_pass()
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.mutations, [])

    def test_red_ci_and_stale_escalate_once_and_pending_waits(self) -> None:
        dispatch.record("claimed", ticket=79)
        self.provider.reviews, self.provider.checks = [], []
        self.provider.thread["isResolved"] = True
        with mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "unit", "bucket": "fail"}]):
            manage.frontier_pass()
        self.assertEqual([e["reason"] for e in self.escalations()], ["PR #80: CI failed (unit)"])
        self.issue_labels = ["ready-for-human"]
        with mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "unit", "bucket": "fail"}]):
            manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 1)
        self.issue_labels = ["ready-for-agent"]
        with mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "unit", "bucket": "pending"}]):
            manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 1)
        self.pr = pr_row(updated="2026-01-01T00:00:00Z")
        manage.frontier_pass()
        self.assertIn("stale_days = 7", self.escalations()[-1]["reason"])
        self.assertEqual(len(self.escalations()), 2)

    def approved_head(self, head):
        dispatch.record("claimed", ticket=79)
        dispatch.record("attempt", ticket=79, gate="PASS", head=head, actual_head=head)
        dispatch.record("review", ticket=79, verdict="APPROVE", accepted=True, head=head, actual_head=head)

    def quiet_provider(self):
        self.provider.reviews, self.provider.checks = [], []
        self.provider.thread["isResolved"] = True

    def test_review_all_waits_for_a_manager_approve_bound_to_the_exact_head(self) -> None:
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(H)
        # The pipeline's own approval call does not label and is not an error.
        self.assertTrue(dispatch.approve_pr(79, H))
        self.assertEqual(self.mutations, [])
        self.manager_output = "DECISION: APPROVE\nLooks right"
        manage.frontier_pass()
        manage_rows = [e for e in self.events() if e.get("event") == "manage"]
        self.assertEqual([(e["pr"], e["head"], e["decision"], e["round"]) for e in manage_rows], [(80, H, "APPROVE", 1)])
        self.assertTrue(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))
        self.assertEqual([e["head"] for e in self.events() if e.get("event") == "approved"], [H])
        prompt = (self.repo / ".factory/manager-prompt-79.md").read_text()
        self.assertIn("DECISION: APPROVE|FIX|CLOSE|HUMAN", prompt)
        self.assertIn(f"head {H}", prompt)
        # Same head again: one decision per head, no second manager run.
        self.mutations.clear()
        manage.frontier_pass()
        self.assertEqual(self.mutations, [])
        # A refreshed head is a new head: the old decision never re-binds.
        self.pr = pr_row(head=H2)
        self.provider.heads = []
        self.approved_head(H2)
        self.assertFalse(dispatch.manager_approval(self.events(), 79, H2))
        manage.frontier_pass()  # rounds = 1 exhausted
        self.assertIn("manager rounds exhausted", self.escalations()[-1]["reason"])
        self.assertFalse(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))

    def test_review_all_close_and_human_decisions_act_through_the_shared_apply(self) -> None:
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(H)
        self.manager_output = "DECISION: CLOSE\nDuplicate of shipped work"
        manage.frontier_pass()
        flat = [" ".join(cmd) for cmd in self.mutations]
        self.assertTrue(any(c.startswith("gh pr close 80") for c in flat), flat)
        self.assertTrue(any("issue edit 79" in c and "--add-label wontfix-proposal" in c for c in flat), flat)
        self.assertFalse(any("issue close" in c for c in flat), flat)
        self.assertFalse(any("ready-for-agent" in c for c in flat), flat)
        self.assertEqual(self.escalations(), [])

    def test_manager_approval_is_discarded_when_the_head_moves_while_thinking(self) -> None:
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(H)
        self.manager_output = "DECISION: APPROVE\nfine"
        moved = pr_row(head=H2)
        original = self.gh_json

        def racing(args):
            if args[:2] == ["pr", "view"] and "state,headRefOid,reviewDecision" in args:
                return moved
            return original(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=racing):
            manage.frontier_pass()
        self.assertFalse(any(e.get("event") == "manage" for e in self.events()))
        self.assertEqual(self.mutations, [])


class CloseDecisionTest(unittest.TestCase):
    def test_close_proposes_wontfix_on_human_issue_and_closes_factory_child(self) -> None:
        for created in (False, True):
            with self.subTest(factory_created=created), tempfile.TemporaryDirectory() as d:
                repo = Path(d)
                (repo / ".factory").mkdir()
                dispatch.configure(config.Config(repo, "example/project"))
                if created:
                    dispatch.record("issue-created", ticket=79, parent=70)
                calls = []
                with mock.patch.object(dispatch, "gh_json", return_value={"number": 80, "state": "OPEN", "baseRefName": "main"}), \
                        mock.patch.object(dispatch, "run", side_effect=lambda cmd, *a, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")):
                    manage.apply(79, {"title": "t", "body": "b"}, "CLOSE", "Not worth landing", None, repo / "packet.md")
                flat = [" ".join(c) for c in calls]
                self.assertEqual(flat[0][:len("gh pr close 80 --repo example/project --comment Factory manager: Not worth landing")],
                                 "gh pr close 80 --repo example/project --comment Factory manager: Not worth landing")
                self.assertEqual(any("issue close 79" in c for c in flat), created)
                self.assertEqual(any("--add-label wontfix-proposal" in c for c in flat), not created)
                self.assertFalse(any("ready-for-agent" in c for c in flat))

    def test_close_requires_an_open_pr_on_the_configured_target(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".factory").mkdir()
            dispatch.configure(config.Config(repo, "example/project"))
            for pr in ({"number": 80, "state": "MERGED", "baseRefName": "main"}, {"number": 80, "state": "OPEN", "baseRefName": "stable"}):
                with self.subTest(pr=pr), mock.patch.object(dispatch, "gh_json", return_value=pr), \
                        mock.patch.object(dispatch, "run") as run, self.assertRaises(ValueError):
                    manage.apply(79, {"title": "t", "body": "b"}, "CLOSE", "x", None, repo / "p.md")
                run.assert_not_called()

    def test_parse_accepts_close_and_approve_only_where_allowed(self) -> None:
        self.assertEqual(manage.parse("DECISION: CLOSE\nwhy", {})[0], "CLOSE")
        self.assertEqual(manage.parse("DECISION: APPROVE\nwhy", {})[0], "HUMAN")
        self.assertEqual(manage.parse("DECISION: APPROVE\nwhy", {}, approval=True)[0], "APPROVE")
        self.assertEqual(manage.parse("DECISION: CLOSE\n", {})[0], "HUMAN")


if __name__ == "__main__":
    unittest.main()
