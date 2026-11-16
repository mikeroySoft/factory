from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import decisions, dispatch
from factory.config import Config


class CleanupKeepsTicketLockIdentity(unittest.TestCase):
    """Post-merge cleanup must not unlink the ticket lock out from under a live holder.

    A decision op and the dispatcher coordinate through flock on .factory/locks/<n>.lock.
    Unlinking that name while one party holds it lets the next opener create a second
    inode and lock it too, so two parties hold "the" lock at once.
    """

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = Config(root=Path(temp.name).resolve(), repo="acme/widgets")
        dispatch.configure(self.cfg)
        self.lock = dispatch.ticket_lock(7)

    def cleanup_after_merge(self) -> None:
        with (
            mock.patch.object(dispatch, "retain_handoff", return_value=True),
            mock.patch.object(dispatch, "run"),
        ):
            dispatch.cleanup_after_merge(7, "deadbeef")

    def test_cleanup_during_a_decision_hold_keeps_the_lock_exclusive(self) -> None:
        with decisions._coordination_locks(self.cfg, [{"kind": "ticket", "number": 7}]):
            held = os.stat(self.lock)
            self.cleanup_after_merge()
            current = os.stat(self.lock)
            self.assertEqual((current.st_dev, current.st_ino), (held.st_dev, held.st_ino))
            self.assertTrue(dispatch.lock_held(self.lock))
            with self.lock.open("w") as opener, self.assertRaises(BlockingIOError):
                fcntl.flock(opener, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.assertRaises(decisions.DecisionError) as busy,
                decisions._coordination_locks(self.cfg, [{"kind": "ticket", "number": 7}]),
            ):
                pass
            self.assertEqual(busy.exception.code, "target_busy")

    def test_cleanup_during_a_dispatch_hold_keeps_decisions_out(self) -> None:
        with self.lock.open("w") as claim:
            fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.cleanup_after_merge()
            current, held = os.stat(self.lock), os.fstat(claim.fileno())
            self.assertEqual((current.st_dev, current.st_ino), (held.st_dev, held.st_ino))
            with (
                self.assertRaises(decisions.DecisionError) as busy,
                decisions._coordination_locks(self.cfg, [{"kind": "ticket", "number": 7}]),
            ):
                pass
            self.assertEqual(busy.exception.code, "target_busy")


if __name__ == "__main__":
    unittest.main()
