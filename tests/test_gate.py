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


if __name__ == "__main__":
    unittest.main()
