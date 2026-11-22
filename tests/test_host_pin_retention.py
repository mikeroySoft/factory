"""#888: escalation and resume retain the host holding unpublished work."""
import copy
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import manage
from tests.test_host_claim_transitions import TimelineGitHub
from tests.test_host_claims import HostClaimRaceTest as Harness


class HostPinRetentionTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def set_up_owner(self):
        self.github = TimelineGitHub()
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        return tank

    def test_escalation_keeps_host_pin(self):
        tank = self.set_up_owner()
        tank.escalate(7, "gate failed; local commits kept", None, code="gate_failed")
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        self.assertIn({"name": "ready-for-human"}, self.github.issue["labels"])
        self.assertEqual(self.github.issue["assignees"], [])

    def test_manager_continue_keeps_pin_and_foreign_dispatch_cannot_claim(self):
        tank = self.set_up_owner()
        tank.escalate(7, "gate failed; local commits kept", None, code="gate_failed")
        packet = tank.FACTORY / "escalations" / "7.md"
        with mock.patch.object(manage, "dispatch", tank):
            manage.apply(7, copy.deepcopy(self.github.issue), "RETRY", "Continue kept work", None, packet)
        self.assertIn({"name": "ready-for-agent"}, self.github.issue["labels"])
        ultron = self.dispatcher("ultron")
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        self.assertNotIn({"name": "host:ultron"}, self.github.issue["labels"])
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_dispatcher_requeue_keeps_host_pin(self):
        tank = self.set_up_owner()
        tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent")
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        self.assertEqual(self.github.issue["assignees"], [])
        ultron = self.dispatcher("ultron")
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [])

    def test_foreign_manager_resume_keeps_expired_owner_pin(self):
        self.github = TimelineGitHub()
        self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        ultron = self.dispatcher("ultron")
        ultron.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent")
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [])

    def test_explicit_host_pin_removal_allows_reassignment(self):
        tank = self.set_up_owner()
        tank.escalate(7, "gate failed", None, code="gate_failed")
        tank.run(["gh", "issue", "edit", "7", "--repo", tank.REPO,
                  "--remove-label", "host:tank", "--add-label", "ready-for-agent"])
        ultron = self.dispatcher("ultron")
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, ["ultron"])

    def test_old_same_host_claim_cannot_bypass_resume_pin(self):
        self.github = TimelineGitHub()
        now = datetime.now(UTC)
        self.github.add_claim("tank", now - timedelta(hours=4))
        self.github.add_claim("tank", now - timedelta(hours=3))
        ultron = self.dispatcher("ultron")
        ultron.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent")
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])

    def test_reescalation_preserves_all_existing_host_labels(self):
        tank = self.set_up_owner()
        self.github.add_claim("old", datetime.now(UTC) - timedelta(hours=3))
        before = {row["name"] for row in self.github.issue["labels"] if row["name"].startswith("host:")}
        tank.escalate(7, "gate failed", None, code="gate_failed")
        tank.escalate(7, "gate failed again", None, code="gate_failed")
        after = {row["name"] for row in self.github.issue["labels"] if row["name"].startswith("host:")}
        self.assertEqual(after, before)


del Harness
