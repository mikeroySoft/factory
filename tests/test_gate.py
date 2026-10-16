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


if __name__ == "__main__":
    unittest.main()
