"""Behavioral boundaries for abandoned-worker cleanup and lifecycle ownership."""
from __future__ import annotations

import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import darwin_observer, dispatch, lifecycle, platform_identity

BOOT = "11111111-2222-3333-4444-555555555555"


class WorktreeCleanupEdgesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.events = Path(temporary.name) / "events.jsonl"
        self.processes = {}
        self.contexts = {}
        self.signals = []
        self.sleeps = []
        self.scan_complete = True
        self.now = 0.0
        self.after_scan = lambda: None
        self.signal_action = self.terminate
        self.add_process(os.getpid(), ppid=1)
        for patch in (
            mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}),
            mock.patch.object(dispatch, "EVENTS", self.events, create=True),
            mock.patch.object(platform_identity, "darwin", return_value=True),
            mock.patch.object(platform_identity, "boot_id", return_value=BOOT),
            mock.patch.object(platform_identity, "process", side_effect=self.process),
            mock.patch.object(darwin_observer, "scan", side_effect=self.scan),
            mock.patch.object(dispatch.os, "kill", side_effect=self.kill),
            mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: self.now),
            mock.patch.object(dispatch.time, "sleep", side_effect=self.sleep),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def add_process(self, pid, *, ppid=None, start_ticks=None):
        identity = {
            "pid": pid, "boot_id": BOOT,
            "start_ticks": start_ticks if start_ticks is not None else 1_800_000_000_000_000 + pid,
            "pid_namespace": "darwin", "uid": os.getuid(),
            "state": "S", "ppid": os.getpid() if ppid is None else ppid,
        }
        self.processes[pid] = identity
        return identity

    def process(self, pid):
        if pid not in self.processes:
            raise ProcessLookupError(pid)
        return dict(self.processes[pid])

    def scan(self, *args, **kwargs):
        processes = {pid: dict(identity) for pid, identity in self.processes.items()}
        contexts = dict(self.contexts)
        self.after_scan()
        return processes, contexts, set(), self.scan_complete

    def launch(self, pid, *, event="lifecycle", kind="child_start", stage="worker", ticket=7):
        identity = self.add_process(pid)
        execution = lifecycle.Execution(self.events, stage, ticket=ticket, lazy=True)
        row = execution.emit(kind, child_process=dict(identity))
        lifecycle.append(self.events, {**row, "event": event})
        self.tag(pid, execution.execution_id)
        return execution.execution_id

    def tag(self, pid, *execution_ids):
        self.contexts[pid] = {"schema_version": 1, "execution_ids": list(execution_ids)}

    def kill(self, pid, sig):
        self.signals.append((pid, sig, self.now))
        self.signal_action(pid, sig)

    def terminate(self, pid, sig):
        self.processes.pop(pid, None)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def signal_pairs(self):
        return [(pid, sig) for pid, sig, _ in self.signals]

    def receipts(self):
        return [row for row in lifecycle.read_events(self.events)
                if row.get("event") == "worker-cleanup"]

    def assert_receipt(self, pids):
        [receipt] = self.receipts()
        self.assertEqual(receipt["ticket"], 7)
        self.assertEqual(receipt["pids"], pids)

    def test_selects_only_lifecycle_worker_child_start_for_requested_ticket(self):
        self.launch(410001)
        self.launch(410002, event="attempt")
        self.launch(410003, kind="child_exit")
        self.launch(410004, stage="gate")
        self.launch(410005, ticket=8)
        self.launch(410006, ticket="7")

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM)])
        self.assert_receipt([410001])
        self.assertTrue(all(pid in self.processes for pid in range(410002, 410007)))

    def test_owns_worker_ancestry_and_detached_tag_not_dispatcher_siblings(self):
        execution_id = self.launch(410001)
        self.add_process(410002, ppid=410001)
        self.add_process(410003, ppid=410002)
        self.add_process(410004, ppid=1)
        self.tag(410004, execution_id)
        self.add_process(410005)
        self.launch(410006, ticket=8)
        self.tag(os.getpid(), execution_id)

        dispatch._stop_worker_descendants(7)

        self.assertEqual(set(self.signal_pairs()),
                         {(pid, signal.SIGTERM) for pid in range(410001, 410005)})
        self.assertEqual(len(self.signals), 4)
        self.assert_receipt([410001, 410002, 410003, 410004])
        self.assertIn(os.getpid(), self.processes)
        self.assertIn(410005, self.processes)
        self.assertIn(410006, self.processes)

    def test_no_matching_launch_has_no_signals_or_cleanup_receipt(self):
        self.launch(410001, ticket=8)
        with mock.patch.object(darwin_observer, "scan", side_effect=AssertionError("unowned scan")):
            dispatch._stop_worker_descendants(7)
        self.assertEqual(self.signals, [])
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.sleeps, [])

    def test_dispatcher_only_evidence_is_not_signaled_or_receipted(self):
        execution_id = self.launch(410001)
        self.contexts.pop(410001)
        self.processes.pop(410001)
        self.tag(os.getpid(), execution_id)

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signals, [])
        self.assertEqual(self.receipts(), [])

    def test_process_attributed_to_two_worker_launches_is_signaled_once(self):
        first = self.launch(410002)
        second = self.launch(410001)
        self.add_process(410003, ppid=1)
        self.tag(410003, first, second)

        dispatch._stop_worker_descendants(7)

        self.assertEqual(set(self.signal_pairs()),
                         {(pid, signal.SIGTERM) for pid in (410001, 410002, 410003)})
        self.assertEqual(len(self.signals), 3)
        self.assert_receipt([410001, 410002, 410003])

    def test_term_exit_needs_no_kill_or_wait(self):
        self.launch(410001)

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM)])
        self.assertEqual(self.sleeps, [])
        self.assertNotIn(410001, self.processes)
        self.assert_receipt([410001])

    def test_registered_live_launcher_without_context_is_still_stopped(self):
        self.launch(410001)
        self.contexts.pop(410001)

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM)])
        self.assertNotIn(410001, self.processes)
        self.assert_receipt([410001])

    def test_incomplete_scan_without_candidates_is_not_successful_cleanup(self):
        self.launch(410001)
        self.contexts.pop(410001)
        self.processes.pop(410001)
        self.scan_complete = False

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signals, [])
        self.assertEqual(self.receipts(), [])

    def test_incomplete_scan_with_candidates_has_no_success_receipt(self):
        self.launch(410001)
        self.scan_complete = False

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            dispatch._stop_worker_descendants(7)

        self.assertEqual(self.receipts(), [])

    def test_kill_only_surviving_identity_after_term_grace(self):
        self.launch(410001)
        self.launch(410002)

        def stop(pid, sig):
            if pid == 410001 or sig == signal.SIGKILL:
                self.terminate(pid, sig)

        self.signal_action = stop
        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM),
                                               (410002, signal.SIGTERM),
                                               (410002, signal.SIGKILL)])
        self.assertGreaterEqual(self.signals[-1][2], 10)
        self.assertLess(self.signals[-1][2], 10.1)
        self.assertTrue(self.sleeps)
        self.assertEqual(set(self.sleeps), {0.05})
        self.assert_receipt([410001, 410002])

    def test_identity_dead_after_discovery_is_never_signaled(self):
        self.launch(410001)
        self.after_scan = lambda: self.processes.pop(410001)

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signals, [])
        self.assertEqual(self.sleeps, [])
        self.assert_receipt([410001])

    def test_reused_pid_after_discovery_is_never_signaled(self):
        self.launch(410001)
        replacement = {**self.processes[410001], "start_ticks": 1_900_000_000_000_000}
        self.after_scan = lambda: self.processes.update({410001: replacement})

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signals, [])
        self.assertEqual(self.processes[410001], replacement)
        self.assertEqual(self.sleeps, [])
        self.assert_receipt([410001])

    def test_zombie_after_discovery_is_not_signaled(self):
        self.launch(410001)
        self.after_scan = lambda: self.processes[410001].update(state="Z")

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signals, [])
        self.assertEqual(self.sleeps, [])
        self.assert_receipt([410001])

    def test_reused_pid_after_term_is_not_killed(self):
        self.launch(410001)
        replacement = {**self.processes[410001], "start_ticks": 1_900_000_000_000_000}
        self.signal_action = lambda pid, sig: self.processes.update({pid: replacement})

        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM)])
        self.assertEqual(self.processes[410001], replacement)
        self.assertEqual(self.sleeps, [])
        self.assert_receipt([410001])

    def test_process_disappearing_during_term_does_not_prevent_other_cleanup(self):
        self.launch(410001)
        self.launch(410002)

        def disappear(pid, sig):
            self.terminate(pid, sig)
            if pid == 410001:
                raise ProcessLookupError(pid)

        self.signal_action = disappear
        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM),
                                               (410002, signal.SIGTERM)])
        self.assert_receipt([410001, 410002])

    def test_process_disappearing_during_kill_is_successful_cleanup(self):
        self.launch(410001)

        def disappear(pid, sig):
            if sig == signal.SIGKILL:
                self.terminate(pid, sig)
                raise ProcessLookupError(pid)

        self.signal_action = disappear
        dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM),
                                               (410001, signal.SIGKILL)])
        self.assertGreaterEqual(self.signals[-1][2], 10)
        self.assert_receipt([410001])

    def test_survivors_raise_after_both_grace_periods_without_success_receipt(self):
        self.launch(410002)
        self.launch(410001)
        self.signal_action = lambda pid, sig: None

        with self.assertRaisesRegex(RuntimeError,
                                    r"worker descendants still alive for #7: \[410001, 410002\]"):
            dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410002, signal.SIGTERM),
                                               (410001, signal.SIGTERM),
                                               (410002, signal.SIGKILL),
                                               (410001, signal.SIGKILL)])
        self.assertGreaterEqual(self.now, 20)
        self.assertLess(self.now, 20.2)
        self.assertEqual(set(self.sleeps), {0.05})
        self.assertEqual(self.receipts(), [])
        self.assertIn(410001, self.processes)
        self.assertIn(410002, self.processes)

    def test_signal_permission_failure_propagates_without_success_receipt(self):
        self.launch(410001)

        def denied(pid, sig):
            raise PermissionError("cannot signal worker")

        self.signal_action = denied
        with self.assertRaisesRegex(PermissionError, "cannot signal worker"):
            dispatch._stop_worker_descendants(7)

        self.assertEqual(self.signal_pairs(), [(410001, signal.SIGTERM)])
        self.assertEqual(self.receipts(), [])
        self.assertIn(410001, self.processes)
