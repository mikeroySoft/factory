"""Bug-ticket gate regressions for failing-test-first and existing-test protection."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch

ROOT = Path(__file__).resolve().parents[1]
TEST_ID = "tests.test_regression.RegressionTest.test_value"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def repository(root: Path, *, fixed: bool = False) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / ".factory.toml").write_text(
        '[leak_scan]\npattern = ""\n'
        "[gate]\n"
        "[[gate.check]]\n"
        'name = "test"\n'
        f"run = [{sys.executable!r}, \"-m\", \"unittest\", \"discover\", \"-s\", \"tests\"]\n"
    )
    (repo / "value.py").write_text(f"def value():\n    return {fixed}\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_existing.py").write_text(
        "import unittest\n\n"
        "class ExistingTest(unittest.TestCase):\n"
        "    def test_existing(self):\n"
        "        self.assertTrue(True)\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text(
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn existing_rust_test() {\n"
        "        assert!(true);\n"
        "    }\n"
        "}\n"
    )
    commit(repo, "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def add_repro(repo: Path) -> None:
    (repo / "tests" / "test_regression.py").write_text(
        "import unittest\n\n"
        "from value import value\n\n"
        "class RegressionTest(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertTrue(value())\n"
    )
    commit(repo, "reproduce bug")


def run_gate(repo: Path, *args: str) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
    proc = subprocess.run(
        [sys.executable, "-m", "factory", "gate", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    report = repo / ".factory" / "gate-report.md"
    return proc.returncode, report.read_text() if report.exists() else proc.stdout + proc.stderr


class BugReproductionGateTest(unittest.TestCase):
    def test_bug_ticket_records_failing_test_before_and_passing_test_at_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            add_repro(repo)
            (repo / "value.py").write_text("def value():\n    return True\n")
            commit(repo, "fix bug")

            code, report = run_gate(repo, "--bug", "--repro-test", TEST_ID)

            self.assertEqual(code, 0, report)
            self.assertIn("- bug-repro-before: EXPECTED FAIL", report)
            self.assertIn("- bug-repro-head: PASS", report)

    def test_test_that_passes_before_fix_does_not_reproduce_bug(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory), fixed=True)
            add_repro(repo)
            (repo / "README.md").write_text("fix commit\n")
            commit(repo, "claimed fix")

            code, report = run_gate(repo, "--bug", "--repro-test", TEST_ID)

            self.assertEqual(code, 1, report)
            self.assertIn("test does not reproduce the bug", report)

    def test_bug_without_repro_declaration_fails_but_reason_can_skip_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            (repo / "value.py").write_text("def value():\n    return True\n")
            commit(repo, "fix without test")

            code, report = run_gate(repo, "--bug")
            self.assertEqual(code, 1, report)
            self.assertIn("bug ticket needs a `Repro test:` handoff line or `No repro test:` ticket line", report)

            code, report = run_gate(
                repo, "--bug", "--no-repro-test", "hardware-only failure cannot run in CI"
            )
            self.assertEqual(code, 0, report)
            self.assertIn(
                "- bug-repro: SKIP (No repro test: hardware-only failure cannot run in CI)",
                report,
            )


class ExistingTestProtectionGateTest(unittest.TestCase):
    def test_python_skip_and_rust_ignore_or_deletion_fail_unless_overridden(self) -> None:
        cases = {
            "python skip": lambda repo: (repo / "tests" / "test_existing.py").write_text(
                "import unittest\n\n"
                "class ExistingTest(unittest.TestCase):\n"
                "    @unittest.skip(\"hide failure\")\n"
                "    def test_existing(self):\n"
                "        self.assertTrue(True)\n"
            ),
            "rust ignore": lambda repo: (repo / "src" / "lib.rs").write_text(
                "#[cfg(test)]\n"
                "mod tests {\n"
                "    #[ignore]\n"
                "    #[test]\n"
                "    fn existing_rust_test() {\n"
                "        assert!(true);\n"
                "    }\n"
                "}\n"
            ),
            "rust deletion": lambda repo: (repo / "src" / "lib.rs").write_text(
                "#[cfg(test)]\nmod tests {}\n"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                repo = repository(Path(directory))
                mutate(repo)
                commit(repo, name)

                code, report = run_gate(
                    repo, "--bug", "--no-repro-test", "existing-test protection fixture"
                )
                self.assertEqual(code, 1, report)
                self.assertIn("- protected-tests: FAIL", report)

                code, report = run_gate(
                    repo,
                    "--bug",
                    "--no-repro-test",
                    "existing-test protection fixture",
                    "--protected-override",
                )
                self.assertEqual(code, 0, report)

    def test_non_bug_ticket_keeps_existing_gate_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            (repo / "src" / "lib.rs").write_text("#[cfg(test)]\nmod tests {}\n")
            commit(repo, "ordinary product change")

            code, report = run_gate(repo)

            self.assertEqual(code, 0, report)
            self.assertNotIn("protected-tests", report)


class BugGateDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        dispatch.configure(config.Config(Path(self.temp.name), "acme/widgets"))

    def test_gate_flags_read_bug_repro_skip_and_explicit_override_lines(self) -> None:
        self.assertEqual(
            dispatch.gate_flags(
                None,
                {"bug"},
                "No repro test: requires unavailable hardware\n"
                "factory-protected-override: ticket explicitly permits the test rewrite",
                "Repro test: ignored.when.no_repro_is_declared",
            ),
            {
                "protected_override": True,
                "bug": True,
                "no_repro_test": "requires unavailable hardware",
            },
        )
        self.assertEqual(
            dispatch.gate_flags(None, {"bug"}, "", f"Repro test: {TEST_ID}"),
            {"protected_override": False, "bug": True, "repro_test": TEST_ID},
        )

    def test_gate_trace_contains_both_reproduction_results(self) -> None:
        report = (
            "# Gate report\n\n"
            "- bug-repro-before: EXPECTED FAIL\n"
            "- bug-repro-head: PASS\n"
            "- test: PASS\n"
        )
        with mock.patch.object(dispatch, "record") as record:
            dispatch._record_gate_decision(7, True, report, "a" * 40, ".factory/gate.md", 1.0)

        row = record.call_args.kwargs
        summaries = [item["summary"] for item in row["inputs"] if item["kind"] == "repro_test"]
        self.assertEqual(
            summaries,
            ["bug-repro-before: EXPECTED FAIL", "bug-repro-head: PASS"],
        )


if __name__ == "__main__":
    unittest.main()
