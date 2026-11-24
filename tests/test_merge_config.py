"""Isolated merge-check configuration loading."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config

BASE = '[repo]\nslug = "acme/widgets"\n'


class MergeConfigTest(unittest.TestCase):
    def load(self, repo_toml: str = BASE) -> config.Config:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / config.CONFIG_NAME).write_text(repo_toml)
            with mock.patch.object(config, "repo_root", return_value=root), \
                    mock.patch.object(config, "host_config", return_value={}):
                return config.load(root)

    def test_defaults(self) -> None:
        cfg = self.load()

        self.assertEqual(cfg.advisory_checks, [])
        self.assertEqual(cfg.queued_check_timeout, "6h")
        self.assertEqual(cfg.queued_check_timeout_seconds, 6 * 60 * 60)

    def test_custom_values(self) -> None:
        cfg = self.load(BASE + '''
[merge]
advisory_checks = ["codecov/*", "docs-?"]
queued_check_timeout = "1d2h3m4s"
''')

        self.assertEqual(cfg.advisory_checks, ["codecov/*", "docs-?"])
        self.assertEqual(cfg.queued_check_timeout, "1d2h3m4s")
        self.assertEqual(cfg.queued_check_timeout_seconds, 93_784)

    def test_merge_must_be_a_table(self) -> None:
        with self.assertRaisesRegex(config.ConfigError, r"\[merge\] must be a table"):
            self.load('merge = "invalid"\n' + BASE)

    def test_advisory_checks_must_be_nonempty_string_globs(self) -> None:
        for value in ('"lint"', '[""]', '[1]'):
            with self.subTest(value=value), self.assertRaisesRegex(
                config.ConfigError, "merge.advisory_checks must be an array of nonempty fnmatch globs"
            ):
                self.load(BASE + f"\n[merge]\nadvisory_checks = {value}\n")

    def test_queued_check_timeout_must_be_a_positive_duration(self) -> None:
        for value in ('""', '"0s"', '"0d0h0m0s"', '"1"', '"1fortnight"', '6'):
            with self.subTest(value=value), self.assertRaisesRegex(
                config.ConfigError, "merge.queued_check_timeout must be"
            ):
                self.load(BASE + f"\n[merge]\nqueued_check_timeout = {value}\n")


if __name__ == "__main__":
    unittest.main()
