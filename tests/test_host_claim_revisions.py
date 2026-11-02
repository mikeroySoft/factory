"""Lifecycle and trust boundaries for opt-in host claims."""
import copy
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests.test_host_claims import HostClaimRaceTest as Harness


class HostClaimRevisionTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def test_foreign_and_expired_claims_preserve_lifecycle_transitions(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        for host, age in (("ultron", 0), ("tank", 5)):
            with self.subTest(host=host):
                self.github.comments.clear()
                self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                claim = self.github.add_claim(host, datetime.now(UTC) - timedelta(seconds=age))

                tank.unclaim(7, "--remove-label", "ready-for-agent", "--add-label", "ready-for-human")

                self.assertIn({"name": "ready-for-human"}, self.github.issue["labels"])
                self.assertNotIn({"name": "ready-for-agent"}, self.github.issue["labels"])
                self.assertEqual(self.github.issue["assignees"], [])
                if host == "ultron":
                    self.assertEqual(self.github.comments, [claim])
                    self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
                else:
                    self.assertEqual(self.github.comments, [])
                    self.assertNotIn({"name": "host:tank"}, self.github.issue["labels"])

                tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent",
                             assignee_last=True)
                self.assertIn({"name": "ready-for-agent"}, self.github.issue["labels"])
                self.assertNotIn({"name": "ready-for-human"}, self.github.issue["labels"])

    def test_completed_worker_with_open_pr_remains_held_after_ttl(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.open_prs = [{"number": 70}]
        ultron = self.dispatcher("ultron", claim_ttl="1s")
        after_ttl = datetime.now(UTC) + timedelta(seconds=5)
        for module in (tank, ultron):
            with self.subTest(host=module.cfg.host_name), \
                    mock.patch.object(module, "datetime", wraps=datetime) as clock:
                clock.now.return_value = after_ttl
                [issue] = module.frontier()
                self.assertIn("held", issue)
                before = copy.deepcopy((self.github.issue, self.github.comments))
                module.process_ticket(issue, 1, False)
                self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, ["tank"])

    def test_human_assignee_remains_frontier_hold_with_stale_claim(self):
        self.github.add_claim("ultron", datetime.now(UTC) - timedelta(seconds=5))
        self.github.issue["assignees"].append({"login": "human"})
        tank = self.dispatcher("tank", claim_ttl="1s")
        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "assigned")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_heartbeat_waits_before_refreshing_and_stays_within_ttl(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        claim = tank.acquire_host_claim(7, copy.deepcopy(self.github.issue), [])
        self.addCleanup(claim.stop)
        self.assertFalse(self.github.comment_patched.wait(timeout=0.05))
        self.assertTrue(self.github.comment_patched.wait(timeout=0.7))

    def test_untrusted_claim_comment_cannot_hold_or_be_deleted(self):
        forged = self.github.add_claim("intruder", datetime(2099, 1, 1, tzinfo=UTC))
        forged["user"] = {"login": "outside-commenter"}
        self.github.issue["labels"] = [{"name": "ready-for-agent"}]
        self.github.issue["assignees"] = []
        tank = self.dispatcher("tank")

        [issue] = tank.frontier()
        self.assertNotIn("held", issue)
        tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertIn(forged, self.github.comments)
        tank.unclaim(7)
        self.assertEqual(self.github.comments, [forged])


# Avoid collecting the imported harness class as a second copy of its suite.
del Harness
