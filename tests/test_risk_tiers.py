from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory import config, dispatch, lifecycle, risk


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


class RiskTierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "README.md").write_text("base\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def classify(self, changes: dict[str, str], *, opinion=None):
        for name, content in changes.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "candidate")
        head = git(self.repo, "rev-parse", "HEAD")
        with contextlib.chdir(self.repo):
            return risk.classify(self.base, head, opinion=opinion)

    def test_classify_low_medium_and_high_paths(self) -> None:
        tier, rules = self.classify({"docs/guide.txt": "docs\n", "tests/test_new.py": "pass\n"})
        self.assertEqual(tier, "Low")
        self.assertIn("low:docs/**", rules)
        self.assertIn("low:tests/** additions", rules)

        self.setUp()
        tier, rules = self.classify({"factory/flow.py": "value = 1\n"})
        self.assertEqual((tier, rules), ("Medium", ["medium:other path"]))

        self.setUp()
        tier, rules = self.classify({"factory/dispatch.py": "candidate rules say low\n"})
        self.assertEqual(tier, "High")
        self.assertIn("high:approval, gate, and dispatch control", rules)

    def test_additions_behind_default_off_flag_are_low(self) -> None:
        tier, rules = self.classify({
            "factory/optional.py": "ENABLED = False\n\nif ENABLED:\n    register_feature()\n",
        })
        self.assertEqual(tier, "Low")
        self.assertIn("low:default-off code", rules)

    def test_markdown_exceptions_and_modified_tests_are_not_low(self) -> None:
        tier, _ = self.classify({"AGENTS.md": "rules\n"})
        self.assertEqual(tier, "Medium")

        self.setUp()
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_existing.py").write_text("old\n")
        git(self.repo, "add", "tests/test_existing.py")
        git(self.repo, "commit", "-q", "-m", "test base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        tier, _ = self.classify({"tests/test_existing.py": "changed\n"})
        self.assertEqual(tier, "Medium")

    def test_second_opinion_can_only_raise_and_failure_keeps_deterministic_tier(self) -> None:
        tier, rules = self.classify({"factory/dispatch.py": "high\n"}, opinion=lambda *_: "Low")
        self.assertEqual(tier, "High")
        self.assertNotIn("model:Low", rules)

        self.setUp()
        tier, rules = self.classify({"docs/note.md": "low\n"}, opinion=lambda *_: "Medium")
        self.assertEqual(tier, "Medium")
        self.assertIn("model:Medium", rules)

        self.setUp()
        def broken(*_):
            raise TimeoutError("offline")

        tier, _ = self.classify({"docs/note.md": "low\n"}, opinion=broken)
        self.assertEqual(tier, "Low")

    def test_recorded_event_and_decision_are_bound_to_head(self) -> None:
        (self.repo / "docs").mkdir()
        (self.repo / "docs/note.md").write_text("low\n")
        git(self.repo, "add", "docs/note.md")
        git(self.repo, "commit", "-q", "-m", "candidate")
        head = git(self.repo, "rev-parse", "HEAD")
        dispatch.configure(config.Config(self.repo, "acme/widgets"))

        self.assertEqual(
            dispatch.record_risk(self.repo, 7, head, base=self.base),
            ("Low", ["low:docs/**"]),
        )

        rows = lifecycle.read_events(self.repo / ".factory/events.jsonl")
        self.assertTrue(any(
            row.get("event") == "risk" and row.get("head") == head
            and row.get("tier") == "Low"
            for row in rows
        ))
        self.assertTrue(any(
            row.get("event") == "decision" and row.get("stage") == "risk"
            and row.get("chosen") == "Low"
            for row in rows
        ))


    def test_candidate_cannot_lower_rule_for_risk_module(self) -> None:
        tier, rules = self.classify({"factory/risk.py": "HIGH_PATHS = ()\n"})
        self.assertEqual(tier, "High")
        self.assertIn("high:risk policy", rules)


if __name__ == "__main__":
    unittest.main()
