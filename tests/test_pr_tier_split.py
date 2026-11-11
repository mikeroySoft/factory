from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from factory import config

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PR_TIER = TESTS / "pr_tier"
SLOW = "test_full_suite_leaves_no_tempdirs_behind"
DEFINES_SLOW = re.compile(rf"^\s*(?:async\s+)?def {SLOW}\b", re.MULTILINE)


def checks() -> list[dict]:
    return tomllib.loads((ROOT / config.CONFIG_NAME).read_text())["gate"]["check"]


def discovered(start: Path) -> list[Path]:
    """`test*.py` files `unittest discover -s <start>` imports: it recurses only into packages."""
    found = sorted(start.glob("test*.py"))
    for sub in sorted(p for p in start.iterdir() if p.is_dir() and (p / "__init__.py").is_file()):
        found += discovered(sub)
    return found


class PrTierSplitTest(unittest.TestCase):
    def test_attempt_suite_does_not_reach_the_full_suite_rerun(self):
        [test] = [c for c in checks() if c["name"] == "test"]
        self.assertEqual(test.get("when", "attempt"), "attempt")
        self.assertEqual(test["run"][-3:], ["discover", "-s", "tests"])
        reached = [p for p in discovered(TESTS) if DEFINES_SLOW.search(p.read_text())]
        self.assertEqual(reached, [])
        moved = [p for p in PR_TIER.glob("test*.py") if DEFINES_SLOW.search(p.read_text())]
        self.assertEqual(len(moved), 1)

    def test_pr_tier_dir_is_not_a_package(self):
        self.assertTrue(PR_TIER.is_dir())
        self.assertFalse((PR_TIER / "__init__.py").exists())

    def test_one_pr_tier_check_runs_the_pr_tier_suite(self):
        runners = [c for c in checks() if c["run"][-3:] == ["discover", "-s", "tests/pr_tier"]]
        self.assertEqual(len(runners), 1)
        [check] = runners
        self.assertEqual(check.get("when"), "pr")
        # The gate runs a check at tier T only when CHECK_TIERS ranks its `when` at or after T,
        # reporting it SKIP at earlier tiers: pr must rank after attempt and not after release.
        self.assertGreater(config.CHECK_TIERS.index("pr"), config.CHECK_TIERS.index("attempt"))
        self.assertLess(config.CHECK_TIERS.index("pr"), config.CHECK_TIERS.index("release"))

    def test_ci_runs_the_pr_tier_suite(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertRegex(ci, r"(?m)^\s*- run: python -m unittest discover -s tests/pr_tier\s*$")


if __name__ == "__main__":
    unittest.main()
