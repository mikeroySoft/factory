"""Throttle mode state, presets, and admission policy (#442)."""
from __future__ import annotations

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, schedule, throttle
from tests.test_factory import make_repo
from tests.test_schedule import BOUND, REPO, ticket


class ConfigTest(unittest.TestCase):
    def test_presets_default_to_existing_knobs_and_accept_bounded_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(
                Path(d),
                "[dispatch]\nmax_active = 7\nmax_attempts = 6\nreview_rounds = 4\nbudget_min = 120\n"
                "[manager]\nrounds = 2\n"
                "[throttle]\nmaintenance_label = \"maintenance\"\npace_low = 0\npace_high = 4\n"
                "aggression_high = { review_rounds = 8, manager_rounds = 7, max_attempts = 5, budget_min = 180 }\n",
            )
            cfg = config.load(repo)
        self.assertEqual(cfg.throttle_pace, {"low": 0, "normal": 7, "high": 4})
        self.assertEqual(
            cfg.throttle_aggression["normal"],
            {"review_rounds": 4, "manager_rounds": 2, "max_attempts": 6, "budget_min": 120},
        )
        self.assertEqual(cfg.throttle_aggression["high"]["budget_min"], 180)
        self.assertEqual(cfg.throttle_maintenance_label, "maintenance")
        self.assertEqual(config.unknown_keys({"throttle": {"pace_low": 1, "surprise": 2}}), ["throttle.surprise"])

    def test_invalid_throttle_values_are_rejected(self):
        bad = (
            "pace_low = -1\n",
            "maintenance_label = \"\"\n",
            "aggression_high = { review_rounds = -1, manager_rounds = 1, max_attempts = 1, budget_min = 1 }\n",
            "aggression_high = { review_rounds = 1, manager_rounds = 1, max_attempts = 0, budget_min = 1 }\n",
            "aggression_high = { review_rounds = 1, manager_rounds = 1, max_attempts = 1, budget_min = 0 }\n",
            "aggression_high = { review_rounds = 1, manager_rounds = 1, max_attempts = 1, budget_min = 1, extra = 2 }\n",
        )
        for value in bad:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as d:
                repo = make_repo(Path(d), f"[throttle]\n{value}")
                with self.assertRaises(config.ConfigError):
                    config.load(repo)


class StateAndCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(Path(temp.name), REPO)

    def test_mode_changes_are_atomic_idempotent_and_journaled_once(self):
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("normal"))
        self.assertTrue(throttle.set_mode(self.cfg, throttle.Mode("maintenance"), who="michael"))
        self.assertFalse(throttle.set_mode(self.cfg, throttle.Mode("maintenance"), who="michael"))
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("maintenance"))
        [row] = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl") if row["event"] == "mode"]
        self.assertEqual((row["who"], row["mode"], row["feature"]), ("michael", "maintenance", None))
        self.assertIn("at", row)

    def test_feature_drive_command_validates_the_current_repository_issue(self):
        valid = {"state": "OPEN", "labels": [{"name": config.LABEL_AGENT}], "url": f"https://github.com/{REPO}/issues/7"}
        cases = (
            ({**valid, "state": "CLOSED"}, "closed"),
            ({**valid, "labels": []}, "ready-for-agent"),
            ({**valid, "url": "https://github.com/other/repo/issues/7"}, "another repository"),
        )
        for issue, message in cases:
            with self.subTest(message=message), mock.patch.object(throttle.config, "load", return_value=self.cfg), \
                    mock.patch.object(throttle, "issue", return_value=issue), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(throttle.main(["feature-drive", "7"]), 2)
                self.assertIn(message, stderr.getvalue())
                self.assertEqual(throttle.read(self.cfg), throttle.Mode("normal"))
        with mock.patch.object(throttle.config, "load", return_value=self.cfg), \
                mock.patch.object(throttle, "issue", return_value=valid), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(throttle.main(["feature-drive", "7"]), 0)
            self.assertEqual(throttle.read(self.cfg), throttle.Mode("feature-drive", 7))
            self.assertIn("feature-drive #7", stdout.getvalue())
        with mock.patch.object(throttle.config, "load", return_value=self.cfg), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(throttle.main([]), 0)
            self.assertEqual(stdout.getvalue().strip(), "feature-drive #7")

    def test_open_initiative_is_a_valid_feature_target(self):
        initiative = {"state": "OPEN", "labels": [{"name": config.LABEL_INITIATIVE}], "url": f"https://github.com/{REPO}/issues/9"}
        with mock.patch.object(throttle.config, "load", return_value=self.cfg), \
                mock.patch.object(throttle, "issue", return_value=initiative), redirect_stdout(io.StringIO()):
            self.assertEqual(throttle.main(["feature-drive", "9"]), 0)

    def test_completed_feature_tree_returns_to_normal_once(self):
        throttle.set_mode(self.cfg, throttle.Mode("feature-drive", 7), who="michael")
        parents = {8: 7, 9: 8}
        self.assertFalse(throttle.auto_reset(self.cfg, parents, lambda number: "OPEN" if number == 9 else "CLOSED"))
        self.assertTrue(throttle.auto_reset(self.cfg, parents, lambda _number: "CLOSED"))
        self.assertFalse(throttle.auto_reset(self.cfg, parents, lambda _number: "CLOSED"))
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("normal"))
        rows = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl") if row["event"] == "mode"]
        self.assertEqual(rows[-1]["who"], "auto")


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config(Path("/nonexistent"), REPO, max_active=2, max_attempts=3, review_rounds=3,
                                 manager_rounds=3, budget_min=90)
        self.cfg.throttle_maintenance_label = "maintenance"
        self.cfg.throttle_pace = {"low": 1, "normal": 2, "high": 3}
        self.cfg.throttle_aggression = {
            "low": {"review_rounds": 3, "manager_rounds": 3, "max_attempts": 3, "budget_min": 90},
            "normal": {"review_rounds": 3, "manager_rounds": 3, "max_attempts": 3, "budget_min": 90},
            "high": {"review_rounds": 5, "manager_rounds": 5, "max_attempts": 4, "budget_min": 150},
        }

    def test_normal_mode_preserves_the_existing_plan_output(self):
        rows = [ticket(3, "2026-01-03T00:00:00Z"), ticket(1, "2026-01-01T00:00:00Z", body="unbound")]
        before = schedule.plan(rows, 2, repo=REPO, streak=0, experiments=0)
        after = schedule.plan(rows, 2, repo=REPO, streak=0, experiments=0, mode=throttle.Mode("normal"))
        self.assertEqual(after, before)

    def test_maintenance_holds_nonmaintenance_work_without_mutating_labels(self):
        rows = [
            ticket(1, "2026-01-01T00:00:00Z", body="unbound", labels=("ready-for-agent", "bug")),
            ticket(2, "2026-01-02T00:00:00Z", body="unbound", labels=("ready-for-agent", "chore")),
            ticket(3, "2026-01-03T00:00:00Z", body="unbound", labels=("ready-for-agent", "maintenance")),
            ticket(4, "2026-01-04T00:00:00Z", body="unbound", labels=("ready-for-agent", "enhancement")),
        ]
        original = copy.deepcopy(rows)
        decided = schedule.plan(rows, 4, repo=REPO, streak=0, experiments=0,
                                mode=throttle.Mode("maintenance"), maintenance_label="maintenance")
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 2, 3])
        held = next(row for row in decided["explained"] if row["ticket"] == 4)
        self.assertEqual(held, {"ticket": 4, "decision": "held", "reason": "maintenance mode: not a bug/chore ticket"})
        self.assertEqual(rows, original)

    def test_feature_and_descendant_take_the_reserved_slot_without_advancing_fairness(self):
        now = ticket(3, "2026-01-03T00:00:00Z")
        unknown = ticket(1, "2026-01-01T00:00:00Z", body="unbound")
        feature = ticket(7, "2026-01-04T00:00:00Z", body="unbound")
        decided = schedule.plan([unknown, now, feature], 2, repo=REPO, streak=2, experiments=0,
                                mode=throttle.Mode("feature-drive", 7))
        self.assertEqual([row["number"] for row in decided["selected"]], [7, 3])
        child = ticket(8, "2026-01-05T00:00:00Z", body="unbound")
        decided = schedule.plan([unknown, now, child], 2, repo=REPO, streak=0, experiments=0, parents={8: 7},
                                mode=throttle.Mode("feature-drive", 7))
        self.assertEqual([row["number"] for row in decided["selected"]], [8, 3])

    def test_mode_capacity_is_clamped_and_only_feature_tree_gets_high_aggression(self):
        self.cfg.manager_max_active_cap = 2
        self.assertEqual(throttle.capacity(self.cfg, throttle.Mode("maintenance")), 1)
        self.cfg.throttle_pace["low"] = 5
        self.assertEqual(throttle.capacity(self.cfg, throttle.Mode("maintenance")), 2)
        parents = {8: 7, 9: 8}
        mode = throttle.Mode("feature-drive", 7)
        self.assertEqual(throttle.limits(self.cfg, mode, 7, parents)["review_rounds"], 5)
        self.assertEqual(throttle.limits(self.cfg, mode, 9, parents)["manager_rounds"], 5)
        self.assertEqual(throttle.limits(self.cfg, mode, 6, parents), self.cfg.throttle_aggression["normal"])


if __name__ == "__main__":
    unittest.main()
