"""#430: an owner requeue opens a ticket generation; its budgets restart, factory requeues never do."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
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

    def test_a_source_unavailable_at_the_watermark_is_judged_by_the_release_time(self) -> None:
        self.escalated_then_released()
        self.timeline = [{"event": "labeled", "label": {"name": "ready-for-agent"}, "created_at": "2026-09-11T00:00:00Z"}]
        self.provider.fail = "threads"
        manage.frontier_pass()
        [mark] = [e for e in self.events() if e.get("event") == "feedback-watermark"]
        self.assertEqual(len(mark["pairs"]), 2)
        self.provider.fail = None
        for _ in range(2):  # the unchanged thread predates the owner's release
            manage.frontier_pass()
            self.assertEqual([e["code"] for e in self.escalations()], ["gate_failed"])
        # A thread comment written after the release is new feedback: it still escalates.
        self.provider.comments.append({**self.provider.comments[0], "id": "RC_2", "body": "One more",
                                       "updatedAt": "2026-09-12T00:00:00Z"})
        manage.frontier_pass()
        self.assertEqual([e["code"] for e in self.escalations()], ["gate_failed", "pr_feedback"])
        [delivered] = [e for e in self.events() if e.get("event") == "feedback-delivered"]
        self.assertEqual([pair[0].rsplit(":", 1)[1] for pair in delivered["pairs"]], ["RC_2"])

    def test_feedback_newer_than_the_release_in_a_late_source_escalates_alone(self) -> None:
        self.escalated_then_released()
        self.timeline = [{"event": "labeled", "label": {"name": "ready-for-agent"}, "created_at": "2026-09-11T00:00:00Z"}]
        self.provider.fail = "threads"
        manage.frontier_pass()
        self.provider.fail = None
        self.provider.comments.append({**self.provider.comments[0], "id": "RC_2", "body": "One more",
                                       "updatedAt": "2026-09-12T00:00:00Z"})
        manage.frontier_pass()
        self.assertEqual([e["code"] for e in self.escalations()], ["gate_failed", "pr_feedback"])
        [delivered] = [e for e in self.events() if e.get("event") == "feedback-delivered"]
        self.assertEqual([pair[0].rsplit(":", 1)[1] for pair in delivered["pairs"]], ["RC_2"])
        marks = [(len(e["pairs"]), e["sources"]) for e in self.events() if e.get("event") == "feedback-watermark"]
        self.assertEqual(marks, [(2, ["checks", "reviews"]), (1, ["threads"])])

    def test_a_source_unavailable_at_the_watermark_without_a_known_release_waits(self) -> None:
        self.escalated_then_released()
        self.provider.fail = "threads"
        manage.frontier_pass()
        self.provider.fail = None
        manage.frontier_pass()
        self.assertEqual([e["code"] for e in self.escalations()], ["gate_failed"])
        # Once taken, the late source's watermark is final: an edit after it is a new revision.
        self.provider.comments[0]["body"] = "Edited after the watermark"
        manage.frontier_pass()
        self.assertEqual([e["code"] for e in self.escalations()], ["gate_failed", "pr_feedback"])

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
        self.assertEqual(refresh_suite.git(self.root, "rev-parse", f"refs/factory-evidence/9-generation-1-remote-{old_head[:12]}"),
                         old_head)
        [receipt] = self.events("generation-restart")
        self.assertEqual((receipt["remote"], receipt["base"]), (old_head, main))
        [claimed] = self.events("claimed")
        self.assertEqual((claimed["generation"], claimed["base"]), (1, main))
        self.assertEqual(self.events("escalate")[1:], [])
        self.assertFalse((dispatch.FACTORY / "wt-9" / "leftover.txt").exists())

    def fresh_requeue(self, worker: list[str]) -> str:
        """Ticket 9 escalated, then released by its owner with fresh intent; returns the old remote head."""
        self.cfg.workers["default"] = worker
        self.github.assignees = []
        self.github.pr_labels = set()
        for row in ({"event": "escalate", "ticket": 9, "code": "gate_failed", "reason": "x", "round": 1},
                    {"event": "human-decision", "ticket": 9, "status": "success", "actor": "alice",
                     "action": {"name": "requeue_agent", "args": {"number": 9, "intent": "fresh"}}}):
            lifecycle.append(dispatch.EVENTS, row)
        return self.github.head()

    def committing_worker(self) -> list[str]:
        worker = self.tmp / "append_worker.py"
        worker.write_text(
            "import subprocess\nfrom pathlib import Path\n"
            "path = Path('work.txt')\n"
            "path.write_text((path.read_text() if path.exists() else '') + 'x\\n')\n"
            "subprocess.run(['git', 'add', 'work.txt'], check=True)\n"
            "subprocess.run(['git', 'commit', '-qm', 'work'], check=True)\n"
        )
        return [sys.executable, str(worker)]

    def process(self) -> bool | None:
        with self.patched(), mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")):
            [ready] = dispatch.frontier()
            return dispatch.process_ticket(ready, 30, False)

    def concurrent_push(self) -> str:
        """Someone else's commit lands on `origin/agent/9` on top of its current head."""
        head = self.github.head()
        tree = refresh_suite.git(self.origin, "rev-parse", f"{head}^{{tree}}")
        pushed = refresh_suite.git(self.origin, "commit-tree", tree, "-p", head, "-m", "owner commit")
        refresh_suite.git(self.origin, "update-ref", "refs/heads/agent/9", pushed)
        return pushed

    def evidence(self, sha: str) -> list[str]:
        return refresh_suite.git(self.root, "for-each-ref", "--contains", sha, "--format=%(refname)",
                                 "refs/factory-evidence/").split()

    def test_a_remote_commit_pushed_after_the_fresh_restart_preserved_the_branch_is_never_overwritten(self) -> None:
        old_head = self.fresh_requeue(self.committing_worker())
        record, pushed = dispatch.record, []

        def push_once_claimed(kind: str, **fields) -> dict:
            if kind == "claimed" and not pushed:  # after the restart fetched and preserved the branch
                pushed.append(self.concurrent_push())
            return record(kind, **fields)

        with mock.patch.object(dispatch, "record", side_effect=push_once_claimed):
            # Keep the claim hook; other receipts can now carry their own `kind` field.
            dispatch.record.side_effect = lambda event, **fields: (
                push_once_claimed(event, **fields) if event == "claimed" else record(event, **fields)
            )
            self.process()
        self.assertEqual(self.github.head(), pushed[0])
        self.assertEqual([e["code"] for e in self.events("escalate")][1:], ["lease_refused"])
        self.assertTrue(self.evidence(old_head))

    def test_replaying_an_interrupted_fresh_restart_keeps_the_heads_it_first_preserved(self) -> None:
        old_head = self.fresh_requeue(self.committing_worker())
        wt = dispatch.ensure_worktree(9)
        (wt / "unpublished.txt").write_text("local only\n")
        refresh_suite.git(wt, "add", "unpublished.txt")
        refresh_suite.git(wt, "commit", "-qm", "unpublished")
        unpublished = refresh_suite.git(wt, "rev-parse", "HEAD")
        journal = dispatch.EVENTS.read_text()
        dispatch.restart_worktree(9, 1)
        dispatch.EVENTS.write_text(journal)  # interrupted before anything after the restart was journaled
        dispatch.restart_worktree(9, 1)
        self.assertTrue(self.evidence(unpublished))
        self.assertTrue(self.evidence(old_head))
        main = refresh_suite.git(self.origin, "rev-parse", "main")
        self.assertEqual(refresh_suite.git(dispatch.FACTORY / "wt-9", "rev-parse", "HEAD"), main)

    def test_a_healthy_retry_after_an_infra_only_fresh_claim_publishes_the_fresh_start(self) -> None:
        old_head = self.fresh_requeue([sys.executable, "-c", "print('Authentication failed'); raise SystemExit(1)"])
        self.assertIs(self.process(), False)
        self.assertEqual((self.events("attempt"), len(self.events("worker-infra"))), ([], 1))
        dispatch.configure(self.cfg)
        self.cfg.workers["default"] = self.committing_worker()
        self.process()
        self.assertEqual(self.events("escalate")[1:], [])
        main = refresh_suite.git(self.origin, "rev-parse", "main")
        self.assertEqual(refresh_suite.git(self.origin, "rev-parse", f"{self.github.head()}^"), main)
        self.assertTrue(self.evidence(old_head))
        self.assertEqual([claim["base"] for claim in self.events("claimed")], [main, main])

    def test_a_remote_commit_pushed_between_an_infra_only_claim_and_its_retry_is_never_overwritten(self) -> None:
        self.fresh_requeue([sys.executable, "-c", "print('Authentication failed'); raise SystemExit(1)"])
        self.assertIs(self.process(), False)
        pushed = self.concurrent_push()
        dispatch.configure(self.cfg)
        self.cfg.workers["default"] = self.committing_worker()
        self.process()
        self.assertEqual(self.github.head(), pushed)
        self.assertEqual([e["code"] for e in self.events("escalate")][1:], ["lease_refused"])

    def test_after_its_first_publication_a_fresh_generation_leases_like_any_other(self) -> None:
        self.fresh_requeue(self.committing_worker())
        with self.patched(), mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")):
            [ready] = dispatch.frontier()
            dispatch.process_ticket(ready, 30, False)
            published = self.github.head()
            pushed = self.concurrent_push()
            wt = dispatch.FACTORY / "wt-9"
            self.assertEqual(dispatch.agent_lease(wt, 9), pushed)
            refresh_suite.git(wt, "commit", "-q", "--allow-empty", "-m", "next round")
            with self.assertRaises(dispatch.LeaseRefused):
                dispatch.push_agent(wt, 9, pushed)
        self.assertEqual(self.github.head(), pushed)
        self.assertEqual(refresh_suite.git(self.origin, "rev-parse", f"{pushed}^"), published)

    def test_owner_requeue_restarts_the_review_bounce_budget(self) -> None:
        finding = {"severity": "important", "pass": "bug", "path": "work.txt", "line": 1, "summary": "Not yet."}
        self.cfg.reviewer = [sys.executable, "-c", (
            "import json; print('- work.txt:1: Not yet.\\n```json\\n' + "
            f"json.dumps({{'verdict': 'REVISE', 'findings': [{finding!r}]}}) + '\\n```\\nVERDICT: REVISE')")]
        self.cfg.review_rounds = 2
        self.cfg.workers["default"] = self.committing_worker()
        self.github.assignees, self.github.pr_labels = [], set()
        lifecycle.append(dispatch.EVENTS, {"event": "escalate", "ticket": 9, "code": "gate_failed", "reason": "x", "round": 1})
        self.process()
        rounds = [(e["round"], e["deferred"]) for e in self.events("review-rework")]
        self.assertEqual(rounds, [(1, False), (2, False)])
        self.assertEqual([e["code"] for e in self.events("escalate")][1:], ["review_revise"])
        dispatch.configure(self.cfg)
        self.github.issue_labels, self.github.assignees = {config.LABEL_AGENT}, []
        self.process()  # observes the owner's release: generation 2 gets its own review bounces
        self.assertEqual([e["generation"] for e in self.events("generation")], [1, 2])
        rounds = [(e["round"], e["deferred"]) for e in self.events("review-rework")]
        self.assertEqual(rounds, [(1, False), (2, False)] * 2)
        self.assertEqual([e["code"] for e in self.events("escalate")][1:], ["review_revise", "review_revise"])


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


class RequeueReceiptContractTest(unittest.TestCase):
    """#671: literal receipt and pre-land ordering contracts, not just fresh round counts."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = config.Config(Path(temp.name), "acme/widgets")
        dispatch.configure(self.cfg)
        self.issue = {"number": 7, "state": "OPEN", "labels": [{"name": config.LABEL_AGENT}],
                      "assignees": []}
        self.escalation_at = "2026-11-05T00:00:00Z"
        lifecycle.append(dispatch.EVENTS, {**ESCALATED, "at": self.escalation_at})

    def test_generation_receipt_has_one_kind_and_complete_shape(self) -> None:
        for intent in ("resume", "fresh"):
            with self.subTest(intent=intent):
                if intent == "fresh":
                    lifecycle.append(dispatch.EVENTS, {**ESCALATED, "at": self.escalation_at})
                    dispatch.record(
                        "human-decision", ticket=7, status="success", actor="alice", proposal_id="p1",
                        action={"name": "requeue_agent", "args": {"number": 7, "intent": intent}},
                    )
                with lifecycle.scope(dispatch.EVENTS, "dispatcher", dispatcher=True) as execution:
                    dispatch.observe_owner_requeue(7, self.issue)
                    receipt = [row for row in lifecycle.read_events(dispatch.EVENTS)
                               if row.get("event") == "generation"][-1]
                    self.assertEqual(receipt, {
                        "at": receipt["at"], "event": "generation", "kind": "generation", "ticket": 7,
                        "cause": "owner_requeue", "generation": 1 if intent == "resume" else 2,
                        "intent": intent, "source": "label" if intent == "resume" else "requeue_agent",
                        "actor": None if intent == "resume" else "alice",
                        "decision": None if intent == "resume" else "p1",
                        "escalation_at": self.escalation_at, "execution_id": execution.execution_id,
                        "dispatcher_run_id": execution.dispatcher_run_id,
                    })
                    self.assertRegex(receipt["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_owner_reset_is_persisted_before_land_pass_entry(self) -> None:
        # Exhaust the old generation; a reset inside landing is too late for this contract.
        for round_number in range(1, self.cfg.review_rounds + 1):
            dispatch.record("conflict-requeue", ticket=7, round=round_number)
        lifecycle.append(dispatch.EVENTS, {**ESCALATED, "at": self.escalation_at})

        def land(dry_run: bool) -> None:
            self.assertFalse(dry_run)
            opened = generation.current(lifecycle.read_events(dispatch.EVENTS), 7)
            self.assertEqual(opened.number, 1, "owner reset must be journaled before land_pass starts")
            self.assertEqual([row["event"] for row in opened.rows], ["generation"])

        with mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(config, "load", return_value=self.cfg), \
                mock.patch.object(config, "engine_commit", return_value="same"), \
                mock.patch.object(config, "engine_target", return_value="same"), \
                mock.patch.object(dispatch, "_sweep_stale_factory_tempdirs"), \
                mock.patch.object(dispatch, "worker_health_pass"), \
                mock.patch.object(dispatch, "gh_json", return_value=[self.issue]), \
                mock.patch.object(dispatch, "land_pass", side_effect=land) as landing, \
                mock.patch.object(dispatch, "review_intake_pass"), \
                mock.patch("factory.manage.manage_pass"), \
                mock.patch.object(dispatch, "ticket_pass"), \
                mock.patch("factory.learn.learn_pass"):
            dispatch.gh_json.side_effect = lambda argv: self.issue if argv[:2] == ["issue", "view"] else [self.issue]
            self.assertEqual(dispatch.main([]), 0)
        landing.assert_called_once_with(False)


class PreLandRequeueTest(unittest.TestCase):
    """#671: only locked, direct issue state can authorize a pre-land reset."""

    setUp = RequeueReceiptContractTest.setUp

    def scheduled_pass(self, fresh: dict, *, dry_run: bool = False) -> tuple[list[int], list[dict]]:
        reads, at_land = [], []

        def github(argv: list[str]) -> dict | list[dict]:
            if argv[:2] == ["issue", "list"]:
                return [self.issue]  # stale search data: ready and unassigned
            self.assertEqual(argv[:3], ["issue", "view", "7"])
            self.assertEqual(set(argv[-1].split(",")), {"state", "labels", "assignees"})
            self.assertTrue(dispatch.lock_held(dispatch.ticket_lock(7)), "refresh must hold the ticket lock")
            reads.append(7)
            return fresh

        def land(_dry_run: bool) -> None:
            at_land.extend(row for row in lifecycle.read_events(dispatch.EVENTS)
                           if row.get("event") == "generation")

        with mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(config, "load", return_value=self.cfg), \
                mock.patch.object(config, "engine_commit", return_value="same"), \
                mock.patch.object(config, "engine_target", return_value="same"), \
                mock.patch.object(dispatch, "_sweep_stale_factory_tempdirs"), \
                mock.patch.object(dispatch, "worker_health_pass"), \
                mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "land_pass", side_effect=land), \
                mock.patch.object(dispatch, "review_intake_pass"), \
                mock.patch("factory.manage.manage_pass"), \
                mock.patch.object(dispatch, "ticket_pass"), \
                mock.patch("factory.learn.learn_pass"):
            self.assertEqual(dispatch.main(["--dry-run"] if dry_run else []), 0)
        return reads, at_land

    def test_locked_refresh_opens_once_before_landing_and_excludes_spent_rounds(self) -> None:
        dispatch.record("conflict-requeue", ticket=7, round=self.cfg.review_rounds)
        lifecycle.append(dispatch.EVENTS, {**ESCALATED, "at": self.escalation_at})
        reads, opened = self.scheduled_pass(self.issue)
        self.assertEqual(reads, [7])
        self.assertEqual([(row["generation"], row["intent"]) for row in opened], [(1, "resume")])
        self.assertEqual(dispatch._generation_rows(7), opened)
        self.assertEqual(dispatch.escalation_packet(7, "another failure", None, self.cfg.factory / "wt-7")[1], 1)
        self.assertEqual(self.scheduled_pass(self.issue), ([], opened))

    def test_stale_ready_listing_cannot_release_another_dispatchers_escalation(self) -> None:
        fresh = {**self.issue, "labels": [{"name": config.LABEL_HUMAN}]}
        reads, opened = self.scheduled_pass(fresh)
        self.assertEqual(reads, [7])  # direct refresh, not the process-local escalation set
        self.assertEqual(opened, [])
        self.assertEqual(generation.current(lifecycle.read_events(dispatch.EVENTS), 7).number, 0)

    def test_closed_assigned_and_initiative_tickets_do_not_open_generations(self) -> None:
        for change in ({"state": "CLOSED"}, {"assignees": [{"login": "alice"}]},
                       {"labels": [{"name": config.LABEL_AGENT}, {"name": config.LABEL_INITIATIVE}]}):
            with self.subTest(change=change):
                self.assertEqual(self.scheduled_pass({**self.issue, **change}), ([7], []))

    def test_dry_run_and_held_lock_leave_the_journal_unchanged(self) -> None:
        import fcntl

        before = dispatch.EVENTS.read_bytes()
        self.assertEqual(self.scheduled_pass(self.issue, dry_run=True), ([], []))
        self.assertEqual(dispatch.EVENTS.read_bytes(), before)
        with dispatch.ticket_lock(7).open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.scheduled_pass(self.issue), ([], []))
        self.assertEqual(dispatch.EVENTS.read_bytes(), before)
        self.assertEqual(self.scheduled_pass(self.issue)[0], [7])  # next pass can retry

    def test_only_unresolved_owner_escalations_are_refreshed(self) -> None:
        dispatch.record("claimed", ticket=8)
        lifecycle.append(dispatch.EVENTS, {**ESCALATED, "ticket": 9})
        dispatch.record("manage", ticket=9, decision="RETRY")
        lifecycle.append(dispatch.EVENTS, {**ESCALATED, "ticket": 10, "reason": "manager_failed"})
        self.assertEqual(self.scheduled_pass(self.issue)[0], [7])

    def test_terminal_histories_stop_refreshing_until_a_new_escalation(self) -> None:
        closed = {**self.issue, "state": "CLOSED"}
        for terminal in ("merged", "CLOSE", "RESOLVE"):
            with self.subTest(terminal=terminal):
                lifecycle.append(dispatch.EVENTS, ESCALATED)
                if terminal == "merged":
                    dispatch.record("merged", ticket=7, pr=17, head=H)
                else:
                    manage._record_wontfix(7, terminal, "Already finished")
                for _ in range(2):
                    self.assertEqual(self.scheduled_pass(closed), ([], []))
                self.assertFalse(dispatch.ticket_lock(7).exists(), "terminal tickets must not recreate locks")
                dispatch.observe_owner_requeue(7, self.issue)
                self.assertEqual(generation.current(lifecycle.read_events(dispatch.EVENTS), 7).number, 0)

        # A later escalation makes a reopened ticket relevant again.
        lifecycle.append(dispatch.EVENTS, ESCALATED)
        reads, opened = self.scheduled_pass(self.issue)
        self.assertEqual(reads, [7])
        self.assertEqual([row["generation"] for row in opened], [1])

    def test_host_claim_holds_and_unclaimed_assignments_cannot_open_generations(self) -> None:
        self.cfg.host_name = "local"
        assigned = {**self.issue, "assignees": [{"login": "alice"}]}
        with mock.patch.object(dispatch, "host_claims", return_value=[]):
            self.assertEqual(self.scheduled_pass(assigned), ([7], []))
        with mock.patch.object(dispatch, "host_claims", return_value=[]), \
                mock.patch.object(dispatch, "host_claim_hold", return_value="host label (other)"):
            self.assertEqual(self.scheduled_pass(self.issue), ([7], []))

    def test_expired_requeued_host_claim_preserves_shared_bot_assignment_eligibility(self) -> None:
        from datetime import UTC, datetime, timedelta

        self.cfg.host_name = "local"
        stale = {"host": "old", "id": 1, "created": datetime.now(UTC) - timedelta(days=1),
                 "heartbeat": datetime.now(UTC) - timedelta(days=1), "requeue": "factory release"}
        assigned = {**self.issue, "assignees": [{"login": "factory"}]}
        with mock.patch.object(dispatch, "host_claims", return_value=[stale]), \
                mock.patch.object(dispatch, "host_claim_login", return_value="factory"):
            reads, opened = self.scheduled_pass(assigned)
        self.assertEqual(reads, [7])
        self.assertEqual([row["generation"] for row in opened], [1])

    def test_receipt_context_presence_and_legacy_nulls(self) -> None:
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                lifecycle.append(dispatch.EVENTS, ESCALATED)
                if scoped:
                    with lifecycle.scope(dispatch.EVENTS, "manage") as execution:
                        dispatch.observe_owner_requeue(7, self.issue)
                else:
                    dispatch.observe_owner_requeue(7, self.issue)
                receipt = [row for row in lifecycle.read_events(dispatch.EVENTS)
                           if row.get("event") == "generation"][-1]
                expected = {
                    "at": receipt["at"], "event": "generation", "kind": "generation", "ticket": 7,
                    "cause": "owner_requeue", "generation": 2 if scoped else 1, "intent": "resume",
                    "source": "label", "actor": None, "decision": None, "escalation_at": None,
                }
                if scoped:
                    expected.update(execution_id=execution.execution_id, dispatcher_run_id=None)
                self.assertEqual(receipt, expected)

    def test_unreadable_history_does_not_block_other_resets_or_later_stages(self) -> None:
        import subprocess

        lifecycle.append(dispatch.EVENTS, {**ESCALATED, "ticket": 8})
        reads, landed = [], []

        def github(argv: list[str]) -> dict | list[dict]:
            if argv[:2] == ["issue", "list"]:
                return [self.issue]  # stale listing still advertises the unreadable ticket
            self.assertEqual(argv[:2], ["issue", "view"])
            n = int(argv[2])
            self.assertTrue(dispatch.lock_held(dispatch.ticket_lock(n)))
            reads.append(n)
            if n == 7:
                raise subprocess.CalledProcessError(1, ["gh", *argv], stderr="Could not resolve issue #7")
            self.assertEqual(n, 8)
            return {**self.issue, "number": 8}

        def land(_dry_run: bool) -> None:
            events = lifecycle.read_events(dispatch.EVENTS)
            landed.append((generation.current(events, 7).number, generation.current(events, 8).number))

        with mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(config, "load", return_value=self.cfg), \
                mock.patch.object(config, "engine_commit", return_value="same"), \
                mock.patch.object(config, "engine_target", return_value="same"), \
                mock.patch.object(dispatch, "_sweep_stale_factory_tempdirs"), \
                mock.patch.object(dispatch, "worker_health_pass"), \
                mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "land_pass", side_effect=land), \
                mock.patch.object(dispatch, "review_intake_pass") as intake, \
                mock.patch("factory.manage.manage_pass") as manager, \
                mock.patch.object(dispatch, "ticket_pass") as tickets, \
                mock.patch("factory.learn.learn_pass"):
            for _ in range(2):
                self.assertEqual(dispatch.main([]), 0)
                self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))
                self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(8)))
        self.assertEqual(reads, [7, 8, 7])
        self.assertEqual(landed, [(0, 1), (0, 1)])
        self.assertEqual((intake.call_count, manager.call_count, tickets.call_count), (2, 2, 2))
        receipts = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "generation"]
        self.assertEqual([(row["ticket"], row["generation"]) for row in receipts], [(8, 1)])


if __name__ == "__main__":
    unittest.main()
