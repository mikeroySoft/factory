from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import config

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = "def test_old():\n    assert True\n\n\ndef test_kept():\n    assert 1\n"
APPROVED = "def test_old():\n    assert 'approved'\n\n\ndef test_kept():\n    assert 1\n"


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, input=stdin
    ).stdout.strip()


def blob(repo: Path, content: str) -> str:
    return git(repo, "hash-object", "--stdin", stdin=content)


def repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / config.CONFIG_NAME).write_text(
        '[gate]\nprotected_paths = [".factory.toml"]\nadd_only_paths = ["tests/**"]\n'
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_old.py").write_text(ORIGINAL)
    (repo / "tests" / "test_other.py").write_text("def test_other():\n    assert True\n")
    (repo / "tests" / "data.bin").write_bytes(b"\0\1\2")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def commit(repo: Path, message: str = "edit") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def run_gate(repo: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "factory", "gate", "--bug", "--no-repro-test", "allow fixture", *args],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    report = repo / ".factory" / "gate-report.md"
    return proc.returncode, (report.read_text() if report.exists() else "") + proc.stdout + proc.stderr


class AddOnlyAllowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = repository(Path(self.temp.name))
        self.base = git(self.repo, "rev-parse", "HEAD")

    def gate(self, *allows: str, base: str | None = None) -> tuple[int, str]:
        args = [arg for allow in allows for arg in ("--add-only-allow", allow)]
        return run_gate(self.repo, *args, "--add-only-allow-base", base or self.base)

    def approved(self) -> str:
        return f"tests/test_old.py={blob(self.repo, APPROVED)}"

    def assert_refused(self, code: int, report: str) -> None:
        self.assertEqual(code, 1, report)
        self.assertIn("- protected-paths: FAIL", report)

    def test_exact_approved_change_passes_both_checks(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        code, report = self.gate(self.approved())
        self.assertEqual(code, 0, report)
        self.assertNotIn("FAIL", report)

    def test_without_allow_the_same_change_still_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        code, report = run_gate(self.repo)
        self.assert_refused(code, report)

    def test_unrelated_add_only_edit_still_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        (self.repo / "tests" / "test_other.py").write_text("def test_other():\n    assert False\n")
        commit(self.repo)
        code, report = self.gate(self.approved())
        self.assert_refused(code, report)
        self.assertIn("tests/test_other.py", report)

    def test_unrelated_function_change_fails_protected_tests(self) -> None:
        # test_other.py gains a line in place of nothing: additive, so only protected-tests can catch it.
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        (self.repo / "tests" / "test_other.py").write_text("def test_other():\n    assert True\n    assert 2\n")
        commit(self.repo)
        code, report = self.gate(self.approved())
        self.assertEqual(code, 1, report)
        self.assertIn("- protected-tests: FAIL", report)
        self.assertIn("test_other", report)
        self.assertNotIn("tests/test_old.py:test_old", report)

    def test_one_byte_drift_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED.replace("approved", "approveD"))
        commit(self.repo)
        self.assert_refused(*self.gate(self.approved()))

    def test_stale_blob_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        stale = self.approved()
        (self.repo / "tests" / "test_old.py").write_text(APPROVED + "\n\ndef test_new():\n    assert 3\n")
        commit(self.repo, "later edit")
        self.assert_refused(*self.gate(stale))

    def test_base_mismatch_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        head = git(self.repo, "rev-parse", "HEAD")
        self.assert_refused(*self.gate(self.approved(), base=head))

    def test_dirty_worktree_substitution_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        (self.repo / "tests" / "test_old.py").write_text(APPROVED + "# substituted\n")
        self.assert_refused(*self.gate(self.approved()))

    def test_approved_worktree_over_unapproved_commit_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED + "# committed\n")
        commit(self.repo)
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        self.assert_refused(*self.gate(self.approved()))

    def test_symlinked_worktree_file_fails(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        target = Path(self.temp.name) / "approved.py"
        target.write_text(APPROVED)
        (self.repo / "tests" / "test_old.py").unlink()
        (self.repo / "tests" / "test_old.py").symlink_to(target)
        self.assert_refused(*self.gate(self.approved()))

    def test_malformed_or_out_of_scope_entries_fail(self) -> None:
        (self.repo / "tests" / "test_old.py").write_text(APPROVED)
        commit(self.repo)
        sha = blob(self.repo, APPROVED)
        for entry in (
            f"tests/../tests/test_old.py={sha}",
            f"./tests/test_old.py={sha}",
            f"tests//test_old.py={sha}",
            f"/tests/test_old.py={sha}",
            f"Tests/test_old.py={sha}",
            f".factory.toml={sha}",
            f"src/test_old.py={sha}",
            f"tests/test_old.py={sha[:12]}",
            "tests/test_old.py",
        ):
            with self.subTest(entry=entry):
                code, report = self.gate(entry)
                self.assertNotEqual(code, 0, report)
                self.assertNotIn("gate PASS", report)
        with self.subTest("duplicate"):
            code, report = self.gate(self.approved(), self.approved())
            self.assertNotEqual(code, 0, report)
        with self.subTest("missing base"):
            code, report = run_gate(self.repo, "--add-only-allow", self.approved())
            self.assertNotEqual(code, 0, report)

    def test_allow_cannot_exempt_a_test_outside_add_only_scope(self) -> None:
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "test_mod.py").write_text("def test_mod():\n    assert True\n")
        commit(self.repo, "outside test")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        changed = "def test_mod():\n    assert False\n"
        (self.repo / "pkg" / "test_mod.py").write_text(changed)
        commit(self.repo)
        code, report = self.gate(f"pkg/test_mod.py={blob(self.repo, changed)}")
        self.assert_refused(code, report)
        self.assertIn("outside add-only scope", report)

    def test_structural_changes_fail_even_when_blob_matches(self) -> None:
        cases = {
            "delete": lambda: (self.repo / "tests" / "test_old.py").unlink(),
            "rename": lambda: git(self.repo, "mv", "tests/test_old.py", "tests/test_moved.py"),
            "mode": lambda: (self.repo / "tests" / "test_old.py").chmod(0o755),
        }
        sha = blob(self.repo, ORIGINAL)
        for name, change in cases.items():
            with self.subTest(name):
                git(self.repo, "reset", "-q", "--hard", self.base)
                change()
                commit(self.repo)
                self.assert_refused(*self.gate(f"tests/test_old.py={sha}"))
        with self.subTest("binary"):
            git(self.repo, "reset", "-q", "--hard", self.base)
            (self.repo / "tests" / "data.bin").write_bytes(b"\0\1\3")
            commit(self.repo)
            data = git(self.repo, "rev-parse", "HEAD:tests/data.bin")
            self.assert_refused(*self.gate(f"tests/data.bin={data}"))
        with self.subTest("symlink in commit"):
            git(self.repo, "reset", "-q", "--hard", self.base)
            (self.repo / "tests" / "test_old.py").unlink()
            (self.repo / "tests" / "test_old.py").symlink_to("test_other.py")
            commit(self.repo)
            link = git(self.repo, "rev-parse", "HEAD:tests/test_old.py")
            self.assert_refused(*self.gate(f"tests/test_old.py={link}"))


if __name__ == "__main__":
    unittest.main()
