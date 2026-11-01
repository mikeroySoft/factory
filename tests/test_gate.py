"""#228: `[gate].add_only_paths` lets a worker add tests without touching existing ones.

Run: python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOML = '[gate]\nprotected_paths = [".factory.toml", "checks/**"]\nadd_only_paths = ["tests/**"]\n'
ADD_ONLY = "- protected-paths: FAIL (add-only path removed or changed existing lines)"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def gate(cwd: Path, *args: str) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("FACTORY_LIFECYCLE_CONTEXT", None)  # #253: never journal into a worker's live ledger
    proc = subprocess.run([sys.executable, "-m", "factory", "gate", *args],
                          cwd=cwd, capture_output=True, text=True, env=env, check=False)
    report = cwd / ".factory" / "gate-report.md"
    return proc.returncode, proc.stdout + proc.stderr, report.read_text() if report.exists() else ""


def repo_with_test(tmp: Path, toml: str = TOML) -> Path:
    """A base ref (`origin/main`) with `toml` and one committed test file of two lines."""
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / ".factory.toml").write_text(toml)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def commit(repo: Path, message: str = "worker") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


class AddOnlyPathsTest(unittest.TestCase):
    def test_new_test_file_passes_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            (repo / "tests" / "test_new.py").write_text("def test_b():\n    assert True\n")
            commit(repo)
            code, out, report = gate(repo)
            self.assertEqual(code, 0, report + out)
            self.assertNotIn("- protected-paths: FAIL", report)

    def test_appending_to_existing_test_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            with (repo / "tests" / "test_old.py").open("a") as f:
                f.write("\n\ndef test_b():\n    assert True\n")
            commit(repo)
            code, out, report = gate(repo)
            self.assertEqual(code, 0, report + out)
            self.assertNotIn("- protected-paths: FAIL", report)

    def test_editing_deleting_renaming_or_chmod_fails_with_add_only_reason(self) -> None:
        cases = {
            "edit": lambda p: p.write_text("def test_a():\n    assert False\n"),
            "delete line": lambda p: p.write_text("def test_a():\n"),
            "delete file": lambda p: p.unlink(),
            "rename": lambda p: p.rename(p.with_name("test_renamed.py")),
            "mode": lambda p: p.chmod(p.stat().st_mode | 0o111),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as d:
                repo = repo_with_test(Path(d))
                mutate(repo / "tests" / "test_old.py")
                commit(repo)
                code, _, report = gate(repo)
                self.assertEqual(code, 1, report)
                self.assertIn(ADD_ONLY, report)
                self.assertIn("tests/test_old.py", report)
                if name in ("edit", "delete line"):
                    self.assertIn("-    assert True", report)
                # The human escape hatch still applies.
                code, _, report = gate(repo, "--protected-override")
                self.assertEqual(code, 0, report)

    def test_fully_protected_paths_still_fail_on_any_change(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            (repo / "checks").mkdir()
            (repo / "checks" / "new.txt").write_text("added\n")
            commit(repo)
            code, _, report = gate(repo)
            self.assertEqual(code, 1, report)
            self.assertIn("- protected-paths: FAIL\n", report)
            self.assertNotIn("add-only", report)

    def test_worker_cannot_widen_add_only_allowance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), '[gate]\nprotected_paths = [".factory.toml", "checks/**"]\n')
            (repo / ".factory.toml").write_text(
                '[gate]\nprotected_paths = [".factory.toml"]\nadd_only_paths = ["checks/**", "tests/**"]\n')
            (repo / "checks").mkdir()
            (repo / "checks" / "new.txt").write_text("added\n")
            commit(repo)
            code, _, report = gate(repo)
            self.assertEqual(code, 1, report)
            self.assertIn("- protected-paths: FAIL\n", report)
            self.assertIn("+++ b/.factory.toml", report)
            # A pure addition under the worker's own add-only glob is still a full-protection hit.
            self.assertIn("+++ b/checks/new.txt", report)

    def test_upstream_refs_exempt_upstream_content_but_not_worker_edits(self) -> None:
        """#151: a sync worker gets no bypass. Content equal to an `--upstream` ref is
        upstream's; anything else under a protected path is the worker's and still fails."""
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), TOML + '[leak_scan]\nextra = ["codename"]\n')
            # Upstream rewrote an existing test (not additive) and ships a word the leak scan flags.
            (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert 1\n")
            (repo / "NOTES.md").write_text("codename upstream notes\n")
            commit(repo, "upstream")
            tip = git(repo, "rev-parse", "HEAD")
            code, _, report = gate(repo)
            self.assertEqual(code, 1, report)
            self.assertIn(ADD_ONLY, report)
            code, out, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 0, report + out)
            self.assertIn("- leak-scan: PASS", report)
            # The worker edits the same test and adds its own flagged word: both are caught.
            (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert 2\n")
            commit(repo)
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 1, report)
            self.assertIn(ADD_ONLY, report)
            self.assertIn("+    assert 2", report)
            # An unknown ref fails closed instead of silently exempting nothing.
            code, _, report = gate(repo, "--upstream", "0" * 40)
            self.assertEqual(code, 1, report)
            self.assertIn("- protected-paths: FAIL\n", report)
            git(repo, "reset", "-q", "--hard", tip)
            (repo / "worker.md").write_text("codename\n")
            commit(repo)
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 1, report)
            self.assertIn("worker.md:1: [codename]", report)
            self.assertNotIn("NOTES.md", report)

    def test_upstream_tip_does_not_exempt_fork_only_files(self) -> None:
        """#151 round 2: a path upstream never changed is the fork's. Matching the tip
        (both absent, or reset to upstream's version) does not make a worker edit upstream's."""
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            root = git(repo, "rev-parse", "HEAD")
            (repo / "tests" / "test_fork.py").write_text("def test_fork():\n    assert True\n")
            (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert True\n# fork line\n")
            commit(repo, "fork")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(repo, "checkout", "-q", root)
            (repo / "NOTES.md").write_text("upstream\n")
            commit(repo, "upstream")
            tip = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "main")
            git(repo, "merge", "-q", "--no-ff", "-m", "sync", tip)
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 0, report)
            git(repo, "rm", "-q", "tests/test_fork.py")
            git(repo, "checkout", "-q", tip, "--", "tests/test_old.py")
            git(repo, "commit", "-q", "-m", "worker drops fork tests")
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 1, report)
            self.assertIn(ADD_ONLY, report)
            self.assertIn("--- a/tests/test_fork.py", report)
            self.assertIn("-# fork line", report)


class GateOutputEncodingTest(unittest.TestCase):
    def test_configured_check_replaces_non_utf8_output(self) -> None:
        toml = TOML + (
            "\n[[gate.check]]\n"
            'name = "bytes"\n'
            f'run = ["{sys.executable}", "-c", '
            '"import os; os.write(1, bytes([255])); print(\'done\')"]\n'
            "exclusive = false\n"
        )
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), toml)
            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")
            self.assertEqual(code, 0, report + out)
            self.assertIn("- bytes: PASS", report)

    def test_failed_check_replaces_non_utf8_output(self) -> None:
        toml = TOML + (
            "\n[[gate.check]]\n"
            'name = "bytes"\n'
            f'run = ["{sys.executable}", "-c", '
            '"import os; os.write(2, bytes([255, 254]) + b\' failed\\\\n\'); raise SystemExit(7)"]\n'
            "exclusive = false\n"
        )
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), toml)
            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")
            self.assertEqual(code, 1, report + out)
            self.assertIn("- bytes: FAIL", report)
            self.assertIn("\ufffd\ufffd failed", report)

    def test_timeout_replaces_non_utf8_output(self) -> None:
        from unittest import mock

        from factory import gate as gate_module

        command = [
            sys.executable,
            "-c",
            "import os, time; os.write(1, bytes([255])); time.sleep(30)",
        ]
        with mock.patch.object(gate_module, "CHECK_TIMEOUT", 2):
            result = gate_module.timed(command)
        self.assertEqual(result.returncode, 124)
        self.assertIn("\ufffd", result.stdout)
        self.assertIn("check timed out", result.stdout)

    def test_leak_scan_finds_pattern_beside_non_utf8_filename(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), TOML + '\n[leak_scan]\nextra = ["codename"]\n')
            git(repo, "config", "core.quotePath", "false")
            descriptor = os.open(
                os.path.join(os.fsencode(repo), b"\xff-leak.txt"),
                os.O_WRONLY | os.O_CREAT,
                0o600,
            )
            os.write(descriptor, b"codename\n")
            os.close(descriptor)
            commit(repo)

            code, out, report = gate(repo, "--skip", "conflict-markers")

            self.assertEqual(code, 1, report + out)
            self.assertIn("- leak-scan: FAIL", report)
            self.assertIn("\ufffd-leak.txt:1: [codename] codename", report)



class FailedTestEvidenceTest(unittest.TestCase):
    def test_failed_unittest_and_pytest_ids_are_journaled(self) -> None:
        import json

        output = (
            "print('FAIL: test_port "
            "(tests.test_status.StatusTest.test_port)'); "
            "print('FAILED tests/test_cli.py::CliTest::test_help - assertion'); "
            "raise SystemExit(1)"
        )
        toml = TOML + (
            "\n[[gate.check]]\n"
            'name = "tests"\n'
            f"run = [{json.dumps(sys.executable)}, \"-c\", {json.dumps(output)}]\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)

            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")

            self.assertEqual(code, 1, report + out)
            rows = [
                json.loads(line)
                for line in (repo / ".factory" / "events.jsonl").read_text().splitlines()
            ]
            [result] = [
                row for row in rows
                if row.get("stage") == "gate-check"
                and row.get("kind") == "result"
                and row.get("check") == "tests"
            ]
            self.assertEqual(result["failed_tests"], [
                "tests.test_status.StatusTest.test_port",
                "tests/test_cli.py::CliTest::test_help",
            ])

    def test_parses_unittest_subtest_failure_id(self) -> None:
        from factory import gate as factory_gate

        output = """FAIL: test_x (mod.C.test_x) (i=1)
ERROR: test_y (mod.C.test_y) (name='a.b')
FAIL: test_z (mod.C.test_z) (a=1, b=2)
"""
        self.assertEqual(factory_gate.failed_test_ids(output), [
            "mod.C.test_x",
            "mod.C.test_y",
            "mod.C.test_z",
        ])

    def test_parses_cargo_libtest_failure_id(self) -> None:
        from factory import gate as factory_gate

        output = """running 1 test
---- comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone stdout ----
thread 'comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone' panicked

failures:
    comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone

test result: FAILED. 0 passed; 1 failed
"""
        self.assertEqual(factory_gate.failed_test_ids(output), [
            "comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone",
        ])

    def _failed_output(self, output: str, name: str = "tests") -> tuple[str, str, dict]:
        import json

        script = f"import sys; sys.stdout.write({output!r}); raise SystemExit(1)"
        toml = TOML + (
            "\n[[gate.check]]\n"
            f"name = {json.dumps(name)}\n"
            f"run = [{json.dumps(sys.executable)}, \"-c\", {json.dumps(script)}]\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")
            rows = [
                json.loads(line)
                for line in (repo / ".factory" / "events.jsonl").read_text().splitlines()
            ]
        self.assertEqual(code, 1, report + out)
        [result] = [
            row for row in rows
            if row.get("stage") == "gate-check"
            and row.get("kind") == "result"
            and row.get("check") == name
        ]
        return out, report, result

    def test_pytest_summary_uses_full_output_before_tail(self) -> None:
        output = "\n".join([
            "____________________________ test_first ____________________________",
            ">       assert 1 == 2",
            "E       AssertionError: assert 1 == 2",
            "E       assert 1 == 2",
            "tests/test_math.py:12: AssertionError",
            *(f"later output {index}" for index in range(90)),
            "FAILED tests/test_math.py::test_first - AssertionError: assert 1 == 2",
        ])

        _, report, _ = self._failed_output(output)

        summary, tail = report.split("```", 1)
        self.assertIn("## tests failure\n\nFailing tests:\n", summary)
        self.assertIn("- `tests/test_math.py::test_first`", summary)
        self.assertIn("`tests/test_math.py:12`", summary)
        self.assertIn("AssertionError: assert 1 == 2", summary)
        self.assertIn("Expected/actual: assert 1 == 2", summary)
        self.assertNotIn("tests/test_math.py:12", tail)

    def test_unittest_fail_and_error_summaries_include_location_and_message(self) -> None:
        cases = [
            (
                (
                    "FAIL: test_value (tests.test_math.MathTest.test_value)\n"
                    "Traceback (most recent call last):\n"
                    '  File "tests/test_math.py", line 17, in test_value\n'
                    '    self.assertEqual("expected", "actual")\n'
                    "AssertionError: 'expected' != 'actual'\n"
                    "- expected\n"
                    "+ actual\n"
                ),
                "tests.test_math.MathTest.test_value",
                "tests/test_math.py:17",
                "AssertionError: 'expected' != 'actual'",
            ),
            (
                (
                    "ERROR: test_load (tests.test_worker.WorkerTest.test_load)\n"
                    "Traceback (most recent call last):\n"
                    '  File "tests/test_worker.py", line 21, in test_load\n'
                    '    raise RuntimeError("broken")\n'
                    "RuntimeError: broken\n"
                ),
                "tests.test_worker.WorkerTest.test_load",
                "tests/test_worker.py:21",
                "RuntimeError: broken",
            ),
        ]
        for output, test_id, location, message in cases:
            with self.subTest(test_id=test_id):
                _, report, _ = self._failed_output(output)
                summary = report.split("```", 1)[0]
                self.assertIn(f"- `{test_id}`", summary)
                self.assertIn(f"`{location}`", summary)
                self.assertIn(message, summary)

    def test_cargo_summary_includes_panic_location_and_assertion(self) -> None:
        output = """running 1 test
---- crate::tests::compares_values stdout ----
thread 'crate::tests::compares_values' panicked at src/lib.rs:42:5:
assertion `left == right` failed
  left: 1
 right: 2

failures:
    crate::tests::compares_values

test result: FAILED. 0 passed; 1 failed
"""

        _, report, _ = self._failed_output(output)

        summary = report.split("```", 1)[0]
        self.assertIn("- `crate::tests::compares_values`", summary)
        self.assertIn("`src/lib.rs:42`", summary)
        self.assertIn("assertion `left == right` failed", summary)
        self.assertIn("left: 1", summary)
        self.assertIn("right: 2", summary)

    def test_multiple_failures_are_listed_in_report_order_with_first_detail(self) -> None:
        output = """____________________________ test_first ____________________________
E       AssertionError: assert 1 == 2
tests/test_math.py:12: AssertionError
FAILED tests/test_math.py::test_first - AssertionError: assert 1 == 2
FAILED tests/test_other.py::test_second - ValueError: bad value
"""

        _, report, _ = self._failed_output(output)

        summary = report.split("```", 1)[0]
        first = "tests/test_math.py::test_first"
        second = "tests/test_other.py::test_second"
        self.assertLess(summary.index(first), summary.index(second))
        self.assertEqual(summary.count(first), 1)
        self.assertEqual(summary.count(second), 1)
        self.assertIn("`tests/test_math.py:12`", summary)

    def test_unparseable_test_failure_keeps_tail_with_explicit_note(self) -> None:
        output = "\n".join(f"opaque failure line {index}" for index in range(85))

        _, report, result = self._failed_output(output)

        self.assertIn("Failing tests: could not identify failing tests.\n\n```", report)
        self.assertNotIn("opaque failure line 4\n", report)
        self.assertIn("opaque failure line 5\n", report)
        self.assertNotIn("failure_summary", result)

    def test_journal_records_first_failure_summary_with_location(self) -> None:
        output = """FAIL: test_value (tests.test_math.MathTest.test_value)
Traceback (most recent call last):
  File "tests/test_math.py", line 17, in test_value
    self.assertEqual("expected", "actual")
AssertionError: 'expected' != 'actual'
- expected
+ actual
"""

        _, _, result = self._failed_output(output)

        self.assertEqual(result["failed_tests"], ["tests.test_math.MathTest.test_value"])
        self.assertEqual(result["failure_summary"], {
            "test": "tests.test_math.MathTest.test_value",
            "location": "tests/test_math.py:17",
            "message": "AssertionError: 'expected' != 'actual'",
            "expected_actual": ["- expected", "+ actual"],
        })

    def test_non_test_failure_report_is_unchanged(self) -> None:
        _, report, result = self._failed_output("lint exploded\n", name="lint")

        self.assertIn("## lint failure\n```\nlint exploded\n```", report)
        self.assertNotIn("Failing tests", report)
        self.assertNotIn("failure_summary", result)

    def test_pytest_summary_is_scoped_to_first_failed_test(self) -> None:
        output = """________________ ERROR at setup of test_setup _________________
E       RuntimeError: setup broke
tests/test_setup.py:5: RuntimeError
____________________________ test_first ____________________________
E       AssertionError: assert 1 == 2
E       assert 1 == 2
tests/test_math.py:12: AssertionError
____________________________ test_second ___________________________
E       ValueError: later failure
tests/test_other.py:22: ValueError
FAILED tests/test_math.py::test_first - AssertionError: assert 1 == 2
FAILED tests/test_other.py::test_second - ValueError: later failure
"""

        _, report, result = self._failed_output(output)

        summary = report.split("```", 1)[0]
        self.assertIn("`tests/test_math.py:12`", summary)
        self.assertIn("AssertionError: assert 1 == 2", summary)
        self.assertNotIn("tests/test_setup.py:5", summary)
        self.assertNotIn("RuntimeError: setup broke", summary)
        self.assertNotIn("tests/test_other.py:22", summary)
        self.assertNotIn("ValueError: later failure", summary)
        self.assertEqual(result["failure_summary"]["location"], "tests/test_math.py:12")

    def test_unittest_error_accepts_generic_exception_name(self) -> None:
        output = """ERROR: test_load (tests.test_worker.WorkerTest.test_load)
Traceback (most recent call last):
  File "tests/test_worker.py", line 21, in test_load
    raise Exception("broken")
Exception: broken
"""

        _, report, result = self._failed_output(output)

        summary = report.split("```", 1)[0]
        self.assertIn("`tests/test_worker.py:21`", summary)
        self.assertIn("Exception: broken", summary)
        self.assertEqual(result["failure_summary"]["message"], "Exception: broken")

    def test_mutation_failure_with_test_ids_is_unchanged(self) -> None:
        output = """FAIL: test_value (tests.test_math.MathTest.test_value)
Traceback (most recent call last):
  File "tests/test_math.py", line 17, in test_value
    self.assertEqual(1, 2)
AssertionError: 1 != 2
"""

        _, report, result = self._failed_output(output, name="mutation")

        self.assertIn(f"## mutation failure\n```\n{output}```", report)
        self.assertNotIn("Failing tests", report)
        self.assertNotIn("failure_summary", result)
        self.assertEqual(result["failed_tests"], ["tests.test_math.MathTest.test_value"])

    def test_pytest_helper_frame_keeps_terminal_failure_evidence(self) -> None:
        output = """____________________________ test_helper ____________________________
tests/test_math.py:12: in test_helper
    compare_values()
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
tests/helpers.py:27: in compare_values
>       raise RuntimeError("broken")
E       RuntimeError: broken
FAILED tests/test_math.py::test_helper - RuntimeError: broken
"""

        _, report, result = self._failed_output(output)

        self.assertIn("`tests/helpers.py:27`", report)
        self.assertIn("RuntimeError: broken", report)
        self.assertEqual(result["failure_summary"]["location"], "tests/helpers.py:27")

    def test_pytest_nested_parameter_id_preserves_double_colon(self) -> None:
        output = """________________ Outer.Inner.test_value[pkg::symbol] _________________
>       raise ValueError("bad symbol")
E       ValueError: bad symbol
tests/test_symbols.py:31: ValueError
FAILED tests/test_symbols.py::Outer::Inner::test_value[pkg::symbol] - ValueError: bad symbol
"""

        _, report, result = self._failed_output(output)

        test_id = "tests/test_symbols.py::Outer::Inner::test_value[pkg::symbol]"
        self.assertIn(f"- `{test_id}` — `tests/test_symbols.py:31`", report)
        self.assertEqual(result["failure_summary"]["test"], test_id)

    def test_unittest_chained_error_uses_terminal_exception_and_location(self) -> None:
        output = """ERROR: test_wrap (tests.test_worker.WorkerTest.test_wrap)
Traceback (most recent call last):
  File "factory/worker.py", line 8, in load
    raise ValueError("root")
ValueError: root

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "tests/test_worker.py", line 44, in test_wrap
    raise RuntimeError("wrapped") from exc
RuntimeError: wrapped
"""

        _, report, result = self._failed_output(output)

        self.assertIn("`tests/test_worker.py:44`", report)
        self.assertIn("RuntimeError: wrapped", report)
        self.assertNotIn("`factory/worker.py:8`", report)
        self.assertEqual(result["failure_summary"]["location"], "tests/test_worker.py:44")
        self.assertEqual(result["failure_summary"]["message"], "RuntimeError: wrapped")

    def test_pytest_message_uses_error_then_source_fallback(self) -> None:
        from factory import gate as factory_gate

        cases = [
            (
                """____________________________ test_value ____________________________
>       assert 1 == 2
E       AssertionError: values differ
E       assert 1 == 2
tests/test_value.py:9: AssertionError
""",
                "AssertionError: values differ",
                ["assert 1 == 2"],
            ),
            (
                """____________________________ test_value ____________________________
>       raise RuntimeError("broken")
tests/test_value.py:9: RuntimeError
""",
                'raise RuntimeError("broken")',
                [],
            ),
        ]
        for output, message, expected_actual in cases:
            with self.subTest(message=message):
                summary = factory_gate.first_failure_summary(
                    output, "tests/test_value.py::test_value"
                )
                self.assertEqual(summary, {
                    "test": "tests/test_value.py::test_value",
                    "location": "tests/test_value.py:9",
                    "message": message,
                    "expected_actual": expected_actual,
                })

    def test_failure_summary_details_only_the_first_test(self) -> None:
        from factory import gate as factory_gate

        lines = factory_gate.failure_summary_lines(
            ["tests/test_a.py::test_a", "tests/test_b.py::test_b"],
            {
                "location": "tests/test_a.py:7",
                "message": "AssertionError: mismatch",
                "expected_actual": ["expected: 1", "actual: 2"],
            },
        )

        self.assertEqual(lines, [
            "Failing tests:",
            "- `tests/test_a.py::test_a` — `tests/test_a.py:7`: AssertionError: mismatch",
            "  Expected/actual: expected: 1 | actual: 2",
            "- `tests/test_b.py::test_b`",
        ])

if __name__ == "__main__":
    unittest.main()
