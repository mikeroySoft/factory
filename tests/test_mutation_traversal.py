"""Proof-mode exclusion must fail closed when it cannot traverse a prepared copy.

Gap (PM-confirmed, not observed in a real mutation campaign): exclude_non_head walks each
copy with os.walk and no onerror, so a retained directory (or the copy root) that cannot be
listed is silently skipped. Known HEAD tracked files there remain readable by path, so the
tracked-byte check still passes, and a non-HEAD entry inside survives into the mutant
children. These cases require the traversal to raise (main writes no completion record; the
gate reads FAIL), and require the starting copy to be a real, owned, visited directory.

Real: scripts.mutation.main in proof mode and its real exclude_non_head / os.walk, child
preparation (`git clone --shared --no-checkout` + shutil.copytree), the tracked-byte
check, the tally/record writer, gate.read_mutation_proof and (caller cases) the witness
harness's dispatcher -> gate -> risk-check -> head-acceptance chain. Synthetic only as in
tests/test_mutation_input_integrity.py: cosmic-ray init/use_db, mutate_and_test (a recorder
that appends to log["mutants"] FIRST and never raises), attrs.evolve, the baseline child,
plus the witness harness's process/record/reviewer boundaries. No raising observer
(tests/test_mutation_nonhead_inputs.observed_children) is used here: on old code it would
raise on the unreadable directory inside the mutant step and make these tests pass for the
wrong reason. Old code therefore shows up as recorded mutants and a complete PASS record.

Fault sources:
- Real permissions: a 0311 (write+search, no read) retained `tests/` or copy root after the
  baseline. Coverage requires this test identity to be unable to list such a directory
  (non-root macOS/Linux); that precondition is probed first and the case is skipped with an
  explicit limitation message otherwise (e.g. root), never silently claimed.
- Injected: os.scandir raises EACCES for one target only when called directly by os.walk
  (frame check), only while the real exclude_non_head runs and only in the chosen phase
  (preparation and/or post-baseline). Not raised in fixture setup, baseline or reporting.
- Starting-copy guards: missing, non-directory and symlink roots are real; a non-owned root
  is os.geteuid/os.getuid reporting another uid during the call only; a walk that never
  visits the root is os.walk replaced during the call only.
"""
from __future__ import annotations

import contextlib
import errno
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from factory import mutation_scope
from scripts import mutation
from tests import test_mutation_input_integrity as integrity
from tests import test_mutation_nonhead_inputs as nonhead

LIMITATION = ("this test identity can list a 0311 directory (e.g. root): the real-permission "
              "traversal bypass is not reproducible here; only the injected cases cover it")
INJECTED_TEST = ("tests/test_injected.py", nonhead.SHADOW_TEST)
INJECTED_SHADOW = ("config.py", nonhead.SHADOW_MODULE)  # shadows the source's `import config`


def identity_cannot_list_0311() -> bool:
    """Precondition probe: True only if a 0311 directory raises PermissionError on listing
    for this identity while an entry in it stays readable by path."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe"
        probe.mkdir()
        (probe / "entry").write_text("known\n")
        os.chmod(probe, 0o311)
        try:
            try:
                with os.scandir(probe) as entries:
                    list(entries)
            except PermissionError:
                return (probe / "entry").read_text() == "known\n"
            return False
        finally:
            os.chmod(probe, 0o700)


def unlistable_after_baseline(rel: str, entry: tuple[str, str], probes: list, expected: dict):
    """Baseline action: in every child write a real non-HEAD `entry`, then chmod `rel`
    ("." is the copy root) 0311. Records, per child, (target, listing raised
    PermissionError, every HEAD tracked file still holds HEAD bytes by path)."""
    path, content = entry

    def baseline(copy: Path) -> None:
        for clone in sorted(copy.parent.glob("copy*")):
            (clone / path).write_text(content)
            target = clone if rel == "." else clone / rel
            os.chmod(target, 0o311)
            try:
                with os.scandir(target) as entries:
                    list(entries)
                unlistable = False
            except PermissionError:
                unlistable = True
            probes.append((str(target), unlistable, integrity.matches_head(clone, expected)))
    return baseline


def written_after_baseline(entry: tuple[str, str]):
    path, content = entry

    def baseline(copy: Path) -> None:
        for clone in sorted(copy.parent.glob("copy*")):
            (clone / path).write_text(content)
    return baseline


@contextlib.contextmanager
def unlistable_during_cleanup(rel: str, *, phases: set[str], started, injected: list):
    """Injected EACCES from os.scandir on `<copy>/<rel>` ("." is the copy root), raised
    only when the caller frame is os.walk itself, only while the real
    scripts.mutation.exclude_non_head runs, and only in `phases` ("prep": before the
    baseline, "post": after it, as `started()` reports). Each raised error is appended to
    `injected`."""
    real_exclude = mutation.exclude_non_head
    real_scandir = os.scandir
    walk_code = os.walk.__code__
    targets: set[str] = set()

    def scandir(path="."):
        if targets and isinstance(path, (str, bytes, os.PathLike)) \
                and sys._getframe(1).f_code is walk_code \
                and os.fsdecode(os.fspath(path)) in targets:
            error = PermissionError(errno.EACCES, "Permission denied (injected)", os.fsdecode(os.fspath(path)))
            injected.append(error)
            raise error
        return real_scandir(path)

    def exclude_non_head(scope, *copies):
        if ("post" if started() else "prep") in phases:
            targets.update(os.fspath(c) if rel == "." else os.path.join(os.fspath(c), rel) for c in copies)
        try:
            return real_exclude(scope, *copies)
        finally:
            targets.clear()

    with mock.patch.object(os, "scandir", scandir), \
            mock.patch.object(mutation, "exclude_non_head", exclude_non_head):
        yield


def is_injected(error: BaseException, injected: list) -> bool:
    return any(error is e or error.__cause__ is e for e in injected)


@contextlib.contextmanager
def process_uid(uid: int):
    """Bounded uid metadata injection: this process reports `uid` as its (effective) uid."""
    with mock.patch.object(os, "geteuid", lambda: uid), mock.patch.object(os, "getuid", lambda: uid):
        yield


class TraversalFixture(integrity.ProducerFixture):
    def setUp(self) -> None:
        super().setUp()
        self.expected = integrity.head_bytes(self.root, self.head)
        self.head_names = set(self.expected)
        # Production's scratch directory lives in this test's temp; owner modes are given
        # back only at test cleanup, after production behavior was measured.
        scratch = self.root.parent / "scratch"
        scratch.mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(scratch))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(nonhead.writable_again, self.root.parent)  # before the temp cleanup

    def run_producer(self, log: dict, **boundaries) -> tuple[int, str]:
        """mutation.main in proof mode; `log` stays readable if main raises."""
        self.proof.unlink(missing_ok=True)
        os.environ[mutation.PROOF_ENV] = str(self.proof)
        os.environ[mutation.PROOF_BASE_ENV] = self.base
        out = io.StringIO()
        with integrity.producer_boundaries(log, root=self.root, head=self.head, **boundaries), \
                contextlib.redirect_stdout(out):
            code = mutation.main(["--blocking"])
        return code, out.getvalue()

    def assert_no_mutation_work(self, log: dict, *, baseline_ran: bool) -> None:
        self.assertEqual(log["mutants"], [])  # zero mutation work
        self.assertIsNone(self.record())  # no completion record
        self.assertEqual(self.verdict(1), "FAIL")
        self.assertEqual(len(log["baselines"]), 1 if baseline_ran else 0)
        self.assertEqual(len(log["clones"]), 2 if baseline_ran else 1)

    def prepare_copy(self, name: str) -> Path:
        copy = self.root.parent / name
        integrity.REAL_RUN(["git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(self.root), str(copy)], check=True)
        integrity.REAL_COPYTREE(self.root, copy, ignore=mutation.COPY_IGNORE, dirs_exist_ok=True)
        return copy


class RealPermissionTraversalTest(TraversalFixture):
    """Real 0311 modes after the baseline (expected RED on the old silent walker)."""

    def setUp(self) -> None:
        super().setUp()
        if not identity_cannot_list_0311():
            self.skipTest(LIMITATION)

    def assert_real_failure_fails_closed(self, rel: str, entry: tuple[str, str]) -> None:
        log: dict = {}
        probes: list = []
        with self.assertRaises(PermissionError) as raised:
            self.run_producer(log, baseline=unlistable_after_baseline(rel, entry, probes, self.expected))
        # The precondition held on the actual children: unlistable, tracked bytes readable.
        self.assertEqual(len(probes), 2)
        self.assertTrue(all(unlistable and readable for _, unlistable, readable in probes), probes)
        # The failure is the traversal of that directory, not anything else.
        self.assertIn(raised.exception.filename, {target for target, _, _ in probes})
        self.assert_no_mutation_work(log, baseline_ran=True)

    def test_unlistable_retained_tests_directory_fails_closed(self) -> None:
        self.assert_real_failure_fails_closed("tests", INJECTED_TEST)

    def test_unlistable_copy_root_fails_closed(self) -> None:
        self.assert_real_failure_fails_closed(".", INJECTED_SHADOW)


class InjectedTraversalTest(TraversalFixture):
    """Portable: injected os.scandir failures inside the real os.walk."""

    CASES: ClassVar[dict[str, tuple[str, set[str], str]]] = {  # name -> (rel, phases, entry introduced in the checkout or by the baseline)
        "root at preparation": (".", {"prep", "post"}, "checkout"),
        "tests/ at preparation": ("tests", {"prep", "post"}, "checkout"),
        "root after the baseline": (".", {"post"}, "baseline"),
        "tests/ after the baseline": ("tests", {"post"}, "baseline"),
    }

    def test_unlistable_root_or_retained_directory_fails_closed(self) -> None:
        for name, (rel, phases, source) in self.CASES.items():
            with self.subTest(case=name):
                entry = INJECTED_SHADOW if rel == "." else INJECTED_TEST
                log: dict = {}
                injected: list = []
                boundaries = {}
                if source == "checkout":
                    (self.root / entry[0]).write_text(entry[1])  # untracked in the checkout
                else:
                    boundaries["baseline"] = written_after_baseline(entry)
                try:
                    with unlistable_during_cleanup(rel, phases=phases, injected=injected,
                                                   started=lambda log=log: bool(log.get("baselines"))), \
                            self.assertRaises(OSError) as raised:
                        self.run_producer(log, **boundaries)
                finally:
                    (self.root / entry[0]).unlink(missing_ok=True)
                self.assertTrue(injected)  # the real os.walk reached the injected fault
                self.assertTrue(is_injected(raised.exception, injected), raised.exception)
                self.assert_no_mutation_work(log, baseline_ran=source == "baseline")

    def test_injection_harness_is_transparent_positive_control(self) -> None:
        log: dict = {}
        injected: list = []
        with unlistable_during_cleanup("no-such-dir", phases={"prep", "post"}, injected=injected,
                                       started=lambda: bool(log.get("baselines"))):
            code, _ = self.run_producer(log, baseline=written_after_baseline(INJECTED_TEST))
        self.assertEqual(injected, [])
        record = self.record()
        self.assertEqual((code, record["status"], record["reason"]), (0, "complete", "no-blocking-survivors"))
        self.assertEqual(self.verdict(code), "PASS")
        self.assertEqual({key for key, _ in log["mutants"]}, integrity.HOT)
        self.assertTrue(all(intact for _, intact in log["mutants"]))


class StartingCopyGuardTest(TraversalFixture):
    """exclude_non_head itself: the starting copy is a real, owned directory it visits."""

    def setUp(self) -> None:
        super().setUp()
        self.scope = mutation_scope.compute(self.base)

    def test_real_owned_directory_is_cleaned_positive_control(self) -> None:
        copy = self.prepare_copy("copy0")
        for path, content in (INJECTED_TEST, INJECTED_SHADOW):
            (copy / path).write_text(content)
        with process_uid(os.geteuid()):  # the uid injection harness, reporting the real uid
            removed = mutation.exclude_non_head(self.scope, copy)
        self.assertEqual(sorted(r.split(": ", 1)[1] for r in removed), ["config.py", "tests/test_injected.py"])
        self.assertEqual(nonhead.non_head_paths(copy, self.head_names), [])
        self.assertEqual(mutation_scope.tracked_mismatches(self.head, copy), [])

    def test_missing_root_raises(self) -> None:
        missing = self.root.parent / "missing-copy"
        with self.assertRaises(OSError):
            mutation.exclude_non_head(self.scope, missing)
        self.assertFalse(os.path.lexists(missing))

    def test_non_directory_root_raises_and_is_unchanged(self) -> None:
        not_dir = self.root.parent / "copy-file"
        not_dir.write_text("not a directory\n")
        os.chmod(not_dir, 0o444)
        before = nonhead.snapshot(not_dir)
        with self.assertRaises(OSError):
            mutation.exclude_non_head(self.scope, not_dir)
        self.assertEqual(nonhead.snapshot(not_dir), before)

    def test_symlink_root_raises_without_touching_the_external_target(self) -> None:
        external = self.prepare_copy("external")  # HEAD layout plus a non-HEAD entry
        (external / INJECTED_TEST[0]).write_text(INJECTED_TEST[1])
        os.chmod(external / "tests", 0o555)
        os.chmod(external, 0o555)
        before = nonhead.snapshot(external)
        link = self.root.parent / "copy-link"
        link.symlink_to(external, target_is_directory=True)
        with self.assertRaises(OSError):
            mutation.exclude_non_head(self.scope, link)
        self.assertEqual(nonhead.snapshot(external), before)  # bytes and modes unchanged
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(external))

    def test_non_owned_root_raises_and_is_unchanged(self) -> None:
        copy = self.prepare_copy("copy0")
        (copy / INJECTED_TEST[0]).write_text(INJECTED_TEST[1])
        before = nonhead.snapshot(copy)
        with process_uid(os.lstat(copy).st_uid + 1), self.assertRaises(OSError):
            mutation.exclude_non_head(self.scope, copy)
        self.assertEqual(nonhead.snapshot(copy), before)

    def test_walk_that_never_visits_the_root_raises(self) -> None:
        copy = self.prepare_copy("copy0")
        (copy / INJECTED_TEST[0]).write_text(INJECTED_TEST[1])
        walks = {
            "empty": lambda top, *a, **k: iter(()),
            "subdirectory only": lambda top, *a, **k: iter(
                [(os.path.join(os.fspath(top), "tests"), [], ["test_example.py"])]),
        }
        for name, walk in walks.items():
            with self.subTest(walk=name):
                before = nonhead.snapshot(copy)
                with mock.patch.object(os, "walk", walk), self.assertRaises(OSError):
                    mutation.exclude_non_head(self.scope, copy)
                self.assertEqual(nonhead.snapshot(copy), before)


class TraversalCallerTest(integrity.IntegrityCallerTest):
    """Through the real dispatcher -> gate -> risk-check -> head-acceptance chain."""

    def failing(self, inner, context):
        def step(child):
            with context():
                try:
                    return inner(child)
                except OSError as exc:  # as the runner process would: traceback, exit 1, no record
                    return f"Traceback (most recent call last):\n{type(exc).__name__}: {exc}\n", 1
        return step

    def assert_blocked_without_mutation(self, result: dict) -> None:
        self.blocked(result, "missing completion record")
        self.assertEqual(len(self.log["baselines"]), 1)
        self.assertEqual(self.log["mutants"], [])

    def test_injected_unlistable_directory_after_the_baseline_blocks(self) -> None:
        for rel, entry in ((".", INJECTED_SHADOW), ("tests", INJECTED_TEST)):
            with self.subTest(rel=rel):
                self.tearDown_reset()
                self.configure()
                injected: list = []
                inner = self.producer(baseline=written_after_baseline(entry))

                def context(rel=rel, injected=injected):
                    return unlistable_during_cleanup(rel, phases={"post"}, injected=injected,
                                                     started=lambda: bool(self.log.get("baselines")))

                self.assert_blocked_without_mutation(self.invoke([self.failing(inner, context)]))
                self.assertTrue(injected)

    def test_real_unlistable_tests_directory_after_the_baseline_blocks(self) -> None:
        if not identity_cannot_list_0311():
            self.skipTest(LIMITATION)
        self.configure()
        probes: list = []
        expected = integrity.head_bytes(self.root, self.head)
        inner = self.producer(baseline=unlistable_after_baseline("tests", INJECTED_TEST, probes, expected))
        self.assert_blocked_without_mutation(self.invoke([self.failing(inner, contextlib.nullcontext)]))
        self.assertTrue(probes and all(unlistable and readable for _, unlistable, readable in probes), probes)


for _name in [name for name in dir(integrity.IntegrityCallerTest) if name.startswith("test_")]:
    setattr(TraversalCallerTest, _name, None)


if __name__ == "__main__":
    unittest.main()
