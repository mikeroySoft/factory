"""Release-bar sample size, confidence bounds, floors, and legacy reset."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, release


def metrics(tickets: int, *, touches: int = 0, passed: int | None = None) -> dict[str, float | int | None]:
    attempts = tickets if passed is not None else 0
    return {
        "human_rate": round(touches / max(tickets, 1), 6),
        "human_touches": touches,
        "human_ticket_rate": 0.0,
        "first_pass": round(passed / attempts, 6) if attempts else None,
        "first_passes": passed or 0,
        "attempts": attempts,
        "tickets": tickets,
    }


class ReleaseBarSampleTest(unittest.TestCase):
    def test_small_window_is_carried_as_pending(self) -> None:
        old = {"human_rate": 0.2, "first_pass": 0.8, "tickets": 20, "pending_tickets": 0}
        result = release.next_bar(
            old,
            metrics(5, passed=5),
            bar_min_tickets=20,
            bar_floor_human_rate=0.1,
            bar_floor_first_pass=0.9,
        )
        self.assertEqual(result["human_rate"], 0.2)
        self.assertEqual(result["first_pass"], 0.8)
        self.assertEqual(result["pending_tickets"], 5)

    def test_four_small_windows_tighten_once_from_pooled_counts(self) -> None:
        bar: dict[str, float | int] = {
            "human_rate": 0.2,
            "first_pass": 0.8,
            "tickets": 20,
            "pending_tickets": 0,
        }
        values = []
        for _ in range(4):
            bar = release.next_bar(
                bar,
                metrics(5, passed=5),
                bar_min_tickets=20,
                bar_floor_human_rate=0.1,
                bar_floor_first_pass=0.9,
            )
            values.append(bar["human_rate"])
        self.assertEqual(values[:3], [0.2, 0.2, 0.2])
        self.assertLess(values[3], 0.2)
        self.assertGreaterEqual(values[3], 0.1)
        self.assertEqual(bar["pending_tickets"], 0)

    def test_touch_rate_above_one_does_not_tighten_or_break_the_bar(self) -> None:
        old = {"human_rate": 0.2, "first_pass": 0.8, "tickets": 20, "pending_tickets": 0}
        result = release.next_bar(
            old,
            metrics(2, touches=3, passed=2),
            bar_min_tickets=2,
            bar_floor_human_rate=0.1,
            bar_floor_first_pass=0.9,
        )
        self.assertEqual(result["human_rate"], 0.2)
        self.assertEqual(result["pending_tickets"], 0)

    def test_wilson_bounds_and_empty_sample(self) -> None:
        self.assertAlmostEqual(release.wilson(0, 21)[1], 0.155, places=3)
        self.assertAlmostEqual(release.wilson(0, 5)[1], 0.434, places=3)
        self.assertEqual(release.wilson(5, 5)[1], 1.0)
        self.assertIsNone(release.wilson(0, 0))

    def test_release_config_defaults_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "git@github.com:acme/widgets.git"], check=True)
            (root / ".factory.toml").write_text("[repo]\nslug = \"acme/widgets\"\n")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "config")}):
                loaded = config.load(root)
                self.assertEqual(
                    (loaded.release_bar_min_tickets, loaded.release_bar_floor_human_rate,
                     loaded.release_bar_floor_first_pass),
                    (20, 0.1, 0.9),
                )
                (root / ".factory.toml").write_text(
                    "[repo]\nslug = \"acme/widgets\"\n\n[release]\n"
                    "bar_min_tickets = 30\nbar_floor_human_rate = 0.2\n"
                    "bar_floor_first_pass = 0.8\n"
                )
                loaded = config.load(root)
                self.assertEqual(
                    (loaded.release_bar_min_tickets, loaded.release_bar_floor_human_rate,
                     loaded.release_bar_floor_first_pass),
                    (30, 0.2, 0.8),
                )

    def test_legacy_bar_is_reset_to_floors_before_comparison(self) -> None:
        cfg = config.Config(root=Path("."), repo="acme/widgets")
        message = json.dumps({"human_rate": 0.037829, "first_pass": 0.961979, "tickets": 5})
        bar, reset = release._reset_legacy_bar(
            release._bar(f"Release\n\n```json\n{message}\n```"), cfg
        )
        self.assertTrue(reset)
        self.assertEqual(bar["human_rate"], 0.1)
        self.assertEqual(bar["first_pass"], 0.9)


if __name__ == "__main__":
    unittest.main()
