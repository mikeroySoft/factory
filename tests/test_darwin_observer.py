"""Darwin observer regressions. Linux bytes stay on the procfs path; this file installs the private backend."""
from __future__ import annotations

import ctypes
import fcntl
import json
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
SUPPORTED_FACTS = ("darwin", "arm64", 8, "little", (27, 2))
UNSUPPORTED_FACTS = ("darwin", "x86_64", 8, "little", (27, 2))


def ident(pid, start=100, ppid=1, state="S"):
    return {"pid": pid, "boot_id": BOOT, "start_ticks": start, "pid_namespace": "darwin",
            "ppid": ppid, "uid": os.getuid(), "state": state}


def pack_args(argc, argv, env, path=b"/usr/bin/python"):
    """KERN_PROCARGS2 bytes: argc, path, pointer pad, argc argv strings, env."""
    body = struct.pack("<i", argc) + path + b"\x00"
    body += b"\x00" * darwin_observer.procargs_padding(len(path) + 1)
    for arg in argv:
        body += arg + b"\x00"
    for item in env:
        body += item + b"\x00"
    return body


def pack_bsd(pid, ppid, uid, status, sec, usec):
    raw = bytearray(darwin_observer.PROC_BSDINFO_SIZE)
    struct.pack_into("<I", raw, darwin_observer.OFF_STATUS, status)
    struct.pack_into("<i", raw, darwin_observer.OFF_PID, pid)
    struct.pack_into("<i", raw, darwin_observer.OFF_PPID, ppid)
    struct.pack_into("<I", raw, darwin_observer.OFF_UID, uid)
    struct.pack_into("<Q", raw, darwin_observer.OFF_START_SEC, sec)
    struct.pack_into("<Q", raw, darwin_observer.OFF_START_USEC, usec)
    return bytes(raw)


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
        first = {"pid": 7, "sec": 100, "usec": 10, "ppid": 1, "uid": 1, "status": darwin_observer.SRUN}
        changed = {**first, "usec": 11}
        self.assertFalse(darwin_observer._same_start(first, changed))
        asleep = {**first, "status": darwin_observer.SSLEEP}
        self.assertTrue(darwin_observer._same_start(first, asleep))
        self.assertFalse(darwin_observer._same_start(first, {**first, "ppid": 9}))
        self.assertFalse(darwin_observer._same_start(first, {**first, "uid": 9}))
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
        empty_argv = pack_args(2, [b"", b"python"], [b"PATH=/bin", self.payload("exec-1")], path=b"/x")
        kind, parsed = self.classify(empty_argv)
        self.assertEqual(kind, "tagged")
        self.assertEqual(parsed["execution_ids"], ["exec-1"])
        nested = pack_args(1, [b"python"], [ENV.encode() + b"=" + b"[" * 10000 + b"]" * 10000])
        self.assertEqual(self.classify(nested)[0], "unknown")
        unterminated = pack_args(1, [b"python"], [b"PATH=/bin"])[:-1]
        self.assertEqual(self.classify(unterminated)[0], "unknown")

    def test_bounds_are_the_verified_receipt(self):
        self.assertEqual(darwin_observer.PROC_BSDINFO_SIZE, 136)
        self.assertEqual(darwin_observer.PROC_PIDTBSDINFO, 3)
        self.assertEqual(darwin_observer.OFF_START_SEC, 120)
        self.assertEqual(darwin_observer.OFF_START_USEC, 128)
        self.assertEqual(darwin_observer.KERN_PROCARGS2, 49)
        self.assertEqual(darwin_observer.ARGMAX_CAP, 1 << 20)
        self.assertGreaterEqual(darwin_observer.SCAN_IDENTITY_LIMIT, 1061)
        self.assertEqual(darwin_observer.SCAN_CONTEXT_LIMIT, 256)
        self.assertEqual(darwin_observer.LIST_RETRIES, 4)
        self.assertEqual(darwin_observer.SUPPORTED_MAC, (27, 2))
        self.assertEqual(darwin_observer.SUPPORTED_MACHINE, "arm64")
        self.assertTrue(darwin_observer.abi_supported(SUPPORTED_FACTS))
        self.assertFalse(darwin_observer.abi_supported(UNSUPPORTED_FACTS))
        self.assertEqual(
            darwin_observer.abi_supported(),
            darwin_observer.abi_supported(darwin_observer.abi_facts()),
        )
        root = Path(platform_identity.__file__).resolve().parent
        for name in ("platform_identity.py", "darwin_observer.py", "lifecycle.py"):
            text = (root / name).read_text()
            self.assertNotIn("subprocess", text)
            self.assertNotIn("/usr/sbin/ioreg", text)
            self.assertNotIn("/usr/sbin/lsof", text)
            self.assertNotIn("/bin/ps", text)


class _ListLib:
    def __init__(self, answer):
        self.calls = []
        self.answer = answer

    def proc_listallpids(self, buf, nbytes):
        self.calls.append(nbytes)
        count, pids, errno = self.answer(nbytes)
        if errno:
            ctypes.set_errno(errno)
        if pids is not None and buf is not None and nbytes:
            cap = nbytes // ctypes.sizeof(ctypes.c_int)
            slot = ctypes.cast(buf, ctypes.POINTER(ctypes.c_int))
            for index, pid in enumerate(pids[:cap]):
                slot[index] = pid
        return count


class _InfoLib:
    def __init__(self, blobs):
        self.blobs = list(blobs)
        self.calls = []

    def proc_pidinfo(self, pid, flavor, arg, buf, size):
        self.calls.append((pid, flavor, size))
        ctypes.memmove(buf, self.blobs.pop(0), size)
        return size


class NativeSeamTest(unittest.TestCase):
    def setUp(self):
        self._libproc = darwin_observer._libproc
        self._libc = darwin_observer._libc
        darwin_observer._libproc = None
        darwin_observer._libc = None
        self.addCleanup(self._restore)

    def _restore(self):
        darwin_observer._libproc = self._libproc
        darwin_observer._libc = self._libc

    def test_unverified_abi_does_not_read(self):
        calls = []

        def forbidden():
            calls.append("lib")
            raise AssertionError("native read")

        self.assertTrue(darwin_observer.abi_supported(SUPPORTED_FACTS))
        self.assertFalse(darwin_observer.abi_supported(UNSUPPORTED_FACTS))
        with patch.object(darwin_observer, "abi_facts", return_value=UNSUPPORTED_FACTS), \
                patch.object(darwin_observer, "_lib", side_effect=forbidden):
            with self.assertRaises(OSError) as blocked:
                darwin_observer.native_process(1, BOOT)
            self.assertEqual(str(blocked.exception), "unsupported darwin ABI")
            self.assertEqual(darwin_observer._native_scan(ENV, {1}), ({}, {}, set(), False))
            with self.assertRaises(OSError) as listed:
                darwin_observer._list_pids(object())
            self.assertEqual(str(listed.exception), "unsupported darwin ABI")
        self.assertEqual(calls, [])
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_lib", side_effect=forbidden):
            with self.assertRaises(AssertionError):
                darwin_observer.native_process(1, BOOT)
            with self.assertRaises(AssertionError):
                darwin_observer._native_scan(ENV, {1})
            with self.assertRaises(OSError) as opened:
                darwin_observer._list_pids(object())
            self.assertEqual(str(opened.exception), "proc_listallpids is unavailable")
        self.assertEqual(calls, ["lib", "lib"])

    def test_missing_library_and_symbol_fail_closed(self):
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(ctypes, "CDLL", side_effect=OSError("libproc missing")):
            with self.assertRaises(OSError) as missing:
                darwin_observer.native_process(4, BOOT)
        self.assertNotIsInstance(missing.exception, ProcessLookupError)

        class Empty:
            pass

        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_lib", return_value=(Empty(), Empty())):
            with self.assertRaises(OSError) as symbol:
                darwin_observer.native_process(4, BOOT)
        self.assertNotIsInstance(symbol.exception, ProcessLookupError)

    def test_live_status_change_keeps_the_same_start(self):
        common = dict(pid=42, ppid=7, uid=501, sec=100, usec=8)
        lib = _InfoLib([
            pack_bsd(status=darwin_observer.SRUN, **common),
            pack_bsd(status=darwin_observer.SSLEEP, **common),
        ])
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_lib", return_value=(lib, None)):
            identity = darwin_observer.native_process(42, BOOT)
        self.assertEqual(identity["start_ticks"], 100)
        self.assertEqual(identity["state"], "S")
        self.assertNotIn("usec", identity)
        self.assertEqual([call[0] for call in lib.calls], [42, 42])
        moved = _InfoLib([
            pack_bsd(status=darwin_observer.SRUN, **common),
            pack_bsd(status=darwin_observer.SRUN, **{**common, "ppid": 8}),
        ])
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_lib", return_value=(moved, None)):
            with self.assertRaises(OSError):
                darwin_observer.native_process(42, BOOT)

    def test_pid_list_full_buffer_grows_and_errors_stay_incomplete(self):
        width = ctypes.sizeof(ctypes.c_int)

        def grow(nbytes):
            if nbytes == 0:
                return 100, None, 0
            cap = nbytes // width
            if cap < 200:
                return cap, list(range(1, cap + 1)), 0
            return 30, list(range(1, 31)), 0

        lib = _ListLib(grow)
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS):
            pids, complete = darwin_observer._list_pids(lib)
        self.assertEqual(pids, list(range(1, 31)))
        self.assertTrue(complete)
        self.assertGreater(len(lib.calls), 2)
        self.assertTrue(all(call == 0 or call % width == 0 for call in lib.calls))
        self.assertLess(lib.calls[1] // width, lib.calls[2] // width)

        def always_full(nbytes):
            if nbytes == 0:
                return 8, None, 0
            cap = nbytes // width
            return cap, list(range(1, cap + 1)), 0

        full = _ListLib(always_full)
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS):
            _pids, complete = darwin_observer._list_pids(full)
        self.assertFalse(complete)
        self.assertEqual(len(full.calls), 1 + darwin_observer.LIST_RETRIES)

        def broken(nbytes):
            return -1, None, 1

        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS):
            self.assertEqual(darwin_observer._list_pids(_ListLib(broken)), (None, False))

        def oversized(nbytes):
            if nbytes == 0:
                return 10, None, 0
            cap = nbytes // width
            if cap < 100:
                return cap + 50, list(range(1, cap + 1)), 0
            return 4, [1, 0, -5, 8], 0

        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS):
            pids, complete = darwin_observer._list_pids(_ListLib(oversized))
        self.assertEqual(pids, [1, 8])
        self.assertFalse(complete)

    def test_context_reads_prefer_reparented_same_uid(self):
        orphan = 900
        pids = list(range(1, 401)) + [orphan]
        tag = (ENV + "=" + json.dumps(
            {"schema_version": 1, "execution_ids": ["exec-orphan"]}, separators=(",", ":"))).encode()

        def info(pid, lib=None):
            if pid == orphan:
                return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": 50, "usec": 8}
            return {"pid": pid, "ppid": 2, "uid": 9, "status": 2, "sec": 1, "usec": 1}

        def args(_libc, pid, _capacity):
            if pid == orphan:
                return pack_args(1, [b"python"], [b"PATH=/bin", tag])
            return pack_args(1, [b"other"], [b"PATH=/bin"])

        def inputs():
            return pids, True, darwin_observer.ARGMAX_CAP, object(), object()

        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", inputs), \
                patch.object(darwin_observer, "_bsdinfo", info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT):
            _processes, contexts, _unknown, complete = darwin_observer._native_scan(ENV, {501})
        self.assertTrue(complete)
        self.assertEqual(set(contexts), {orphan})
        self.assertEqual(contexts[orphan]["execution_ids"], ["exec-orphan"])

        limit = darwin_observer.SCAN_CONTEXT_LIMIT
        crowded = list(range(1, limit + 1)) + [10000]
        seen = []

        def crowded_info(pid, lib=None):
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": 5, "usec": 1}

        def crowded_args(_libc, pid, _capacity):
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def crowded_inputs():
            return crowded, True, darwin_observer.ARGMAX_CAP, object(), object()

        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", crowded_inputs), \
                patch.object(darwin_observer, "_bsdinfo", crowded_info), \
                patch.object(darwin_observer, "_procargs", crowded_args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT):
            _processes, contexts, _unknown, complete = darwin_observer._native_scan(ENV, {501})
        self.assertFalse(complete)
        self.assertNotIn(10000, seen)
        self.assertNotIn(10000, contexts)
        self.assertEqual(len(seen), limit)


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
