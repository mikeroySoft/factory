"""Darwin observer regressions. Linux bytes stay on the procfs path; this file installs the private backend."""
from __future__ import annotations

import fcntl
import os
import struct
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from factory import darwin_observer, lifecycle, platform_identity, runtime_events

BOOT = "11111111-2222-3333-4444-555555555555"
HOST = str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"))
ENV = lifecycle.CONTEXT_ENV


def ident(pid, start=100, ppid=1, state="S"):
    return {"pid": pid, "boot_id": BOOT, "start_ticks": start, "pid_namespace": "darwin",
            "ppid": ppid, "uid": os.getuid(), "state": state}


def pack_args(argc, argv, env, path=b"/usr/bin/python"):
    body = struct.pack("<i", argc) + path + b"\x00\x00"
    for arg in argv:
        body += arg + b"\x00"
    for item in env:
        body += item + b"\x00"
    return body


class Memory:
    def __init__(self):
        self.boot = BOOT
        self.host = HOST
        self.procs = {}
        self.contexts = {}
        self.complete = True
        self.exhausted = False
        self.locks = None

    def boot_id(self):
        return self.boot

    def host_id(self):
        return self.host

    def process(self, pid):
        proc = self.procs.get(pid)
        if proc is None:
            raise ProcessLookupError(pid)
        return dict(proc)

    def lock_holders(self, lock):
        return self.locks

    def scan(self):
        if self.exhausted:
            return {}, {}, set(), False
        processes, contexts, unknown = {}, {}, set()
        complete = self.complete
        for pid, proc in self.procs.items():
            if proc["state"] in {"Z", "X"}:
                continue
            processes[pid] = dict(proc)
            kind = self.contexts.get(pid)
            if kind == "unknown":
                unknown.add(pid)
                complete = False
            elif isinstance(kind, dict):
                contexts[pid] = kind
        return processes, contexts, unknown, complete


class DarwinObserverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.memory = Memory()
        self.memory.procs[os.getpid()] = ident(os.getpid(), start=1, ppid=0)
        darwin_observer.install(self.memory)
        self.addCleanup(darwin_observer.reset)
        for context in (
            patch.object(sys, "platform", "darwin"),
            patch.dict(os.environ, {ENV: ""}),
            patch("subprocess.run", side_effect=AssertionError("observer spawned")),
        ):
            context.start()
            self.addCleanup(context.stop)

    def tag(self, *ids):
        return {"schema_version": 1, "execution_ids": list(ids)}

    def pair(self):
        dispatcher = lifecycle.Execution(self.path, "dispatcher", dispatcher=True)
        dispatcher.process = ident(424242, start=10)
        dispatcher.emit("enter")
        token = lifecycle._CURRENT.set(dispatcher)
        try:
            worker = lifecycle.Execution(self.path, "worker")
        finally:
            lifecycle._CURRENT.reset(token)
        worker.process = ident(424243, start=11)
        worker.emit("enter")
        worker.emit("handoff", handoff_id="handoff-1")
        return dispatcher, worker

    def states(self):
        return {row["stage"]: row for row in lifecycle.observe(self.path)}

    def test_reparented_tagged_orphan_keeps_both_ancestors_active(self):
        dispatcher, worker = self.pair()
        self.memory.procs[424244] = ident(424244, start=50, ppid=1)
        self.memory.contexts[424244] = self.tag(*worker.execution_ids)
        self.memory.procs[424245] = ident(424245, start=60, ppid=424242)
        observed = self.states()
        self.assertEqual(observed["dispatcher"]["state"], "active")
        self.assertEqual(observed["worker"]["state"], "active")
        for stage in ("dispatcher", "worker"):
            pids = {item["pid"] for item in observed[stage]["evidence"]["descendants"]}
            self.assertEqual(pids, {424244})
        self.assertIn(dispatcher.execution_id, worker.execution_ids)

    def test_unrelated_same_uid_process_does_not_prove_activity(self):
        self.pair()
        self.memory.procs[424245] = ident(424245, start=60, ppid=1)
        before = self.path.read_bytes()
        observed = self.states()
        self.assertEqual(observed["dispatcher"]["state"], "unknown")
        self.assertEqual(observed["worker"]["state"], "unknown")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(observed["worker"]["evidence"]["descendants"], [])

    def test_pid_reuse_and_start_identity_do_not_match_the_recorded_process(self):
        execution = lifecycle.Execution(self.path, "worker")
        execution.process = ident(424242, start=100)
        execution.emit("enter")
        self.memory.procs[424242] = ident(424242, start=200)
        self.assertEqual(lifecycle._process_state(execution.process), "dead")
        self.assertEqual(lifecycle.observe(self.path)[0]["state"], "interrupted")
        self.assertEqual(execution.process["start_ticks"], 100)
        self.assertNotIn("start_usec", execution.process)

    def test_usec_disagrees_between_reads_and_journal_keeps_integer_seconds(self):
        first = {"sec": 100, "usec": 10, "ppid": 1, "uid": 1, "status": 2}
        second = {**first, "usec": 11}
        self.assertFalse(darwin_observer._same_start(first, second))
        self.assertTrue(darwin_observer._same_start(first, dict(first)))
        public = darwin_observer._public({**first, "pid": 7}, BOOT)
        self.assertEqual(public["start_ticks"], 100)
        self.assertNotIn("usec", public)

    def test_unknown_context_is_not_interruption(self):
        self.pair()
        self.memory.procs[424244] = ident(424244, start=50, ppid=1)
        self.memory.contexts[424244] = "unknown"
        before = self.path.read_bytes()
        observed = self.states()
        self.assertEqual({row["state"] for row in observed.values()}, {"unknown"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(observed["worker"]["evidence"]["descendants"], [])

    def test_positive_tag_proves_active_when_the_scan_is_incomplete(self):
        _dispatcher, worker = self.pair()
        self.memory.complete = False
        self.memory.procs[424244] = ident(424244, start=50, ppid=1)
        self.memory.contexts[424244] = self.tag(worker.execution_id, worker.root_execution_id)
        observed = self.states()
        self.assertEqual(observed["worker"]["state"], "active")
        self.assertFalse(observed["worker"]["evidence"]["scan_complete"])

    def test_retry_exhaustion_is_unknown(self):
        self.pair()
        self.memory.exhausted = True
        before = self.path.read_bytes()
        observed = self.states()
        self.assertEqual({row["state"] for row in observed.values()}, {"unknown"})
        self.assertFalse(observed["worker"]["evidence"]["scan_complete"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_recorded_child_supplements_only_an_unknown_context(self):
        _dispatcher, worker = self.pair()
        self.memory.procs[424246] = ident(424246, start=70)
        self.memory.contexts[424246] = "unknown"
        worker.child(424246)
        unknown = self.states()["worker"]
        self.assertEqual(unknown["state"], "active")
        self.assertIn(424246, {item["pid"] for item in unknown["evidence"]["descendants"]})
        self.memory.contexts[424246] = None
        absent = self.states()["worker"]
        self.assertEqual(absent["state"], "active")
        self.assertNotIn(424246, {item["pid"] for item in absent["evidence"]["descendants"]})

    def test_unavailable_attribution_keeps_requester_distinct_from_owner(self):
        path = Path(self.tmp.name) / "gpu.lock"
        path.touch()
        owner = lifecycle.Execution(self.path, "holder")
        owner.emit("enter")
        owner.resource("acquired", path, scope="host")
        requester = lifecycle.Execution(self.path, "requester")
        requester.emit("enter")
        requester.resource("requested", path, scope="host")
        with path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            with patch("fcntl.flock", side_effect=AssertionError("passive flock")):
                projected = runtime_events.project(self.path, [(path, "host")])
            resource = lifecycle.resources(self.path, [(path, "host")])[0]
        self.assertEqual(projected["resources"][0]["state"], "unknown")
        self.assertIsNone(projected["resources"][0]["owner"])
        self.assertEqual(projected["resources"][0]["evidence"]["attribution"], "unavailable")
        self.assertEqual(resource["state"], "held")
        self.assertEqual(resource["ownership"], "unknown")
        self.assertIsNone(resource["owner"])
        self.assertEqual(resource["evidence"]["attribution"], "unavailable")
        self.assertEqual([item["execution_id"] for item in resource["requests"]], [requester.execution_id])
        self.assertNotEqual(requester.execution_id, owner.execution_id)


class ProcargsTest(unittest.TestCase):
    def classify(self, data, capacity=darwin_observer.ARGMAX_CAP):
        return darwin_observer.classify_procargs(data, capacity=capacity, context_env=ENV)

    def payload(self, execution_id="exec-1"):
        import json
        body = json.dumps({"schema_version": 1, "execution_ids": [execution_id]}, separators=(",", ":"))
        return (ENV + "=" + body).encode()

    def test_exact_membership_and_rejected_context_shapes(self):
        good = pack_args(1, [b"python"], [b"PATH=/bin", self.payload("exec-1")])
        kind, parsed = self.classify(good)
        self.assertEqual(kind, "tagged")
        self.assertEqual(parsed["execution_ids"], ["exec-1"])
        cases = {
            "empty": pack_args(1, [b"python"], []),
            "malformed": b"\x01\x00\x00\x00" + b"no-nul",
            "bad-json": pack_args(1, [b"python"], [ENV.encode() + b"={"]),
            "ambiguous": pack_args(1, [b"python"], [self.payload("a"), self.payload("b")]),
            "huge-argc": struct.pack("<i", darwin_observer.ARGC_MAX + 1) + b"x\x00\x00",
        }
        for name, blob in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.classify(blob)[0], "unknown")
        absent = pack_args(1, [b"python"], [b"PATH=/bin", ENV.encode() + b"_EXTRA=1"])
        self.assertEqual(self.classify(absent)[0], "absent")
        self.assertEqual(self.classify(good, capacity=len(good))[0], "unknown")

    def test_bounds_are_the_verified_receipt(self):
        self.assertEqual(darwin_observer.PROC_BSDINFO_SIZE, 136)
        self.assertEqual(darwin_observer.PROC_PIDTBSDINFO, 3)
        self.assertEqual(darwin_observer.OFF_START_SEC, 120)
        self.assertEqual(darwin_observer.OFF_START_USEC, 128)
        self.assertEqual(darwin_observer.KERN_PROCARGS2, 49)
        self.assertEqual(darwin_observer.ARGMAX_CAP, 1 << 20)
        self.assertLessEqual(darwin_observer.SCAN_PID_LIMIT, 256)
        self.assertLessEqual(darwin_observer.SCAN_CONTEXT_LIMIT, 128)
        self.assertEqual(darwin_observer.LIST_RETRIES, 4)
        root = Path(platform_identity.__file__).resolve().parent
        for name in ("platform_identity.py", "darwin_observer.py", "lifecycle.py"):
            text = (root / name).read_text()
            self.assertNotIn("subprocess", text)
            self.assertNotIn("/usr/sbin/ioreg", text)
            self.assertNotIn("/usr/sbin/lsof", text)
            self.assertNotIn("/bin/ps", text)


class HostBootFailureTest(unittest.TestCase):
    def test_failure_does_not_spawn(self):
        spawned = []

        def boom(*args, **kwargs):
            spawned.append(args)
            raise AssertionError(args)

        with (
            patch.object(sys, "platform", "darwin"),
            patch("subprocess.run", side_effect=boom),
            patch("factory.platform_identity._sysctl_string", return_value=None),
            patch("factory.platform_identity._iokit_host_id", side_effect=OSError("io kit")),
        ):
            self.assertIsNone(platform_identity.boot_id())
            self.assertIsNone(platform_identity.host_id())
            self.assertIsNone(lifecycle._boot_id())
            self.assertIsNone(lifecycle._host_id())
        self.assertEqual(spawned, [])


if __name__ == "__main__":
    unittest.main()
