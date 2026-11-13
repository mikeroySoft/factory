"""Survivor kills for factory.mutation_scope: exact resolved annotations, symlinked-directory
ancestors and retargeted committed symlinks, each against real git and a real filesystem.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import tempfile
import typing
import unittest
from pathlib import Path
from unittest import mock

from factory import mutation_scope
from tests.test_mutation_scope import git

FILES = tuple[tuple[str, str | None, tuple[int, ...]], ...]

FUNCTION_HINTS = {
    "_git": {"cwd": Path | None, "args": str, "return": str},
    "resolve": {"ref": str, "cwd": Path | None, "return": str},
    "diff_command": {"revs": str, "return": list[str]},
    "parse": {"diff": str, "return": FILES},
    "_blob_id": {"data": bytes, "oid": str, "return": str},
    "_tree": {"head": str, "cwd": Path | None, "return": list[tuple[str, str, str]]},
    "tracked_paths": {"head": str, "cwd": Path | None, "return": frozenset[str]},
    "linked_ancestors": {"directory": Path, "return": list[str]},
    "tracked_mismatches": {"head": str, "directory": Path, "cwd": Path | None,
                           "return": list[str]},
    "compute": {"base_ref": str, "cwd": Path | None, "return": mutation_scope.Scope},
}

SCOPE_MEMBER_HINTS = {
    "lines": {"return": dict[str, frozenset[int]]},
    "line_count": {"return": int},
    "fingerprint": {"return": str},
    "receipt": {"return": dict},
}


def written_in_module(function: object) -> bool:
    """A function whose code is in factory/mutation_scope.py (not dataclass-generated)."""
    return (inspect.isfunction(function)
            and function.__code__.co_filename == mutation_scope.__file__)


def scope_members() -> dict[str, typing.Callable]:
    members = {}
    for name, member in vars(mutation_scope.Scope).items():
        function = member.fget if isinstance(member, property) else member
        if written_in_module(function):
            members[name] = function
    return members


class AnnotationTest(unittest.TestCase):
    """Exact resolved hints of every function and Scope member written in mutation_scope.

    The completeness tests fail whenever mutation_scope gains or loses a function or Scope
    member: extend FUNCTION_HINTS / SCOPE_MEMBER_HINTS with its exact hints, do not delete them.
    """

    def test_hint_table_covers_exactly_the_module_functions(self) -> None:
        functions = {name for name, function in vars(mutation_scope).items()
                     if written_in_module(function)}
        self.assertEqual(functions, {"_git", "resolve", "diff_command", "parse", "_blob_id",
                                     "_tree", "tracked_paths", "linked_ancestors",
                                     "tracked_mismatches", "compute"})
        self.assertEqual(set(FUNCTION_HINTS), functions)

    def test_hint_table_covers_exactly_the_scope_members(self) -> None:
        members = set(scope_members())
        self.assertEqual(members, {"lines", "line_count", "fingerprint", "receipt"})
        self.assertEqual(set(SCOPE_MEMBER_HINTS), members)

    def test_function_type_hints_are_exact(self) -> None:
        for name, expected in FUNCTION_HINTS.items():
            with self.subTest(function=name):
                function = getattr(mutation_scope, name)
                self.assertEqual(typing.get_type_hints(function), expected)

    def test_scope_field_type_hints_are_exact(self) -> None:
        self.assertEqual(typing.get_type_hints(mutation_scope.Scope),
                         {"base": str, "merge_base": str, "head": str, "files": FILES})

    def test_scope_member_type_hints_are_exact(self) -> None:
        members = scope_members()
        for name, expected in SCOPE_MEMBER_HINTS.items():
            with self.subTest(member=name):
                self.assertEqual(typing.get_type_hints(members[name]), expected)


class LinkedAncestorsTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_symlinked_directory_itself_is_not_reported(self) -> None:
        (self.root / "real" / "pkg").mkdir(parents=True)
        (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        linked = mutation_scope.linked_ancestors(["pkg/a.py", "top.py"], self.root / "link")
        self.assertEqual(linked, [])

    def test_symlinked_ancestor_under_directory_is_reported(self) -> None:
        (self.root / "real").mkdir()
        (self.root / "pkg").symlink_to(self.root / "real", target_is_directory=True)
        linked = mutation_scope.linked_ancestors(["pkg/a.py", "top.py"], self.root)
        self.assertEqual(linked, ["pkg"])


class TrackedSymlinkTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        # Hermetic against inherited GIT_* (GIT_DIR, GIT_INDEX_FILE, GIT_DEFAULT_HASH, ...):
        # both the fixture's git() (tests.test_mutation_scope, which adds GIT_ENV on top of
        # os.environ) and mutation_scope's own git calls read os.environ at call time.
        inherited = {name: value for name, value in os.environ.items()
                     if not name.startswith("GIT_")}
        environ = mock.patch.dict(os.environ, inherited, clear=True)
        environ.start()
        self.addCleanup(environ.stop)
        # SHA-1 pinned: the S40 kill below orders candidate targets by their SHA-1 blob id.
        git(self.root, "init", "-q", "--object-format=sha1", "-b", "main")
        (self.root / "link").symlink_to("committed-target")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--no-verify", "-m", "symlink")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.oid = git(self.root, "rev-parse", "HEAD:link")

    def test_committed_symlink_matches(self) -> None:
        self.assertEqual(mutation_scope.tracked_mismatches(self.head, self.root, self.root), [])

    def test_retargeted_symlink_with_higher_blob_id_is_a_mismatch(self) -> None:
        def blob_id(text: str) -> str:
            data = text.encode()
            return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

        self.assertEqual(len(self.oid), 40)  # the fixture repository really is SHA-1
        target = next(f"other-{i}" for i in range(1000) if blob_id(f"other-{i}") > self.oid)
        (self.root / "link").unlink()
        (self.root / "link").symlink_to(target)
        mismatches = mutation_scope.tracked_mismatches(self.head, self.root, self.root)
        self.assertEqual(mismatches, ["link"])


if __name__ == "__main__":
    unittest.main()
