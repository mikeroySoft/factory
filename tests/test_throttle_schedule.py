"""Throttle admission preserves normal order and reserves one feature slot."""
from __future__ import annotations

import copy
import unittest

from factory import schedule
from tests.test_schedule import QUEUE, REPO, ticket


class ThrottleScheduleTest(unittest.TestCase):
    def plan(self, rows, **kwargs):
        return schedule.plan(rows, 2, repo=REPO, streak=3, experiments=0, **kwargs)

    def test_normal_preserves_the_existing_policy(self):
        before = self.plan(QUEUE)
        after = self.plan(QUEUE, mode="normal")
        self.assertEqual(before["selected"], after["selected"])
        self.assertEqual(before["explained"], after["explained"])

    def test_maintenance_holds_without_mutating_labels(self):
        rows = [ticket(1, "1", labels=("bug",)), ticket(2, "2", labels=("chore",)),
                ticket(3, "3", labels=("enhancement",)), ticket(4, "4", labels=("upkeep",))]
        original = copy.deepcopy(rows)
        result = self.plan(rows, mode="maintenance", maintenance_labels=["upkeep"])
        self.assertEqual([r["number"] for r in result["selected"]], [1, 2])
        held = next(r for r in result["explained"] if r["ticket"] == 3)
        self.assertEqual((held["decision"], held["reason"]),
                         ("held", "maintenance mode: not a bug/chore ticket"))
        self.assertEqual(next(r for r in result["explained"] if r["ticket"] == 4)["decision"], "deferred")
        self.assertEqual(rows, original)

    def test_feature_beats_now_and_does_not_spend_fairness(self):
        rows = [ticket(1, "1", body="unbound"), ticket(3, "3"), ticket(9, "9", body="unbound")]
        result = self.plan(rows, mode="feature-drive", feature=9)
        self.assertEqual([r["number"] for r in result["selected"]], [9, 3])
        self.assertEqual(schedule.advance(3, 0, result["explained"][0]), (3, 0))
        self.assertIn("feature-drive", result["explained"][0]["reason"])
        self.assertEqual(schedule.explicit_streak([
            {"event": "claimed", "priority": "NEXT"},
            {"event": "claimed", "priority": None, "feature_drive": True},
            {"event": "claimed", "priority": "NEXT"},
        ]), 2)

    def test_nested_child_gets_only_one_reserved_slot(self):
        rows = [ticket(3, "3"), ticket(10, "1", body="unbound"), ticket(11, "2", body="unbound")]
        result = self.plan(rows, mode="feature-drive", feature=9, parents={10: 9, 11: 10})
        self.assertEqual([r["number"] for r in result["selected"]], [10, 3])
        ordinary = self.plan(rows, mode="feature-drive", feature=9, parents={10: 9}, feature_slot=False)
        self.assertEqual(ordinary["selected"][0]["number"], 3)

    def test_reservation_does_not_bypass_holds_or_experiment_limit(self):
        rows = [ticket(9, "1", labels=("experiment",)), ticket(3, "3")]
        result = schedule.plan(rows, 2, repo=REPO, streak=0, experiments=1,
                               mode="feature-drive", feature=9)
        self.assertEqual([r["number"] for r in result["selected"]], [3])
        rows[0]["held"] = "assigned"
        self.assertEqual(self.plan(rows, mode="feature-drive", feature=9)["selected"], [rows[1]])
