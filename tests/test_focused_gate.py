"""Focused executions remain visible, with their original revision, across gate runs."""
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_gate import TOML, gate, git, repo_with_test


class FocusedGateTest(unittest.TestCase):
    def make_repo(self, directory, *, configured=False, failing=False):
        command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_focus.py"]
        toml = TOML
        if configured:
            toml += '\n[[gate.check]]\nname = "focused"\nrun = ' + json.dumps(command) + '\n'
        repo = repo_with_test(Path(directory), toml)
        (repo / "tests" / "test_focus.py").write_text(
            "import unittest\nclass Focus(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertTrue(True)\n"
            f"    def test_two(self):\n        self.assertTrue({not failing})\n"
        )
        git(repo, "add", "tests/test_focus.py")
        git(repo, "commit", "-qm", "focused tests")
        return repo, shlex.join(command), git(repo, "rev-parse", "HEAD")

    def focused_lines(self, report):
        return [line for line in report.splitlines() if line.startswith("Focused test:")]

    def test_configured_focused_command_reports_result_and_head(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, command, head = self.make_repo(directory, configured=True)
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            [line] = self.focused_lines(report)
            self.assertIn(command, line)
            self.assertIn("PASS", line)
            self.assertIn("Ran 2 tests", line)
            self.assertIn(head, line)
            self.assertNotIn("STALE", line)

    def test_worker_receipt_is_retained_and_flagged_after_head_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, command, head = self.make_repo(directory)
            code, output, report = gate(repo, "--focused-command", command)
            self.assertEqual(code, 0, report + output)
            [line] = self.focused_lines(report)
            self.assertIn(command, line)
            self.assertIn(head, line)
            self.assertIn("PASS", line)
            self.assertIn("Ran 2 tests", line)
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            self.assertIn(line, report.splitlines())
            git(repo, "commit", "--allow-empty", "-qm", "new head")
            current = git(repo, "rev-parse", "HEAD")
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            [stale] = self.focused_lines(report)
            self.assertIn("STALE", stale)
            self.assertIn(head, stale)
            self.assertIn(current, stale)
            self.assertIn(command, stale)

    def test_failed_command_has_counts_and_fails_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, command, head = self.make_repo(directory, failing=True)
            code, output, report = gate(repo, "--focused-command", command)
            self.assertEqual(code, 1, report + output)
            [line] = self.focused_lines(report)
            for text in (command, "FAIL", "Ran 2 tests", "failures=1", head):
                self.assertIn(text, line)

    def test_no_focused_command_is_explicit_even_on_protected_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _, _ = self.make_repo(directory)
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            self.assertIn("Focused tests: none recorded", report.splitlines())
            (repo / ".factory.toml").write_text(TOML + "# protected edit\n")
            git(repo, "add", ".factory.toml")
            git(repo, "commit", "-qm", "protected edit")
            code, output, report = gate(repo)
            self.assertEqual(code, 1, report + output)
            self.assertIn("Focused tests: none recorded", report.splitlines())

    def test_unrecognized_counts_are_not_invented_and_command_stays_one_line(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _, head = self.make_repo(directory)
            command = shlex.join([sys.executable, "-c", "print('custom runner')"]) + "\n"
            code, output, report = gate(repo, "--focused-command", command)
            self.assertEqual(code, 0, report + output)
            [line] = self.focused_lines(report)
            self.assertIn(json.dumps(command, ensure_ascii=False), line)
            self.assertIn("counts unavailable", line)
            self.assertIn(head, line)

    def test_forged_same_head_receipt_is_unverified_worker_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, command, head = self.make_repo(directory)
            (repo / ".factory").mkdir(exist_ok=True)
            (repo / ".factory" / "focused-tests.jsonl").write_text(json.dumps({
                "command": command, "head": head, "end_head": head,
                "result": "PASS", "counts": "Ran 91 tests; OK", "exit": 0,
            }) + "\n")
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            [line] = self.focused_lines(report)
            for text in ("worker-recorded / unverified", command, head, "Ran 91 tests"):
                self.assertIn(text, line)
            self.assertNotIn("STALE", line)

    def test_absolute_pytest_failures_without_summary_are_recorded(self):
        for mode, status in (("exit", 3), ("timeout", 124), ("missing", 127)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / "repo" / ".venv" / "bin" / "pytest"
                command = [str(executable), "tests/test_focus.py"]
                toml = TOML + '\n[[gate.check]]\nname = "focused"\nrun = ' + json.dumps(command) + '\n'
                repo = repo_with_test(Path(directory), toml)
                if mode != "missing":
                    executable.parent.mkdir(parents=True)
                    executable.write_text(
                        f"#!{sys.executable}\n"
                        + ("import time\ntime.sleep(60)\n" if mode == "timeout" else "raise SystemExit(3)\n")
                    )
                    executable.chmod(0o755)
                head = git(repo, "rev-parse", "HEAD")
                code, output, report = gate(repo, "--check-timeout", "1")
                self.assertEqual(code, 1, report + output)
                [line] = self.focused_lines(report)
                for text in (shlex.join(command), "FAIL", "counts unavailable", f"exit={status}", head):
                    self.assertIn(text, line)


if __name__ == "__main__":
    unittest.main()
