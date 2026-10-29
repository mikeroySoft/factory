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

    def test_human_requeue_waits_for_a_fresh_worker_head_then_gets_manager_budget(self) -> None:
        self.cfg.manager_review = "all"
        self.cfg.manager_rounds = 1
        self.quiet_provider()
        dispatch.record("manage", ticket=79, pr=80, head=H, decision="HUMAN", round=1)
        dispatch.record("rounds-reset", ticket=79, actor="alice", requeued_at=NOW)
        self.approved_head(H)
        self.manager_output = "DECISION: APPROVE\nLooks right now"

        manage.frontier_pass()

        self.assertEqual(len([e for e in self.events() if e.get("event") == "manage"]), 1)
        self.assertEqual(self.mutations, [])

        self.pr = pr_row(head=H2)
        self.approved_head(H2)
        manage.frontier_pass()

        manage_rows = [e for e in self.events() if e.get("event") == "manage"]
        self.assertEqual([(e["head"], e["round"]) for e in manage_rows], [(H, 1), (H2, 1)])
        self.assertFalse(any(e.get("code") == "manager_exhausted" for e in self.escalations()))
        self.assertTrue(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))

    def direct_requeue(self, actor="alice"):
        at = "2026-10-28T15:50:00Z"
        # Append fixed-time evidence so the timeline ordering does not depend on the clock.
        lifecycle.append(dispatch.EVENTS, {
            "event": "escalate", "ticket": 79, "reason": "gate failed", "round": 1, "at": at,
        })
        dispatch.record("comment", ticket=79, kind="escalation", round=1, comment=101)
        self.timeline = [
            {"event": "labeled", "label": {"name": "ready-for-human"},
             "created_at": at, "actor": {"login": "runner"}},
            {"event": "commented", "id": 101, "created_at": at, "user": {"login": "runner"}},
            {"event": "commented", "id": 102, "created_at": "2026-10-28T15:52:00Z",
             "user": {"login": actor}, "body": "Try the amended scope."},
            {"event": "labeled", "label": {"name": "ready-for-agent"},
             "created_at": "2026-10-28T15:53:00Z", "actor": {"login": actor}},
        ]

    def test_shared_account_owner_requeue_renews_rounds(self) -> None:
        self.direct_requeue(actor="runner")

        dispatch._record_human_round_reset(79)

        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual((reset["actor"], reset["requeued_at"]),
                         ("runner", "2026-10-28T15:53:00Z"))

    def test_owner_requeue_renews_rounds_when_human_label_was_already_present(self) -> None:
        self.direct_requeue()
        self.timeline.pop(0)

        dispatch._record_human_round_reset(79)

        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual(reset["actor"], "alice")

    def test_owner_requeue_renews_rounds_when_handoff_follows_decision(self) -> None:
        self.direct_requeue()
        self.timeline.insert(-1, {
            "event": "commented", "id": 103, "created_at": "2026-10-28T15:52:30Z",
            "user": {"login": "runner"}, "body": "Factory handoff",
        })
        dispatch.record("comment", ticket=79, kind="handoff", comment=103)

        dispatch._record_human_round_reset(79)

        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual(reset["actor"], "alice")

    def test_existing_human_label_and_later_handoff_do_not_hide_owner_requeue(self) -> None:
        self.direct_requeue()
        self.timeline.pop(0)
        self.timeline.insert(-1, {
            "event": "commented", "id": 103, "created_at": "2026-10-28T15:52:30Z",
            "user": {"login": "runner"}, "body": "Factory handoff",
        })
        dispatch.record("comment", ticket=79, kind="handoff", comment=103)

        dispatch._record_human_round_reset(79)

        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual(reset["actor"], "alice")

    def test_human_release_after_failed_fix_or_split_renews_rounds(self) -> None:
        for decision in ("FIX", "SPLIT"):
            with self.subTest(decision=decision):
                self.direct_requeue()
                dispatch.record("manage", ticket=79, round=1, decision=decision)

                dispatch._record_human_round_reset(79)
                dispatch._record_human_round_reset(79)

                resets = [e for e in self.events() if e.get("event") == "rounds-reset"]
                self.assertEqual(len(resets), 1 if decision == "FIX" else 2)
                self.assertEqual((resets[-1]["actor"], resets[-1]["requeued_at"]),
                                 ("alice", "2026-10-28T15:53:00Z"))

    def test_actual_factory_release_does_not_renew_rounds(self) -> None:
        for kind in ("manage", "conflict-requeue", "gate-requeue", "risk-requeue"):
            with self.subTest(kind=kind):
                self.direct_requeue(actor="runner")
                if kind == "manage":
                    dispatch.record("comment", ticket=79, kind="manager", comment=102)
                else:
                    self.timeline.pop(2)
                dispatch.record(kind, ticket=79, decision="RETRY", round=1)
                dispatch._record_human_round_reset(79)
                self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))

    def test_human_comment_during_factory_relabel_does_not_renew_rounds(self) -> None:
        self.direct_requeue()
        self.timeline = self.timeline[:2]
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-28T15:51:00Z", "event": "manage", "ticket": 79,
            "round": 1, "decision": "RETRY",
        })
        packet = self.repo / ".factory" / "packet.md"
        packet.write_text("Original failure evidence")

        def interleaved_run(cmd, *args, **kwargs):
            result = self.fake_run(cmd, *args, **kwargs)
            if cmd[1:3] == ["issue", "comment"]:
                self.timeline.append({
                    "event": "commented", "id": 7901,
                    "created_at": "2026-10-28T15:51:30Z",
                    "user": {"login": "runner"}, "body": "Factory manager: Retry",
                })
            elif cmd[1:3] == ["issue", "edit"]:
                self.timeline.extend([
                    {
                        "event": "commented", "id": 103,
                        "created_at": "2026-10-28T15:52:00Z",
                        "user": {"login": "alice"}, "body": "One more clarification.",
                    },
                    {
                        "event": "labeled", "label": {"name": "ready-for-agent"},
                        "created_at": "2026-10-28T15:53:00Z",
                        "actor": {"login": "runner"},
                    },
                ])
            return result

        with mock.patch.object(dispatch, "run", side_effect=interleaved_run):
            manage.apply(
                79, {"title": "Ticket", "body": "Scope", "labels": []},
                "RETRY", "Retry", None, packet,
            )
        dispatch._record_human_round_reset(79)

        self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))

    def test_failed_factory_relabel_after_owner_decision_does_not_veto_reset(self) -> None:
        self.direct_requeue()
        self.timeline[2]["created_at"] = "2026-10-28T15:51:00Z"
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-28T15:52:00Z", "event": "manage", "ticket": 79,
            "round": 1, "decision": "RETRY",
        })

        dispatch._record_human_round_reset(79)

        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual(reset["actor"], "alice")

    def test_later_human_release_is_not_vetoed_by_an_earlier_factory_requeue(self) -> None:
        self.direct_requeue()
        dispatch.record("conflict-requeue", ticket=79, round=1)
        self.timeline[2:2] = [
            {"event": "labeled", "label": {"name": "ready-for-agent"},
             "created_at": "2026-10-28T15:51:00Z", "actor": {"login": "runner"}},
            {"event": "labeled", "label": {"name": "ready-for-human"},
             "created_at": "2026-10-28T15:51:30Z", "actor": {"login": "alice"}},
        ]
        dispatch._record_human_round_reset(79)
        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual(reset["actor"], "alice")

    def test_failed_factory_relabel_does_not_veto_later_owner_requeue(self) -> None:
        self.direct_requeue()
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-28T15:51:00Z", "event": "manage", "ticket": 79,
            "round": 1, "decision": "RETRY",
        })

        dispatch._record_human_round_reset(79)

        [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
        self.assertEqual(reset["actor"], "alice")

    def test_round_reset_keeps_escalation_attempts_and_log_evidence(self) -> None:
        dispatch.record("attempt", ticket=79, attempt=1, gate="FAIL", log="/tmp/old-worker.log")
        dispatch.record("escalate", ticket=79, reason="budget", round=4)
        dispatch.record("rounds-reset", ticket=79, actor="alice", requeued_at=NOW)
        dispatch.record("attempt", ticket=79, attempt=2, gate="FAIL", log="/tmp/new-worker.log")
        packet, round_number = dispatch.escalation_packet(79, "gate failed", None, self.repo)

        self.assertEqual(round_number, 1)
        text = packet.read_text()
        self.assertIn("| 1 | FAIL |", text)
        self.assertIn("| 2 | FAIL |", text)
        self.assertIn("- `/tmp/old-worker.log`", text)
        self.assertIn("- `/tmp/new-worker.log`", text)

    def test_applied_manager_recovery_distinguishes_owner_from_factory_release(self) -> None:
        packet = self.repo / ".factory" / "packet.md"
        packet.write_text("Original failure evidence")
        wt = self.repo / ".factory" / "wt-79"
        wt.mkdir()

        def recovery_run(cmd, *args, **kwargs):
            if cmd[:3] == ["git", "branch", "--show-current"]:
                return subprocess.CompletedProcess(cmd, 0, "agent/79\n", "")
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, H, "")
            result = self.fake_run(cmd, *args, **kwargs)
            if cmd[:3] == ["gh", "issue", "create"]:
                return subprocess.CompletedProcess(
                    cmd, 0, "https://github.com/example/project/issues/81", "",
                )
            return result

        for decision, data in (
            ("FIX", {"worker": "default", "guidance": "Repair the gate"}),
            ("SPLIT", [{"title": "Child", "body": "Child scope"}]),
            ("RETRY", None),
        ):
            with self.subTest(decision=decision):
                self.direct_requeue()
                human_release = self.timeline[-2:]
                self.timeline = self.timeline[:-2]
                self.issue_labels = ["ready-for-human"]
                self.mutations.clear()
                before_resets = len([e for e in self.events() if e.get("event") == "rounds-reset"])
                dispatch.record("manage", ticket=79, round=1, decision=decision)
                with mock.patch.object(dispatch, "run", side_effect=recovery_run), \
                        mock.patch.object(dispatch, "worker_round",
                                          return_value=(False, "gate failed", None, H)):
                    manage.apply(79, self.gh_json(["issue", "view"]), decision,
                                 "Recovery guidance", data, packet)

                factory_released = any(
                    cmd[:3] == ["gh", "issue", "edit"]
                    and "--add-label" in cmd and "ready-for-agent" in cmd
                    for cmd in self.mutations
                )
                self.assertEqual(factory_released, decision == "RETRY")
                if any(cmd[:3] == ["gh", "issue", "comment"] for cmd in self.mutations):
                    self.timeline.append({
                        "event": "commented", "id": 7901, "created_at": "2026-10-28T15:51:00Z",
                        "user": {"login": "runner"}, "body": "Recovery guidance",
                    })
                if factory_released:
                    self.timeline.append({
                        "event": "labeled", "label": {"name": "ready-for-agent"},
                        "created_at": "2026-10-28T15:53:00Z", "actor": {"login": "runner"},
                    })
                else:
                    self.timeline.extend(human_release)
                self.issue_labels = ["ready-for-agent"]

                dispatch._record_human_round_reset(79)
                resets = [e for e in self.events() if e.get("event") == "rounds-reset"]
                self.assertEqual(len(resets) - before_resets, 0 if factory_released else 1)

    def test_round_reset_preserves_unconsumed_rework_context(self) -> None:
        for kind, fields, heading in (
            ("conflict-requeue", {"verb": "rebase", "files": ["conflicted.py"]}, "Refresh conflict"),
            ("gate-requeue", {"reports": ["original failing gate"]}, "Refresh gate failure"),
            ("risk-requeue", {"packet": "mandatory dissent findings"}, "High-risk disagreement"),
        ):
            with self.subTest(kind=kind):
                dispatch.record(kind, ticket=79, pr=80, head=H, round=1, **fields)
                dispatch.record("escalate", ticket=79, reason="budget", round=2)
                dispatch.record("rounds-reset", ticket=79, actor="alice", requeued_at=NOW)

                self.assertIn(heading, dispatch.refresh_requeue_context(79))
                if kind == "risk-requeue":
                    self.assertEqual(dispatch.pending_risk_requeue_head(79), H)
                dispatch.record("attempt", ticket=79, head=H2)
                self.assertEqual(dispatch.refresh_requeue_context(79), "")
                self.assertIsNone(dispatch.pending_risk_requeue_head(79))

    def test_manager_relabel_outcome_not_comment_timing_controls_reset(self) -> None:
        for actor in ("alice", "runner"):
            for succeeds in (False, True):
                with self.subTest(actor=actor, succeeds=succeeds):
                    dispatch.EVENTS.unlink(missing_ok=True)
                    self.direct_requeue(actor=actor)
                    release = self.timeline.pop()
                    release["actor"]["login"] = "runner"
                    lifecycle.append(dispatch.EVENTS, {
                        "at": "2026-10-28T15:52:30Z", "event": "manage",
                        "ticket": 79, "round": 1, "decision": "RETRY",
                    })
                    packet = self.repo / ".factory" / "packet.md"
                    packet.write_text("Original failure evidence")

                    def relabel(cmd, *args, succeeds=succeeds, release=release, **kwargs):
                        result = self.fake_run(cmd, *args, **kwargs)
                        if cmd[1:3] == ["issue", "edit"]:
                            if not succeeds:
                                raise subprocess.CalledProcessError(1, cmd, stderr="label failed")
                            self.timeline.append(release)
                        return result

                    with mock.patch.object(dispatch, "run", side_effect=relabel):
                        if succeeds:
                            manage.apply(79, {"title": "Ticket", "body": "Scope"},
                                         "RETRY", "Retry", None, packet)
                        else:
                            with self.assertRaises(subprocess.CalledProcessError):
                                manage.apply(79, {"title": "Ticket", "body": "Scope"},
                                             "RETRY", "Retry", None, packet)
                            release["actor"]["login"] = actor
                            self.timeline.append(release)

                    dispatch._record_human_round_reset(79)
                    dispatch._record_human_round_reset(79)
                    resets = [e for e in self.events() if e.get("event") == "rounds-reset"]
                    self.assertEqual(len(resets), 0 if succeeds else 1)
                    if not succeeds:
                        self.assertEqual(resets[0]["actor"], actor)
                        self.assertEqual(resets[0]["requeued_at"], release["created_at"])
                    self.assertEqual(len([e for e in self.events()
                                          if e.get("event") == "manage"]), 1)

    def test_comment_receipts_do_not_turn_unrelated_events_into_machine_comments(self) -> None:
        self.direct_requeue()
        dispatch.record("claimed", ticket=79, comment=102)

        dispatch._record_human_round_reset(79)

        self.assertEqual(len([e for e in self.events() if e.get("event") == "rounds-reset"]), 1)

    def test_machine_only_comments_do_not_authorize_an_owner_relabel(self) -> None:
        self.direct_requeue()
        dispatch.record("comment", ticket=79, kind="handoff", comment=102)
        dispatch.record("claimed", ticket=79, kind="escalation", comment=999)

        dispatch._record_human_round_reset(79)

        self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))

    def test_manager_failure_does_not_hide_the_escalation_release_boundary(self) -> None:
        self.direct_requeue()
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-28T15:52:30Z", "event": "escalate", "ticket": 79,
            "round": 1, "reason": "manager_failed",
        })

        dispatch._record_human_round_reset(79)

        self.assertEqual(len([e for e in self.events() if e.get("event") == "rounds-reset"]), 1)

    def test_first_and_latest_ticket_reset_bound_only_budget_events(self) -> None:
        first = {"event": "rounds-reset", "ticket": 79}
        old = {"event": "manage", "ticket": 79, "round": 1}
        latest = {"event": "rounds-reset", "ticket": 79}
        fresh = {"event": "manage", "ticket": 79, "round": 1}
        other = {"event": "rounds-reset", "ticket": 80}
        self.assertEqual(dispatch._current_round_events([first, old], 79), [old])
        self.assertEqual(
            dispatch._current_round_events([first, old, latest, fresh, other], 79), [fresh],
        )
        self.assertEqual(dispatch._current_round_events([old, other], 79), [old])

    def test_manager_rounds_ignore_other_prs_for_the_same_ticket(self) -> None:
        self.cfg.manager_review = "all"
        self.cfg.manager_rounds = 1
        self.quiet_provider()
        self.approved_head(H)
        dispatch.record("manage", ticket=79, pr=81, head=H2, decision="HUMAN", round=1)
        self.manager_output = "DECISION: APPROVE\nReady"

        manage.frontier_pass()

        self.assertEqual([(e["pr"], e["round"]) for e in self.events()
                          if e.get("event") == "manage"], [(81, 1), (80, 1)])
        self.assertFalse(any(e.get("code") == "manager_exhausted" for e in self.escalations()))
        self.assertTrue(any(e.get("event") == "approved" for e in self.events()))

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

    def test_release_decision_can_follow_the_first_human_label_without_machine_comment(self) -> None:
        self.direct_requeue()
        self.timeline.pop(1)
        self.timeline.insert(-1, {
            "event": "unlabeled", "label": {"name": "ready-for-human"},
            "created_at": "2026-10-28T15:52:30Z", "actor": {"login": "alice"},
        })

        dispatch._record_human_round_reset(79)

        self.assertEqual(len([e for e in self.events() if e.get("event") == "rounds-reset"]), 1)

    def test_decision_before_the_latest_human_label_is_not_a_release_decision(self) -> None:
        self.direct_requeue()
        self.timeline.insert(-1, {
            "event": "labeled", "label": {"name": "ready-for-human"},
            "created_at": "2026-10-28T15:52:30Z", "actor": {"login": "runner"},
        })

        dispatch._record_human_round_reset(79)

        self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))

    def test_machine_receipts_require_comment_events_even_for_missing_human_label(self) -> None:
        self.direct_requeue()
        self.timeline = [self.timeline[-1]]
        self.timeline.insert(0, {
            "event": "commented", "id": 104, "created_at": "2026-10-28T15:51:00Z",
            "user": {"login": "runner"}, "body": "Not an escalation",
        })
        self.timeline.insert(1, {
            "event": "commented", "id": 102, "created_at": "2026-10-28T15:52:00Z",
            "user": {"login": "alice"}, "body": "Owner decision",
        })
        dispatch.record("review", ticket=79, kind="escalation", comment=104)

        dispatch._record_human_round_reset(79)

        self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))

    def test_delayed_or_uncertain_automatic_receipt_never_grants_fresh_budget(self) -> None:
        from factory import handoff

        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                dispatch.EVENTS.unlink(missing_ok=True)
                self.direct_requeue(actor="runner")
                release = self.timeline.pop()
                before = list(self.timeline)
                after = subprocess.CalledProcessError(1, ["gh", "api"]) if unavailable else before
                with mock.patch.object(handoff, "timeline", side_effect=[before, after]):
                    dispatch.requeue_agent(79)
                self.timeline.append(release)

                dispatch._record_human_round_reset(79)

                self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))
                # The uncertain receipt is generation-bound, not a permanent
                # classification of this login or a veto on later owner releases.
                self.timeline.extend([
                    {"event": "labeled", "id": 105, "label": {"name": "ready-for-human"},
                     "created_at": "2026-10-28T15:54:00Z", "actor": {"login": "runner"}},
                    {"event": "commented", "id": 106, "created_at": "2026-10-28T15:55:00Z",
                     "user": {"login": "runner"}, "body": "Owner decision: Try again"},
                    {"event": "labeled", "id": 107, "label": {"name": "ready-for-agent"},
                     "created_at": "2026-10-28T15:56:00Z", "actor": {"login": "runner"}},
                ])
                dispatch._record_human_round_reset(79)
                dispatch._record_human_round_reset(79)
                [reset] = [e for e in self.events() if e.get("event") == "rounds-reset"]
                self.assertEqual(reset["requeued_at"], "2026-10-28T15:56:00Z")

    def test_automatic_noop_does_not_claim_a_preexisting_owner_release(self) -> None:
        self.direct_requeue(actor="runner")

        dispatch.requeue_agent(79)
        dispatch._record_human_round_reset(79)

        self.assertEqual(len([e for e in self.events() if e.get("event") == "rounds-reset"]), 1)

    def test_existing_human_label_uses_the_latest_escalation_comment_boundary(self) -> None:
        self.direct_requeue()
        self.timeline.pop(0)
        self.timeline[:0] = [
            {"event": "commented", "id": 200 + index, "created_at": "2026-10-28T15:49:00Z",
             "user": {"login": "runner"}, "body": "Earlier context"}
            for index in range(4)
        ]

        dispatch._record_human_round_reset(79)

        self.assertEqual(len([e for e in self.events() if e.get("event") == "rounds-reset"]), 1)

    def test_missing_or_empty_comment_body_is_not_evidence_of_an_owner_decision(self) -> None:
        for body in (None, "", "  \n"):
            with self.subTest(body=body):
                dispatch.EVENTS.unlink(missing_ok=True)
                self.direct_requeue()
                self.timeline[2]["body"] = body

                dispatch._record_human_round_reset(79)

                self.assertFalse(any(e.get("event") == "rounds-reset" for e in self.events()))

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
