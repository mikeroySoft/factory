"""Real-flock boundaries for wait and resource observations in the F01 journal."""
from __future__ import annotations

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


class ResourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "events.jsonl"
        self.lock = Path(temporary.name) / "exclusive.lock"
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(lifecycle.CONTEXT_ENV, None)

    def spawn(self, code: str) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", code, str(self.path), str(self.lock)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )

        def stop() -> None:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdin.close()
            proc.stdout.close()

        self.addCleanup(stop)
        return proc

    def holder(self, *, instrumented=True, stage="holder", delay_release=False) -> subprocess.Popen:
        if not instrumented:
            code = """
import fcntl, sys
with open(sys.argv[2], 'a+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('held', flush=True)
    sys.stdin.readline()
"""
        else:
            code = f"""
import fcntl, sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), {stage!r}) as execution:
    with open(sys.argv[2], 'a+') as lock:
        request = execution.resource('requested', sys.argv[2], scope='host', blocking=True)
        if lifecycle._lock_state(lifecycle._lock(sys.argv[2])) == 'held':
            execution.wait('exclusive_resource', resource=request['resource'])
            print('waiting', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        execution.resource('acquired', sys.argv[2], scope='host', blocking=True)
        execution.wait_end()
        print('held', flush=True)
        sys.stdin.readline()
        fcntl.flock(lock, fcntl.LOCK_UN)
        if {delay_release!r}:
            print('unlocked', flush=True)
            sys.stdin.readline()
        execution.resource('released', sys.argv[2], scope='host', blocking=True)
"""
        return self.spawn(code)

    def signal(self, proc: subprocess.Popen) -> None:
        proc.stdin.write("continue\n")
        proc.stdin.flush()

    def snapshot(self) -> dict:
        return lifecycle.resources(self.path, [(self.lock, "host")])[0]

    def test_requester_is_not_owner_and_acquires_only_after_release(self) -> None:
        holder = self.holder()
        self.assertEqual(holder.stdout.readline().strip(), "held")
        requester = self.holder(stage="requester")
        self.assertEqual(requester.stdout.readline().strip(), "waiting")
        first = self.snapshot()
        self.assertEqual((first["state"], first["ownership"]), ("held", "confirmed"))
        self.assertEqual(first["owner"]["process"]["pid"], holder.pid)
        self.assertEqual([request["process"]["pid"] for request in first["requests"]], [requester.pid])
        states = {state["stage"]: state for state in lifecycle.observe(self.path)}
        self.assertEqual(states["requester"]["state"], "active")
        self.assertEqual(states["requester"]["wait"]["reason"], "exclusive_resource")
        self.assertFalse(any(row["kind"] == "lock_acquired" for row in states["requester"]["events"]))
        unchanged = self.snapshot()
        self.assertEqual(unchanged["event_id"], first["event_id"])
        self.signal(holder)
        self.assertEqual(holder.wait(timeout=10), 0)
        self.assertEqual(requester.stdout.readline().strip(), "held")
        second = self.snapshot()
        self.assertEqual(second["owner"]["process"]["pid"], requester.pid)
        self.assertNotEqual(second["owner"]["acquisition_id"], first["owner"]["acquisition_id"])
        self.assertEqual(second["resource"]["id"], first["resource"]["id"])
        self.assertEqual(second["requests"], [])
        waiting = next(state for state in lifecycle.observe(self.path) if state["stage"] == "requester")
        self.assertIsNone(waiting["wait"])
        self.signal(requester)
        self.assertEqual(requester.wait(timeout=10), 0)
        released = self.snapshot()
        self.assertEqual((released["state"], released["ownership"], released["owner"]), ("free", "none", None))

    def test_external_holder_is_unknown_and_observers_deduplicate(self) -> None:
        holder = self.holder(instrumented=False)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        observers = [self.spawn("""
import json, sys
from pathlib import Path
from factory import lifecycle
print(json.dumps(lifecycle.resources(Path(sys.argv[1]), [(sys.argv[2], 'host')])[0]), flush=True)
""") for _ in range(3)]
        snapshots = [json.loads(observer.stdout.readline()) for observer in observers]
        for observer in observers:
            self.assertEqual(observer.wait(timeout=10), 0)
        self.assertEqual({snapshot["state"] for snapshot in snapshots}, {"held"})
        self.assertEqual({snapshot["ownership"] for snapshot in snapshots}, {"unknown"})
        self.assertTrue(all(snapshot["owner"] is None for snapshot in snapshots))
        self.assertEqual(len({snapshot["event_id"] for snapshot in snapshots}), 1)
        self.assertEqual(lifecycle.observe(self.path), [])
        self.assertEqual(len(lifecycle.read_events(self.path)), 1)

    def test_live_old_holder_cannot_own_external_replacement_during_release_gap(self) -> None:
        old = self.holder(delay_release=True)
        self.assertEqual(old.stdout.readline().strip(), "held")
        self.assertEqual(self.snapshot()["owner"]["process"]["pid"], old.pid)
        self.signal(old)
        self.assertEqual(old.stdout.readline().strip(), "unlocked")
        replacement = self.holder(instrumented=False)
        self.assertEqual(replacement.stdout.readline().strip(), "held")
        observed = self.snapshot()
        self.assertIsNone(old.poll())
        self.assertEqual((observed["state"], observed["ownership"], observed["owner"]), ("held", "unknown", None))
        self.signal(old)
        self.assertEqual(old.wait(timeout=10), 0)
        self.assertEqual(self.snapshot()["event_id"], observed["event_id"])

    def test_dead_holder_replacement_and_old_release_do_not_erase_new_owner(self) -> None:
        old = self.holder()
        self.assertEqual(old.stdout.readline().strip(), "held")
        before = self.snapshot()
        acquired = next(row for row in lifecycle.read_events(self.path) if row["kind"] == "lock_acquired")
        old.kill()
        old.wait(timeout=10)
        replacement = self.holder(stage="replacement")
        self.assertEqual(replacement.stdout.readline().strip(), "held")
        after = self.snapshot()
        self.assertEqual(after["owner"]["process"]["pid"], replacement.pid)
        lifecycle.append(self.path, {**acquired, "kind": "lock_released", "sequence": acquired["sequence"] + 1,
                                     "event_id": "late-old-release", "locks": []})
        self.assertEqual(self.snapshot()["owner"], after["owner"])
        self.assertNotEqual(after["owner"]["execution_id"], before["owner"]["execution_id"])

    def test_killed_requester_loses_pending_wait_and_request_once(self) -> None:
        holder = self.holder(instrumented=False)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        requester = self.holder(stage="requester")
        self.assertEqual(requester.stdout.readline().strip(), "waiting")
        self.assertEqual(self.snapshot()["requests"][0]["process"]["pid"], requester.pid)
        requester.kill()
        requester.wait(timeout=10)
        first = lifecycle.observe(self.path)[0]
        self.assertEqual(first["state"], "interrupted")
        self.assertIsNone(first["wait"])
        observed = self.snapshot()
        self.assertEqual(observed["requests"], [])
        self.assertEqual(observed["ownership"], "unknown")
        rows = lifecycle.read_events(self.path)
        self.assertEqual(lifecycle.observe(self.path)[0]["events"], first["events"])
        self.assertEqual(self.snapshot()["event_id"], observed["event_id"])
        self.assertEqual(lifecycle.read_events(self.path), rows)

    def test_interrupted_release_is_observed_not_backdated_or_synthesized(self) -> None:
        holder = self.holder()
        self.assertEqual(holder.stdout.readline().strip(), "held")
        first = self.snapshot()
        holder.kill()
        holder.wait(timeout=10)
        after_death = lifecycle._now()
        observed = self.snapshot()
        self.assertEqual((observed["state"], observed["owner"]), ("free", None))
        self.assertGreaterEqual(observed["at"], after_death)
        self.assertNotEqual(observed["event_id"], first["event_id"])
        self.assertEqual(self.snapshot()["event_id"], observed["event_id"])
        self.assertFalse(any(row["kind"] == "lock_released" for row in lifecycle.read_events(self.path)))
        self.assertEqual(lifecycle.observe(self.path)[0]["state"], "interrupted")

    def test_terminal_execution_is_not_owner_even_while_its_process_holds_lock(self) -> None:
        holder = self.spawn("""
import fcntl, sys
from pathlib import Path
from factory import lifecycle
with open(sys.argv[2], 'a+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    with lifecycle.scope(Path(sys.argv[1]), 'holder') as execution:
        execution.resource('acquired', sys.argv[2], scope='host')
        print('held', flush=True)
        sys.stdin.readline()
    print('scope-exited', flush=True)
    sys.stdin.readline()
""")
        self.assertEqual(holder.stdout.readline().strip(), "held")
        self.assertEqual(self.snapshot()["ownership"], "confirmed")
        self.signal(holder)
        self.assertEqual(holder.stdout.readline().strip(), "scope-exited")
        observed = self.snapshot()
        self.assertEqual((observed["state"], observed["ownership"], observed["owner"]), ("held", "unknown", None))

    def test_unavailable_attribution_never_confirms_even_a_live_acquisition(self) -> None:
        holder = self.holder()
        self.assertEqual(holder.stdout.readline().strip(), "held")
        with patch.object(lifecycle, "_lock_holders", return_value=None):
            observed = self.snapshot()
        self.assertEqual((observed["state"], observed["ownership"], observed["owner"]), ("held", "unknown", None))

    def test_resource_scope_and_canonical_path_bound_identity(self) -> None:
        self.lock.touch()
        alias = self.lock.with_name("alias")
        alias.symlink_to(self.lock)
        other = self.path.parent / "other" / "events.jsonl"
        host = lifecycle.resources(self.path, [(self.lock, "host"), (alias, "host")])
        self.assertEqual(len(host), 1)
        self.assertEqual(lifecycle.resources(other, [(alias, "host")])[0]["resource"]["id"], host[0]["resource"]["id"])
        repository = next(row for row in lifecycle.resources(self.path, [(self.lock, "repository")])
                          if row["resource"]["scope"] == "repository")
        other_repository = next(row for row in lifecycle.resources(other, [(self.lock, "repository")])
                                if row["resource"]["scope"] == "repository")
        self.assertNotEqual(repository["resource"]["id"], other_repository["resource"]["id"])

    def test_unknown_execution_and_malformed_waits_do_not_claim_pending_wait(self) -> None:
        with lifecycle.scope(self.path, "gate") as execution:
            wait = execution.wait("exclusive_resource")
            lifecycle.append(self.path, {**wait, "sequence": wait["sequence"] + 1,
                                         "wait": {**wait["wait"], "mode": []}})
            with patch.object(lifecycle, "_process_state", return_value="unknown"):
                observed = lifecycle.observe(self.path)[0]
            self.assertEqual(observed["state"], "unknown")
            self.assertIsNone(observed["wait"])
            self.assertEqual(lifecycle.observe(self.path)[0]["wait"]["event_id"], wait["event_id"])
            execution.wait_end()
            self.assertIsNone(lifecycle.observe(self.path)[0]["wait"])

    def test_malformed_acquisition_cannot_create_owner(self) -> None:
        holder = self.holder(instrumented=False)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        with lifecycle.scope(self.path, "requester") as execution:
            requested = execution.resource("requested", self.lock, scope="host")
            for resource in ({**requested["resource"], "scope": []}, {**requested["resource"], "id": "unrelated"}):
                lifecycle.append(self.path, {**requested, "kind": "lock_acquired", "resource": resource,
                                             "acquisition_id": "bad", "lock": str(self.lock)})
            self.assertEqual(self.snapshot()["ownership"], "unknown")
            self.assertIsNone(self.snapshot()["owner"])

    def test_schedule_requires_confirmed_idle_future_and_deduplicates(self) -> None:
        arguments = {"next_at": "2030-01-01T12:00:00Z", "timer_active": True, "service_active": False,
                     "observed_at": "2030-01-01T11:00:00Z"}
        scheduled = lifecycle.observe_schedule(self.path, **arguments)
        self.assertEqual(scheduled["wait"]["reason"], "scheduled_next_pass")
        repeated = lifecycle.observe_schedule(self.path, **{**arguments, "observed_at": "2030-01-01T11:05:00Z"})
        self.assertEqual(repeated["event_id"], scheduled["event_id"])
        self.assertEqual(repeated["at"], scheduled["at"])
        self.assertEqual(repeated["observed_at"], "2030-01-01T11:05:00Z")
        for change in ({"timer_active": None}, {"service_active": True}, {"service_active": None},
                       {"next_at": None}, {"observed_at": "2030-01-01T12:00:01Z"}):
            self.assertIsNone(lifecycle.observe_schedule(self.path, **{**arguments, **change})["wait"])
        self.assertEqual(lifecycle.observe(self.path), [])


if __name__ == "__main__":
    unittest.main()
