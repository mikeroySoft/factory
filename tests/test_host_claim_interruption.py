"""Interrupt exits release ownership; an open agent PR remains the durable hold."""
import copy
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class HostClaimInterruptionTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def interrupt_pr_gate(self, module, *, forced=False):
        before_assignees = copy.deepcopy(self.github.issue["assignees"])
        claims = []

        def killed(*args):
            claims.extend(module.host_claims(7))
            self.github.open_prs = [{"number": 70}]
            raise KeyboardInterrupt("host killed during PR gate")

        with mock.patch.object(module, "pr_gate_and_review", side_effect=killed), \
                self.assertRaises(KeyboardInterrupt):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=forced)
        self.assertEqual(len(claims), 1)
        self.assertEqual(module.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], before_assignees)
        self.assertFalse(any(row["name"].startswith("host:")
                             for row in self.github.issue["labels"]))
        return claims[0]

    def test_interrupted_open_pr_holds_frontier_and_forced_processing(self):
        tank = self.dispatcher("tank")
        self.interrupt_pr_gate(tank)
        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70")
        before = copy.deepcopy((self.github.issue, self.github.comments))

        tank.process_ticket(issue, 1, False)
        tank.process_ticket(issue, 1, False, forced=True)

        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, ["tank"])
        cleared = next(row for row in lifecycle.read_events(tank.EVENTS)
                       if row.get("event") == "assignee-cleared")
        self.assertEqual(
            (cleared["ticket"], cleared["login"], cleared["exit_path"]),
            (7, "@me", "interrupted"),
        )

    def test_completed_attempt_releases_ownership_and_next_pass_holds_on_pr(self):
        tank = self.dispatcher("tank")

        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(tank.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])
        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70")
        tank.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_requeue_marker_does_not_bypass_interrupted_open_pr(self):
        tank = self.dispatcher("tank")
        claim = self.interrupt_pr_gate(tank)
        self.github.comments.append({
            "id": 100,
            "body": f"Factory requeue: nonce={claim['nonce']}\nContinue work",
            "created_at": datetime.now(UTC).isoformat(),
            "user": {"login": "@me"},
        })

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70")
        tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, ["tank"])

    def test_expired_durable_claim_recovers_when_no_pr_is_open(self):
        now = datetime.now(UTC)
        old = self.github.add_claim("tank", now - timedelta(hours=3))
        self.github.issue["assignees"] = []
        tank = self.dispatcher("tank")
        with mock.patch.object(tank, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertNotIn(old, self.github.comments)
        self.assertEqual(tank.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])

    def test_heartbeat_after_frontier_prevents_stale_recovery(self):
        now = datetime.now(UTC)
        claim = self.github.add_claim("tank", now - timedelta(hours=3))
        tank = self.dispatcher("tank")
        with mock.patch.object(tank, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            claim["body"] = (
                f"Factory claim: host=tank nonce={claim['body'].split('nonce=', 1)[1].split()[0]} "
                f"at={now.isoformat()}"
            )
            before = copy.deepcopy((self.github.issue, self.github.comments))
            tank.process_ticket(issue, 1, False)

        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_interruption_preserves_preexisting_authenticated_assignment(self):
        self.github.issue["assignees"] = [{"login": "@me"}]
        tank = self.dispatcher("tank")

        self.interrupt_pr_gate(tank, forced=True)

        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
