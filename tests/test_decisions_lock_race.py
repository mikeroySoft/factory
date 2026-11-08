"""Decision locks under the dirfd-relative O_CREAT create race, without kernel timing.

On Darwin the loser of two concurrent openat(dirfd, name, O_RDWR|O_CREAT) calls for one
new name was observed to fail with ENOENT while the directory stayed linked and the other
opener created the name; O_CREAT|O_EXCL racers got EEXIST instead. These tests make
os.open return that outcome deterministically and check that apply still serializes,
runs each step once, keeps lock identity, and fails closed on an unusable directory or name.

Supported, and asserted: the lock name is resolved relative to the already-open directory
fd, a symlinked name is never followed, and a name that vanishes after EEXIST is created
again a bounded number of times. Not supported, so never asserted: mutual exclusion against
a third party that unlinks, replaces or renames the lock name or the state directory while
an apply holds its lock.

This is a bounded create-race correction (b2), not replacement-resistant exactly-once:
the held-name unlink/recreate limitation remains OPEN.
"""
from __future__ import annotations

import errno
import fcntl
import os
import stat
import threading
import unittest
from unittest import mock

from factory import decisions
from tests import test_decisions as base

WAIT = 10.0  # seconds; every wait below is bounded by it
REAL_OPEN = os.open
REAL_FLOCK = fcntl.flock


def identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def after_eexist(name: str, *actions):
    """os.open running actions[i] right after the i-th O_EXCL open of name fails with EEXIST."""
    pending = list(actions)
    opens: list[str] = []

    def fake(path, flags, mode=0o777, *, dir_fd=None):
        if path != name or dir_fd is None:
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        opens.append("excl" if flags & os.O_EXCL else "plain")
        try:
            return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
        except FileExistsError:
            if flags & os.O_EXCL and pending:
                pending.pop(0)()
            raise

    return fake, opens


def make_lock(path) -> tuple[int, int]:
    os.close(REAL_OPEN(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600))
    return identity(os.lstat(path))



def spurious_enoent(name: str, victims: set[str]):
    """os.open failing each victim thread's first plain O_CREAT open of an existing name.

    Regression guard only: the pre-fix `_lock_file` opened with plain O_CREAT and hit this
    ENOENT. The current `_lock_file` never issues that open (O_CREAT|O_EXCL, then no O_CREAT),
    so with the fix applied the injection never fires and the real EEXIST path runs instead.
    """
    injected: list[str] = []

    def fake(path, flags, mode=0o777, *, dir_fd=None):
        thread = threading.current_thread().name
        if (
            path == name and dir_fd is not None and thread in victims and thread not in injected
            and flags & os.O_CREAT and not flags & os.O_EXCL
        ):
            try:
                os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                injected.append(thread)
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)

    return fake


class DecisionLockRaceTest(unittest.TestCase):
    setUp = base.DecisionTest.setUp
    completed = staticmethod(base.DecisionTest.completed)
    touch = base.DecisionTest.touch
    edit = base.DecisionTest.edit
    mutations = base.DecisionTest.mutations
    proposal = base.DecisionTest.proposal
    apply = base.DecisionTest.apply
    hold = None

    def transport(self, argv: list[str], **kwargs):
        if self.hold is not None and argv[:3] == ["gh", "issue", "comment"]:
            self.hold()
        return base.DecisionTest.transport(self, argv, **kwargs)

    def directory(self) -> int:
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        return fd

    def test_create_race_loser_reopens_after_eexist_waits_and_replays_the_single_receipt(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Raced once"}])
        name = f"apply-{preview['proposal_id']}.lock"
        in_effect, release, loser_blocked = threading.Event(), threading.Event(), threading.Event()
        fake = spurious_enoent(name, {"loser"})
        lock_fds: dict[str, int] = {}
        dir_fds: dict[str, int] = {}
        held: dict[str, tuple[int, int]] = {}

        def opener(path, flags, mode=0o777, *, dir_fd=None):
            fd = fake(path, flags, mode, dir_fd=dir_fd)
            if path == name and dir_fd is not None:
                lock_fds[threading.current_thread().name] = fd
                dir_fds[threading.current_thread().name] = dir_fd
            return fd

        def flock(fd, operation):
            role = threading.current_thread().name
            try:
                result = REAL_FLOCK(fd, operation)
            except BlockingIOError:
                if role == "loser" and lock_fds.get(role) == fd and not loser_blocked.is_set():
                    held["loser"] = identity(os.fstat(fd))
                    held["loser dir"] = identity(os.fstat(dir_fds[role]))
                    loser_blocked.set()
                raise
            if role == "winner" and lock_fds.get(role) == fd and operation & fcntl.LOCK_EX:
                held["winner"] = identity(os.fstat(fd))
                held["winner dir"] = identity(os.fstat(dir_fds[role]))
            return result

        def hold() -> None:
            in_effect.set()
            if not release.wait(WAIT):
                raise TimeoutError("the competing apply never blocked on the held apply lock")

        results: dict[str, dict] = {}
        errors: dict[str, BaseException] = {}

        def run(role: str) -> None:
            try:
                results[role] = self.apply(preview)
            except BaseException as exc:  # reported by the assertions below
                errors[role] = exc

        winner = threading.Thread(target=run, args=("winner",), name="winner")
        loser = threading.Thread(target=run, args=("loser",), name="loser")
        self.hold = hold
        with (
            mock.patch.object(decisions.os, "open", opener),
            mock.patch.object(decisions.fcntl, "flock", flock),
        ):
            try:
                winner.start()
                self.assertTrue(in_effect.wait(WAIT), "the first apply never reached its effect")
                loser.start()
                blocked = loser_blocked.wait(WAIT)
                # Observed while the winner is parked inside its effect and holds its lock.
                loser_pending = loser.is_alive() and not {"loser"} & (results.keys() | errors.keys())
                mutations_while_held = self.mutations()
                held_while_held = dict(held)
                [linked] = self.root.rglob(name)
                linked_while_held = identity(os.lstat(linked)), identity(os.stat(linked.parent))
                probe = REAL_OPEN(linked, os.O_RDWR | os.O_NOFOLLOW)
                try:
                    with self.assertRaises(BlockingIOError):
                        REAL_FLOCK(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(probe)
            finally:
                release.set()
                winner.join(WAIT)
                loser.join(WAIT)
        self.assertTrue(blocked, "the second apply never tried and failed to take the held lock")
        self.assertTrue(loser_pending, "the second apply finished before the first released")
        self.assertEqual(mutations_while_held, [])
        self.assertEqual(held_while_held["loser"], held_while_held["winner"])
        self.assertEqual(held_while_held["loser dir"], held_while_held["winner dir"])
        self.assertEqual(linked_while_held, (held_while_held["winner"], held_while_held["winner dir"]))
        self.assertFalse(winner.is_alive() or loser.is_alive())
        failed = {role: getattr(exc, "code", repr(exc)) for role, exc in errors.items()}
        self.assertEqual(failed, {})
        self.assertEqual(results["winner"]["status"], "success")
        self.assertEqual(results["loser"], results["winner"])
        self.assertEqual(self.mutations(), ["comment"])

    def test_lock_open_creates_a_new_name_once_and_reopens_an_existing_inode(self) -> None:
        directory = self.directory()
        existing = self.root / "apply-x.lock"
        existing.touch(mode=0o600)
        spurious = spurious_enoent(existing.name, {threading.current_thread().name})
        opens: list[tuple[str, bool, bool]] = []

        def recording(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd == directory:
                opens.append((path, bool(flags & os.O_CREAT), bool(flags & os.O_EXCL)))
            return spurious(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(decisions.os, "open", recording):
            created = decisions._lock_file(directory, "apply-new.lock")
            self.addCleanup(os.close, created)
            reopened = decisions._lock_file(directory, existing.name)
            self.addCleanup(os.close, reopened)
        self.assertEqual(opens, [
            ("apply-new.lock", True, True),
            (existing.name, True, True),
            (existing.name, False, False),
        ])
        self.assertEqual(stat.S_IMODE(os.fstat(created).st_mode), 0o600)
        opened, expected = os.fstat(reopened), existing.stat()
        self.assertEqual((opened.st_dev, opened.st_ino), (expected.st_dev, expected.st_ino))

    def test_removed_directory_or_symlinked_lock_name_fails_closed(self) -> None:
        victim = self.root / "victim"
        victim.mkdir()
        removed = os.open(victim, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, removed)
        victim.rmdir()
        live = self.directory()
        target = self.root / "target"
        target.write_text("keep")
        (self.root / "file.lock").symlink_to(target)
        (self.root / "dangling.lock").symlink_to(self.root / "absent")
        for label, directory, name, cause in (
            ("removed directory", removed, "apply-x.lock", errno.ENOENT),
            ("symlink to a file", live, "file.lock", errno.ELOOP),
            ("dangling symlink", live, "dangling.lock", errno.ELOOP),
        ):
            with self.subTest(label):
                calls: list[str] = []

                def counting(path, flags, mode=0o777, *, dir_fd=None):
                    calls.append(path)
                    return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)

                with (
                    mock.patch.object(decisions.os, "open", counting),
                    self.assertRaises(decisions.DecisionError) as caught,
                ):
                    decisions._lock_file(directory, name)
                self.assertEqual(caught.exception.code, "storage_unavailable")
                self.assertEqual(caught.exception.__cause__.errno, cause)
                if directory == removed:
                    self.assertEqual(calls, [name])
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse((self.root / "absent").exists())

    def test_lock_name_that_keeps_vanishing_exhausts_a_bounded_number_of_opens(self) -> None:
        directory = self.directory()
        exclusive: list[bool] = []

        def churn(path, flags, mode=0o777, *, dir_fd=None):
            if path != "apply-x.lock":
                return REAL_OPEN(path, flags, mode, dir_fd=dir_fd)
            exclusive.append(bool(flags & os.O_EXCL))
            if flags & os.O_EXCL:
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)

        with (
            mock.patch.object(decisions.os, "open", churn),
            self.assertRaises(decisions.DecisionError) as caught,
        ):
            decisions._lock_file(directory, "apply-x.lock")
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertEqual(exclusive, [True, False] * 3)

    def locks_dir(self, label: str = "locks"):
        path = self.root / label
        path.mkdir()
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        return path, fd

    def opened(self, fd: int) -> tuple[int, int]:
        self.addCleanup(os.close, fd)
        return identity(os.fstat(fd))

    def test_symlink_substituted_after_eexist_fails_closed_without_following_it(self) -> None:
        path, directory = self.locks_dir()
        original_dir = identity(os.stat(path))
        lock = path / "apply-x.lock"
        make_lock(lock)
        target = self.root / "target"
        target.write_text("keep")
        os.chmod(target, 0o600)
        target_before = identity(os.stat(target)), os.stat(target).st_mtime_ns

        def substitute() -> None:
            lock.unlink()
            lock.symlink_to(target)

        fake, opens = after_eexist(lock.name, substitute)
        with (
            mock.patch.object(decisions.os, "open", fake),
            self.assertRaises(decisions.DecisionError) as caught,
        ):
            decisions._lock_file(directory, lock.name)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(caught.exception.__cause__.errno, errno.ELOOP)
        self.assertEqual(opens, ["excl", "plain"])
        self.assertEqual(identity(os.fstat(directory)), original_dir)
        self.assertEqual(identity(os.stat(path)), original_dir)
        self.assertTrue(stat.S_ISLNK(os.lstat(lock).st_mode))
        self.assertEqual((identity(os.stat(target)), os.stat(target).st_mtime_ns), target_before)
        self.assertEqual(target.read_text(), "keep")

    def test_directory_renamed_or_replaced_between_attempts_stays_bound_to_the_open_directory(self) -> None:
        for vanish in (False, True):
            with self.subTest(name_also_removed=vanish):
                path, directory = self.locks_dir(f"locks-{vanish}")
                original_dir = identity(os.stat(path))
                original_lock = make_lock(path / "apply-x.lock")
                moved = self.root / f"moved-{vanish}"
                replacement: list[tuple[tuple[int, int], tuple[int, int]]] = []

                def replace_directory() -> None:
                    path.rename(moved)
                    if vanish:
                        (moved / "apply-x.lock").unlink()
                    path.mkdir()
                    replacement.append((identity(os.stat(path)), make_lock(path / "apply-x.lock")))

                fake, opens = after_eexist("apply-x.lock", replace_directory)
                with mock.patch.object(decisions.os, "open", fake):
                    got = self.opened(decisions._lock_file(directory, "apply-x.lock"))
                [(replacement_dir, replacement_lock)] = replacement
                self.assertEqual(opens, ["excl", "plain", "excl"] if vanish else ["excl", "plain"])
                self.assertEqual(identity(os.fstat(directory)), original_dir)
                self.assertEqual(identity(os.stat(moved)), original_dir)
                self.assertNotEqual(replacement_dir, original_dir)
                self.assertEqual(got, identity(os.lstat(moved / "apply-x.lock")))
                self.assertNotEqual(got, replacement_lock)
                self.assertEqual(identity(os.lstat(path / "apply-x.lock")), replacement_lock)
                if vanish:
                    self.assertNotEqual(got, original_lock)
                else:
                    self.assertEqual(got, original_lock)

    def test_lock_name_recreated_while_the_winner_holds_the_original_inode(self) -> None:
        # Non-regression only: the fallback opens whatever the name links to now, in the open
        # directory. Exclusion against the winner's unlinked inode is not supported or asserted.
        path, directory = self.locks_dir()
        original_dir = identity(os.stat(path))
        lock = path / "apply-x.lock"
        winner = decisions._lock_file(directory, lock.name)
        self.addCleanup(os.close, winner)
        REAL_FLOCK(winner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = identity(os.fstat(winner))
        recreated: list[tuple[int, int]] = []

        def recreate() -> None:
            lock.unlink()
            recreated.append(make_lock(lock))

        fake, opens = after_eexist(lock.name, recreate)
        with mock.patch.object(decisions.os, "open", fake):
            got = self.opened(decisions._lock_file(directory, lock.name))
        [new] = recreated
        self.assertEqual(opens, ["excl", "plain"])
        self.assertNotEqual(new, original)
        self.assertEqual(got, new)
        self.assertEqual(got, identity(os.lstat(lock)))
        self.assertEqual(identity(os.fstat(winner)), original)
        self.assertEqual(os.fstat(winner).st_nlink, 0)
        self.assertEqual(identity(os.fstat(directory)), original_dir)
        self.assertEqual(identity(os.stat(path)), original_dir)

    def test_name_removed_after_eexist_then_fallback_enoent_recovers_by_creating_it_again(self) -> None:
        path, directory = self.locks_dir()
        original_dir = identity(os.stat(path))
        lock = path / "apply-x.lock"
        make_lock(lock)
        fake, opens = after_eexist(lock.name, lock.unlink)
        enoent: list[str] = []

        def recording(path_, flags, mode=0o777, *, dir_fd=None):
            try:
                return fake(path_, flags, mode, dir_fd=dir_fd)
            except FileNotFoundError:
                enoent.append(path_)
                raise

        with mock.patch.object(decisions.os, "open", recording):
            outcome = decisions._lock_file(directory, lock.name)
        # Explicit outcome: recovery, not storage_unavailable.
        got = self.opened(outcome)
        self.assertEqual(opens, ["excl", "plain", "excl"])
        self.assertEqual(enoent, [lock.name])
        self.assertEqual(got, identity(os.lstat(lock)))
        self.assertEqual(stat.S_IMODE(os.fstat(outcome).st_mode), 0o600)
        self.assertEqual(identity(os.fstat(directory)), original_dir)
        self.assertEqual(identity(os.stat(path)), original_dir)
        REAL_FLOCK(outcome, fcntl.LOCK_EX | fcntl.LOCK_NB)



if __name__ == "__main__":
    unittest.main()
