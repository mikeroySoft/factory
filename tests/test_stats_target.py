from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config


class StatsCycleTargetConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def load(self, text: str = "") -> config.Config:
        (self.root / config.CONFIG_NAME).write_text(text)
        with (
            mock.patch.object(config, "repo_root", return_value=self.root),
            mock.patch.object(config, "remote_slug", return_value="acme/widgets"),
            mock.patch.object(config, "host_config", return_value={}),
        ):
            return config.load()

    def test_default_cycle_target_hours(self) -> None:
        self.assertEqual(config.Config(self.root, "acme/widgets").stats_cycle_target_hours, 6.6)
        self.assertEqual(self.load().stats_cycle_target_hours, 6.6)

    def test_cycle_target_hours_accepts_positive_int_or_float(self) -> None:
        for literal, expected in (("12", 12.0), ("4.25", 4.25)):
            with self.subTest(value=literal):
                value = self.load(f"[stats]\ncycle_target_hours = {literal}\n").stats_cycle_target_hours
                self.assertEqual(value, expected)
                self.assertIsInstance(value, float)

    def test_cycle_target_hours_rejects_invalid_values(self) -> None:
        for value in ("true", '"6.6"', "0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaisesRegex(
                config.ConfigError,
                r"stats\.cycle_target_hours must be a positive finite number",
            ):
                self.load(f"[stats]\ncycle_target_hours = {value}\n")

    def test_stats_must_be_a_table(self) -> None:
        with self.assertRaisesRegex(config.ConfigError, r"\[stats\] must be a table"):
            self.load("stats = 6.6\n")


if __name__ == "__main__":
    unittest.main()
