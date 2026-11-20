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

    def test_receipts_are_isolated_between_worktrees(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, command, head = self.make_repo(directory)
            other = Path(directory) / "other"
            git(repo, "worktree", "add", "--detach", str(other), head)
            code, output, report = gate(other, "--focused-command", command)
            self.assertEqual(code, 0, report + output)
            self.assertIn(command, report)
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            self.assertIn("Focused tests: none recorded", report)
            self.assertNotIn(command, report)
            code, output, report = gate(other)
            self.assertEqual(code, 0, report + output)
            self.assertIn(command, report)

    def test_invalid_row_does_not_hide_later_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, command, head = self.make_repo(directory)
            (repo / ".factory").mkdir(exist_ok=True)
            path = repo / ".factory" / "focused-tests.jsonl"
            path.write_text('{"incomplete":\n' + json.dumps({"result": "PASS"}) + "\n")
            code, output, report = gate(repo, "--focused-command", command)
            self.assertEqual(code, 0, report + output)
            self.assertIn("invalid execution receipt", report)
            [line] = self.focused_lines(report)
            for text in (command, head, "PASS", "Ran 2 tests"):
                self.assertIn(text, line)

    def test_unsafe_receipt_paths_do_not_hang_or_redirect_writes(self):
        import os
        import subprocess

        from tests.test_gate import ROOT

        for kind in ("fifo", "symlink-fifo", "symlink-file", "oversized"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                repo, command, _ = self.make_repo(directory)
                (repo / ".factory").mkdir(exist_ok=True)
                path = repo / ".factory" / "focused-tests.jsonl"
                target = Path(directory) / "target"
                if kind in ("fifo", "symlink-fifo"):
                    os.mkfifo(path if kind == "fifo" else target)
                    if kind == "symlink-fifo":
                        path.symlink_to(target)
                elif kind == "symlink-file":
                    target.write_text("do not modify\n")
                    path.symlink_to(target)
                else:
                    path.write_bytes(b"x" * (1024 * 1024) + b"\n")
                env = {**os.environ, "PYTHONPATH": str(ROOT)}
                env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "factory", "gate", "--focused-command", command],
                        cwd=repo, env=env, capture_output=True, text=True, timeout=5, check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.fail(f"{kind} receipt blocked the gate")
                report = (repo / ".factory" / "gate-report.md").read_text()
                self.assertEqual(proc.returncode, 0, report + proc.stdout + proc.stderr)
                self.assertIn("Focused tests: evidence unavailable", report)
                if kind == "symlink-file":
                    self.assertEqual(target.read_text(), "do not modify\n")
                if kind == "oversized":
                    [line] = self.focused_lines(report)
                    self.assertIn(command, line)
                    self.assertLess(len(report), 10000)

    def test_reproduction_without_runner_summary_is_recorded(self):
        for mode in ("import-error", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                repo, _, _ = self.make_repo(directory)
                test = repo / "tests" / "test_repro.py"
                test.write_text(
                    ("import missing_dependency\n" if mode == "import-error"
                     else "import time\ntime.sleep(60)\n")
                    + "import unittest\nclass Repro(unittest.TestCase):\n"
                    "    def test_value(self):\n        self.assertTrue(True)\n"
                )
                git(repo, "add", "tests/test_repro.py")
                git(repo, "commit", "-qm", "reproduce")
                before = git(repo, "rev-parse", "HEAD")
                git(repo, "commit", "--allow-empty", "-qm", "claimed fix")
                head = git(repo, "rev-parse", "HEAD")
                code, output, report = gate(
                    repo, "--bug", "--repro-test", "tests.test_repro.Repro.test_value",
                    "--check-timeout", "1",
                )
                self.assertEqual(code, 1, report + output)
                lines = self.focused_lines(report)
                self.assertEqual(len(lines), 2 if mode == "import-error" else 1, report)
                for line in lines:
                    for text in ("-c", "tests/test_repro.py", "FAIL", "counts unavailable"):
                        self.assertIn(text, line)
                self.assertIn(before, lines[0])
                self.assertIn("STALE", lines[0])
                if mode == "import-error":
                    self.assertIn(head, lines[1])
                    self.assertNotIn("STALE", lines[1])

    def test_focused_timeout_preserves_unknown_lifecycle_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _, _ = self.make_repo(directory)
            command = shlex.join([sys.executable, "-c", "import time; time.sleep(60)"])
            code, output, report = gate(repo, "--focused-command", command, "--check-timeout", "1")
            self.assertEqual(code, 1, report + output)
            events = [json.loads(line) for line in (repo / ".factory" / "events.jsonl").read_text().splitlines()]
            [exit_event] = [row for row in events if row["stage"] == "gate" and row["kind"] == "exit"]
            self.assertEqual(exit_event["outcome"], "unknown")
            self.assertEqual(exit_event["reason"], "check_timeout")

    def test_failed_append_keeps_current_execution_in_report(self):
        import fcntl

        for configured in (False, True):
            for failing in (False, True):
                with self.subTest(configured=configured, failing=failing), tempfile.TemporaryDirectory() as directory:
                    repo, command, head = self.make_repo(directory, configured=configured, failing=failing)
                    path = repo / ".factory" / "focused-tests.jsonl"
                    path.parent.mkdir(exist_ok=True)
                    with path.open("w") as locked:
                        fcntl.flock(locked, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        args = [] if configured else ["--focused-command", command]
                        code, output, report = gate(repo, *args)
                        self.assertEqual(code, int(failing), report + output)
                        [line] = self.focused_lines(report)
                        for text in (command, head, "FAIL" if failing else "PASS", "Ran 2 tests"):
                            self.assertIn(text, line)
                        self.assertNotIn("STALE", line)
                        self.assertNotIn("Focused tests: none recorded", report)
                        self.assertEqual(path.read_text(), "")

    def test_unrecognized_configured_command_always_has_a_receipt(self):
        for mode, status in (("pass", 0), ("fail", 3), ("timeout", 124), ("missing", 127)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / "repo" / "ctest"
                command = [str(executable), "-R", "focused_test", "--output-on-failure"]
                toml = TOML + '\n[[gate.check]]\nname = "focused"\nrun = ' + json.dumps(command) + '\n'
                repo = repo_with_test(Path(directory), toml)
                if mode != "missing":
                    executable.write_text(
                        f"#!{sys.executable}\n"
                        + ("import time\ntime.sleep(60)\n" if mode == "timeout"
                           else f"print('custom runner')\nraise SystemExit({status})\n")
                    )
                    executable.chmod(0o755)
                head = git(repo, "rev-parse", "HEAD")
                code, output, report = gate(repo, "--check-timeout", "1")
                self.assertEqual(code, int(status != 0), report + output)
                [line] = self.focused_lines(report)
                for text in (
                    shlex.join(command), "PASS" if status == 0 else "FAIL",
                    "counts unavailable", f"exit={status}", f'head="{head}"', f'end-head="{head}"',
                ):
                    self.assertIn(text, line)
                self.assertNotIn("STALE", line)
                self.assertNotIn("Focused tests: none recorded", report)

    def test_full_suite_receipt_is_separate_from_focused_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
            toml = TOML + '\n[[gate.check]]\nname = "test"\nrun = ' + json.dumps(command) + '\n'
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_suite.py").write_text(
                "import unittest\nclass Suite(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertEqual(2 + 2, 4)\n"
            )
            git(repo, "add", "tests/test_suite.py")
            git(repo, "commit", "-qm", "suite test")
            head = git(repo, "rev-parse", "HEAD")
            code, output, report = gate(repo)
            self.assertEqual(code, 0, report + output)
            [line] = [line for line in report.splitlines() if line.startswith("Full suite:")]
            for text in (shlex.join(command), "PASS", "Ran 1 test", head, "exit=0"):
                self.assertIn(text, line)
            self.assertNotIn("STALE", line)
            self.assertEqual(self.focused_lines(report), [])
            self.assertIn("Focused tests: none recorded", report.splitlines())


if __name__ == "__main__":
    unittest.main()
