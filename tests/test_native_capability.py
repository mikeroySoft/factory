"""Passive native readings are not admission, and unknown is not free or zero."""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from factory import (
    darwin_observer,
    dispatch,
    gate,
    lifecycle,
    platform_identity,
    runtime_events,
    runtime_local,
)
from factory.config import Config

BOOT = "11111111-2222-3333-4444-555555555555"
HOST = str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:native-capability"))
HOLDER = """\
import fcntl, os, sys, time
op, path, signal_path = sys.argv[1:]
handle = open(path, "a")
if op == "hold":
    fcntl.flock(handle, fcntl.LOCK_EX)
    os.close(os.open(signal_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    sys.stdin.readline()
else:
    open(signal_path, "w").close()
    start = os.path.join(os.path.dirname(signal_path), "race.go")
    while not os.path.exists(start):
        time.sleep(0.01)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        open(signal_path + ".out", "w").write("lost")
    else:
        open(signal_path + ".out", "w").write("won")
        time.sleep(2)
"""


class _NoHolders:
    def boot_id(self):
        return BOOT

    def host_id(self):
        return HOST

    def process(self, pid):
        return {"pid": pid, "boot_id": BOOT, "start_ticks": 1, "pid_namespace": "darwin",
                "ppid": 1, "uid": os.getuid(), "state": "S"}

    def lock_holders(self, lock):
        return None

    def scan(self):
        return {}, {}, set(), True


class NativeCapabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(root=self.root, repo="local/native", max_active=1)
        self.cfg.lock = self.root / "gpu.lock"
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
            EVENTS=self.events, MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.procs = []

    def tearDown(self):
        darwin_observer.reset()
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    def hold(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        ready = self.root / f"ready-{path.name}"
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, "hold", str(path), str(ready)],
            stdin=subprocess.PIPE, text=True,
        )
        self.procs.append(proc)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() > deadline or proc.poll() is not None:
                self.fail(f"holder for {path.name} did not lock")
            time.sleep(0.01)
        return proc

    def release(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)

    def command(self, operation, *, root=None, unit=""):
        if operation == "service":
            return "LoadState=loaded\nActiveState=active\n"
        if operation == "timer":
            return "LoadState=loaded\nActiveState=inactive\n"
        if operation == "schedule":
            return "[]"
        raise AssertionError(operation)

    def passive(self, cfg):
        opened = []
        real = os.open

        def traced(path, flags, *args, **kwargs):
            opened.append(os.fsdecode(path))
            return real(path, flags, *args, **kwargs)

        history = {
            "status": "available",
            "capacity": {"configured": 1, "active": 0, "complete": True},
        }
        with patch.object(runtime_local, "_command", self.command), patch("os.open", traced):
            row, errors = runtime_local.dispatcher(cfg, [], history)
        return row, errors, opened

    def test_linux_kernel_table_counts_only_live_ticket_flocks(self):
        self.assertFalse(platform_identity.darwin())
        (self.cfg.factory / "locks").mkdir(parents=True)
        self.assertEqual(runtime_local._capacity(self.cfg), 0)
        proc = self.hold(dispatch.ticket_lock(7))
        self.assertEqual(runtime_local._capacity(self.cfg), 1)
        self.assertTrue(dispatch.lock_held(dispatch.ticket_lock(7)))
        self.release(proc)
        self.assertEqual(runtime_local._capacity(self.cfg), 0)
        self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))

    def test_empty_lock_directory_stays_zero_without_a_kernel_table(self):
        (self.cfg.factory / "locks").mkdir(parents=True)
        (self.cfg.factory / "locks" / "merge.lock").touch()
        with patch.object(sys, "platform", "darwin"):
            row, errors, opened = self.passive(self.cfg)
        self.assertEqual(row["capacity"], {"configured": 1, "active": 0, "complete": True})
        self.assertNotIn("/proc/locks", opened)
        self.assertFalse(any(error["source"] == "admission" for error in errors))

    def test_numeric_ticket_lock_is_not_a_capacity_count(self):
        proc = self.hold(dispatch.ticket_lock(7))
        self.assertTrue(dispatch.lock_held(dispatch.ticket_lock(7)))
        with dispatch.ticket_lock(7).open("a") as second, self.assertRaises(OSError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(sys, "platform", "darwin"):
            row, errors, opened = self.passive(self.cfg)
        self.assertIsNone(row["capacity"]["active"])
        self.assertFalse(row["capacity"]["complete"])
        self.assertNotEqual(row["capacity"]["active"], 0)
        self.assertEqual(row["capacity"]["configured"], 1)
        self.assertEqual(row["observation"], "partial")
        self.assertIn(
            {"source": "admission", "scope": "repository", "code": "locks_unavailable"}, errors)
        self.assertNotIn("/proc/locks", opened)
        self.release(proc)

    def test_stale_free_observation_is_not_a_fresh_reading(self):
        path = self.root / "gpu.lock"
        path.touch()
        darwin_observer.install(_NoHolders())
        with patch.object(sys, "platform", "darwin"):
            recorded = lifecycle.resources(self.events, [(path, "host")])[0]
            self.hold(path)
            calls = []
            real = fcntl.flock

            def spy(handle, operation):
                calls.append(operation)
                return real(handle, operation)

            with patch("fcntl.flock", spy):
                projected = runtime_events.project(self.events, [(path, "host")])["resources"][0]
        self.assertEqual(recorded["state"], "free")
        self.assertEqual(recorded["ownership"], "none")
        self.assertEqual(projected["state"], "unknown")
        self.assertEqual(projected["ownership"], "unknown")
        self.assertIsNone(projected["owner"])
        self.assertEqual(projected["observation"], "unavailable")
        self.assertNotEqual(projected["event_id"], recorded["event_id"])
        self.assertEqual(calls, [])

    def test_external_holder_blocks_admission_until_the_process_exits(self):
        proc = self.hold(dispatch.ticket_lock(7))
        with patch.object(dispatch, "frontier") as frontier, \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(1, False)
        frontier.assert_not_called()
        process.assert_not_called()
        waits = [row["wait"] for row in lifecycle.read_events(self.events)
                 if row.get("event") == "lifecycle" and row["kind"] == "wait"]
        self.assertEqual([(wait["reason"], wait["mode"]) for wait in waits],
                         [("capacity_reached", "admission")])
        with patch.object(sys, "platform", "darwin"):
            row, errors, opened = self.passive(self.cfg)
        self.assertIsNone(row["capacity"]["active"])
        self.assertFalse(row["capacity"]["complete"])
        self.assertNotEqual(row["capacity"]["active"], 0)
        self.assertIn(
            {"source": "admission", "scope": "repository", "code": "locks_unavailable"}, errors)
        self.assertNotIn("/proc/locks", opened)
        self.release(proc)
        self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))
        handle = dispatch.ticket_lock(7).open("a")
        self.addCleanup(handle.close)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)

    def test_one_nonblocking_acquirer_wins(self):
        path = self.root / "race.lock"
        path.touch()
        start = self.root / "race.go"
        racers = []
        for name in ("a", "b"):
            ready = self.root / f"race-{name}"
            proc = subprocess.Popen(
                [sys.executable, "-c", HOLDER, "race", str(path), str(ready)],
                text=True,
            )
            racers.append((proc, ready))
            self.procs.append(proc)
        deadline = time.monotonic() + 5
        while not all(ready.exists() for _, ready in racers):
            if time.monotonic() > deadline:
                self.fail("racers did not reach the start")
            time.sleep(0.01)
        start.touch()
        while not all(Path(str(ready) + ".out").exists() for _, ready in racers):
            if time.monotonic() > deadline + 5:
                self.fail("racers did not finish")
            time.sleep(0.01)
        results = sorted(Path(str(ready) + ".out").read_text() for _, ready in racers)
        self.assertEqual(results, ["lost", "won"])

    def test_gate_exclusive_still_blocks_when_passive_capacity_is_unavailable(self):
        ticket = self.hold(dispatch.ticket_lock(7))
        proc = self.hold(self.cfg.lock)
        gate.configure(self.cfg)
        done = threading.Event()
        box = {}

        def acquire(execution):
            try:
                held = gate._acquire_exclusive(execution)
                box["held"] = held
                gate._release_exclusive(execution, held)
            except BaseException as exc:  # noqa: BLE001 — the assertion reports the thread failure
                box["error"] = exc
            finally:
                done.set()

        with lifecycle.scope(self.events, "gate") as execution:
            thread = threading.Thread(target=acquire, args=(execution,))
            thread.start()
            self.assertFalse(done.wait(0.4))
            with patch.object(sys, "platform", "darwin"):
                row, errors, _opened = self.passive(self.cfg)
            self.assertIsNone(row["capacity"]["active"])
            self.assertFalse(row["capacity"]["complete"])
            self.assertIn(
                {"source": "admission", "scope": "repository", "code": "locks_unavailable"}, errors)
            self.release(proc)
            self.assertTrue(done.wait(5))
            thread.join(5)
        self.assertNotIn("error", box)
        self.assertFalse(thread.is_alive())
        self.release(ticket)
