"""Proof-mode prepared copies must reject a retained HEAD ancestor directory that is a symlink.

Gap (PM-confirmed, ordinary and NONCONCURRENT; not adversarial sandbox, provenance or race
scope): after the baseline, a retained `tests/` or `factory/` directory in a prepared child
can be replaced by a symlink to an external, owned directory holding HEAD's exact tracked
bytes plus an extra discoverable test or importable module. exclude_non_head keeps the
link (its own path is tracked-ancestor), os.walk never descends into it, and
tracked_mismatches checks only the final component and reads through the ancestor, so the
run completes and the gate reads PASS while the mutant children address external files.

Required (product phase, not here): before cleanup and before any tracked read, lstat the
HEAD-required ancestor directories of each prepared copy, within that copy only, and reject
a symlink ancestor (an OSError naming `<copy>/<ancestor>`, as the existing starting-copy
check does) without following, repairing, unlinking or chmodding it or its target. HEAD's
own 120000 symlink LEAVES stay permitted with the existing readlink semantics.

Real: scripts.mutation.main in proof mode and its real exclude_non_head / pinned_drift /
mutation_scope, child preparation (`git clone --shared --no-checkout` + shutil.copytree),
the worker count and clone bookkeeping, the tally/record writer, gate.read_mutation_proof,
and (caller cases) the witness harness's dispatch.pr_gate_and_review -> run_gate -> gate ->
risk-check -> head-acceptance chain. Synthetic only as in tests/test_mutation_input_integrity.py:
cosmic-ray init/use_db, mutate_and_test (a recorder that appends to log["mutants"] FIRST and
never raises), attrs.evolve, the baseline child, plus the witness harness's
process/record/reviewer boundaries. The caller step converts an OSError from main into a
traceback and exit 1 exactly as the runner process would; it raises nothing itself.

Instrumentation (nonraising, never changes a result): only while the production
exclude_non_head or pinned_drift runs AFTER the baseline, path-based os.stat / os.lstat /
os.open / os.scandir / os.listdir / os.readlink / io.open / open calls are recorded, then
run unchanged; each such call is bracketed by an lstat-only snapshot (bytes, modes, link
targets; never following a link) of every prepared copy, taken while recording is off.
A non-HEAD sentinel written into every copy by the baseline makes cleanup observable: a
guard that rejects only after cleanup (or after cleaning an earlier copy) changes it.

The existing tracked-byte guard is in scope too (its final-component check is the same
ancestor bug): mutation_scope.tracked_mismatches must report a nonempty list of HEAD's own
tracked paths (its unchanged return-list contract) for a copy whose `tests/` or `factory/`
is such a link, without probing through the link or above the copy. No new public API is
prescribed: the direct cases call only the existing exclude_non_head / tracked_mismatches.

Tested boundary: the attack is placed by the (synthetic) baseline child in the real
prepared copies. One nested HEAD path (tests/nested/test_nested.py, committed per test in
that test's own temp repository; the shared fixture and TRACKED are unchanged) shows the
shallow-first guard stops at the symlinked ancestor, at `tests/` or at `tests/nested/`, with
no probe below the link or above the copy. The committed symlink-leaf control is direct-API only: the
production copy step (shutil.copytree, symlinks=False) turns a committed link into a
regular file, an existing limitation this phase does not broaden, so that copy is prepared
here with copytree(symlinks=True) as a checkout of HEAD would hold it. Synthetic producer
bookkeeping is not completed REAL mutation or release proof.
"""
from __future__ import annotations

import builtins
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from factory import mutation_scope
from scripts import mutation
from tests import test_mutation_input_integrity as integrity
from tests import test_mutation_nonhead_inputs as nonhead
from tests import test_mutation_traversal as traversal
from tests.test_mutation_scope import git

# Retained HEAD ancestor -> the extra discoverable test / importable module its external
# replacement holds next to HEAD's exact tracked bytes.
EXTRA = {"tests": ("test_external.py", nonhead.SHADOW_TEST),
         "factory": ("__init__.py", nonhead.SHADOW_MODULE)}
SENTINEL = ("baseline-output.log", "ordinary baseline output\n")  # non-HEAD, at each copy root
PROBES = ("stat", "lstat", "open", "scandir", "listdir", "readlink")
FOLLOWING = {"stat", "open", "scandir", "listdir"}  # on the link path itself: follows it


NESTED = ("tests/nested/test_nested.py", "def test_nested():\n    assert True\n")


def external_fixture(top: Path, rel: str, expected: dict[str, bytes],
                     extra: tuple[str, str] | None = None) -> Path:
    """An owned external directory: HEAD's tracked files under `rel` (same bytes) plus
    `extra` (default EXTRA[rel]); files 0444, directories 0555 so any chmod or write shows
    in a snapshot."""
    files = {name[len(rel) + 1:]: data.decode() for name, data in expected.items()
             if name.startswith(f"{rel}/")}
    name, content = extra or EXTRA[rel]
    files[name] = content
    nonhead.readonly_tree(top, files)
    return top


def recorded_call(events: list, kind: str, function, *args):
    """Nonraising instrumentation of one direct production call: its path probes are
    appended to `events` under `kind`; its result or exception passes through unchanged."""
    active: list = []
    with recorded_probes(events, active):
        active.append(kind)
        try:
            return function(*args)
        finally:
            active.pop()


def link_ancestor(copy: Path, rel: str, external: Path) -> Path:
    """Replace the real retained directory copy/<rel> with a symlink to `external`."""
    link = copy / rel
    shutil.rmtree(link)
    link.symlink_to(external, target_is_directory=True)
    return link


def snapshots(clones) -> dict[str, dict | str]:
    """Nonraising lstat snapshot of each prepared copy (an error is recorded, not raised)."""
    taken = {}
    for clone in clones:
        try:
            taken[clone] = nonhead.snapshot(Path(clone))
        except OSError as exc:
            taken[clone] = f"unreadable: {exc!r}"
    return taken


@contextlib.contextmanager
def recorded_probes(events: list, active: list):
    """Nonraising: while `active` is nonempty, each path-based probe call is appended to
    `events` as (active[-1], kind, normalized path) and then runs unchanged. os.stat with
    follow_symlinks=False is recorded as "lstat"; fd and dir_fd-relative calls are not
    resolved (only their path text is recorded)."""
    def probe(kind, real):
        def call(*args, **kwargs):
            path = args[0] if args else kwargs.get("path", ".")
            if active and isinstance(path, (str, bytes, os.PathLike)):
                name = "lstat" if kind == "stat" and kwargs.get("follow_symlinks", True) is False else kind
                events.append((active[-1], name, os.path.normpath(os.fsdecode(os.fspath(path)))))
            return real(*args, **kwargs)
        return call

    patches = [mock.patch.object(os, name, probe(name, getattr(os, name))) for name in PROBES]
    patches += [mock.patch.object(io, "open", probe("open", io.open)),
                mock.patch.object(builtins, "open", probe("open", builtins.open))]
    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        yield


@contextlib.contextmanager
def guard_window(*, started, clones, windows: list, events: list):
    """Wrap the production exclude_non_head ("exclude") and pinned_drift ("drift"): calls
    after the baseline (`started()`) record their probes and append
    {"name", "before", "after", "error"} to `windows` (copy snapshots on entry and exit).
    The production call's own result or exception is passed through unchanged."""
    active: list = []

    def windowed(name, real):
        def call(scope, *directories):
            if not started():
                return real(scope, *directories)
            entry = {"name": name, "before": snapshots(clones()), "error": None}
            active.append(name)
            try:
                return real(scope, *directories)
            except BaseException as exc:
                entry["error"] = exc
                raise
            finally:
                active.pop()
                entry["after"] = snapshots(clones())
                windows.append(entry)
        return call

    with recorded_probes(events, active), \
            mock.patch.object(mutation, "exclude_non_head", windowed("exclude", mutation.exclude_non_head)), \
            mock.patch.object(mutation, "pinned_drift", windowed("drift", mutation.pinned_drift)):
        yield


def through_link(events: list, link: str, external: str) -> list:
    """Probes that follow `link`: anything strictly below it or at/below its target, or a
    following probe (stat/open/listing) of the link path itself."""
    def under(path: str, top: str) -> bool:
        return path == top or path.startswith(top + os.sep)

    return [e for e in events if e[2].startswith(link + os.sep) or under(e[2], external)
            or (e[2] == link and e[1] in FOLLOWING)]


def above_copies(events: list, clones) -> list:
    """Probes of any strict ancestor of a prepared copy (outside the prepared-root boundary)."""
    ancestors = {str(parent) for clone in clones for parent in Path(clone).parents}
    return [e for e in events if e[2] in ancestors]


def linked_after_baseline(rel: str, index: int, external: Path, expected: dict, probes: list):
    """Baseline action: write SENTINEL into every copy, then replace copy<index>/<rel> (a
    real retained HEAD directory) with a symlink to `external`. Records the precondition on
    the actual child: (link, HEAD bytes read through it match, extra path present)."""
    def baseline(copy: Path) -> None:
        clones = sorted(copy.parent.glob("copy*"))
        for clone in clones:
            (clone / SENTINEL[0]).write_text(SENTINEL[1])
        target = clones[index] / rel
        shutil.rmtree(target)
        target.symlink_to(external, target_is_directory=True)
        same = {name: (clones[index] / name).read_bytes() == data
                for name, data in expected.items() if name.startswith(f"{rel}/")}
        probes.append((str(target), same, (target / EXTRA[rel][0]).is_file()))
    return baseline


def checkout_state(root: Path) -> dict:
    """Bytes and modes of the checkout's tracked top-level entries (its journal excluded)."""
    return {name: nonhead.snapshot(root / name) for name in ("AGENTS.md", "factory", "scripts", "tests")}


class AncestorLinkCallerTest(integrity.IntegrityCallerTest):
    """Through the real dispatcher -> gate -> risk-check -> head-acceptance chain."""

    CASES: ClassVar[dict[str, tuple[str, int]]] = {  # name -> (retained ancestor, copy index)
        "tests/ in copy0": ("tests", 0),
        "tests/ in later worker copy1": ("tests", 1),
        "factory/ in copy0": ("factory", 0),
        "factory/ in later worker copy1": ("factory", 1),
    }

    def setUp(self) -> None:
        super().setUp()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        self.outside = Path(outside.name).resolve()
        self.addCleanup(nonhead.writable_again, self.outside)  # before the temp cleanup

    def run_chain(self, baseline) -> tuple[dict, list, list, list]:
        windows: list = []
        events: list = []
        rejected: list = []
        inner = self.producer(baseline=baseline)

        def step(child):
            with guard_window(started=lambda: bool(self.log.get("baselines")),
                              clones=lambda: list(self.log["clones"]), windows=windows, events=events):
                try:
                    return inner(child)
                except OSError as exc:  # as the runner process would: traceback, exit 1, no record
                    rejected.append(exc)
                    return f"Traceback (most recent call last):\n{type(exc).__name__}: {exc}\n", 1

        return self.invoke([step]), windows, events, rejected

    def test_symlinked_retained_ancestor_after_the_baseline_blocks_before_cleanup(self) -> None:
        for name, (rel, index) in self.CASES.items():
            with self.subTest(case=name):
                self.tearDown_reset()
                self.configure()
                expected = integrity.head_bytes(self.root, self.head)
                external = external_fixture(self.outside / name.replace("/", "").replace(" ", "-"),
                                            rel, expected)
                # Precondition on the external target itself: HEAD's exact bytes plus EXTRA.
                leaves = {n: d for n, d in expected.items() if n.startswith(f"{rel}/")}
                self.assertTrue(leaves)
                for path, data in leaves.items():
                    self.assertEqual((external / path[len(rel) + 1:]).read_bytes(), data)
                self.assertTrue((external / EXTRA[rel][0]).is_file())
                outside_before = nonhead.snapshot(external)
                checkout_before = checkout_state(self.root)
                probes: list = []

                result, windows, events, rejected = self.run_chain(
                    linked_after_baseline(rel, index, external, expected, probes))

                self.blocked(result, "missing completion record")  # FAIL+FAIL, no APPROVE/acceptance
                log = self.log
                self.assertEqual(log["mutants"], [])  # rejected before any mutation
                self.assertEqual(len(log["baselines"]), 1)
                self.assertEqual(len(log["clones"]), 2)  # two workers were prepared
                clone = log["clones"][index]
                self.assertEqual(Path(clone).name, f"copy{index}")
                if index:  # a later worker copy, not the one the baseline ran in
                    self.assertNotEqual(clone, log["baselines"][0][0])
                link = str(Path(clone) / rel)
                self.assertEqual(probes, [(link, dict.fromkeys(leaves, True), True)])
                # The rejection is the guard's, naming the symlinked ancestor.
                self.assertEqual(len(rejected), 1, rejected)
                self.assertEqual(rejected[0].filename and os.fsdecode(os.fspath(rejected[0].filename)), link)
                # Rejected in the first post-baseline exclusion, before any cleanup or read.
                self.assertEqual([w["name"] for w in windows], ["exclude"])
                self.assertIs(windows[0]["error"], rejected[0])
                for each in log["clones"]:
                    self.assertEqual(windows[0]["after"][each], windows[0]["before"][each])
                    self.assertEqual(windows[0]["before"][each][SENTINEL[0]][::2],
                                     ("file", SENTINEL[1].encode()))
                linked = windows[0]["after"][clone][rel]
                self.assertEqual((linked[0], linked[2]), ("symlink", str(external)))  # left in place
                self.assertEqual(through_link(events, link, str(external)), [])
                self.assertEqual(above_copies(events, log["clones"]), [])
                # Nothing outside the disposable copies changed: target and checkout.
                self.assertEqual(nonhead.snapshot(external), outside_before)
                self.assertEqual(checkout_state(self.root), checkout_before)

    def test_real_ancestors_allow_cleanup_and_completion_positive_control(self) -> None:
        self.configure()

        def outputs(copy: Path) -> None:
            for clone in sorted(copy.parent.glob("copy*")):
                (clone / SENTINEL[0]).write_text(SENTINEL[1])
                (clone / "tests" / EXTRA["tests"][0]).write_text(EXTRA["tests"][1])

        result, windows, events, rejected = self.run_chain(outputs)
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        self.assertEqual(rejected, [])
        self.assertEqual({key for key, _ in self.log["mutants"]}, integrity.HOT)
        clones = self.log["clones"]
        self.assertEqual(len(clones), 2)
        self.assertEqual([w["name"] for w in windows], ["exclude", "drift"])
        # The snapshots see cleanup: exactly the sentinel and the extra test went, per copy.
        gone = {SENTINEL[0], f"tests/{EXTRA['tests'][0]}"}
        for clone in clones:
            before, after = windows[0]["before"][clone], windows[0]["after"][clone]
            self.assertTrue(gone <= set(before), sorted(before))
            self.assertEqual(after, {k: v for k, v in before.items() if k not in gone})
            # The probes see tracked reads through real ancestors (not vacuous).
            self.assertIn(("drift", "open", os.path.join(clone, "tests", "test_example.py")), events)
        self.assertEqual(above_copies(events, clones), [])


for _name in [name for name in dir(integrity.IntegrityCallerTest) if name.startswith("test_")]:
    setattr(AncestorLinkCallerTest, _name, None)


class AncestorLinkGuardTest(traversal.TraversalFixture):
    """exclude_non_head itself (complements, never replaces, the caller cases above). The
    fixture's own cleanup restores owner modes under root.parent (incl. outside/)."""

    def test_symlinked_ancestor_in_a_later_copy_is_rejected_before_any_copy_is_cleaned(self) -> None:
        scope = mutation_scope.compute(self.base)
        for rel in EXTRA:
            with self.subTest(rel=rel):
                copies = [self.prepare_copy(f"{rel}-copy{i}") for i in range(2)]
                for copy in copies:
                    (copy / SENTINEL[0]).write_text(SENTINEL[1])
                external = external_fixture(self.root.parent / "outside" / rel, rel, self.expected)
                link = copies[1] / rel
                shutil.rmtree(link)
                link.symlink_to(external, target_is_directory=True)
                before = {copy: nonhead.snapshot(copy) for copy in copies}
                outside_before = nonhead.snapshot(external)
                events: list = []
                active: list = []

                def call(copies=copies, active=active):
                    active.append("exclude")
                    try:
                        return mutation.exclude_non_head(scope, *copies)
                    finally:
                        active.pop()

                with recorded_probes(events, active), self.assertRaises(OSError) as raised:
                    call()
                self.assertEqual(os.fsdecode(os.fspath(raised.exception.filename)), str(link))
                self.assertEqual({copy: nonhead.snapshot(copy) for copy in copies}, before)  # copy0 too
                self.assertEqual(nonhead.snapshot(external), outside_before)
                self.assertEqual(through_link(events, str(link), str(external)), [])
                self.assertEqual(above_copies(events, [str(c) for c in copies]), [])

    def assert_linked_copy(self, copy: Path, rel: str, link: Path, external: Path,
                           expected: dict[str, bytes], extra: str) -> list[str]:
        """Precondition, read with recording off: HEAD's exact bytes through the link plus
        the extra input. Returns HEAD's tracked paths under `rel`."""
        self.assertEqual((link.is_symlink(), os.readlink(link)), (True, str(external)))
        leaves = [name for name in expected if name.startswith(f"{rel}/")]
        self.assertTrue(leaves)
        self.assertTrue(integrity.matches_head(copy, expected))  # HEAD's bytes everywhere
        self.assertTrue((link / extra).is_file())
        return leaves

    def test_tracked_mismatches_reports_a_symlinked_ancestor_without_following_it(self) -> None:
        for rel in EXTRA:
            with self.subTest(rel=rel):
                copy = self.prepare_copy(f"{rel}-mismatch-copy")
                self.assertTrue(integrity.matches_head(copy, self.expected))
                external = external_fixture(self.root.parent / "outside" / f"{rel}-mismatch",
                                            rel, self.expected)
                link = link_ancestor(copy, rel, external)
                leaves = self.assert_linked_copy(copy, rel, link, external, self.expected, EXTRA[rel][0])
                before, outside_before = nonhead.snapshot(copy), nonhead.snapshot(external)
                events: list = []

                mismatches = recorded_call(events, "mismatches", mutation_scope.tracked_mismatches,
                                           self.head, copy)

                self.assertIsInstance(mismatches, list)
                self.assertTrue(mismatches)  # identical bytes behind a link are not HEAD's
                self.assertLessEqual(set(mismatches), set(leaves))  # HEAD tracked paths only
                self.assertEqual(through_link(events, str(link), str(external)), [])
                self.assertEqual(above_copies(events, [str(copy)]), [])
                self.assertEqual(nonhead.snapshot(copy), before)
                self.assertEqual(nonhead.snapshot(external), outside_before)

    def test_nested_head_path_stops_at_the_symlinked_ancestor(self) -> None:
        nested = self.root / NESTED[0]
        nested.parent.mkdir()
        nested.write_text(NESTED[1])
        git(self.root, "add", "--", NESTED[0])
        git(self.root, "commit", "-q", "--no-verify", "-m", "nested HEAD path")
        head = git(self.root, "rev-parse", "HEAD")
        expected = integrity.head_bytes(self.root, head)
        self.assertEqual(expected[NESTED[0]], NESTED[1].encode())
        scope = mutation_scope.compute(self.base)
        self.assertEqual(scope.head, head)
        for rel in ("tests", "tests/nested"):  # the shallow and the nested ancestor
            with self.subTest(rel=rel):
                label = rel.replace("/", "-")
                copy = self.prepare_copy(f"nested-{label}-copy")
                self.assertTrue(integrity.matches_head(copy, expected))
                (copy / SENTINEL[0]).write_text(SENTINEL[1])
                external = external_fixture(self.root.parent / "outside" / f"nested-{label}", rel,
                                            expected, extra=EXTRA["tests"])
                link = link_ancestor(copy, rel, external)
                leaves = self.assert_linked_copy(copy, rel, link, external, expected, EXTRA["tests"][0])
                self.assertIn(NESTED[0], leaves)
                before, outside_before = nonhead.snapshot(copy), nonhead.snapshot(external)
                events: list = []

                with self.assertRaises(OSError) as raised:
                    recorded_call(events, "exclude", mutation.exclude_non_head, scope, copy)
                self.assertEqual(os.fsdecode(os.fspath(raised.exception.filename)), str(link))
                self.assertEqual(nonhead.snapshot(copy), before)  # sentinel kept: nothing cleaned
                self.assertEqual(nonhead.snapshot(external), outside_before)

                mismatches = recorded_call(events, "mismatches", mutation_scope.tracked_mismatches,
                                           head, copy)
                self.assertIn(NESTED[0], mismatches)
                self.assertLessEqual(set(mismatches), set(leaves))
                self.assertEqual(through_link(events, str(link), str(external)), [])
                self.assertEqual(above_copies(events, [str(copy)]), [])
                self.assertEqual(nonhead.snapshot(copy), before)
                self.assertEqual(nonhead.snapshot(external), outside_before)

    def test_committed_symlink_leaves_are_kept_and_matched_positive_control(self) -> None:
        (self.root / "tests" / "example_link.py").symlink_to("../factory/example.py")
        (self.root / "factory_link").symlink_to("factory", target_is_directory=True)
        git(self.root, "add", "--", "tests/example_link.py", "factory_link")
        git(self.root, "commit", "-q", "--no-verify", "-m", "symlink leaves")
        head = git(self.root, "rev-parse", "HEAD")
        modes = {line.split("\t")[1]: line.split()[0] for line in
                 git(self.root, "ls-tree", "-r", head, "--", "tests/example_link.py", "factory_link").splitlines()}
        self.assertEqual(modes, {"tests/example_link.py": "120000", "factory_link": "120000"})
        scope = mutation_scope.compute(self.base)
        self.assertEqual(scope.head, head)
        copy = self.root.parent / "leaf-copy"  # as a checkout of HEAD holds it (see docstring)
        integrity.REAL_RUN(["git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(self.root), str(copy)], check=True)
        integrity.REAL_COPYTREE(self.root, copy, ignore=mutation.COPY_IGNORE, dirs_exist_ok=True,
                                symlinks=True)
        (copy / SENTINEL[0]).write_text(SENTINEL[1])
        target_before = nonhead.snapshot(copy / "factory")
        checkout_before = nonhead.snapshot(self.root)
        removed = mutation.exclude_non_head(scope, copy)
        self.assertEqual([r.split(": ", 1)[1] for r in removed], [SENTINEL[0]])  # ordinary cleanup
        self.assertEqual(os.readlink(copy / "tests" / "example_link.py"), "../factory/example.py")
        self.assertEqual(os.readlink(copy / "factory_link"), "factory")
        self.assertEqual(mutation_scope.tracked_mismatches(head, copy), [])
        self.assertEqual(nonhead.snapshot(copy / "factory"), target_before)
        self.assertEqual(nonhead.snapshot(self.root), checkout_before)


if __name__ == "__main__":
    unittest.main()
