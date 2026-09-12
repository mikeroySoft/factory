"""Behavioral checks for the shared lifecycle journal and process reconciliation."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import lifecycle

ROOT = Path(__file__).resolve().parents[1]


class LifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("FACTORY_LIFECYCLE_CONTEXT", None)

    def spawn(self, code: str, *, env: dict | None = None) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", code, str(self.path)],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        def stop() -> None:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stdin.close()
        self.addCleanup(stop)
        return proc

    def test_overlapping_producers_have_distinct_ordered_executions(self) -> None:
        code = """
import sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else 'worker') as execution:
    print('entered', flush=True)
    sys.stdin.readline()
    for index in range(25):
        execution.emit('result', index=index)
        lifecycle.append(Path(sys.argv[1]), {'event': 'attempt', 'ticket': index})
"""
        producers = [self.spawn(code.replace("'worker'", repr(stage)))
                     for stage in ("worker", "gate", "review", "merge")]
        for proc in producers:
            self.assertEqual(proc.stdout.readline().strip(), "entered")
        active = lifecycle.observe(self.path)
        self.assertEqual({state["stage"]: state["state"] for state in active},
                         {"worker": "active", "gate": "active", "review": "active", "merge": "active"})
        for proc in producers:
            proc.stdin.write("\n")
            proc.stdin.flush()
        for proc in producers:
            self.assertEqual(proc.wait(timeout=20), 0)
        rows = lifecycle.read_events(self.path)
        states = lifecycle.observe(self.path)
        self.assertEqual(len(states), 4)
        self.assertEqual({state["stage"] for state in states}, {"worker", "gate", "review", "merge"})
        events = [row for row in rows if row.get("event") == "lifecycle"]
        self.assertEqual(len({row["event_id"] for row in events}), len(events))
        for state in states:
            self.assertEqual(state["state"], "completed")
            self.assertEqual([row["sequence"] for row in state["events"]], list(range(1, 28)))
        self.assertEqual(sum(row.get("event") == "attempt" for row in rows), 100)

    def test_abrupt_death_reconciles_once_at_observation_time(self) -> None:
        proc = self.spawn("""
import sys, time
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'worker'):
    print('entered', flush=True)
    time.sleep(120)
""")
        self.assertEqual(proc.stdout.readline().strip(), "entered")
        self.assertEqual(lifecycle.observe(self.path)[0]["state"], "active")
        proc.kill()
        proc.wait(timeout=10)
        observed_after = lifecycle._now()
        observers = [self.spawn("import sys; from pathlib import Path; from factory import lifecycle; lifecycle.observe(Path(sys.argv[1]))")
                     for _ in range(4)]
        for observer in observers:
            self.assertEqual(observer.wait(timeout=10), 0)
        first = lifecycle.observe(self.path)[0]
        second = lifecycle.observe(self.path)[0]
        self.assertEqual(first["state"], "interrupted")
        self.assertGreaterEqual(first["ended_at"], observed_after)
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(sum(event["kind"] == "exit" for event in first["events"]), 1)
        self.assertEqual(first["events"][-1]["kind"], "exit")
        self.assertTrue(first["events"][-1]["reconciled"])

    def test_reused_pid_is_not_live_and_missing_identity_is_unknown(self) -> None:
        with lifecycle.scope(self.path, "worker") as execution:
            entered = lifecycle.read_events(self.path)[0]
        entered["execution_id"] = "reused-process"
        entered["process"]["start_ticks"] -= 1
        lifecycle.append(self.path, entered)
        entered = {**entered, "execution_id": "missing-process", "process": None}
        lifecycle.append(self.path, entered)
        states = {state["execution_id"]: state for state in lifecycle.observe(self.path)}
        self.assertEqual(states["reused-process"]["state"], "interrupted")
        self.assertEqual(states["missing-process"]["state"], "unknown")
        self.assertEqual(states[execution.execution_id]["state"], "completed")

    def test_unavailable_liveness_is_unknown_not_interrupted(self) -> None:
        with lifecycle.scope(self.path, "review"):
            entered = lifecycle.read_events(self.path)[0]
        entered["execution_id"] = "unobservable-process"
        lifecycle.append(self.path, entered)
        with patch.object(lifecycle, "_process_state", return_value="unknown"):
            state = lifecycle.observe(self.path)[-1]
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(len(state["events"]), 1)

    def test_held_lock_blocks_interruption_after_owner_death(self) -> None:
        lock = Path(self.tmp.name) / "ticket.lock"
        with lock.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            proc = self.spawn(f"""
import sys, os
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'ticket', lock=Path({str(lock)!r})):
    os._exit(0)
""")
            self.assertEqual(proc.wait(timeout=10), 0)
            self.assertEqual(lifecycle.observe(self.path)[0]["state"], "unknown")
        self.assertEqual(lifecycle.observe(self.path)[0]["state"], "interrupted")

    def test_live_child_survives_dead_parent(self) -> None:
        proc = self.spawn("""
import os, subprocess, sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'worker') as execution:
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env=execution.env())
    execution.child(child.pid)
    print(child.pid, flush=True)
    os._exit(0)
""")
        child_pid = int(proc.stdout.readline())
        def kill_child() -> None:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
        self.addCleanup(kill_child)
        self.assertEqual(proc.wait(timeout=10), 0)
        state = lifecycle.observe(self.path)[0]
        self.assertEqual(state["state"], "active")
        self.assertIsNone(state["ended_at"])

    def test_spawn_registration_gap_is_not_false_interruption(self) -> None:
        proc = self.spawn("""
import os, sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'worker') as execution:
    execution.env()
    os._exit(0)
""")
        self.assertEqual(proc.wait(timeout=10), 0)
        state = lifecycle.observe(self.path)[0]
        self.assertEqual(state["state"], "unknown")
        self.assertIsNone(state["ended_at"])

    def test_legacy_and_truncated_rows_do_not_create_activity(self) -> None:
        self.path.write_bytes(b'{"event":"attempt","ticket":3}\nnot json\n{"event":"lifecycle"')
        lifecycle.append(self.path, {"event": "approved", "ticket": 3})
        self.assertEqual([row["event"] for row in lifecycle.read_events(self.path)], ["attempt", "approved"])
        self.assertEqual(lifecycle.observe(self.path), [])
        # Even a syntactically complete unterminated row is not promoted on append.
        with self.path.open("ab") as handle:
            handle.write(b'{"event":"attempt","ticket":4}')
        lifecycle.append(self.path, {"event": "merged", "ticket": 3})
        self.assertEqual([row["event"] for row in lifecycle.read_events(self.path)], ["attempt", "approved", "merged"])

    def test_nested_and_subprocess_scopes_preserve_causal_association(self) -> None:
        with lifecycle.scope(self.path, "dispatcher", dispatcher=True) as dispatcher:
            with lifecycle.scope(self.path, "ticket", ticket=17) as ticket:
                ticket.attempt = 2
                ticket.review_round = 1
                with lifecycle.scope(self.path, "worker") as worker:
                    env = worker.env()
                    proc = self.spawn("""
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path('/unused/events.jsonl'), 'gate'):
    pass
""", env=env)
                    worker.child(proc.pid)
                    self.assertEqual(proc.wait(timeout=10), 0)
                    worker.child_done(proc.pid)
                    states = lifecycle.observe(self.path)
                    active = {state["stage"] for state in states if state["state"] == "active"}
                    self.assertEqual(active, {"dispatcher", "ticket", "worker"})
        states = {state["stage"]: state for state in lifecycle.observe(self.path)}
        gate = states["gate"]
        self.assertEqual(gate["parent_execution_id"], worker.execution_id)
        self.assertEqual(gate["dispatcher_run_id"], dispatcher.dispatcher_run_id)
        self.assertEqual((gate["ticket"], gate["attempt"], gate["review_round"]), (17, 2, 1))
        with lifecycle.scope(self.path, "triage") as triage:
            self.assertIsNone(triage.dispatcher_run_id)
            self.assertEqual(triage.root_execution_id, triage.execution_id)

    def test_mechanism_failure_and_product_feedback_remain_distinct(self) -> None:
        with self.assertRaises(FileNotFoundError):
            with lifecycle.scope(self.path, "gate"):
                raise FileNotFoundError("configured executable missing")
        with lifecycle.scope(self.path, "gate") as execution:
            execution.outcome = "check_failed"
        with self.assertRaises(ValueError):
            with lifecycle.scope(self.path, "review"):
                raise ValueError("unclassified")
        states = lifecycle.observe(self.path)
        self.assertEqual([state["state"] for state in states], ["failed", "completed", "unknown"])
        self.assertEqual([state["outcome"] for state in states], ["mechanism_failure", "check_failed", "unknown"])

    def test_live_grandchild_keeps_ancestors_observable(self) -> None:
        proc = self.spawn("""
import os, subprocess, sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'dispatcher', dispatcher=True):
    with lifecycle.scope(Path(sys.argv[1]), 'worker') as execution:
        code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); print(p.pid,flush=True)"
        child = subprocess.Popen([sys.executable, '-u', '-c', code], env=execution.env())
        execution.child(child.pid)
        child.wait()
        execution.child_done(child.pid)
        os._exit(0)
""")
        grandchild_pid = int(proc.stdout.readline())
        def stop_grandchild() -> None:
            try:
                os.kill(grandchild_pid, 9)
            except ProcessLookupError:
                pass
        self.addCleanup(stop_grandchild)
        self.assertEqual(proc.wait(timeout=10), 0)
        states = lifecycle.observe(self.path)
        self.assertEqual({state["stage"]: state["state"] for state in states},
                         {"dispatcher": "active", "worker": "active"})

    def test_exception_does_not_hide_unreaped_child(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            with lifecycle.scope(self.path, "worker") as execution:
                child = self.spawn("import time; time.sleep(120)", env=execution.env())
                execution.child(child.pid)
                raise KeyboardInterrupt()
        state = lifecycle.observe(self.path)[0]
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["evidence"]["children"][0]["state"], "alive")

    def test_dead_launched_tree_retains_unprovable_descendant_uncertainty(self) -> None:
        proc = self.spawn("""
import os, subprocess, sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), 'worker') as execution:
    child = subprocess.Popen([sys.executable, '-c', 'pass'], env=execution.env())
    execution.child(child.pid)
    child.wait()
    execution.child_done(child.pid)
    os._exit(0)
""")
        self.assertEqual(proc.wait(timeout=10), 0)
        state = lifecycle.observe(self.path)[0]
        self.assertEqual(state["state"], "unknown")
        self.assertIsNone(state["ended_at"])

    def test_malformed_versioned_payload_is_ignored(self) -> None:
        with lifecycle.scope(self.path, "gate"):
            entry = lifecycle.read_events(self.path)[0]
        lifecycle.append(self.path, {**entry, "execution_id": "malformed", "process": []})
        lifecycle.append(self.path, {**entry, "execution_id": "malformed-lock", "locks": None})
        lifecycle.append(self.path, {key: value for key, value in entry.items() if key != "stage"})
        self.assertEqual([state["state"] for state in lifecycle.observe(self.path)], ["completed"])


if __name__ == "__main__":
    unittest.main()
