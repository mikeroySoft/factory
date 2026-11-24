"""Worker gate detection and watchdog boundary behavior."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import dispatch, lifecycle


class ActiveWorkerGateWatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.events = Path(self.temp.name) / "events.jsonl"
        self.start = (None, "head", "", None)

    @staticmethod
    def row(worker, **fields):
        return {
            "event": "lifecycle",
            "kind": "enter",
            "parent_execution_id": worker.execution_id,
            "execution_id": "gate",
            "stage": "gate",
            "process": worker.process,
            **fields,
        }

    def watch_at_budget(self, rows=(), *, current=True):
        proc = mock.Mock()
        proc.wait.side_effect = [subprocess.TimeoutExpired("worker", 5), 0]
        cfg = SimpleNamespace(
            exit_grace=0, idle_timeout=0, check_timeout=1, checks=[]
        )
        patches = (
            mock.patch.object(dispatch, "cfg", cfg, create=True),
            mock.patch.object(dispatch.time, "monotonic", side_effect=[0, 10]),
            mock.patch.object(dispatch, "_activity", return_value=self.start),
            mock.patch.object(dispatch, "raise_if_interrupted"),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            if not current:
                with mock.patch.object(lifecycle, "current", return_value=None):
                    return dispatch._watch(
                        proc, Path("wt"), Path("log"), Path("handoff"), 10, self.start
                    ), proc.wait.call_count
            with lifecycle.scope(self.events, "worker") as worker:
                for row in rows(worker):
                    lifecycle.append(self.events, row)
                fired = dispatch._watch(
                    proc, Path("wt"), Path("log"), Path("handoff"), 10, self.start
                )
        return fired, proc.wait.call_count

    def assert_budget_not_deferred(self, rows=(), **kwargs):
        fired, waits = self.watch_at_budget(rows, **kwargs)
        self.assertEqual(fired, "budget_min")
        self.assertEqual(waits, 1)

    def test_no_current_worker_cannot_defer_the_budget_timeout(self):
        self.assert_budget_not_deferred(current=False)

    def test_non_lifecycle_rows_cannot_pose_as_active_gates(self):
        for event in ("aaa", "zzz"):
            with self.subTest(event=event):
                self.assert_budget_not_deferred(
                    lambda worker, event=event: [self.row(worker, event=event)]
                )

    def test_only_enter_events_extend_nested_gate_ancestry(self):
        def rows(worker, kind):
            return [
                self.row(
                    worker,
                    kind=kind,
                    stage="worker",
                    execution_id="nested",
                ),
                self.row(
                    worker,
                    parent_execution_id="nested",
                    execution_id="nested-gate",
                ),
            ]

        fired, waits = self.watch_at_budget(lambda worker: rows(worker, "enter"))
        self.assertIsNone(fired)
        self.assertEqual(waits, 2)
        self.assert_budget_not_deferred(lambda worker: rows(worker, "exit"))

    def test_non_gate_stage_cannot_defer_the_budget_timeout(self):
        self.assert_budget_not_deferred(
            lambda worker: [self.row(worker, stage="before-gate")]
        )

    def test_gate_exit_removes_the_watchdog_extension(self):
        self.assert_budget_not_deferred(
            lambda worker: [
                self.row(worker),
                self.row(worker, kind="exit"),
            ]
        )

    def test_only_a_live_gate_process_defers_the_budget_timeout(self):
        def dead(worker):
            process = dict(worker.process)
            process["start_ticks"] += 1
            return [self.row(worker, process=process)]

        cases = (
            ("dead", dead),
            ("unknown", lambda worker: [self.row(worker, process=None)]),
        )
        for name, rows in cases:
            with self.subTest(state=name):
                self.assert_budget_not_deferred(rows)


class WatchThresholdTest(unittest.TestCase):
    start = (None, "head", "", None)
    done = (None, "new-head", "", None)

    def watch(
        self,
        ticks,
        *,
        activities=None,
        deadline=1000,
        exit_grace=0,
        idle_timeout=0,
        check_timeout=1,
        checks=(),
    ):
        ticks = list(ticks)
        activities = list(activities or [self.start] * len(ticks))
        proc = mock.Mock()
        waits = 0

        def wait(timeout=None):
            nonlocal waits
            waits += 1
            if waits > len(ticks):
                return 0
            raise subprocess.TimeoutExpired("worker", timeout)

        proc.wait.side_effect = wait
        cfg = SimpleNamespace(
            exit_grace=exit_grace,
            idle_timeout=idle_timeout,
            check_timeout=check_timeout,
            checks=list(checks),
        )
        with mock.patch.object(dispatch, "cfg", cfg, create=True), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=[0, *ticks]), \
                mock.patch.object(dispatch, "_activity", side_effect=activities), \
                mock.patch.object(dispatch, "_active_worker_gate", return_value=True), \
                mock.patch.object(dispatch, "raise_if_interrupted"):
            fired = dispatch._watch(
                proc, Path("wt"), Path("log"), Path("handoff"), deadline, self.start
            )
        return fired, waits

    def test_active_gate_survives_exit_grace_at_and_above_the_boundary(self):
        for quiet in (10, 11):
            with self.subTest(quiet=quiet):
                fired, waits = self.watch(
                    [0, quiet],
                    activities=[self.done, self.done],
                    exit_grace=10,
                )
                self.assertIsNone(fired)
                self.assertEqual(waits, 3)

    def test_exit_grace_does_not_start_the_hard_cap_below_the_boundary(self):
        fired, waits = self.watch(
            [0, 9.5, 10, 12.5],
            activities=[self.done] * 4,
            exit_grace=10,
        )
        self.assertIsNone(fired)
        self.assertEqual(waits, 5)

    def test_idle_timeout_uses_minutes_and_starts_only_at_the_boundary(self):
        fired, waits = self.watch(
            [119, 120, 122],
            idle_timeout=2,
        )
        self.assertIsNone(fired)
        self.assertEqual(waits, 4)

    def test_gate_hard_cap_uses_deadline_check_count_and_exact_equality(self):
        fired, waits = self.watch(
            [110, 149, 150],
            deadline=100,
            check_timeout=10,
            checks=(object(), object()),
        )
        self.assertEqual(fired, "gate_timeout")
        self.assertEqual(waits, 3)


if __name__ == "__main__":
    unittest.main()
