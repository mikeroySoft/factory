"""#579: tickets worked concurrently in one dispatcher pass cannot corrupt shared state."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle, schedule
from tests.test_schedule import REPO, ticket

WAIT = 5.0  # seconds; every wait below is bounded by it
ROWS = 200


class TicketFailed(RuntimeError):
    pass


def blob(n: int, i: int) -> str:
    """Every fourth row is 4-8 KB, so an interleaved write would split a JSON line."""
    return f"#{n}/{i}:" + str(n) * (4096 + i * 41 % 4096 if i % 4 == 0 else 64)


class ConcurrentPassTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = config.Config(root=Path(temp.name), repo=REPO, max_active=2)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", EVENTS=self.events, MAX_ACTIVE=2,
        ))

    def test_two_tickets_in_flight_cannot_corrupt_shared_state(self):
        # #3 (NOW) ranks before #1 (unknown priority); their claim order decides explicit_streak.
        x, y = ticket(3, "2026-01-03T00:00:00Z"), ticket(1, "2026-01-01T00:00:00Z", body="unbound")
        barrier = threading.Barrier(2, timeout=WAIT)
        y_failed, x_done = threading.Event(), threading.Event()
        alone, unsignalled, in_flight, outcome = [], [], {}, {}

        def work(issue, budget_min, dry_run, *args, **kwargs):
            n = issue["number"]
            with lifecycle.scope(self.events, "ticket", ticket=n), dispatch.ticket_lock(n).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if n == x["number"]:
                    time.sleep(0.2)  # a slow claim: claims journaled as they finish would put #1 first
                tier = schedule.priority(n, issue["body"], REPO)[0]
                dispatch.record("claimed", ticket=n, priority=tier, experiment=False)
                if "_factory_admitted" in issue:
                    issue["_factory_admitted"].set()
                else:
                    unsignalled.append(n)
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    alone.append(n)
                    raise
                in_flight[n] = sorted(dispatch.active_tickets())
                for i in range(ROWS):
                    dispatch.record("stress", ticket=n, i=i, blob=blob(n, i))
                if n == y["number"]:
                    y_failed.set()
                    raise TicketFailed(f"#{n} pipeline failed")
                y_failed.wait(WAIT)
                time.sleep(0.2)  # still working when #1's failure lands
            x_done.set()

        def run_pass():
            try:
                with lifecycle.scope(self.events, "dispatcher", dispatcher=True, lazy=True) as dispatcher:
                    outcome["dispatcher"] = dispatcher
                    try:
                        dispatch.ticket_pass(1, False)
                    finally:
                        outcome["x_done"] = x_done.is_set()
            except (TicketFailed, threading.BrokenBarrierError) as error:
                outcome["error"] = error

        runner = threading.Thread(target=run_pass, daemon=True)

        def stop():
            barrier.abort()
            y_failed.set()
            runner.join(WAIT)

        with patch.object(dispatch, "frontier", return_value=[x, y]), \
                patch.object(dispatch, "process_ticket", work):
            self.addCleanup(stop)
            runner.start()
            runner.join(2 * WAIT)
            self.assertFalse(runner.is_alive(), f"ticket_pass did not return within {2 * WAIT:.0f} s")

        self.assertEqual({"waited alone": alone, "no _factory_admitted Event": unsignalled},
                         {"waited alone": [], "no _factory_admitted Event": []},
                         "both selected tickets must be in flight at once, each handed its admission Event")
        self.assertIsInstance(outcome.get("error"), TicketFailed, "the pass must re-raise #1's failure")
        self.assertTrue(outcome["x_done"], "the pass raised #1's failure while #3 was still running")
        self.assertEqual(in_flight, {3: [1, 3], 1: [1, 3]})
        self.assertEqual(dispatch.active_tickets(), [], "a ticket lock outlived its pipeline")

        raw = self.events.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        rows = []
        for number, line in enumerate(raw.split(b"\n")[:-1], 1):
            try:
                rows.append(json.loads(line))
            except ValueError:
                self.fail(f"events.jsonl line {number} is not one JSON object: {line[:120]!r}")
            self.assertIsInstance(rows[-1], dict, f"events.jsonl line {number}")
        self.assertEqual(rows, lifecycle.read_events(self.events))

        dispatcher = outcome["dispatcher"]
        lifecycle_rows = [row for row in rows if row.get("event") == "lifecycle"]
        self.assertEqual(len([row for row in lifecycle_rows if row["execution_id"] == dispatcher.execution_id
                              and row["kind"] == "enter"]), 1, "the dispatcher's buffered rows were written twice")
        scopes = {kind: [row for row in lifecycle_rows if row["stage"] == "ticket" and row["kind"] == kind]
                  for kind in ("enter", "exit")}
        self.assertEqual(sorted(row["ticket"] for row in scopes["enter"]), [1, 3])
        for enter in scopes["enter"]:
            n = enter["ticket"]
            self.assertEqual((enter["dispatcher_run_id"], enter["root_execution_id"]),
                             (dispatcher.dispatcher_run_id, dispatcher.execution_id),
                             f"#{n}'s ticket scope lost the dispatcher context")
            own = [row for row in rows if row.get("event") in {"claimed", "stress"} and row["ticket"] == n]
            self.assertEqual({(row["execution_id"], row["dispatcher_run_id"]) for row in own},
                             {(enter["execution_id"], dispatcher.dispatcher_run_id)}, f"#{n}'s rows")
            stress = [row for row in own if row["event"] == "stress"]
            self.assertEqual([row["i"] for row in stress], list(range(ROWS)), f"#{n}'s rows are incomplete")
            self.assertTrue(all(row["blob"] == blob(n, row["i"]) for row in stress), f"#{n}'s payload changed")
        exits = {row["ticket"]: row for row in scopes["exit"]}
        self.assertEqual(sorted(exits), [1, 3])
        self.assertEqual((exits[3]["outcome"], exits[3]["reason"]), ("completed", None))
        self.assertEqual((exits[1]["outcome"], exits[1]["reason"]), ("unknown", "TicketFailed: #1 pipeline failed"))

        self.assertEqual([row["ticket"] for row in rows if row.get("event") == "claimed"], [3, 1])
        claim = {3: {"event": "claimed", "ticket": 3, "priority": "NOW"},
                 1: {"event": "claimed", "ticket": 1, "priority": None}}
        serial = schedule.explicit_streak([claim[3], claim[1]])
        self.assertNotEqual(serial, schedule.explicit_streak([claim[1], claim[3]]))
        self.assertEqual(schedule.explicit_streak(rows), serial)

    def worker_pass(self, child: str, second_frontier):
        """One pass whose single pick runs the real `run_worker` on `child` inside a real ticket
        lock; the main thread's second `frontier()` call (ranking pick 2) runs `second_frontier`
        once the child has written its pid. Returns (the pass's exception, seconds it took to
        return after that call, the child's pid, `run_worker`'s result)."""
        x = ticket(3, "2026-01-03T00:00:00Z")
        pidfile, wt = self.cfg.root / "child.pid", self.cfg.root / "wt"
        wt.mkdir()
        self.enterContext(patch.object(dispatch, "WATCH_POLL", 0.05))
        calls, result, since = [], {}, {}

        def frontier():
            calls.append(len(calls))
            if len(calls) == 1:
                return [x]
            for _ in range(int(WAIT / 0.01)):
                if pidfile.exists() and pidfile.read_text():
                    break
                time.sleep(0.01)
            since["at"] = time.monotonic()
            return second_frontier()

        def work(issue, budget_min, dry_run, *args, **kwargs):
            n = issue["number"]
            with lifecycle.scope(self.events, "ticket", ticket=n), dispatch.ticket_lock(n).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                issue["_factory_admitted"].set()
                result["worker"] = dispatch.run_worker(
                    [sys.executable, "-c", child, str(pidfile)], wt, self.cfg.root / "worker.log",
                    self.cfg.root / "handoff.md",
                )
                result["after"] = dispatch.run([sys.executable, "-c", "pass"]).returncode
            if result["worker"][0]:
                raise TicketFailed(f"#{n} worker exited {result['worker'][0]}")

        def kill_child():
            if pidfile.exists() and pidfile.read_text():
                with suppress(ProcessLookupError):
                    os.killpg(int(pidfile.read_text()), signal.SIGKILL)

        self.addCleanup(kill_child)
        error = None
        with patch.object(dispatch, "frontier", frontier), patch.object(dispatch, "process_ticket", work), \
                lifecycle.scope(self.events, "dispatcher", dispatcher=True, lazy=True):
            try:
                dispatch.ticket_pass(1, False)
            except (KeyboardInterrupt, RuntimeError) as raised:
                error = raised
        took = time.monotonic() - since["at"]
        self.assertEqual(len(calls), 2, "the pass never ranked a second pick")
        return error, took, int(pidfile.read_text()), result

    def test_interrupt_stops_in_flight_pipelines(self):
        # Ctrl-C reaches only the main thread, here ranking pick 2 while pick 1's worker runs.
        child = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(6)"

        def interrupt():
            raise KeyboardInterrupt

        error, took, pid, result = self.worker_pass(child, interrupt)
        self.assertIsInstance(error, KeyboardInterrupt, "the pass must re-raise the interrupt")
        self.assertLess(took, 3, "the interrupted pass waited for the worker instead of stopping it")
        with self.assertRaises(ProcessLookupError, msg="the worker outlived the interrupted pass"):
            os.kill(pid, 0)
        self.assertEqual(result, {}, "the interrupted pipeline went on after its worker")
        self.assertEqual(dispatch.active_tickets(), [], "a ticket lock outlived its pipeline")
        self.assertEqual(dispatch.run([sys.executable, "-c", "pass"]).returncode, 0,
                         "the interrupt leaked out of the pass")

    def test_ranking_failure_lets_in_flight_pipelines_finish(self):
        child = ("import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(0.5); "
                 "sys.exit(3)")

        def gh_down():
            raise RuntimeError("gh: frontier unavailable")

        logged = []
        with patch.object(dispatch, "log", logged.append):
            error, _, _, result = self.worker_pass(child, gh_down)
        self.assertEqual(repr(error), "RuntimeError('gh: frontier unavailable')",
                         "the pass must raise its own ranking failure")
        self.assertEqual(result, {"worker": (3, None), "after": 0}, "a ranking failure cut short pick 1")
        self.assertIn("#3: pipeline raised TicketFailed('#3 worker exited 3')", logged,
                      "pick 1's failure vanished behind the ranking failure")
        self.assertEqual(dispatch.active_tickets(), [], "a ticket lock outlived its pipeline")


if __name__ == "__main__":
    unittest.main()
