"""#430: an owner requeue opens a ticket generation; its budgets restart, factory requeues never do."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from factory import config, dispatch, generation, lifecycle, manage
from tests import test_actions as action_suite
from tests import test_frontier as frontier_suite
from tests import test_handoff as handoff_suite
from tests import test_refresh_conflict as refresh_suite

H, H2 = frontier_suite.H, frontier_suite.H2
ESCALATED = {"event": "escalate", "ticket": 7, "code": "gate_failed", "reason": "gate failed", "round": 1}


class ProjectionTest(unittest.TestCase):
    def test_legacy_journal_is_generation_zero_over_the_whole_ticket_history(self) -> None:
        rows = [{"event": "claimed", "ticket": 7}, {"event": "claimed", "ticket": 8}, ESCALATED,
                {"event": "manage", "ticket": 7, "decision": "HUMAN", "round": 1}]
        legacy = generation.current(rows, 7)
        self.assertEqual((legacy.number, legacy.intent, legacy.rows), (0, "resume", [rows[0], rows[2], rows[3]]))
        self.assertFalse(legacy.awaiting_attempt)
        self.assertIsNone(generation.owner_requeue(rows[:2], 7))  # never escalated: nothing to release

    def test_a_release_no_factory_row_explains_is_the_owners(self) -> None:
        opened = generation.owner_requeue([ESCALATED, {"event": "manage", "ticket": 7, "decision": "HUMAN"}], 7)
        self.assertEqual({key: opened[key] for key in ("generation", "intent", "source")}, {
            "generation": 1, "intent": "resume", "source": "label"})

    def test_factory_releases_never_open_a_generation(self) -> None:
        for release in ({"event": "conflict-requeue"}, {"event": "gate-requeue"}, {"event": "risk-requeue"},
                        {"event": "review-rework", "deferred": True},
                        {"event": "manage", "decision": "RETRY"}, {"event": "manage", "decision": "ROUTE"}):
            with self.subTest(release=release):
                self.assertIsNone(generation.owner_requeue([ESCALATED, {**release, "ticket": 7}], 7))
        # A frontier decision (`pr`) and a non-deferred rework do not release the ticket.
        for row in ({"event": "manage", "decision": "RETRY", "pr": 9}, {"event": "review-rework", "deferred": False}):
            self.assertIsNotNone(generation.owner_requeue([ESCALATED, {**row, "ticket": 7}], 7))

    def test_one_generation_per_release_then_the_next_escalation_opens_the_next(self) -> None:
        rows = [ESCALATED]
        rows.append({"event": "generation", "ticket": 7, **generation.owner_requeue(rows, 7)})
        self.assertIsNone(generation.owner_requeue(rows, 7))
        self.assertTrue(generation.current(rows, 7).awaiting_attempt)
        rows += [{"event": "attempt", "ticket": 7}, {**ESCALATED, "round": 1}]
        self.assertFalse(generation.current(rows, 7).awaiting_attempt)
        self.assertEqual(generation.owner_requeue(rows, 7)["generation"], 2)

    def test_guarded_requeue_supplies_actor_and_intent_and_a_failed_one_does_not(self) -> None:
        decision = {"event": "human-decision", "ticket": 7, "status": "success", "actor": "alice",
                    "proposal_id": "p1", "action": {"name": "requeue_agent", "args": {"number": 7, "intent": "fresh"}}}
        opened = generation.owner_requeue([ESCALATED, decision], 7)
        self.assertEqual({key: opened[key] for key in ("intent", "source", "actor", "decision")}, {
            "intent": "fresh", "source": "requeue_agent", "actor": "alice", "decision": "p1"})
        failed = generation.owner_requeue([ESCALATED, {**decision, "status": "failure"}], 7)
        self.assertEqual((failed["source"], failed["intent"]), ("label", "resume"))


class FrontierGenerationTest(unittest.TestCase):
    """The frontier fixture of `test_frontier`, borrowed without re-running its tests."""

    setUp = frontier_suite.FrontierTest.setUp
    read, gh_json, fake_run = (frontier_suite.FrontierTest.read, frontier_suite.FrontierTest.gh_json,
                               frontier_suite.FrontierTest.fake_run)
    events, escalations = frontier_suite.FrontierTest.events, frontier_suite.FrontierTest.escalations
    approved_head, quiet_provider = frontier_suite.FrontierTest.approved_head, frontier_suite.FrontierTest.quiet_provider

    def escalated_then_released(self) -> None:
        dispatch.record("claimed", ticket=79)
        dispatch.record("attempt", ticket=79, gate="FAIL")
        dispatch.escalate(79, "gate failed after 3 attempts", None, code="gate_failed")
        dispatch.configure(self.cfg)  # the owner answers in a later pass
        self.issue_labels = ["ready-for-agent"]

    def test_feedback_undelivered_at_the_requeue_waits_for_the_fresh_attempt(self) -> None:
        self.escalated_then_released()
        manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 1)  # not re-escalated on what the owner already saw
        [opened] = [e for e in self.events() if e.get("event") == "generation"]
        self.assertEqual((opened["generation"], opened["source"], opened["intent"]), (1, "label", "resume"))
        [mark] = [e for e in self.events() if e.get("event") == "feedback-watermark"]
        self.assertEqual(len(mark["pairs"]), 3)
        manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 1)
        # An edited review is a new source revision after the requeue: it still escalates.
        self.provider.reviews[0]["body"] = "Fix the other boundary too"
        manage.frontier_pass()
        self.assertEqual(len(self.escalations()), 2)
        [delivered] = [e for e in self.events() if e.get("event") == "feedback-delivered"]
        self.assertEqual(len(delivered["pairs"]), 1)
        self.assertEqual(len([e for e in self.events() if e.get("event") == "feedback-watermark"]), 1)

    def test_feedback_still_undelivered_after_the_fresh_attempt_escalates(self) -> None:
        self.escalated_then_released()
        manage.frontier_pass()
        dispatch.record("attempt", ticket=79, gate="PASS", head=H, actual_head=H)
        manage.frontier_pass()
        self.assertEqual([e["code"] for e in self.escalations()], ["gate_failed", "pr_feedback"])
        self.assertIn("3 unresolved feedback item(s)", self.escalations()[-1]["reason"])

    def manager_said_human_on(self, head: str) -> None:
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(head)
        self.manager_output = "DECISION: HUMAN\nneeds a product call"
        manage.frontier_pass()
        self.assertEqual([e["code"] for e in self.escalations()], ["manager_human"])
        self.issue_labels = ["ready-for-human"]
        dispatch.configure(self.cfg)

    def next_head_asks_the_manager(self) -> None:
        self.pr = frontier_suite.pr_row(head=H2)
        self.provider.heads = []
        self.approved_head(H2)
        self.manager_output = "DECISION: APPROVE\nfine now"
        manage.frontier_pass()

    def test_owner_requeue_after_manager_rounds_restarts_the_manager_budget(self) -> None:
        self.manager_said_human_on(H)
        self.issue_labels = ["ready-for-agent"]
        manage.frontier_pass()  # observes the release; H is already decided
        self.next_head_asks_the_manager()
        manage_rows = [(e["head"], e["decision"], e["round"]) for e in self.events() if e.get("event") == "manage"]
        self.assertEqual(manage_rows, [(H, "HUMAN", 1), (H2, "APPROVE", 1)])
        self.assertEqual([e["code"] for e in self.escalations()], ["manager_human"])
        self.assertTrue(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))

    def test_factory_requeue_keeps_the_spent_manager_budget(self) -> None:
        self.manager_said_human_on(H)
        dispatch.record("conflict-requeue", ticket=79, pr=80, head=H, round=1, files=["x"])
        self.issue_labels = ["ready-for-agent"]
        self.next_head_asks_the_manager()
        self.assertFalse(any(e.get("event") == "generation" for e in self.events()))
        self.assertEqual([e["code"] for e in self.escalations()], ["manager_human", "manager_exhausted"])


class RefreshGenerationTest(unittest.TestCase):
    """The git-backed refresh fixture of `test_refresh_conflict`, borrowed without its tests."""

    setUp = refresh_suite.RefreshConflictTest.setUp
    patched, events = refresh_suite.RefreshConflictTest.patched, refresh_suite.RefreshConflictTest.events

    def test_land_stage_sees_the_owner_requeue_before_counting_conflict_rounds(self) -> None:
        for round_number in range(1, self.cfg.review_rounds + 1):
            dispatch.record("conflict-requeue", ticket=9, pr=102, round=round_number, files=["shared.txt"])
        with self.patched():
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
            self.assertEqual([e["code"] for e in self.events("escalate")], ["refresh_conflict"])
            self.assertEqual(self.github.assignees, [])
            dispatch.configure(self.cfg)
            self.github.issue_labels = {config.LABEL_AGENT}  # the owner relabels; no ticket stage has run
            self.github.pr_labels.add(config.LABEL_APPROVED)
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        self.assertEqual(len(self.events("escalate")), 1)
        self.assertEqual([e["round"] for e in self.events("conflict-requeue")], [1, 2, 3, 1])
        [opened] = self.events("generation")
        self.assertEqual(opened["generation"], 1)

    def test_only_an_unassigned_ticket_escalated_before_this_pass_is_an_owner_release(self) -> None:
        ready = {"labels": [{"name": config.LABEL_AGENT}]}
        with self.patched():
            dispatch.escalate(9, "gate failed", None, code="gate_failed")
            dispatch.observe_owner_requeue(9, ready)  # a read right after this pass's own relabel can lag
            dispatch.configure(self.cfg)
            # `escalate` unassigns in the edit that relabels; an assignee left means that edit never landed.
            self.github.assignees = ["factory"]
            dispatch.observe_owner_requeue(9, ready)
            self.assertEqual(self.events("generation"), [])
            self.github.assignees = []
            dispatch.observe_owner_requeue(9, ready)
        self.assertEqual(len(self.events("generation")), 1)

    def test_fresh_intent_recuts_the_branch_from_main_and_keeps_the_old_heads(self) -> None:
        old_head = self.github.head()
        main = refresh_suite.git(self.origin, "rev-parse", "main")
        stale = dispatch.ensure_worktree(9)
        (stale / "leftover.txt").write_text("uncommitted\n")
        worker = self.tmp / "fresh_worker.py"
        worker.write_text(
            "import subprocess\nfrom pathlib import Path\n"
            "Path('fresh.txt').write_text('fresh\\n')\n"
            "subprocess.run(['git', 'add', 'fresh.txt'], check=True)\n"
            "subprocess.run(['git', 'commit', '-qm', 'fresh attempt'], check=True)\n"
        )
        self.cfg.workers["default"] = [sys.executable, str(worker)]
        self.github.assignees = []
        self.github.pr_labels = set()
        for row in ({"event": "escalate", "ticket": 9, "code": "gate_failed", "reason": "x", "round": 1},
                    {"event": "human-decision", "ticket": 9, "status": "success", "actor": "alice",
                     "action": {"name": "requeue_agent", "args": {"number": 9, "intent": "fresh"}}}):
            lifecycle.append(dispatch.EVENTS, row)
        with self.patched(), mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")):
            [ready] = dispatch.frontier()
            dispatch.process_ticket(ready, 30, False)
        pushed = self.github.head()
        self.assertEqual(refresh_suite.git(self.origin, "rev-parse", f"{pushed}^"), main)
        self.assertEqual(refresh_suite.git(self.root, "rev-parse", "refs/factory-evidence/9-generation-1-remote"), old_head)
        [claimed] = self.events("claimed")
        self.assertEqual((claimed["generation"], claimed["base"]), (1, main))
        self.assertEqual(self.events("escalate")[1:], [])
        self.assertFalse((dispatch.FACTORY / "wt-9" / "leftover.txt").exists())


class HandoffGenerationTest(unittest.TestCase):
    """The `factory manage` CLI fixture of `test_handoff`, borrowed without its tests."""

    setUp, configure, escalate = (handoff_suite.HandoffCli.setUp, handoff_suite.HandoffCli.configure,
                                  handoff_suite.HandoffCli.escalate)
    manage, events, requests = (handoff_suite.HandoffCli.manage, handoff_suite.HandoffCli.events,
                                handoff_suite.HandoffCli.requests)
    calls = handoff_suite.HandoffCli.calls

    def test_requeued_ticket_gets_a_fresh_manager_round_and_its_own_request(self) -> None:
        self.manage(mode="HUMAN")  # manager.rounds = 1: spent
        self.assertEqual([e["request"] for e in self.events("handoff")], ["7/1"])
        self.state["issue"]["labels"] = [{"name": "ready-for-agent"}]
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        dispatch.configure(config.Config(self.repo, handoff_suite.REPO))
        with mock.patch.object(dispatch, "gh_json", return_value={"assignees": []}):
            dispatch.observe_owner_requeue(7, self.state["issue"])
        dispatch.record("attempt", ticket=7, gate="FAIL")
        _, round_number = dispatch.escalation_packet(7, "gate failed again", None, self.factory / "wt-7")
        self.assertEqual(round_number, 1)
        self.escalate("gate failed again", round_number, at="2026-01-02T00:00:00Z")
        self.state["issue"]["labels"] = [{"name": "ready-for-human"}]
        self.manage(mode="HUMAN")
        self.assertTrue(self.ran.exists())  # the new generation's manager round ran
        self.assertEqual([e["request"] for e in self.events("handoff")], ["7/1", "7/g1/1"])
        self.assertIn("Factory handoff request `7/g1/1`", self.requests()[-1]["body"])


class RequeueIntentActionTest(unittest.TestCase):
    """The named-action fixture of `test_actions`, borrowed without its tests."""

    setUp, transport = action_suite.ActionPathsTest.setUp, action_suite.ActionPathsTest.transport
    via_mcp, apply_mcp = action_suite.ActionPathsTest.via_mcp, action_suite.ActionPathsTest.apply_mcp
    via_dashboard, refusals = action_suite.ActionPathsTest.via_dashboard, action_suite.ActionPathsTest.refusals

    def test_fresh_intent_travels_in_the_journaled_decision(self) -> None:
        journal = self.cfg.factory / "events.jsonl"
        lifecycle.append(journal, ESCALATED)
        self.assertEqual(self.refusals("requeue_agent", {"number": 7, "intent": "restart"})[0], "invalid_args")
        preview = self.via_mcp("requeue_agent", {"number": 7, "intent": "fresh"})
        self.assertEqual(self.apply_mcp(preview)["status"], "success")
        opened = generation.owner_requeue(lifecycle.read_events(journal), 7)
        self.assertEqual((opened["intent"], opened["source"], opened["actor"]), ("fresh", "requeue_agent", "alice"))


if __name__ == "__main__":
    unittest.main()
