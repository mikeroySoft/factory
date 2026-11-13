"""#15: the manager PR frontier shepherds factory-owned `agent/<n>` PRs to a terminal state."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, feedback, lifecycle, manage
from tests.test_feedback import H, Provider

ROOT = Path(__file__).resolve().parents[1]
H2 = "c" * 40
# `manage.py` measures staleness against the real clock, so a hardcoded fixture
# date does not stay fresh: every test using this row started escalating as
# stale the moment wall time passed 2026-09-12 + manager_stale_days (7), which
# turned the suite red on 2026-09-19 with no code change. Anchor "recently
# updated" to now; the stale case passes its own explicit old date.
NOW = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr_row(head=H, labels=(), updated=NOW):
    return {"id": "PR_80", "number": 80, "title": "feature", "url": "https://github.com/example/project/pull/80",
            "state": "OPEN", "headRefName": "agent/79", "headRefOid": head, "baseRefName": "main",
            "isCrossRepository": False, "isDraft": False, "labels": [{"name": n} for n in labels],
            "reviewDecision": "", "updatedAt": updated}


def claim_comment(i: int, host: str, at: datetime) -> dict:
    return {"id": i, "user": {"login": "factory-bot"}, "created_at": at.isoformat(),
            "body": f"Factory claim: host={host} nonce=n{i} at={at.isoformat()}"}


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
        self.timeline: list[dict] = []
        self.pr = pr_row()
        self.mutations: list[list[str]] = []
        self.manager_output = "DECISION: HUMAN\nnothing"
        self.claim_comments: list[dict] = []
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
        if args[:2] == ["api", "repos/example/project/issues/79/timeline"]:
            return [self.timeline]
        if args[:2] == ["issue", "view"]:
            return {"id": "I_79", "number": 79, "url": "https://github.com/example/project/issues/79",
                    "title": "Ticket", "body": "Scope", "state": "OPEN",
                    "labels": [{"name": n} for n in self.issue_labels]}
        if args[:2] == ["api", "user"]:
            return {"login": "factory-bot"}
        if args[-1] == "repos/example/project/issues/79/comments":
            return [self.claim_comments]  # host-claim comments (escalate reads them when host_name is set)
        raise AssertionError(args)

    def fake_run(self, cmd, *a, **kw):
        if cmd[0] == "gh":
            self.mutations.append(cmd)
            stdout = (
                "https://github.com/example/project/pull/80#issuecomment-8001"
                if cmd[1:3] == ["pr", "comment"] else
                "https://github.com/example/project/issues/79#issuecomment-7901"
                if cmd[1:3] == ["issue", "comment"] else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
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

    def test_meta_loop_prs_never_enter_the_frontier(self) -> None:
        dispatch.record("claimed", ticket=79)
        self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]))
        for kind in ("curate", "lessons"):
            with self.subTest(kind=kind):
                self.pr = {**pr_row(labels=("factory-approved",)), "headRefName": f"agent/{kind}-2026-10-08"}
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

    def released_after_red_ci(self) -> None:
        """#188: gate failed at 18:52; the owner released the ticket to ready-for-agent at 19:45."""
        dispatch.record("claimed", ticket=79)
        self.provider.reviews = []
        self.provider.thread["isResolved"] = True
        self.provider.checks[0]["updated_at"] = "2026-10-07T18:52:10Z"
        self.timeline = [{"event": "labeled", "label": {"name": "ready-for-agent"}, "created_at": "2026-10-01T00:00:00Z"},
                         {"event": "labeled", "label": {"name": "ready-for-agent"}, "created_at": "2026-10-07T19:45:44Z"}]

    def test_red_ci_older_than_the_release_to_ready_for_agent_waits_for_the_worker(self) -> None:
        self.released_after_red_ci()
        with mock.patch.object(dispatch, "pr_checks", return_value=[
                {"name": "gate", "bucket": "fail", "completedAt": "2026-10-07T18:52:10Z"}]):
            manage.frontier_pass()
        self.assertEqual(self.escalations(), [])
        self.assertEqual(self.mutations, [])

    def test_red_ci_newer_than_the_release_still_escalates(self) -> None:
        self.released_after_red_ci()
        with mock.patch.object(dispatch, "pr_checks", return_value=[
                {"name": "gate", "bucket": "fail", "completedAt": "2026-10-07T18:52:10Z"},
                {"name": "unit", "bucket": "fail", "completedAt": "2026-10-07T20:10:00Z"}]):
            manage.frontier_pass()
        self.assertEqual([e["reason"] for e in self.escalations()], ["PR #80: CI failed (unit)"])

    def test_zero_timestamp_red_check_after_the_release_still_escalates(self) -> None:
        # `gh pr checks` reports commit-status checks with the zero time; that is unknown, not old.
        self.released_after_red_ci()
        with mock.patch.object(dispatch, "pr_checks", return_value=[
                {"name": "ci", "bucket": "fail", "completedAt": "0001-01-01T00:00:00Z"}]):
            manage.frontier_pass()
        self.assertEqual([e["reason"] for e in self.escalations()], ["PR #80: CI failed (ci)"])

    def test_stale_pr_released_recently_is_not_stale(self) -> None:
        # The owner took longer than stale_days to answer; the release restarts the clock.
        self.released_after_red_ci()
        self.provider.checks = []
        self.pr = pr_row(updated="2026-01-01T00:00:00Z")
        self.timeline[-1]["created_at"] = NOW
        manage.frontier_pass()
        self.assertEqual(self.escalations(), [])
        self.assertEqual(self.mutations, [])

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

    def test_failed_close_is_not_replayed_for_the_same_head(self) -> None:
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(H)
        self.manager_output = "DECISION: CLOSE\nDuplicate of shipped work"

        def fail_close(cmd, *args, **kwargs):
            result = self.fake_run(cmd, *args, **kwargs)
            if cmd[:3] == ["gh", "pr", "close"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="close failed")
            return result

        with mock.patch.object(dispatch, "run", side_effect=fail_close):
            manage.frontier_pass()
            self.assertEqual(len([cmd for cmd in self.mutations if cmd[1:3] == ["pr", "comment"]]), 1)
            self.assertEqual(len([cmd for cmd in self.mutations if cmd[1:3] == ["pr", "close"]]), 1)
            self.assertFalse(any(cmd[1:3] == ["issue", "comment"] for cmd in self.mutations))
            self.mutations.clear()
            manage.frontier_pass()

        self.assertEqual(self.mutations, [])
        decisions = [e for e in self.events() if e.get("event") == "manage"]
        self.assertEqual([(e["pr"], e["head"], e["decision"]) for e in decisions], [(80, H, "CLOSE")])
        receipts = [e for e in self.events() if e.get("event") == "comment"]
        self.assertEqual([(e["ticket"], e["pr"], e["comment"]) for e in receipts], [(80, 80, 8001)])

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


    def manage_on(self, host: str, triage: str = "tank") -> None:
        """#723 r3-0440: a whole manage_pass on `host`, with only the unrelated passes stubbed."""
        self.cfg.host_name, self.cfg.triage_host = host, triage
        with mock.patch.object(manage, "viability_pass") as viability, \
                mock.patch.object(manage, "escalation_pass"), mock.patch.object(manage.handoff, "handoff_pass"):
            manage.manage_pass()
        if host != triage:
            viability.assert_not_called()
        else:
            viability.assert_called_once_with(False)

    def test_non_triage_host_escalates_ci_failure_on_its_own_claimed_pr(self) -> None:
        dispatch.record("claimed", ticket=79)  # this host's journal owns the PR
        self.quiet_provider()
        with mock.patch.object(dispatch, "pr_checks", return_value=[
                {"name": "gate", "bucket": "fail", "completedAt": "2026-10-07T18:52:10Z"}]):
            self.manage_on("ultron")
        self.assertEqual([e["reason"] for e in self.escalations()], ["PR #80: CI failed (gate)"])

    def test_non_triage_host_escalates_a_stale_pr_it_claimed(self) -> None:
        dispatch.record("claimed", ticket=79)
        self.quiet_provider()
        self.pr = pr_row(updated="2026-01-01T00:00:00Z")
        self.manage_on("ultron")
        self.assertEqual(len(self.escalations()), 1)

    def test_non_triage_host_gets_its_local_manager_approve_under_review_all(self) -> None:
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(H)
        self.assertTrue(dispatch.approve_pr(79, H))  # waits: no local manager APPROVE yet
        self.assertEqual(self.mutations, [])
        self.manager_output = "DECISION: APPROVE\nLooks right"
        self.manage_on("ultron")
        self.assertEqual([(e["pr"], e["head"], e["decision"]) for e in self.events() if e.get("event") == "manage"],
                         [(80, H, "APPROVE")])
        self.assertTrue(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))

    def test_pr_claimed_only_by_the_other_host_gets_no_frontier_action(self) -> None:
        self.quiet_provider()  # no local `claimed` row: the other host's journal owns ticket 79
        logs: list[str] = []
        with mock.patch.object(dispatch, "pr_checks", return_value=[
                {"name": "gate", "bucket": "fail", "completedAt": "2026-10-07T18:52:10Z"}]), \
                mock.patch.object(dispatch, "log", side_effect=logs.append):
            self.manage_on("ultron")
        # The frontier did look at the PR on this host and refused it for lack of a local claim.
        self.assertTrue(any(m.startswith("PR #80: not factory-owned") for m in logs), logs)
        self.assertEqual(self.escalations(), [])
        self.assertEqual(self.mutations, [])

    def shepherd_moved_ticket(self, expired: str, elected: str) -> dict:
        # #777: ticket 79 moved `expired` -> `elected`, so both hosts' journals hold a `claimed` row;
        # the issue's elected host claim (the live one) decides frontier ownership.
        old, now = datetime.now(UTC) - timedelta(days=2), datetime.now(UTC)
        self.claim_comments = [claim_comment(1, expired, old), claim_comment(2, elected, now)]
        escalated = {}
        for host in ("tank", "ultron"):
            journal = tempfile.TemporaryDirectory()
            self.addCleanup(journal.cleanup)
            (Path(journal.name) / ".factory").mkdir()
            self.cfg = config.Config(Path(journal.name), "example/project", manager=["manager-stub"], manager_stale_days=7)
            dispatch.configure(self.cfg)
            dispatch.record("claimed", ticket=79)  # this host's own journal claims ticket 79
            self.quiet_provider()
            self.mutations.clear()
            with mock.patch.object(dispatch, "pr_checks", return_value=[
                    {"name": "gate", "bucket": "fail", "completedAt": "2026-10-07T18:52:10Z"}]):
                self.manage_on(host)
            escalated[host] = ([e["reason"] for e in self.escalations()], bool(self.mutations))
        return escalated

    def test_moved_ticket_claimed_in_both_journals_is_shepherded_only_by_the_elected_host(self) -> None:
        self.assertEqual(self.shepherd_moved_ticket("tank", "ultron"),
                         {"tank": ([], False), "ultron": (["PR #80: CI failed (gate)"], True)})

    def test_elected_host_sorting_before_this_host_still_owns_the_frontier(self) -> None:
        # "tank" < "ultron": the skip is host inequality, not name order.
        self.assertEqual(self.shepherd_moved_ticket("ultron", "tank"),
                         {"tank": (["PR #80: CI failed (gate)"], True), "ultron": ([], False)})

    def test_skipping_another_hosts_pr_still_shepherds_the_next_pr(self) -> None:
        # PR #82 (ticket 81, elected tank) is listed before PR #80 (ticket 79, claimed here).
        other = {**pr_row(), "id": "PR_82", "number": 82, "headRefName": "agent/81",
                 "url": "https://github.com/example/project/pull/82"}
        fixture = self.gh_json

        def gh_json(args):
            if args[:2] == ["pr", "list"]:
                return [other, self.pr]
            if args[-1] == "repos/example/project/issues/81/comments":
                return [[claim_comment(1, "tank", datetime.now(UTC))]]
            return fixture(args)

        dispatch.record("claimed", ticket=79)
        self.quiet_provider()
        logs: list[str] = []
        with mock.patch.object(dispatch, "gh_json", side_effect=gh_json), \
                mock.patch.object(dispatch, "log", side_effect=logs.append), \
                mock.patch.object(dispatch, "pr_checks", return_value=[
                    {"name": "gate", "bucket": "fail", "completedAt": "2026-10-07T18:52:10Z"}]):
            self.manage_on("ultron")
        self.assertIn("PR #82: skipped, elected claim belongs to host tank", logs)
        self.assertEqual([e["reason"] for e in self.escalations()], ["PR #80: CI failed (gate)"])



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

                def run(cmd, *args, calls=calls, **kwargs):
                    calls.append(cmd)
                    stdout = (
                        "https://github.com/example/project/pull/80#issuecomment-8001"
                        if cmd[1:3] == ["pr", "comment"] else
                        "https://github.com/example/project/issues/79#issuecomment-7901"
                        if cmd[1:3] == ["issue", "comment"] else
                        "closed"
                    )
                    return subprocess.CompletedProcess(cmd, 0, stdout, "")

                with mock.patch.object(
                    dispatch, "gh_json",
                    return_value={"number": 80, "state": "OPEN", "baseRefName": "main"},
                ), mock.patch.object(dispatch, "run", side_effect=run):
                    manage.apply(79, {"title": "t", "body": "b"}, "CLOSE", "Not worth landing", None, repo / "packet.md")

                def actions(subject, action, calls=calls):
                    return [cmd for cmd in calls if cmd[1:3] == [subject, action]]

                self.assertEqual(len(actions("pr", "comment")), 1)
                self.assertEqual(len(actions("pr", "close")), 1)
                self.assertNotIn("--comment", actions("pr", "close")[0])
                self.assertEqual(len(actions("issue", "comment")), 1)
                self.assertEqual(len(actions("issue", "close")), int(created))
                self.assertEqual(len(actions("issue", "edit")), int(not created))
                if created:
                    self.assertIn("not planned", actions("issue", "close")[0])
                    self.assertNotIn("--comment", actions("issue", "close")[0])
                else:
                    self.assertIn(config.LABEL_WONTFIX, actions("issue", "edit")[0])
                self.assertFalse(any("ready-for-agent" in cmd for cmd in calls))

                receipts = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "comment"]
                self.assertEqual(
                    {(e["ticket"], e["pr"], e["comment"], e["url"], e["decision"]) for e in receipts},
                    {
                        (80, 80, 8001, "https://github.com/example/project/pull/80#issuecomment-8001", "CLOSE"),
                        (79, 80, 7901, "https://github.com/example/project/issues/79#issuecomment-7901", "CLOSE"),
                    },
                )

    def test_close_without_pr_comments_with_reason_and_closes_issue(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".factory").mkdir()
            dispatch.configure(config.Config(repo, "example/project"))
            dispatch.record("issue-created", ticket=79, parent=70)
            no_pr = subprocess.CalledProcessError(
                1, ["gh"], stderr='no pull requests found for branch "agent/79"',
            )
            result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
            with mock.patch.object(dispatch, "gh_json", side_effect=no_pr), \
                    mock.patch.object(dispatch, "run", return_value=result) as run, \
                    mock.patch.object(dispatch, "comment_receipt"):
                manage.apply(
                    79, {"title": "t", "body": "b"}, "CLOSE",
                    "Transient host failure.", None, repo / "packet.md",
                )

            commands = [call.args[0] for call in run.call_args_list]
            comment = next(i for i, command in enumerate(commands)
                           if command[1:3] == ["issue", "comment"])
            close = commands.index([
                "gh", "issue", "close", "79", "--repo", dispatch.REPO,
                "--reason", "not planned",
            ])
            self.assertLess(comment, close)
            self.assertIn("Transient host failure.", commands[comment][-1])
            self.assertFalse(any(command[1:3] == ["pr", "close"] for command in commands))

    def test_close_without_pr_leaves_human_issue_open(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".factory").mkdir()
            dispatch.configure(config.Config(repo, "example/project"))
            no_pr = subprocess.CalledProcessError(
                1, ["gh"], stderr='no pull requests found for branch "agent/79"',
            )
            result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
            with mock.patch.object(dispatch, "gh_json", side_effect=no_pr), \
                    mock.patch.object(dispatch, "run", return_value=result) as run, \
                    mock.patch.object(dispatch, "comment_receipt"):
                manage.apply(
                    79, {"title": "t", "body": "b"}, "CLOSE",
                    "No change should land.", None, repo / "packet.md",
                )

            commands = [call.args[0] for call in run.call_args_list]
            self.assertFalse(any(command[1:3] == ["pr", "close"] for command in commands))
            self.assertFalse(any(command[1:3] == ["issue", "close"] for command in commands))
            edit = next(command for command in commands
                        if command[1:3] == ["issue", "edit"])
            self.assertIn(config.LABEL_WONTFIX, edit)

    def test_close_with_closed_pr_preserves_issue_provenance(self) -> None:
        for created in (False, True):
            with self.subTest(factory_created=created), tempfile.TemporaryDirectory() as d:
                repo = Path(d)
                (repo / ".factory").mkdir()
                dispatch.configure(config.Config(repo, "example/project"))
                if created:
                    dispatch.record("issue-created", ticket=79, parent=70)
                result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
                with mock.patch.object(
                    dispatch, "gh_json",
                    return_value={"number": 80, "state": "CLOSED", "baseRefName": "main"},
                ), mock.patch.object(dispatch, "run", return_value=result) as run, \
                        mock.patch.object(dispatch, "comment_receipt"):
                    manage.apply(
                        79, {"title": "t", "body": "b"}, "CLOSE",
                        "No change should land.", None, repo / "packet.md",
                    )

                commands = [call.args[0] for call in run.call_args_list]
                self.assertFalse(any(command[1:3] == ["pr", "close"] for command in commands))
                self.assertEqual(
                    sum(command[1:3] == ["issue", "close"] for command in commands),
                    int(created),
                )
                edits = [command for command in commands if command[1:3] == ["issue", "edit"]]
                self.assertEqual(len(edits), int(not created))
                if edits:
                    self.assertIn(config.LABEL_WONTFIX, edits[0])

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

    def test_close_compares_pr_fields_by_value(self) -> None:
        for state, closes_pr in (
            (bytearray(b"CLOSED").decode(), False),
            (bytearray(b"OPEN").decode(), True),
        ):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as d:
                repo = Path(d)
                (repo / ".factory").mkdir()
                dispatch.configure(config.Config(repo, "example/project"))
                pr = {
                    "number": 80,
                    "state": state,
                    "baseRefName": bytearray(b"main").decode(),
                }
                result = subprocess.CompletedProcess(
                    [], 0, stdout="https://example.test/comment\n",
                )
                with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                        mock.patch.object(dispatch, "run", return_value=result) as run, \
                        mock.patch.object(dispatch, "comment_receipt"):
                    manage.apply(
                        79, {"title": "t", "body": "b"}, "CLOSE",
                        "No change should land.", None, repo / "packet.md",
                    )

                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(
                    any(command[1:3] == ["pr", "close"] for command in commands),
                    closes_pr,
                )

    def test_close_rejects_unknown_states_and_other_targets(self) -> None:
        prs = (
            {"number": 80, "state": "BOGUS", "baseRefName": "main"},
            {"number": 80, "state": "UNKNOWN", "baseRefName": "main"},
            {"number": 80, "state": "OPEN", "baseRefName": "develop"},
        )
        for pr in prs:
            with self.subTest(pr=pr), tempfile.TemporaryDirectory() as d:
                repo = Path(d)
                (repo / ".factory").mkdir()
                dispatch.configure(config.Config(repo, "example/project"))
                with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                        mock.patch.object(dispatch, "run") as run, \
                        self.assertRaises(ValueError):
                    manage.apply(
                        79, {"title": "t", "body": "b"}, "CLOSE",
                        "No change should land.", None, repo / "packet.md",
                    )
                run.assert_not_called()

    def test_parse_accepts_close_and_approve_only_where_allowed(self) -> None:
        self.assertEqual(manage.parse("DECISION: CLOSE\nwhy", {})[0], "CLOSE")
        self.assertEqual(manage.parse("DECISION: APPROVE\nwhy", {})[0], "HUMAN")
        self.assertEqual(manage.parse("DECISION: APPROVE\nwhy", {}, approval=True)[0], "APPROVE")
        self.assertEqual(manage.parse("DECISION: CLOSE\n", {})[0], "HUMAN")


if __name__ == "__main__":
    unittest.main()
