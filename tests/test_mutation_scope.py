"""factory.mutation_scope: the one applicability rule shared by the runner and the gate.

Every case builds a tiny isolated git repository (origin/main = base commit, HEAD = head
commit) and computes the scope with real git; no mutation tool or test suite runs.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import gate, mutation_scope
from scripts import mutation

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
REAL_RUN = subprocess.run  # captured before any test patches subprocess.run


def git(root: Path, *args: str) -> str:
    return REAL_RUN(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                    env={**os.environ, **GIT_ENV}).stdout.strip()


def write_tree(root: Path, files: dict[str, str | None]) -> None:
    for path, content in files.items():
        target = root / path
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)


def commit(root: Path, files: dict[str, str | None], message: str) -> str:
    write_tree(root, files)
    git(root, "add", "-A", "--", *files)
    git(root, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return git(root, "rev-parse", "HEAD")


def build_repo(root: Path, base: dict[str, str], head: dict[str, str | None]) -> tuple[str, str]:
    """`(origin/main commit, HEAD commit)`: base files on main, then one head commit."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    base_commit = commit(root, base, "base")
    git(root, "update-ref", "refs/remotes/origin/main", base_commit)
    return base_commit, commit(root, head, "head")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"

    def scope(self, base: dict[str, str], head: dict[str, str | None]) -> mutation_scope.Scope:
        self.base, self.head = build_repo(self.root, base, head)
        return mutation_scope.compute("origin/main", self.root)


class ApplicabilityTest(Fixture):
    def test_added_and_modified_destination_lines(self) -> None:
        scope = self.scope({"factory/a.py": "x = 1\ny = 2\nz = 3\n"},
                           {"factory/a.py": "x = 1\ny = 20\nz = 3\nw = 4\n", "factory/new.py": "v = 1\n"})
        self.assertEqual(scope.lines, {"factory/a.py": {2, 4}, "factory/new.py": {1}})
        self.assertEqual((scope.base, scope.head, scope.merge_base), (self.base, self.head, self.base))
        self.assertEqual(scope.line_count, 3)

    def test_deletion_only_has_zero_applicable_lines(self) -> None:
        scope = self.scope({"factory/a.py": "x = 1\ny = 2\n", "factory/gone.py": "v = 1\n"},
                           {"factory/a.py": "x = 1\n", "factory/gone.py": None})
        self.assertEqual((scope.lines, scope.line_count), ({}, 0))

    def test_rename_with_edit_counts_every_destination_line(self) -> None:
        scope = self.scope({"factory/old.py": "a = 1\nb = 2\n"},
                           {"factory/old.py": None, "factory/new.py": "a = 1\nb = 3\n"})
        self.assertEqual(scope.lines, {"factory/new.py": {1, 2}})  # --no-renames: delete + add

    def test_docs_tests_and_non_python_changes_are_not_applicable(self) -> None:
        scope = self.scope({"docs/guide.md": "a\n"},
                           {"docs/guide.md": "b\n", "tests/test_x.py": "t = 1\n", "factory/data.json": "{}\n"})
        self.assertEqual(scope.line_count, 0)

    def test_nested_factory_modules_follow_the_git_pathspec(self) -> None:
        scope = self.scope({"README.md": "r\n"}, {"factory/sub/mod.py": "m = 1\n"})
        self.assertEqual(scope.lines, {"factory/sub/mod.py": {1}})

    def test_merge_base_not_base_tip_is_the_left_side(self) -> None:
        self.scope({"factory/a.py": "x = 1\n"}, {"factory/a.py": "x = 2\n"})
        git(self.root, "checkout", "-q", "-b", "later", self.base)
        moved = commit(self.root, {"factory/b.py": "main moved\n"}, "main moves on")
        git(self.root, "update-ref", "refs/remotes/origin/main", moved)
        git(self.root, "checkout", "-q", self.head)
        scope = mutation_scope.compute("origin/main", self.root)
        self.assertEqual((scope.base, scope.merge_base), (moved, self.base))
        self.assertEqual(scope.lines, {"factory/a.py": {1}})  # main's own file is excluded


class FingerprintTest(Fixture):
    def test_fingerprint_binds_contents_and_lines_and_is_deterministic(self) -> None:
        scope = self.scope({"factory/a.py": "x = 1\n"}, {"factory/a.py": "x = 2\n"})
        again = mutation_scope.compute(self.base, self.root)  # a commit resolves to itself
        self.assertEqual(again, scope)
        self.assertEqual(scope.receipt(), {
            "rule": mutation_scope.RULE, "base": self.base, "merge_base": self.base,
            "head": self.head, "fingerprint": scope.fingerprint, "lines": 1,
        })
        commit(self.root, {"factory/a.py": "x = 3\n"}, "same line, new contents")
        changed = mutation_scope.compute(self.base, self.root)
        self.assertEqual(changed.lines, scope.lines)
        self.assertNotEqual(changed.fingerprint, scope.fingerprint)
        self.assertNotEqual(changed.head, scope.head)

    def test_unresolvable_refs_raise_scope_error(self) -> None:
        self.scope({"factory/a.py": "x = 1\n"}, {"factory/a.py": "x = 2\n"})
        for ref in ("origin/missing", "-p", "", "HEAD:factory/a.py"):
            with self.assertRaises(mutation_scope.ScopeError, msg=ref):
                mutation_scope.compute(ref, self.root)

    def test_unparseable_diff_fails_closed(self) -> None:
        for diff in ("@@ -0,0 +1 @@\n", "index 0000000..abc\n+++ b/factory/a.py\n",
                     'index 0000000..' + "a" * 40 + '\n+++ "b/factory/\\303.py"\n'):
            with self.assertRaises(mutation_scope.ScopeError, msg=diff):
                mutation_scope.parse(diff)
        # Header-less input parses (lines only) but can never become a recorded Scope.
        self.assertEqual(mutation_scope.parse("+++ b/factory/a.py\n@@ -0,0 +1 @@\n"),
                         (("factory/a.py", None, (1,)),))


class SharedRuleTest(Fixture):
    def test_runner_lines_are_the_shared_scope_lines(self) -> None:
        scope = self.scope({"factory/a.py": "x = 1\ny = 2\n"},
                           {"factory/a.py": "x = 1\ny = 3\n", "docs/d.md": "d\n"})
        computed: list = []
        with contextlib.chdir(self.root):
            lines = mutation.scope_lines(self.base, computed)  # draft3: proof-only entry point
            default = mutation.changed_lines()
        self.assertEqual(lines, {"factory/a.py": {2}})
        self.assertEqual(default, lines)  # origin/main stays the runner default
        self.assertEqual(computed, [scope])

    def test_both_runner_paths_issue_the_shared_diff_command(self) -> None:
        scope = self.scope({"factory/a.py": "x = 1\n"}, {"factory/a.py": "x = 2\n"})
        seen: list[list[str]] = []

        def recorded(cmd, **kwargs):
            seen.append(list(cmd))
            return REAL_RUN(cmd, **kwargs)

        computed: list = []
        with contextlib.chdir(self.root), mock.patch.object(subprocess, "run", recorded):
            legacy = mutation.changed_lines()  # report-only / unopted: one historical diff
            proof = mutation.scope_lines(self.base, computed)  # proof: resolved identities
        self.assertEqual(legacy, proof)
        # draft3: the unopted branch is baseline 235025a's exact command, not the shared one.
        self.assertEqual(seen[0], ["git", "diff", "--unified=0", "--no-renames", "--diff-filter=AM",
                                   "origin/main...HEAD", "--", "factory/*.py"])
        self.assertEqual(seen[1:], [
            ["git", "rev-parse", "--verify", "--quiet", f"{self.base}^{{commit}}"],
            ["git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
            ["git", "merge-base", self.base, self.head],
            mutation_scope.diff_command(self.base, self.head),
        ])
        self.assertEqual(computed, [scope])

    def test_gate_reader_and_runner_share_the_rule_module(self) -> None:
        self.assertIs(gate.mutation_scope, mutation_scope)
        self.assertEqual(mutation_scope.parse(""), ())


class HunkBoundsTest(unittest.TestCase):
    """parse() must keep later hunks and fail closed on a malformed counter."""

    def test_context_lines_do_not_drop_a_later_hunk(self) -> None:
        blob = "a" * 40
        diff = (
            f"index {blob}..{blob}\n"
            "+++ b/factory/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            " unchanged\n"
            "-old\n"
            "+new\n"
            "@@ -10 +10 @@\n"
            "-gone\n"
            "+here\n"
        )
        parsed = mutation_scope.parse(diff)
        self.assertEqual(parsed, (("factory/a.py", blob, (1, 2, 10)),))

    def test_no_newline_marker_does_not_drop_a_later_hunk(self) -> None:
        blob = "a" * 40
        diff = (
            f"index {blob}..{blob}\n"
            "+++ b/factory/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+new\n"
            " trailing\n"
            "@@ -8 +8 @@\n"
            "-gone\n"
            "+here\n"
        )
        parsed = mutation_scope.parse(diff)
        self.assertEqual(parsed, (("factory/a.py", blob, (1, 2, 8)),))

    def test_an_extra_new_line_in_a_hunk_fails_closed(self) -> None:
        blob = "a" * 40
        diff = (
            f"index {blob}..{blob}\n"
            "+++ b/factory/a.py\n"
            "@@ -1,2 +1 @@\n"
            "-keep-counting\n"
            "+one\n"
            "+extra\n"
        )
        with self.assertRaises(mutation_scope.ScopeError):
            mutation_scope.parse(diff)

    def test_an_extra_old_line_in_a_hunk_fails_closed(self) -> None:
        blob = "a" * 40
        diff = (
            f"index {blob}..{blob}\n"
            "+++ b/factory/a.py\n"
            "@@ -1 +1,2 @@\n"
            "+keep-counting\n"
            "-one\n"
            "-extra\n"
        )
        with self.assertRaises(mutation_scope.ScopeError):
            mutation_scope.parse(diff)


if __name__ == "__main__":
    unittest.main()
