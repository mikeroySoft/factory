"""Proof-mode prepared children hold only the pinned HEAD's paths: a NEW file absent from
HEAD (staged, untracked or ignored) never enters a child that baselines or runs mutants.

The tracked-byte check compares HEAD's `ls-tree` with raw filesystem bytes and ignores
the index entirely, so existing tracked edits are blocked when (and only when) the working
bytes differ; it cannot see a path HEAD does not list. These cases cover that gap with real
files at real paths in a tiny fixture repository (a discoverable `tests/test_*.py` and a
top-level `config.py` that shadows the fixture source's `import config`), plus the
index-independence and ordinary-output controls.

Real: scripts.mutation.main in proof mode, the shared scope rule and byte check, sample(),
child preparation (`git clone --shared --no-checkout` + shutil.copytree), the worker queue,
tally and completion record, gate.read_mutation_proof, and (caller cases) the witness
harness's dispatcher -> gate -> risk-check -> head-acceptance chain. Synthetic (same as
tests/test_mutation_input_integrity.py): cosmic-ray init/use_db (ast enumerator),
mutate_and_test (a recorder returning KILLED), attrs.evolve, and the baseline suite child.
Each observation of a child lists every non-HEAD entry (files, directories, symlinks,
directory symlinks included and never followed) with an independent os.walk against
`git ls-tree` names, and the lstat modes of every retained HEAD path and ancestor
directory; nothing here runs a real test suite or mutant.

Read-only cleanup cases (proof5): real OS modes (0555 directories, 0444 files) in the
checkout, in disposable children and outside both, on the real preparation and
post-baseline exclusion branches. One tight injected refusal (`unremovable`: EPERM from
os.unlink/os.remove/os.rmdir for one entry name, only while the real exclude_non_head
runs) stands in for a condition no mode repair can fix, without root/non-root assumptions;
the existing raise -> no record -> gate FAIL seam is what it witnesses.
"""
from __future__ import annotations

import contextlib
import errno
import io
import os
import shlex
import stat
import sys
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from factory import dispatch, lifecycle, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_input_integrity as integrity
from tests.test_mutation_scope import git

SHADOW_TEST = "import unittest\n\n\nclass Injected(unittest.TestCase):\n    def test_kill(self):\n        self.fail()\n"
SHADOW_MODULE = "raise SystemExit('import-shadow module executed')\n"
# kind -> (path, content, how it is introduced in the checkout)
NEW_INPUTS = {
    "untracked test": ("tests/test_untracked.py", SHADOW_TEST, "untracked"),
    "staged new test": ("tests/test_staged.py", SHADOW_TEST, "staged"),
    "ignored test": ("tests/test_ignored.py", SHADOW_TEST, "ignored"),
    "untracked import shadow": ("config.py", SHADOW_MODULE, "untracked"),
    "staged import shadow in a new package": ("factory/__init__.py", SHADOW_MODULE, "staged"),
    "ignored import shadow": ("attrs.py", SHADOW_MODULE, "ignored"),
}
ORDINARY_OUTPUTS = {"build/report.log": "caller output\n", ".coverage": "data\n"}


STUCK = "stuck.log"
EXTERNAL_TEXT = "external read-only target\n"


def _raise(error: OSError) -> None:
    raise error


def head_ancestors(head_names: set[str]) -> set[str]:
    """Every directory HEAD's paths live in, "." (the copy itself) included."""
    return {parent.as_posix() for name in head_names for parent in PurePosixPath(name).parents}


def non_head_paths(directory: Path, head_names: set[str]) -> list[str]:
    """Independent of production: every file, directory or symlink (directory symlinks
    included, never followed) under `directory` (its own top-level .git excepted) whose
    relative path HEAD does not list as a path or an ancestor directory; a HEAD ancestor
    directory replaced by a symlink is listed too. Unreadable directories raise."""
    ancestors = head_ancestors(head_names)
    found = []
    for current, dirs, files in os.walk(directory, onerror=_raise):
        rel_dir = Path(current).relative_to(directory)
        if rel_dir == Path("."):
            dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*dirs, *files]:
            rel = (rel_dir / name).as_posix()
            if rel not in head_names and rel not in ancestors:
                found.append(rel)
            elif rel in ancestors and (Path(current) / name).is_symlink():
                found.append(f"{rel} (symlink)")
    return sorted(found)


def retained_modes(directory: Path, head_names: set[str]) -> dict[str, int | None]:
    """Independent of production: lstat permission bits of every HEAD path and ancestor
    directory under `directory` (None when missing); symlinks are never followed."""
    modes = {}
    for rel in sorted(set(head_names) | head_ancestors(head_names)):
        try:
            modes[rel] = stat.S_IMODE(os.lstat(directory / rel).st_mode)
        except FileNotFoundError:
            modes[rel] = None
    return modes


def snapshot(top: Path) -> dict[str, tuple]:
    """Independent of production: (kind, mode, bytes or link target) of `top` and of every
    entry below it by lstat, never following a symlink; a top-level .git is skipped."""
    def entry(path: Path) -> tuple:
        info = os.lstat(path)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            return ("symlink", mode, os.readlink(path))
        if stat.S_ISDIR(info.st_mode):
            return ("dir", mode, None)
        return ("file", mode, path.read_bytes())

    entries = {".": entry(top)}
    if entries["."][0] == "dir":
        for current, dirs, files in os.walk(top, onerror=_raise):
            rel_dir = Path(current).relative_to(top)
            if rel_dir == Path("."):
                dirs[:] = [d for d in dirs if d != ".git"]
            for name in [*dirs, *files]:
                entries[(rel_dir / name).as_posix()] = entry(Path(current) / name)
    return entries


def readonly_tree(top: Path, files: dict[str, str]) -> None:
    """Real read-only modes: writes `files` under `top`, then every file 0444 and every
    directory from `top` down 0555 (deepest first)."""
    for rel, content in files.items():
        (top / rel).parent.mkdir(parents=True, exist_ok=True)
        (top / rel).write_text(content)
    directories = [top]
    for current, dirs, names in os.walk(top):
        for name in names:
            os.chmod(Path(current) / name, 0o444)
        directories += [Path(current) / d for d in dirs]
    for directory in reversed(directories):
        os.chmod(directory, 0o555)


def writable_again(top: Path) -> None:
    """Test cleanup only: owner rwx on `top` and every real directory below it (symlinks are
    never followed), so the fixture's temporary directory can be removed."""
    def grant(path: Path) -> None:
        os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | 0o700)

    if top.is_symlink() or not top.is_dir():
        return
    grant(top)
    for current, dirs, _ in os.walk(top):
        for name in dirs:
            if not (Path(current) / name).is_symlink():
                grant(Path(current) / name)


@contextlib.contextmanager
def unremovable(name: str, attempts: list):
    """A tight injected permission failure no mode repair can fix: while the real
    scripts.mutation.exclude_non_head runs (and only then), removing any entry named `name`
    raises EPERM, however its modes or its parent's are changed (as an immutable flag or a
    foreign owner would), without root/non-root assumptions. `attempts` records each
    refused path. Everything else, the scratch directory's own cleanup included, is real."""
    real_exclude = mutation.exclude_non_head
    active: list = []

    def refusing(real):
        def remove(path, *args, **kwargs):
            if active and os.path.basename(os.fsdecode(path)) == name:
                attempts.append(os.fsdecode(path))
                raise PermissionError(errno.EPERM, "Operation not permitted (injected)", os.fsdecode(path))
            return real(path, *args, **kwargs)
        return remove

    def exclude_non_head(*args, **kwargs):
        active.append(True)
        try:
            return real_exclude(*args, **kwargs)
        finally:
            active.pop()

    with mock.patch.object(os, "unlink", refusing(os.unlink)), \
            mock.patch.object(os, "remove", refusing(os.remove)), \
            mock.patch.object(os, "rmdir", refusing(os.rmdir)), \
            mock.patch.object(mutation, "exclude_non_head", exclude_non_head):
        yield


def introduce(root: Path, path: str, content: str, how: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    if how == "staged":
        git(root, "add", "--", path)
    elif how == "ignored":
        with (root / ".git" / "info" / "exclude").open("a") as exclude:
            exclude.write(f"/{path}\n")
        assert git(root, "check-ignore", "--", path) == path


@contextlib.contextmanager
def observed_children(log: dict, head_names: set[str]):
    """Wrap the (synthetic) mutate_and_test boundary to list each mutant child's non-HEAD
    entries and the modes of its retained HEAD paths."""
    log["mutant_extras"] = []
    log["mutant_modes"] = []
    mutating = sys.modules["cosmic_ray"].mutating
    inner = mutating.mutate_and_test

    def mutate_and_test(local, command, timeout):
        copy = Path(shlex.split(command)[2])  # `env -C <copy> ...` (scripts.mutation.test_command)
        log["mutant_extras"].append(non_head_paths(copy, head_names))
        log["mutant_modes"].append(retained_modes(copy, head_names))
        return inner(local, command, timeout)

    mutating.mutate_and_test = mutate_and_test
    try:
        yield
    finally:
        mutating.mutate_and_test = inner


class NonHeadFixture(integrity.ProducerFixture):
    def setUp(self) -> None:
        super().setUp()
        self.head_names = set(integrity.head_bytes(self.root, self.head))

    def produce_observed(self, *, baseline=None, log: dict | None = None) -> tuple[int, str, dict]:
        """`produce`, also listing non-HEAD entries and retained modes in each child at
        baseline and mutant time; `log` (if given) stays readable when main raises."""
        self.proof.unlink(missing_ok=True)
        os.environ[mutation.PROOF_ENV] = str(self.proof)
        os.environ[mutation.PROOF_BASE_ENV] = self.base
        log = {} if log is None else log
        log.update(baseline_extras=[], baseline_modes=[])

        def at_baseline(copy: Path) -> None:
            log["baseline_extras"].append(non_head_paths(copy, self.head_names))
            log["baseline_modes"].append(retained_modes(copy, self.head_names))
            if baseline is not None:
                baseline(copy)

        out = io.StringIO()
        with integrity.producer_boundaries(log, root=self.root, head=self.head, baseline=at_baseline), \
                observed_children(log, self.head_names), contextlib.redirect_stdout(out):
            code = mutation.main(["--blocking"])
        return code, out.getvalue(), log

    def assert_complete(self, code: int, log: dict) -> None:
        record = self.record()
        self.assertEqual((code, record["status"], record["reason"]), (0, "complete", "no-blocking-survivors"))
        self.assertEqual(self.verdict(code), "PASS")
        self.assertEqual({key for key, _ in log["mutants"]}, integrity.HOT)
        self.assertTrue(all(intact for _, intact in log["mutants"]))
        # Every retained HEAD path and ancestor directory keeps the checkout's own modes
        # (copytree copies them) when the baseline and each mutant start.
        expected = retained_modes(self.root, self.head_names)
        self.assertEqual(log["baseline_modes"], [expected])
        self.assertEqual(log["mutant_modes"], [expected] * 3)


class NewInputPreparationTest(NonHeadFixture):
    def test_new_non_head_inputs_in_the_checkout_never_reach_a_child(self) -> None:
        for kind, (path, content, how) in NEW_INPUTS.items():
            with self.subTest(kind=kind):
                introduce(self.root, path, content, how)
                try:
                    code, output, log = self.produce_observed()
                    self.assertEqual(log["baseline_extras"], [[]])  # excluded at preparation
                    self.assertEqual(log["mutant_extras"], [[]] * 3)
                    self.assert_complete(code, log)
                    self.assertIn(f"excluded from prepared copies (absent from HEAD {self.head})", output)
                    # The checkout keeps the developer's file (and its index entry): nothing deleted.
                    self.assertEqual((self.root / path).read_text(), content)
                    if how == "staged":
                        self.assertIn(path, git(self.root, "diff", "--cached", "--name-only"))
                finally:
                    if how == "staged":
                        git(self.root, "rm", "-q", "--cached", "--", path)
                    (self.root / path).unlink()

    def test_new_inputs_written_into_children_by_the_baseline_are_excluded_before_mutation(self) -> None:
        def inject(copy: Path) -> None:
            for clone in [copy, *copy.parent.glob("copy*")]:
                for path, content, _ in NEW_INPUTS.values():
                    (clone / path).parent.mkdir(parents=True, exist_ok=True)
                    (clone / path).write_text(content)
                (clone / "baseline-output.log").write_text("ordinary output\n")

        code, _, log = self.produce_observed(baseline=inject)
        self.assertEqual(log["baseline_extras"], [[]])
        self.assertEqual(log["mutant_extras"], [[]] * 3)  # excluded post-baseline, pre-mutation
        self.assert_complete(code, log)


class ExclusionWalkTest(NonHeadFixture):
    """exclude_non_head on a real copy (added after the fix; edges of the walk itself)."""

    def test_only_head_paths_and_the_copys_own_git_remain(self) -> None:
        copy = self.root.parent / "copy0"
        integrity.REAL_RUN(["git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(self.root), str(copy)], check=True)
        integrity.REAL_COPYTREE(self.root, copy, ignore=mutation.COPY_IGNORE, dirs_exist_ok=True)
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("not in the copy\n")
        (copy / "pkg" / "deep").mkdir(parents=True)
        (copy / "pkg" / "deep" / "test_x.py").write_text(SHADOW_TEST)
        (copy / "tests" / "empty").mkdir()
        (copy / "linked").symlink_to(outside, target_is_directory=True)
        (copy / "tests" / "link.py").symlink_to(copy / "factory" / "example.py")
        git_entries = sorted(p.name for p in (copy / ".git").iterdir())
        scope = mutation_scope.compute(self.base)
        removed = mutation.exclude_non_head(scope, copy)
        self.assertEqual(sorted(r.split(": ", 1)[1] for r in removed),
                         ["linked", "pkg", "tests/empty", "tests/link.py"])
        self.assertEqual(non_head_paths(copy, self.head_names), [])
        self.assertEqual(sorted(p.name for p in (copy / ".git").iterdir()), git_entries)
        self.assertEqual(mutation_scope.tracked_mismatches(self.head, copy), [])
        self.assertEqual((outside / "keep.txt").read_text(), "not in the copy\n")  # link not followed


class IndexIndependenceTest(NonHeadFixture):
    def test_staged_existing_tracked_edit_with_changed_working_bytes_is_blocked(self) -> None:
        for rel in integrity.DIRTY_INPUTS:
            with self.subTest(rel=rel):
                integrity.edit(self.root / rel)
                git(self.root, "add", "--", rel)
                code, _, log = self.produce()
                self.assert_rejected(code, log, "dirty-tracked-inputs", enumerated=False, baseline=False)
                git(self.root, "reset", "-q", "--", rel)
                git(self.root, "checkout", "-q", "--", rel)

    def test_staged_edit_restored_to_head_bytes_is_accepted_regardless_of_the_index(self) -> None:
        for rel in integrity.DIRTY_INPUTS:
            original = (self.root / rel).read_bytes()
            integrity.edit(self.root / rel)
            git(self.root, "add", "--", rel)
            (self.root / rel).write_bytes(original)  # working bytes are HEAD's again
        self.assertEqual(sorted(git(self.root, "diff", "--cached", "--name-only").split()),
                         sorted(integrity.DIRTY_INPUTS))  # the index still differs from HEAD
        code, _, log = self.produce_observed()
        self.assert_complete(code, log)
        self.assertEqual(log["mutant_extras"], [[]] * 3)


class OrdinaryOutputTest(NonHeadFixture):
    def test_caller_and_baseline_outputs_are_allowed(self) -> None:
        for path, content in ORDINARY_OUTPUTS.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(content)

        def outputs(copy: Path) -> None:
            (copy / "tests" / "__pycache__").mkdir(exist_ok=True)
            (copy / "tests" / "__pycache__" / "test_example.cpython.pyc").write_bytes(b"\0")
            (copy / "baseline-output.log").write_text("ok\n")

        code, _, log = self.produce_observed(baseline=outputs)
        self.assert_complete(code, log)
        for path, content in ORDINARY_OUTPUTS.items():  # the checkout's outputs are untouched
            self.assertEqual((self.root / path).read_text(), content)


class ReadOnlyExclusionTest(NonHeadFixture):
    """Real OS read-only modes on the real preparation and post-baseline exclusion branches:
    owned disposable copies are cleaned, nothing outside them changes bytes or modes."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(writable_again, self.root.parent)  # runs before the temp cleanup

    def assert_clean_children(self, code: int, log: dict, before: dict) -> None:
        self.assertEqual(log["baseline_extras"], [[]])
        self.assertEqual(log["mutant_extras"], [[]] * 3)
        self.assert_complete(code, log)  # PASS, and retained modes equal the checkout's
        self.assertEqual(snapshot(self.root), before)  # checkout bytes and modes unchanged

    def test_copied_read_only_output_tree_is_removed_from_children_only(self) -> None:
        readonly_tree(self.root / "build", {"report.log": "caller output\n",
                                            "nested/deep.log": "deep output\n",
                                            "nested/empty/.keep": ""})
        before = snapshot(self.root)
        self.assertEqual(before["build"][:2], ("dir", 0o555))
        code, output, log = self.produce_observed()
        self.assert_clean_children(code, log, before)
        self.assertIn(": build", output)  # disclosed as excluded

    def test_read_only_tree_created_by_the_baseline_is_removed_before_mutation(self) -> None:
        def outputs(copy: Path) -> None:
            for clone in sorted(copy.parent.glob("copy*")):
                readonly_tree(clone / "out", {"a/b/result.log": "baseline output\n", "top.log": "ok\n"})
                readonly_tree(clone / "tests" / "__pycache__", {"test_example.cpython.pyc": "\0"})

        before = snapshot(self.root)
        code, _, log = self.produce_observed(baseline=outputs)
        self.assert_clean_children(code, log, before)

    def test_symlinks_are_unlinked_without_following_to_read_only_targets(self) -> None:
        external = self.root.parent / "external"
        readonly_tree(external, {"secret.txt": EXTERNAL_TEXT, "inner/deeper.txt": EXTERNAL_TEXT})
        external_file = self.root.parent / "external-file.txt"
        external_file.write_text(EXTERNAL_TEXT)
        os.chmod(external_file, 0o444)
        # In the checkout: copytree copies through these (symlinks=False), so the children
        # get real read-only copies of the external tree; the checkout links stay links.
        (self.root / "ext-link").symlink_to(external, target_is_directory=True)
        (self.root / "ext-file-link").symlink_to(external_file)
        outside = {path: snapshot(path) for path in (external, external_file)}
        before = snapshot(self.root)

        def links(copy: Path) -> None:  # the baseline leaves symlinks, some in a 0555 tree
            for clone in sorted(copy.parent.glob("copy*")):
                (clone / "top-dir-link").symlink_to(external, target_is_directory=True)
                (clone / "top-file-link").symlink_to(external_file)
                (clone / "tests" / "dir-link").symlink_to(external, target_is_directory=True)
                nest = clone / "links" / "inner"
                nest.mkdir(parents=True)
                (nest / "dir-link").symlink_to(external, target_is_directory=True)
                (nest / "file-link").symlink_to(external_file)
                (nest / "tracked-dir-link").symlink_to(clone / "factory", target_is_directory=True)
                (nest / "tracked-file-link").symlink_to(clone / "factory" / "example.py")
                os.chmod(nest, 0o555)
                os.chmod(clone / "links", 0o555)

        code, _, log = self.produce_observed(baseline=links)
        self.assert_clean_children(code, log, before)  # incl. factory/ and example.py modes
        self.assertEqual({path: snapshot(path) for path in outside}, outside)
        self.assertEqual(outside[external]["."][:2], ("dir", 0o555))
        self.assertEqual(outside[external_file]["."][:2], ("file", 0o444))

    def test_retained_tracked_ancestors_get_their_original_modes_back(self) -> None:
        (self.root / "tests" / "test_untracked.py").write_text(SHADOW_TEST)
        readonly_tree(self.root / "tests" / "notes", {"n.txt": "notes\n"})
        (self.root / "factory" / "scratch.txt").write_text("scratch\n")
        os.chmod(self.root / "factory", 0o555)
        os.chmod(self.root / "tests", 0o555)
        before = snapshot(self.root)

        def outputs(copy: Path) -> None:  # a suite that re-protects a tracked directory
            for clone in sorted(copy.parent.glob("copy*")):
                tests = clone / "tests"
                os.chmod(tests, 0o755)
                (tests / "baseline.log").write_text("ok\n")
                (tests / "factory-link").symlink_to(clone / "factory", target_is_directory=True)
                readonly_tree(tests / "out", {"result.log": "ok\n"})
                os.chmod(tests, 0o555)

        code, _, log = self.produce_observed(baseline=outputs)
        expected = retained_modes(self.root, self.head_names)
        self.assertEqual((expected["tests"], expected["factory"]), (0o555, 0o555))
        self.assert_clean_children(code, log, before)  # children: tests/ and factory/ 0555 again


class UnrepairableExclusionTest(NonHeadFixture):
    """A removal no permission repair can fix fails closed: main raises (no completion
    record; the gate reads a missing record as FAIL), nothing is mutated."""

    def fails_closed(self, *, baseline=None) -> dict:
        attempts: list = []
        log: dict = {}
        with unremovable(STUCK, attempts), self.assertRaises(OSError) as raised:
            self.produce_observed(baseline=baseline, log=log)
        self.assertTrue(attempts)  # the real removal reached the refusal (not vacuous)
        self.assertIn(STUCK, str(raised.exception))  # the failure names what could not go
        self.assertIsNone(self.record())
        self.assertEqual(self.verdict(1), "FAIL")
        self.assertEqual((log["mutants"], log["mutant_extras"]), ([], []))
        return log

    def test_unremovable_path_at_preparation_fails_closed_before_the_baseline(self) -> None:
        (self.root / STUCK).write_text("checkout output\n")
        before = snapshot(self.root)
        log = self.fails_closed()
        self.assertEqual((len(log["clones"]), log["baselines"]), (1, []))
        self.assertEqual(snapshot(self.root), before)

    def test_unremovable_baseline_output_fails_closed_before_mutation(self) -> None:
        def outputs(copy: Path) -> None:
            for clone in sorted(copy.parent.glob("copy*")):
                (clone / "out" / "deep").mkdir(parents=True)
                (clone / "out" / "deep" / STUCK).write_text("baseline output\n")

        before = snapshot(self.root)
        log = self.fails_closed(baseline=outputs)
        self.assertEqual(len(log["baselines"]), 1)
        self.assertEqual(log["baseline_extras"], [[]])
        self.assertEqual(snapshot(self.root), before)


class NonHeadCallerTest(integrity.IntegrityCallerTest):
    """Through the real dispatcher -> gate -> risk-check -> head-acceptance chain."""

    def observed_producer(self):
        inner = self.producer()
        names = set(integrity.head_bytes(self.root, self.head))
        self.extras: list = []

        def step(child):
            original = integrity.producer_boundaries

            @contextlib.contextmanager
            def boundaries(log, **kwargs):
                with original(log, **kwargs), observed_children(log, names):
                    yield log
                self.extras.extend(log["mutant_extras"])

            with mock.patch.object(integrity, "producer_boundaries", boundaries):
                return inner(child)
        return step

    def accepted(self, result: dict) -> None:
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        row = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"][-1]
        self.assertEqual(row["mutation_scope"], mutation_scope.compute(self.base, self.root).fingerprint)

    def test_new_non_head_inputs_are_excluded_through_the_caller(self) -> None:
        self.configure()
        for path, content, how in NEW_INPUTS.values():
            introduce(self.root, path, content, how)
        result = self.invoke([self.observed_producer()])
        self.accepted(result)
        self.assertEqual(self.extras, [[]] * 3)
        for path, content, _ in NEW_INPUTS.values():
            self.assertEqual((self.root / path).read_text(), content)

    def test_staged_existing_edit_blocks_and_restored_bytes_accept_through_the_caller(self) -> None:
        rel = "tests/test_example.py"
        self.configure()
        original = (self.root / rel).read_bytes()
        integrity.edit(self.root / rel)
        git(self.root, "add", "--", rel)
        self.blocked(self.invoke([witness.COMPLETE_PASS]), "tracked inputs differ from HEAD")
        self.assertEqual(self.children, [])
        self.tearDown_reset()
        self.record_ticket("High")
        (self.root / rel).write_bytes(original)  # index still staged, working bytes HEAD's
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), rel)
        self.accepted(self.invoke([self.observed_producer()]))

    def test_ordinary_untracked_output_is_allowed_through_the_caller(self) -> None:
        self.configure()
        for path, content in ORDINARY_OUTPUTS.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(content)
        self.accepted(self.invoke([self.observed_producer()]))

    def test_copied_read_only_output_tree_is_removed_through_the_caller(self) -> None:
        self.configure()
        self.addCleanup(writable_again, self.root)  # runs before the temp cleanup
        readonly_tree(self.root / "build", {"report.log": "caller output\n", "nested/deep.log": "deep\n"})
        before = snapshot(self.root / "build")
        self.accepted(self.invoke([self.observed_producer()]))
        self.assertEqual(self.extras, [[]] * 3)
        self.assertEqual(snapshot(self.root / "build"), before)

    def test_unremovable_non_head_path_fails_closed_through_the_caller(self) -> None:
        self.configure()
        (self.root / STUCK).write_text("checkout output\n")
        attempts: list = []
        inner = self.producer()

        def step(child):
            with unremovable(STUCK, attempts):
                try:
                    return inner(child)
                except OSError as exc:  # as the runner process would: traceback, exit 1, no record
                    return f"Traceback (most recent call last):\n{type(exc).__name__}: {exc}\n", 1

        self.blocked(self.invoke([step]), "missing completion record")
        self.assertTrue(attempts)
        self.assertEqual((self.log["baselines"], self.log["mutants"]), ([], []))
        self.assertEqual((self.root / STUCK).read_text(), "checkout output\n")


for _name in [name for name in dir(integrity.IntegrityCallerTest) if name.startswith("test_")]:
    setattr(NonHeadCallerTest, _name, None)


if __name__ == "__main__":
    unittest.main()
