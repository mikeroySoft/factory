"""#815: the producer lands using its own SHA-bound evidence, not labels alone."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import config
from tests import test_land_host as fixture


class ProducerLandTest(unittest.TestCase):
    setUp = fixture.LandHostTest.setUp
    dispatcher = fixture.LandHostTest.dispatcher
    claim = fixture.LandHostTest.claim
    approve = fixture.LandHostTest.approve
    events = fixture.LandHostTest.events
    move_main = fixture.LandHostTest.move_main
    assert_untouched = fixture.LandHostTest.assert_untouched

    def test_ultron_lands_own_clean_approved_head(self):
        ultron = self.dispatcher("ultron")
        self.claim("ultron", age=3 * 3600)
        head = self.approve(ultron)
        ultron.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(ultron, "merged")], [head])

    def test_ultron_refreshes_and_reearns_approval_before_landing(self):
        ultron = self.dispatcher("ultron")
        self.claim("ultron", age=3 * 3600)
        old = self.approve(ultron)
        self.move_main()
        with mock.patch.object(ultron, "run_gate", return_value=(True, "gate passed")) as gate:
            ultron.land_pass(False)
        self.assertFalse(self.github.merged)
        head = self.github.head()
        self.assertNotEqual(head, old)
        gate.assert_called_once()
        self.assertEqual([row["head"] for row in self.events(ultron, "review")], [old, head])
        self.assertEqual([row["head"] for row in self.events(ultron, "approved")], [old, head])
        ultron.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(ultron, "merged")], [head])

    def test_tank_preserves_foreign_producer_approval_after_expiry(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.claim("ultron", age=3 * 3600)
        self.approve(ultron)
        tank.land_pass(False)
        self.assert_untouched(tank)
        self.assertEqual(self.commands["tank"], [])

    def test_stale_base_refusal_refreshes_without_stripping_approval(self):
        ultron = self.dispatcher("ultron")
        self.claim("ultron")
        old = self.approve(ultron)
        original = ultron.run.side_effect
        refused = False

        def race(cmd, *args, **kwargs):
            nonlocal refused
            if cmd[:3] == ["gh", "pr", "merge"] and not refused:
                refused = True
                self.move_main()
                raise subprocess.CalledProcessError(
                    1, cmd, "", "GraphQL: Base branch was modified. Review and try the merge again."
                )
            return original(cmd, *args, **kwargs)

        with mock.patch.object(ultron, "run", side_effect=race), \
                mock.patch.object(ultron, "run_gate", return_value=(True, "gate passed")) as gate:
            ultron.land_pass(False)
        self.assertTrue(refused)
        self.assertFalse(self.github.merged)
        self.assertEqual(self.events(ultron, "escalate"), [])
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        head = self.github.head()
        self.assertNotEqual(head, old)
        gate.assert_called_once()
        self.assertEqual([row["head"] for row in self.events(ultron, "review")], [old, head])
        ultron.land_pass(False)
        self.assertTrue(self.github.merged)
