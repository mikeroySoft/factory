from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_candidate_cannot_set_second_reviewer_command(self) -> None:
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n'
            '[review]\nsecond_command = ["candidate-reviewer", "--model", "same/model"]\n'
        )
        with mock.patch.object(config, "host_config", return_value={}), \
                self.assertRaisesRegex(config.ConfigError, "host-only"):
            config.load(self.repo)


class LowPolicyTest(unittest.TestCase):
    def test_low_skips_pr_checks_and_runs_one_primary_review(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(cfg)
        with mock.patch.object(dispatch, "risk_for_head", return_value=("Low", ["low:docs/**"])), \
                mock.patch.object(dispatch, "run_gate") as gate, \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")) as review:
            self.assertEqual(
                dispatch.pr_gate_and_review(mock.Mock(), 7, "attempt report", "a" * 40, {}),
                ("APPROVE", "ok", False),
            )
        gate.assert_not_called()
        review.assert_called_once()

    def test_low_revise_uses_medium_loop_on_next_head(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(cfg)
        with mock.patch.object(dispatch, "risk_for_head", return_value=("Low", ["low:docs/**"])), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "pr report")) as gate, \
                mock.patch.object(
                    dispatch, "review", side_effect=[("REVISE", "fix"), ("APPROVE", "ok")],
                ):
            self.assertEqual(
                dispatch.pr_gate_and_review(mock.Mock(), 7, "attempt", "a" * 40, {})[0],
                "REVISE",
            )
            self.assertEqual(
                dispatch.pr_gate_and_review(mock.Mock(), 7, "attempt", "b" * 40, {})[0],
                "APPROVE",
            )
        gate.assert_called_once()


class DefaultOffStructureTest(RiskTierTest):
    def test_classify_unrelated_found_pattern_is_medium(self) -> None:
        tier, _ = self.classify({
            "factory/optional.py": (
                "found = False\nfor item in items:\n    found = check(item)\n"
                "if found:\n    publish()\n"
            ),
        })
        self.assertEqual(tier, "Medium")

    def test_classify_unguarded_additions_alongside_flag_are_medium(self) -> None:
        tier, _ = self.classify({
            "factory/optional.py": (
                "ENABLED = False\npublish()\nif ENABLED:\n    register_feature()\n"
            ),
        })
        self.assertEqual(tier, "Medium")

    def test_classify_reassigned_flag_is_medium(self) -> None:
        tier, _ = self.classify({
            "factory/optional.py": (
                "ENABLED = False\nENABLED = True\nif ENABLED:\n    register_feature()\n"
            ),
        })
        self.assertEqual(tier, "Medium")

    def test_classify_existing_flag_binding_is_not_new_default_off_code(self) -> None:
        path = self.repo / "optional.py"
        path.write_text("def enable():\n    global ENABLED\n    ENABLED = True\n")
        git(self.repo, "add", "optional.py")
        git(self.repo, "commit", "-qm", "existing binding")
        self.base = git(self.repo, "rev-parse", "HEAD")
        tier, _ = self.classify({
            "optional.py": path.read_text() + "\nENABLED = False\nif ENABLED:\n    publish()\n",
        })
        self.assertEqual(tier, "Medium")

    def test_classify_unrelated_false_assignment_is_not_a_flag_declaration(self) -> None:
        tier, _ = self.classify({
            "factory/optional.py": (
                "__all__ = False\nENABLED = False\nif ENABLED:\n    register_feature()\n"
            ),
        })
        self.assertEqual(tier, "Medium")


if __name__ == "__main__":
    unittest.main()
