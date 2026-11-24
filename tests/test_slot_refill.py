"""#742: freed capacity and landing must not wait for the slowest ticket gate."""
from __future__ import annotations

import fcntl
import threading
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_concurrent_pass as concurrent_fixture
from tests import test_land_host as land_fixture
from tests.test_schedule import ticket

WAIT = 5


class SlotRefillTest(unittest.TestCase):
    def setUp(self):
        self.fixture = concurrent_fixture.ConcurrentPassTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_short_ticket_refills_before_slow_sibling_finishes(self):
        slow_started, short_done, refill_started, release = (threading.Event() for _ in range(4))
        rows = [ticket(n, f"2026-01-{n:02d}", body="unbound") for n in (1, 2, 3)]
        errors, peak = [], []

        def work(issue, *_args):
            n = issue["number"]
            with dispatch.ticket_lock(n).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                peak.append(len(dispatch.active_tickets()))
                issue["_factory_admitted"].set()
                if n == 1:
                    slow_started.set()
                    if not release.wait(WAIT):
                        raise AssertionError("slow pipeline was not released")
                elif n == 2:
                    if not slow_started.wait(WAIT):
                        raise AssertionError("slow sibling never started")
                    short_done.set()
                else:
                    self.assertTrue(short_done.is_set())
                    self.assertFalse(release.is_set(), "refill waited for the slow sibling")
                    refill_started.set()

        def run_pass():
            try:
                dispatch.ticket_pass(1, False)
            except BaseException as exc:
                errors.append(exc)

        with mock.patch.object(dispatch, "frontier", return_value=rows), \
                mock.patch.object(dispatch, "process_ticket", side_effect=work):
            worker = threading.Thread(target=run_pass)
            worker.start()
            try:
                self.assertTrue(refill_started.wait(WAIT), "freed slot was not refilled within the pass")
                self.assertLessEqual(max(peak), 2)
            finally:
                release.set()
                worker.join(WAIT)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
        self.assertEqual(dispatch.active_tickets(), [])


class IndependentLandingTest(unittest.TestCase):
    def test_land_only_merges_approved_green_pr_while_ticket_gate_runs(self):
        fixture = land_fixture.LandHostTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        module = fixture.dispatcher("tank")
        fixture.claim("tank")
        head = fixture.approve(module)
        gate_started, release = threading.Event(), threading.Event()
        errors = []

        def long_gate(*_args, **_kwargs):
            gate_started.set()
            if not release.wait(WAIT):
                raise AssertionError("long gate was not released")
            return True, "PASS"

        def work(issue, *_args):
            with module.ticket_lock(issue["number"]).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                issue["_factory_admitted"].set()
                module.run_gate(module.ROOT, issue["number"])

        def run_pass():
            try:
                module.ticket_pass(1, False)
            except BaseException as exc:
                errors.append(exc)

        with mock.patch.object(module, "frontier", return_value=[ticket(21, "2026-01-01", body="unbound")]), \
                mock.patch.object(module, "process_ticket", side_effect=work), \
                mock.patch.object(module, "run_gate", side_effect=long_gate), \
                mock.patch.object(module, "pin_root", side_effect=AssertionError("landing must not checkout the worker root")), \
                mock.patch.object(module, "worker_health_pass", side_effect=AssertionError("landing must not probe workers")), \
                mock.patch.object(module, "github_preflight", return_value=None), \
                mock.patch.object(config, "load", return_value=module.cfg), \
                mock.patch.object(config, "engine_commit", return_value=None), \
                mock.patch.object(config, "engine_target", return_value=None):
            worker = threading.Thread(target=run_pass)
            worker.start()
            try:
                self.assertTrue(gate_started.wait(WAIT))
                self.assertEqual(module.main(["--land-only"]), 0)
                self.assertTrue(worker.is_alive(), "landing waited for the running ticket gate")
                self.assertTrue(fixture.github.merged)
                self.assertEqual([row["head"] for row in fixture.events(module, "merged")], [head])
            finally:
                release.set()
                worker.join(WAIT)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
        self.assertEqual(module.active_tickets(), [])
        self.assertTrue(any(row.get("event") == "approved" for row in lifecycle.read_events(module.EVENTS)))


if __name__ == "__main__":
    unittest.main()
