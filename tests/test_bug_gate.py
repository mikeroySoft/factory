"""Bug-ticket gate regressions for failing-test-first and existing-test protection."""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import factory.lifecycle as lifecycle  # noqa: PLR0402
from factory import config, dispatch
from factory import gate as gate_module

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

    def test_timeout_is_not_accepted_as_a_failing_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            (repo / "tests" / "test_regression.py").write_text(
                "import time\n"
                "import unittest\n\n"
                "class RegressionTest(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        time.sleep(30)\n"
            )
            commit(repo, "reproduce bug")
            (repo / "README.md").write_text("claimed fix\n")
            commit(repo, "fix bug")

            code, report = run_gate(
                repo,
                "--bug",
                "--repro-test",
                TEST_ID,
                "--check-timeout",
                "1",
                "--skip",
                "test",
            )

            self.assertEqual(code, 1, report)
            self.assertIn("- bug-repro-before: FAIL", report)
            self.assertIn("check timed out", report)
            self.assertNotIn("EXPECTED FAIL", report)

    def test_worktree_setup_failure_is_not_accepted_as_a_failing_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            add_repro(repo)
            (repo / "value.py").write_text("def value():\n    return True\n")
            commit(repo, "fix bug")
            previous = Path.cwd()
            os.chdir(repo)
            try:
                gate_module.configure(config.load(repo))
                failed = subprocess.CompletedProcess([], 1, "worktree setup failed", "")
                with mock.patch.object(gate_module, "_run_at", return_value=(failed, False)):
                    results = gate_module.check_bug_reproduction(
                        "origin/main", TEST_ID, None
                    )
            finally:
                os.chdir(previous)

            self.assertEqual(
                results,
                [("bug-repro-before", "FAIL", "worktree setup failed")],
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

    def test_enclosing_python_skip_fails_but_ticket_override_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            (repo / "tests" / "test_existing.py").write_text(
                "import unittest\n\n"
                "@unittest.skip(\"hide class\")\n"
                "class ExistingTest(unittest.TestCase):\n"
                "    def test_existing(self):\n"
                "        self.assertTrue(True)\n"
            )
            commit(repo, "skip existing test class")

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
                "--test-override",
            )
            self.assertEqual(code, 0, report)

    def test_existing_tests_are_compared_from_the_merge_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            base = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "-b", "worker")
            (repo / "value.py").write_text("def value():\n    return True\n")
            commit(repo, "worker fix")
            git(repo, "checkout", "-q", "-b", "upstream", base)
            (repo / "tests" / "test_existing.py").write_text(
                "import unittest\n\n"
                "class ExistingTest(unittest.TestCase):\n"
                "    def test_existing(self):\n"
                "        self.assertTrue(False)\n"
            )
            commit(repo, "upstream changes test")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(repo, "checkout", "-q", "worker")

            code, report = run_gate(
                repo, "--bug", "--no-repro-test", "existing-test protection fixture"
            )

            self.assertEqual(code, 0, report)
            self.assertNotIn("protected-tests", report)


class BugGateDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        dispatch.configure(config.Config(Path(self.temp.name), "acme/widgets"))

    def test_gate_flags_scope_explicit_override_to_bug_test_protection(self) -> None:
        self.assertEqual(
            dispatch.gate_flags(
                None,
                {"bug"},
                "No repro test: requires unavailable hardware\n"
                "factory-protected-override: ticket explicitly permits the test rewrite",
                "Repro test: ignored.when.no_repro_is_declared",
            ),
            {
                "protected_override": False,
                "test_override": True,
                "bug": True,
                "no_repro_test": "requires unavailable hardware",
            },
        )
        self.assertEqual(
            dispatch.gate_flags(
                None, set(), "factory-protected-override: worker-authored bypass"
            ),
            {"protected_override": False},
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


class ReviewerRegressionGateTest(unittest.TestCase):
    def test_skipped_reproduction_test_fails_at_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            add_repro(repo)
            (repo / "tests" / "test_regression.py").write_text(
                "import unittest\n\n"
                "from value import value\n\n"
                "class RegressionTest(unittest.TestCase):\n"
                "    @unittest.skip(\"hide failure\")\n"
                "    def test_value(self):\n"
                "        self.assertTrue(value())\n"
            )
            commit(repo, "skip reproduction instead of fixing bug")

            code, report = run_gate(repo, "--bug", "--repro-test", TEST_ID)

            self.assertEqual(code, 1, report)
            self.assertIn("- bug-repro-before: EXPECTED FAIL", report)
            self.assertIn("- bug-repro-head: FAIL", report)
            self.assertIn("reproduction test changed at HEAD", report)

    def test_enclosing_pytestmark_and_rust_cfg_fail(self) -> None:
        cases = {
            "module pytestmark": (
                "tests/test_existing.py",
                (
                    "import unittest\n"
                    "import pytest\n\n"
                    "pytestmark = pytest.mark.skip(reason=\"hide module\")\n\n"
                    "class ExistingTest(unittest.TestCase):\n"
                    "    def test_existing(self):\n"
                    "        self.assertTrue(True)\n"
                ),
            ),
            "class pytestmark": (
                "tests/test_existing.py",
                (
                    "import unittest\n"
                    "import pytest\n\n"
                    "class ExistingTest(unittest.TestCase):\n"
                    "    pytestmark = pytest.mark.xfail(reason=\"hide class\")\n\n"
                    "    def test_existing(self):\n"
                    "        self.assertTrue(True)\n"
                ),
            ),
            "rust module cfg": (
                "src/lib.rs",
                (
                    "#[cfg(any())]\n"
                    "mod tests {\n"
                    "    #[test]\n"
                    "    fn existing_rust_test() {\n"
                    "        assert!(true);\n"
                    "    }\n"
                    "}\n"
                ),
            ),
        }
        for name, (path, source) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                repo = repository(Path(directory))
                (repo / path).write_text(source)
                commit(repo, name)

                code, report = run_gate(
                    repo, "--bug", "--no-repro-test", "existing-test protection fixture"
                )

                self.assertEqual(code, 1, report)
                self.assertIn("- protected-tests: FAIL", report)



    def test_deleted_reproduction_test_fails_as_missing_at_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            add_repro(repo)
            (repo / "tests" / "test_regression.py").unlink()
            commit(repo, "delete reproduction instead of fixing bug")

            code, report = run_gate(repo, "--bug", "--repro-test", TEST_ID)

            self.assertEqual(code, 1, report)
            self.assertIn("- bug-repro-before: EXPECTED FAIL", report)
            self.assertIn("- bug-repro-head: FAIL", report)
            self.assertIn("is missing at HEAD", report)

    def test_bare_rust_reproduction_waits_for_exclusive_check_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            lock = root / "gpu.lock"
            (repo / ".factory.toml").write_text(
                '[leak_scan]\npattern = ""\n'
                "[gate]\n"
                f"lock = {str(lock)!r}\n"
                "[[gate.check]]\n"
                'name = "test"\n'
                'run = ["cargo", "test"]\n'
                "exclusive = true\n"
            )
            (repo / "src" / "lib.rs").write_text(
                "pub fn fixed() -> bool { false }\n\n"
                "#[cfg(test)]\n"
                "mod tests {\n"
                "    #[test]\n"
                "    fn existing_rust_test() {\n"
                "        assert!(true);\n"
                "    }\n"
                "}\n"
            )
            commit(repo, "configure exclusive Rust tests")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            (repo / "src" / "lib.rs").write_text(
                "pub fn fixed() -> bool { false }\n\n"
                "#[cfg(test)]\n"
                "mod tests {\n"
                "    #[test]\n"
                "    fn existing_rust_test() {\n"
                "        assert!(true);\n"
                "    }\n\n"
                "    #[test]\n"
                "    fn reproduces_bug() {\n"
                "        assert!(super::fixed());\n"
                "    }\n"
                "}\n"
            )
            commit(repo, "reproduce Rust bug")
            source = (repo / "src" / "lib.rs").read_text()
            (repo / "src" / "lib.rs").write_text(
                source.replace("pub fn fixed() -> bool { false }", "pub fn fixed() -> bool { true }")
            )
            commit(repo, "fix Rust bug")

            marker = root / "cargo-started"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            cargo = bin_dir / "cargo"
            cargo.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['REPRO_STARTED']).touch()\n"
                "args = sys.argv[1:]\n"
                "selector = args[1] if len(args) > 1 and args[1] != '--' else None\n"
                "selected = selector is None or (\n"
                "    selector == 'tests::reproduces_bug' if '--exact' in args\n"
                "    else selector in 'tests::reproduces_bug'\n"
                ")\n"
                "fixed = 'pub fn fixed() -> bool { true }' in Path('src/lib.rs').read_text()\n"
                "raise SystemExit(0 if not selected or fixed else 1)\n"
            )
            cargo.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "PYTHONPATH": str(ROOT),
                "REPRO_STARTED": str(marker),
            }
            env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
            report = repo / ".factory" / "gate-report.md"
            with lock.open("w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "factory",
                        "gate",
                        "--bug",
                        "--repro-test",
                        "reproduces_bug",
                        "--skip",
                        "conflict-markers,test",
                    ],
                    cwd=repo,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                try:
                    deadline = time.monotonic() + 10
                    waiting = False
                    while time.monotonic() < deadline and not marker.exists():
                        waiting = any(
                            row.get("kind") == "wait"
                            and row.get("wait", {}).get("reason") == "exclusive_resource"
                            for row in lifecycle.read_events(repo / ".factory" / "events.jsonl")
                        )
                        if waiting:
                            break
                        time.sleep(0.01)
                    self.assertTrue(waiting, "bug reproduction did not wait for the exclusive lock")
                    self.assertFalse(marker.exists(), "cargo started while the exclusive lock was held")
                finally:
                    fcntl.flock(held, fcntl.LOCK_UN)
                    output, _ = proc.communicate(timeout=15)

            self.assertEqual(proc.returncode, 0, output + report.read_text())
            self.assertIn("- bug-repro-before: EXPECTED FAIL", report.read_text())
            self.assertIn("- bug-repro-head: PASS", report.read_text())


class PythonParseErrorGateTest(unittest.TestCase):
    def test_invalid_python_fixture_at_base_does_not_block_bug_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            fixtures = repo / "fixtures" / "tests"
            fixtures.mkdir(parents=True)
            (fixtures / "invalid.py").write_text("def invalid(:\n")
            commit(repo, "add invalid parser fixture")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            add_repro(repo)
            (repo / "value.py").write_text("def value():\n    return True\n")
            commit(repo, "fix bug")

            code, report = run_gate(repo, "--bug", "--repro-test", TEST_ID)

            self.assertEqual(code, 0, report)
            self.assertIn("- bug-repro-before: EXPECTED FAIL", report)
            self.assertIn("- bug-repro-head: PASS", report)
            self.assertIn("- test: PASS", report)
            self.assertNotIn("- bug-repro: SKIP", report)

    def test_existing_python_test_made_unparseable_still_fails_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            add_repro(repo)
            (repo / "value.py").write_text("def value():\n    return True\n")
            existing = repo / "tests" / "test_existing.py"
            existing.write_text(existing.read_text() + "\ndef invalid(:\n")
            commit(repo, "fix bug but break existing test syntax")

            code, report = run_gate(repo, "--bug", "--repro-test", TEST_ID)

            self.assertEqual(code, 1, report)
            self.assertIn("- protected-tests: FAIL", report)
            self.assertIn("tests/test_existing.py:ExistingTest.test_existing: deleted", report)


if __name__ == "__main__":
    unittest.main()


class RustLifetimeGateTest(unittest.TestCase):
    SOURCE = (
        "pub fn f(s: &'static str) -> usize { s.len() }\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    use super::*;\n"
        "    #[test]\n"
        '    fn counts() { assert_eq!(f("ab"), 2); }\n'
        "    #[test]\n"
        "    fn chars() { assert_eq!('a', 'a'); }\n"
        "}\n"
    )

    def test_lifetime_and_character_literals_preserve_test_discovery(self) -> None:
        tests = gate_module._rust_tests("src/lib.rs", self.SOURCE)
        self.assertEqual(
            {record["display"] for record in tests.values()},
            {"src/lib.rs:counts", "src/lib.rs:chars"},
        )

    def test_character_mask_accepts_single_characters_and_escapes_not_lifetimes(self) -> None:
        literals = ["'a'", "'}'", "'λ'", r"'\n'", r"'\''", r"'\\'", r"'\x7f'", r"'\u{1F600}'"]
        for literal in literals:
            with self.subTest(literal=literal):
                self.assertIsNotNone(gate_module._RUST_MASK.fullmatch(literal))
        for text in ["'static", "'a, 'b", "'ab'", "'\n'", "'a\nb'", r"'\q'"]:
            with self.subTest(not_character=text):
                self.assertIsNone(gate_module._RUST_MASK.fullmatch(text))

    def test_ignore_or_deletion_with_lifetime_fails_unless_authorized(self) -> None:
        for change in ("ignore", "delete"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                repo = repository(Path(directory))
                source = repo / "src" / "lib.rs"
                source.write_text(self.SOURCE)
                commit(repo, "Rust lifetime and character fixture")
                git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
                counts = '    #[test]\n    fn counts() { assert_eq!(f("ab"), 2); }\n'
                source.write_text(self.SOURCE.replace(
                    counts, "    #[ignore]\n" + counts if change == "ignore" else ""
                ))
                commit(repo, f"{change} existing inline test")

                code, report = run_gate(repo, "--bug", "--no-repro-test", "protection fixture")
                self.assertEqual(code, 1, report)
                self.assertIn("- protected-tests: FAIL", report)
                self.assertIn(f"src/lib.rs:counts: {'modified' if change == 'ignore' else 'deleted'}", report)

                flags = dispatch.gate_flags(None, {"bug", config.LABEL_PROTECTED_OVERRIDE})
                self.assertTrue(flags["protected_override"])
                code, report = run_gate(
                    repo, "--bug", "--no-repro-test", "protection fixture", "--protected-override"
                )
                self.assertEqual(code, 0, report)

    def test_new_rust_reproduction_with_lifetime_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            source = repo / "src" / "lib.rs"
            source.write_text(self.SOURCE.replace("s.len()", "0"))
            commit(repo, "Rust bug with lifetime and characters")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            source.write_text(source.read_text().replace(
                "    use super::*;\n",
                "    use super::*;\n"
                "    #[test]\n"
                '    fn reproduces_bug() { assert_eq!(f("abc"), 3); }\n',
            ))
            commit(repo, "add Rust reproduction")
            source.write_text(source.read_text().replace("-> usize { 0 }", "-> usize { s.len() }"))
            commit(repo, "fix Rust bug")
            previous = Path.cwd()
            os.chdir(repo)
            try:
                gate_module.configure(config.load(repo))
                # A local runner exercises both checkouts without requiring a Rust toolchain.
                command = [
                    sys.executable, "-c",
                    ("from pathlib import Path; "
                     "raise SystemExit('-> usize { 0 }' in Path('src/lib.rs').read_text())"),
                ]
                with mock.patch.object(gate_module, "_repro_command", return_value=(command, False)):
                    results = gate_module.check_bug_reproduction("origin/main", "reproduces_bug", None)
            finally:
                os.chdir(previous)
            self.assertEqual(
                [(name, status) for name, status, _ in results],
                [("bug-repro-before", "EXPECTED FAIL"), ("bug-repro-head", "PASS")],
                results,
            )


class AuthenticatedTriageGateTest(unittest.TestCase):
    def flags(self, author: str, text: str, *, login: str = "factory-bot", labels=None) -> dict:
        from factory import onboard, triage

        with mock.patch.object(triage, "LOGIN", None), \
                mock.patch.object(onboard, "gh_login", return_value=login):
            return dispatch.gate_flags(
                None, {"bug"} if labels is None else labels,
                comments=[{"author": {"login": author}, "body": text}],
            )

    def test_authenticated_triage_reason_is_accepted_without_override_authority(self) -> None:
        flags = self.flags(
            "Factory-Bot",
            "Triage: hardware-only failure\n\nAgent brief: investigate\n"
            "No repro test: requires unavailable hardware\n"
            "factory-protected-override: not authorized by a comment",
        )
        self.assertEqual(flags, {
            "protected_override": False, "bug": True,
            "no_repro_test": "requires unavailable hardware",
        })

    def test_untrusted_or_non_triage_comments_cannot_exempt_reproduction(self) -> None:
        for author, text, login in [
            ("spoof", "Triage: copied prefix\nNo repro test: bypass", "factory-bot"),
            ("factory-bot", "Factory dispatcher escalating:\nNo repro test: copied handoff", "factory-bot"),
            ("factory-bot", "Triage: no reason\nNo repro test:   ", "factory-bot"),
            ("factory-bot", "Triage: no authenticated identity\nNo repro test: bypass", ""),
        ]:
            with self.subTest(author=author, text=text, login=login):
                self.assertEqual(
                    self.flags(author, text, login=login),
                    {"protected_override": False, "bug": True},
                )

    def test_body_reason_wins_and_handoff_reason_is_not_authority(self) -> None:
        from factory import triage

        with mock.patch.object(triage, "login", return_value="factory-bot"):
            comments = [{"author": {"login": "factory-bot"}, "body": "Triage: reason\nNo repro test: triage reason"}]
            self.assertEqual(dispatch.gate_flags(
                None, {"bug"}, "No repro test: ticket reason",
                "No repro test: worker reason", comments=comments,
            )["no_repro_test"], "ticket reason")
            self.assertEqual(dispatch.gate_flags(
                None, {"bug"}, "", "No repro test: worker reason", comments=[],
            ), {"protected_override": False, "bug": True})

    def test_non_bug_ignores_comment_exemptions_and_does_not_lookup_identity(self) -> None:
        from factory import triage

        with mock.patch.object(triage, "login") as login:
            flags = dispatch.gate_flags(
                None, set(), comments=[{
                    "author": {"login": "factory-bot"},
                    "body": "Triage: exemption\nNo repro test: hardware-only",
                }],
            )
        self.assertEqual(flags, {"protected_override": False})
        login.assert_not_called()
