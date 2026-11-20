"""#875: the configured landing host can recover an expired foreign claim."""
from __future__ import annotations

import unittest
from unittest import mock

from factory import config
from tests import test_land_host as fixture


class LandClaimTakeoverTest(unittest.TestCase):
    setUp = fixture.LandHostTest.setUp
    dispatcher = fixture.LandHostTest.dispatcher
    claim = fixture.LandHostTest.claim
    approve = fixture.LandHostTest.approve
    events = fixture.LandHostTest.events
    move_main = fixture.LandHostTest.move_main

    def test_expired_foreign_claim_lands_without_deleting_claim_history(self):
        tank = self.dispatcher("tank")
        head = self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        original = self.comments[0].copy()
        self.github.issue_labels.add("host:ultron")

        tank.land_pass(False)

        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assertIn(original, self.comments)
        self.assertFalse(any("DELETE" in cmd for cmd in self.commands["tank"]))
        requeues = [row for row in self.comments if row["body"].startswith("Factory requeue:")]
        self.assertEqual(requeues[0]["body"].splitlines()[0], "Factory requeue: nonce=ultron-nonce")
        takeover = self.events(tank, "claim-takeover")
        self.assertEqual([(row["foreign_host"], row["foreign_nonce"]) for row in takeover],
                         [("ultron", "ultron-nonce")])


if __name__ == "__main__":
    unittest.main()
