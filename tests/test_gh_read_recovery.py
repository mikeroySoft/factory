"""Regression coverage for deferred GitHub-read recovery decisions."""
from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, gh_reads, learn, lifecycle, manage
from tests import test_auto_learn, test_gh_read_claim_lifecycle, test_watch_lifecycle


class DispatchRecoveryTest(unittest.TestCase):
    setUp = test_gh_read_claim_lifecycle.ClaimReadLifecycleTest.setUp
    dispatcher = test_gh_read_claim_lifecycle.ClaimReadLifecycleTest.dispatcher
    launch = test_gh_read_claim_lifecycle.ClaimReadLifecycleTest.launch
    connected_dispatcher = test_gh_read_claim_lifecycle.ClaimReadLifecycleTest.connected_dispatcher

    @staticmethod
    def append(module, *rows: dict) -> None:
        for row in rows:
            lifecycle.append(module.EVENTS, {"ticket": 7, "at": "2026-11-17T00:00:00Z", **row})

    @staticmethod
    def escalation(execution_id: str, round_number: int = 1) -> dict:
        return {
            "event": "escalate", "ticket": 7, "execution_id": execution_id,
            "round": round_number, "reason": f"failure {round_number}",
            "packet": f"/tmp/escalation-{round_number}.md", "log": None,
        }

    def posted_comments(self) -> list[list[str]]:
        return [cmd for cmd in self.commands if cmd[:3] == ["gh", "issue", "comment"]]

    def test_heartbeat_ownership_loss_leaves_no_empty_lifecycle_execution(self):
        module = self.connected_dispatcher("heartbeat-lazy")
        heartbeat = module.HostClaim(7, {"nonce": "claim-no-longer-present"})

        with mock.patch.object(heartbeat.stopped, "wait", return_value=False):
            heartbeat.heartbeat()

        self.assertFalse(any(
            row.get("event") == "lifecycle" and row.get("stage") == "claim-heartbeat"
            for row in lifecycle.read_events(module.EVENTS)
        ))

    def test_pending_escalation_uses_the_most_recent_escalation(self):
        module = self.connected_dispatcher("latest-escalation")
        old = self.escalation("old", 1)
        latest = self.escalation("latest", 2)
        self.append(
            module,
            old,
            {"event": "ticket-read-deferred", "execution_id": "old"},
            latest,
            {"event": "ticket-read-deferred", "execution_id": "latest"},
        )

        self.assertEqual(module._pending_escalation(7)["execution_id"], "latest")

    def test_pending_escalation_requires_a_later_marker_from_the_same_execution(self):
        module = self.connected_dispatcher("matching-deferred")
        escalation = self.escalation("target")
        self.append(
            module,
            {"event": "ticket-read-deferred", "execution_id": "target"},
            escalation,
            {"event": "ticket-read-deferred", "execution_id": "other"},
        )

        self.assertIsNone(module._pending_escalation(7))

    def test_only_the_exact_recovery_execution_closes_pending_escalation(self):
        module = self.connected_dispatcher("exact-recovery")
        escalation = self.escalation("target")
        self.append(
            module,
            escalation,
            {"event": "ticket-read-deferred", "execution_id": "target"},
            {"event": "escalation-recovered", "escalation_execution_id": "other"},
        )
        self.assertEqual(module._pending_escalation(7)["execution_id"], "target")

        self.append(module, {
            "event": "escalation-recovered", "escalation_execution_id": "target",
        })
        self.assertIsNone(module._pending_escalation(7))

    def test_manager_or_human_decision_closes_deferred_escalation_replay(self):
        for terminal in ("manage", "human-decision"):
            with self.subTest(terminal=terminal):
                module = self.connected_dispatcher(f"terminal-{terminal}")
                escalation = self.escalation(f"execution-{terminal}")
                self.append(
                    module,
                    escalation,
                    {"event": "ticket-read-deferred", "execution_id": escalation["execution_id"]},
                    {"event": terminal},
                )
                self.assertIsNone(module._pending_escalation(7))

    def test_unrelated_comment_receipts_do_not_suppress_recovered_publication(self):
        module = self.connected_dispatcher(None)
        escalation = self.escalation("recover-me", 2)
        self.append(
            module,
            escalation,
            {"event": "audit", "kind": "escalation", "round": 2},
            {"event": "comment", "kind": "manager", "round": 2},
            {"event": "comment", "kind": "escalation", "round": 1},
        )

        module._recover_escalation(7, escalation, [])

        self.assertEqual(len(self.posted_comments()), 1)
        [recovered] = [row for row in lifecycle.read_events(module.EVENTS)
                       if row.get("event") == "escalation-recovered"]
        self.assertEqual(recovered["escalation_execution_id"], "recover-me")

    def test_matching_comment_receipt_avoids_duplicate_recovered_publication(self):
        module = self.connected_dispatcher(None)
        escalation = self.escalation("already-published", 1000)
        self.append(
            module,
            escalation,
            {"event": "comment", "kind": "escalation", "round": 1000},
        )

        module._recover_escalation(7, escalation, [])

        self.assertEqual(self.posted_comments(), [])
        [recovered] = [row for row in lifecycle.read_events(module.EVENTS)
                       if row.get("event") == "escalation-recovered"]
        self.assertEqual(recovered["escalation_execution_id"], "already-published")


class ManageReadRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cfg = config.Config(Path(temporary.name), "acme/widgets", manager=["manager"])
        self.enterContext(mock.patch.object(dispatch, "cfg", cfg))
        self.enterContext(mock.patch.object(dispatch, "EVENTS", cfg.factory / "events.jsonl"))

    def test_failed_pr_listing_does_not_skip_the_independent_issue_listing(self):
        calls = []

        def query(args):
            calls.append(args[:2])
            if args[:2] == ["pr", "list"]:
                raise gh_reads.ReadFailed("gh_read_failed")
            self.assertEqual(args[:2], ["issue", "list"])
            return []

        with mock.patch.object(dispatch, "gh_json", side_effect=query):
            manage.viability_pass()

        self.assertEqual(calls, [["pr", "list"], ["issue", "list"]])


class PromotionReadRecoveryTest(unittest.TestCase):
    BRANCH = test_auto_learn.PromotionTest.BRANCH
    HEAD = test_auto_learn.PromotionTest.HEAD
    setUp = test_auto_learn.PromotionTest.setUp
    journal = test_auto_learn.PromotionTest.journal
    scores = test_auto_learn.PromotionTest.scores
    ran = test_auto_learn.PromotionTest.ran
    open_promotion = test_auto_learn.PromotionTest.open_promotion
    rows = test_auto_learn.PromotionTest.rows

    def test_suppressed_revert_read_failure_does_not_record_completion(self):
        self.open_promotion(self.scores(0.5))

        with mock.patch.object(learn, "revert", side_effect=gh_reads.ReadFailed("gh_read_failed")), \
                gh_reads.scope(dispatch.EVENTS, "learn"):
            self.assertEqual(learn.decide_promotions(self.cfg), [])

        self.assertEqual(self.rows("promotion-keep", "promotion-revert"), [])


class WatchReadRecoveryTest(unittest.TestCase):
    setUp = test_watch_lifecycle.WatchLifecycleTest.setUp
    journal = test_watch_lifecycle.WatchLifecycleTest.journal
    run_watch = test_watch_lifecycle.WatchLifecycleTest.run_watch

    def test_externally_closed_filing_is_marked_then_later_open_filing_closes(self):
        self.journal("externally-fixed", 17, timedelta(days=3))
        self.journal("quiet-open", 18, timedelta(days=3))

        calls = self.run_watch({17: "CLOSED", 18: "OPEN"})

        views = [int(call[3]) for call in calls if call[:3] == ["gh", "issue", "view"]]
        closes = [int(call[3]) for call in calls if call[:3] == ["gh", "issue", "close"]]
        self.assertEqual(views, [17, 18])
        self.assertEqual(closes, [18])
        [receipt] = [
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "watch" and row.get("issue") == 17 and "closed" in row
        ]
        self.assertIs(receipt["closed"], True)


if __name__ == "__main__":
    unittest.main()
