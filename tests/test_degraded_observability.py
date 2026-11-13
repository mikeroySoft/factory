"""Degraded observability: an unknown lock is not a free lock."""
from __future__ import annotations

import unittest

from factory import briefing


class UnknownLockIsNotFreeTest(unittest.TestCase):
    def test_unknown_lock_with_no_open_execution_is_uncertain_not_idle(self):
        # Darwin passive reads report lock_held=None. With no recorded execution
        # that absence must not prove the ticket idle.
        ticket = {"number": 7, "lock_held": None}
        self.assertEqual(briefing.execution_evidence(ticket, []), "uncertain")

    def test_proven_free_lock_with_finished_execution_is_not_running(self):
        ticket = {"number": 7, "lock_held": False}
        finished = [{"state": "completed", "ended_at": "2026-10-01T01:00:00Z"}]
        self.assertEqual(briefing.execution_evidence(ticket, finished), "not_running")

    def test_held_lock_is_running(self):
        self.assertEqual(briefing.execution_evidence({"number": 7, "lock_held": True}, []), "running")


if __name__ == "__main__":
    unittest.main()
