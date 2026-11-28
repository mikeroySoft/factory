"""Host labels remain selection pins independently of claim lifetime."""
import copy
import unittest
from datetime import UTC, datetime, timedelta

from tests.test_host_claims import HostClaimRaceTest as Harness


class HostLabelSelectionTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def requeued_ticket(self):
        claim = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        nonce = claim["body"].split("nonce=", 1)[1].split()[0]
        self.github.comments.append({
            "id": 100, "user": {"login": "@me"},
            "body": f"Factory requeue: nonce={nonce}\nOwner requested resume",
        })
        self.github.issue["assignees"] = []
        self.github.open_prs = [{"number": 952}]
        return claim

    def test_expired_requeued_foreign_label_holds_tank_but_selects_ultron(self):
        self.requeued_ticket()
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        [ticket] = tank.frontier()
        self.assertEqual(ticket.get("held"), "host label (ultron)")
        tank.process_ticket(ticket, 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
        [ticket] = ultron.frontier()
        self.assertNotIn("held", ticket)
        ultron.process_ticket(ticket, 1, False)
        self.assertEqual(self.launched, ["ultron"])

    def test_released_claim_leaves_foreign_label_pinned(self):
        claim = self.requeued_ticket()
        self.github.comments.remove(claim)
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        [ticket] = tank.frontier()
        self.assertEqual(ticket.get("held"), "host label (ultron)")
        tank.process_ticket(ticket, 1, False)
        self.assertEqual(self.launched, [])
        self.assertNotIn("held", ultron.frontier()[0])

    def test_foreign_claim_cleanup_preserves_host_label(self):
        self.requeued_ticket()
        tank = self.dispatcher("tank")
        [claim] = tank.host_claims(7)
        tank.delete_host_claim(7, claim)
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
        self.assertFalse(any("--remove-label" in args for _, args in self.github.calls))

    def test_removed_foreign_label_allows_selection(self):
        self.requeued_ticket()
        self.github.issue["labels"] = [{"name": "ready-for-agent"}]
        tank = self.dispatcher("tank")
        ticket = copy.deepcopy(tank.frontier()[0])
        self.assertNotIn("held", ticket)
        tank.process_ticket(ticket, 1, False)
        self.assertEqual(self.launched, ["tank"])


del Harness
