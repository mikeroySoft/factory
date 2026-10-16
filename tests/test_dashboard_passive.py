"""The dashboard consumes bounded local facts without joining the journal lock."""
from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from unittest.mock import patch

from factory import config, dashboard, dispatch, lifecycle


def _hold_exclusive(path: str, ready: multiprocessing.Event, release: multiprocessing.Event) -> None:
    with Path(path).open("r+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        ready.set()
        release.wait(10)


class PassiveDashboardTest(unittest.TestCase):
    def test_snapshot_reads_partial_window_while_another_process_holds_journal_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(dashboard.__dict__), \
             patch.dict(dispatch.__dict__), patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
            root = Path(directory)
            cfg = config.Config(root, "acme/widgets", lock=root / "gpu.lock")
            dashboard.configure(cfg)
            events = cfg.factory / "events.jsonl"
            events.parent.mkdir(parents=True)
            legacy = json.dumps({
                "event": "attempt", "ticket": 7, "seconds": 1, "pad": "x" * 4000,
            }) + "\n"
            events.write_text(legacy * 300)
            execution = lifecycle.Execution(events, "worker", ticket=7)
            execution.emit("enter")
            before = events.read_bytes()

            state = {
                "service_active": False,
                "timer_active": True,
                "next_at": "2100-01-01T00:00:00.000000Z",
                "paused": None,
                "observed_at": "2026-10-09T00:00:00.000000Z",
                "observation": "fresh",
                "capacity": {"configured": cfg.max_active, "active": 0, "complete": True},
                "run_ids": [],
                "latest_transition": None,
            }
            ready, release = multiprocessing.Event(), multiprocessing.Event()
            holder = multiprocessing.Process(
                target=_hold_exclusive, args=(str(events), ready, release),
            )
            holder.start()
            timed_out = False
            pool = None
            try:
                self.assertTrue(ready.wait(5), "journal holder did not acquire LOCK_EX")
                pool = ThreadPoolExecutor(max_workers=1)
                with patch.object(dashboard, "github", side_effect=RuntimeError("offline")), \
                     patch.object(dashboard.runtime_local, "dispatcher", return_value=(state, [])), \
                     patch.object(dashboard, "_timer_last", return_value=None), \
                     patch.object(dashboard, "journal_runs", return_value=[]), \
                     patch.object(dashboard, "upstream_state", return_value={}), \
                     patch.object(dashboard, "triage_llm_online", return_value=False), \
                     patch.object(dashboard.stats, "dispatcher_login", return_value=""):
                    future = pool.submit(dashboard.snapshot)
                    try:
                        snapshot = future.result(timeout=2)
                    except FutureTimeout:
                        timed_out = True
                        release.set()
                        snapshot = future.result(timeout=5)
            finally:
                release.set()
                if pool is not None:
                    pool.shutdown(wait=True)
                holder.join(5)
                if holder.is_alive():
                    holder.terminate()
                    holder.join(5)

            self.assertFalse(timed_out, "snapshot waited for the journal flock")
            self.assertEqual(holder.exitcode, 0)
            self.assertEqual(events.read_bytes(), before)
            self.assertEqual(snapshot["coverage"]["legacy"]["status"], "partial")
            self.assertEqual(snapshot["coverage"]["legacy"]["scope"], "retained_live_journal")
            self.assertTrue(snapshot["coverage"]["legacy"]["truncated"])
            self.assertEqual(
                snapshot["coverage"]["legacy"]["byte_limit"],
                dashboard.DASHBOARD_EVENT_READ_CAP,
            )
            self.assertEqual(
                snapshot["coverage"]["legacy"]["row_limit"],
                dashboard.DASHBOARD_EVENT_ROW_CAP,
            )
            self.assertFalse(snapshot["coverage"]["legacy"]["window_complete"])
            self.assertFalse(snapshot["coverage"]["counts"]["spend"]["complete"])
            self.assertEqual(snapshot["coverage"]["counts"]["spend"]["status"], "partial")
            self.assertLess(snapshot["spend"]["seconds"], 300)
            self.assertEqual(snapshot["executions"][0]["execution_id"], execution.execution_id)


    def test_snapshot_reuses_passive_lock_evidence_without_coordination_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(dashboard.__dict__), \
             patch.dict(dispatch.__dict__), patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
            root = Path(directory)
            cfg = config.Config(root, "acme/widgets", lock=root / "gpu.lock")
            dashboard.configure(cfg)
            locks = cfg.factory / "locks"
            locks.mkdir(parents=True)
            held_ticket = locks / "7.lock"
            held_ticket.touch()
            cfg.lock.touch()

            def issue(number: int) -> dict:
                return {
                    "number": number,
                    "title": f"Ticket {number}",
                    "state": "OPEN",
                    "url": f"https://example.test/issues/{number}",
                    "body": "",
                    "createdAt": "2026-10-09T00:00:00Z",
                    "updatedAt": "2026-10-09T00:00:00Z",
                    "closedAt": None,
                    "labels": {"nodes": [{"name": dashboard.LABEL_TRIAGE}]},
                    "assignees": {"nodes": []},
                    "timelineItems": {"nodes": [], "pageInfo": {"hasPreviousPage": False}},
                }

            state = {
                "service_active": False,
                "timer_active": False,
                "next_at": None,
                "paused": None,
                "observed_at": "2026-10-09T00:00:00.000000Z",
                "observation": "fresh",
                "capacity": {"configured": cfg.max_active, "active": 0, "complete": True},
                "run_ids": [],
                "latest_transition": None,
            }
            github = {
                "repository": {
                    "id": "repository",
                    "issues": {"nodes": [issue(7), issue(8)]},
                    "pullRequests": {"nodes": []},
                },
                "viewer": {"login": ""},
            }
            with held_ticket.open("r+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                with patch.object(dashboard, "github", return_value=github), \
                     patch.object(dashboard.runtime_local, "dispatcher", return_value=(state, [])), \
                     patch.object(dashboard, "_timer_last", return_value=None), \
                     patch.object(dashboard, "journal_runs", return_value=[]), \
                     patch.object(dashboard, "upstream_state", return_value={}), \
                     patch.object(dashboard, "triage_llm_online", return_value=False), \
                     patch.object(dashboard.stats, "dispatcher_login", return_value=""), \
                     patch.object(Path, "mkdir", side_effect=AssertionError("snapshot called mkdir")), \
                     patch.object(fcntl, "flock", side_effect=AssertionError("snapshot called flock")):
                    snapshot = dashboard.snapshot()

            tickets = {ticket["number"]: ticket for ticket in snapshot["tickets"]}
            self.assertIs(tickets[7]["lock_held"], True)
            self.assertIsNone(tickets[8]["lock_held"])
            self.assertIs(snapshot["gpu_lock_held"], False)
            self.assertIsNone(snapshot["active"])


if __name__ == "__main__":
    unittest.main()
