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

    def test_earlier_start_keeps_a_tag_and_validated_ancestry(self):
        _dispatcher, worker = self.pair()
        self.memory.procs[worker.process["pid"]] = ident(
            worker.process["pid"], start=worker.process["start_ticks"], ppid=1)
        self.memory.procs[80] = ident(80, start=5, ppid=1)
        self.memory.contexts[80] = self.tag(worker.execution_id)
        self.memory.procs[82] = ident(82, start=4, ppid=worker.process["pid"])
        observed = self.states()
        pids = {item["pid"] for item in observed["worker"]["evidence"]["descendants"]}
        self.assertEqual(pids, {80, 82})

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
        self._resume = darwin_observer._resume
        darwin_observer._libproc = None
        darwin_observer._libc = None
        darwin_observer._resume = None
        self.addCleanup(self._restore)

    def _restore(self):
        darwin_observer._libproc = self._libproc
        darwin_observer._libc = self._libc
        darwin_observer._resume = self._resume

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
                patch.object(ctypes, "CDLL", side_effect=OSError("libproc missing")), \
                self.assertRaises(OSError) as missing:
            darwin_observer.native_process(4, BOOT)
        self.assertNotIsInstance(missing.exception, ProcessLookupError)

        class Empty:
            pass

        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_lib", return_value=(Empty(), Empty())), \
                self.assertRaises(OSError) as symbol:
            darwin_observer.native_process(4, BOOT)
        self.assertNotIsInstance(symbol.exception, ProcessLookupError)

    def test_live_status_change_keeps_the_same_start(self):
        common = {"pid": 42, "ppid": 7, "uid": 501, "sec": 100, "usec": 8}
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
                patch.object(darwin_observer, "_lib", return_value=(moved, None)), \
                self.assertRaises(OSError):
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

        origins = [{"execution_id": "exec-w", "boot_id": BOOT, "pid": 11, "start_ticks": 10, "uid": 501}]
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", inputs), \
                patch.object(darwin_observer, "_bsdinfo", info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT):
            _processes, contexts, _unknown, complete = darwin_observer._native_scan(ENV, {501}, origins)
        self.assertTrue(complete)
        self.assertEqual(set(contexts), {orphan})
        self.assertEqual(contexts[orphan]["execution_ids"], ["exec-orphan"])

    def test_newborn_orphan_is_read_ahead_of_old_launchd_agents(self):
        owner_start = 1_000
        orphan = 9000
        old = list(range(1, 301))
        spawned = []

        def boom(*args, **kwargs):
            spawned.append(args)
            raise AssertionError("observer spawned")

        def info(pid, lib=None):
            if pid == orphan:
                return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": owner_start + 2, "usec": 8}
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": 10, "usec": 1}

        def tag(execution_id):
            body = json.dumps(
                {"schema_version": 1, "execution_ids": ["exec-d", "exec-w"]}, separators=(",", ":"))
            return (ENV + "=" + body).encode()

        seen = []

        def args(_libc, pid, _capacity):
            seen.append(pid)
            if pid == orphan:
                return pack_args(1, [b"python"], [b"PATH=/bin", tag(pid)])
            return pack_args(1, [b"old"], [b"PATH=/bin"])

        def inputs():
            return old + [orphan], True, darwin_observer.ARGMAX_CAP, object(), object()

        dispatcher = {
            "execution_id": "exec-d",
            "process": {"pid": 10, "boot_id": BOOT, "start_ticks": owner_start, "uid": 501},
        }
        worker = {
            "execution_id": "exec-w",
            "process": {"pid": 11, "boot_id": BOOT, "start_ticks": owner_start + 1, "uid": 501},
        }
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", inputs), \
                patch.object(darwin_observer, "_bsdinfo", info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT), \
                patch("factory.platform_identity.darwin", return_value=True), \
                patch("subprocess.run", side_effect=boom):
            found = lifecycle._descendants([dispatcher, worker], {})
        self.assertEqual(seen[0], orphan)
        self.assertIn(old[-1], seen)
        self.assertGreater(len(seen), 1)
        self.assertEqual(spawned, [])
        for execution_id in ("exec-d", "exec-w"):
            self.assertEqual({item["pid"] for item in found[execution_id]["processes"]}, {orphan})
            state, evidence = lifecycle._evidence(
                [{"kind": "enter", "process": {"boot_id": BOOT, "pid": 11}, "locks": []}],
                {**found[execution_id], "launched": True},
            )
            self.assertEqual(state, "active")
            self.assertTrue(evidence["descendants"])
            self.assertFalse(evidence["descendant_absence_proven"])

    def test_overflow_continues_and_scope_boot_and_pid_reuse_do_not_stick(self):
        owner_start = 1_000
        limit = darwin_observer.SCAN_CONTEXT_LIMIT
        oldest = 1
        newest = limit + 1
        pids = list(range(oldest, newest + 1))
        births = {}

        def info(pid, lib=None):
            sec, usec = births.get(pid, (owner_start + pid, 1))
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": sec, "usec": usec}

        def args(_libc, pid, _capacity):
            seen.append(pid)
            if pid in tagged:
                body = json.dumps(
                    {"schema_version": 1, "execution_ids": ["exec-old"]}, separators=(",", ":"))
                return pack_args(1, [b"python"], [b"PATH=/bin", (ENV + "=" + body).encode()])
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return list(pids), True, darwin_observer.ARGMAX_CAP, object(), object()

        def origins(execution_id="exec-a"):
            return [{
                "execution_id": execution_id, "boot_id": BOOT, "pid": 11,
                "start_ticks": owner_start, "uid": 501,
            }]

        def run(execution_id="exec-a", boot=BOOT):
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=boot), \
                    patch("subprocess.run", side_effect=AssertionError("observer spawned")):
                return darwin_observer._native_scan(ENV, {501}, origins(execution_id))

        seen = []
        tagged = {oldest}
        _processes, contexts, _unknown, complete = run()
        first = list(seen)
        self.assertFalse(complete)
        self.assertNotIn(oldest, first)
        self.assertIn(newest, first)
        self.assertEqual(len(first), limit)
        self.assertNotIn(oldest, contexts)
        seen = []
        _processes, contexts, _unknown, complete = run()
        self.assertEqual(seen, [oldest])
        self.assertFalse(complete)
        self.assertIn(oldest, contexts)
        self.assertEqual(set(first).intersection(seen), set())

        seen = []
        tagged = {newest, 50}
        births[50] = (owner_start + newest + 5, 9)
        pids.append(50)
        _processes, contexts, _unknown, complete = run()
        self.assertIn(50, seen)
        self.assertIn(50, contexts)
        self.assertEqual(contexts[50]["execution_ids"], ["exec-old"])

        seen = []
        tagged = {newest}
        births.pop(50, None)
        _processes, contexts, _unknown, _complete = run("exec-b")
        self.assertIn(newest, seen)
        self.assertEqual(seen[0], newest)

        seen = []
        other = "99999999-9999-9999-9999-999999999999"
        _processes, contexts, _unknown, _complete = run("exec-a", other)
        self.assertEqual(seen[0], newest)
        self.assertIn(newest, contexts)
        same_boot = [{
            "execution_id": "exec-a", "boot_id": other, "pid": 11,
            "start_ticks": owner_start, "uid": 501,
        }]
        seen = []
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", inputs), \
                patch.object(darwin_observer, "_bsdinfo", info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=other):
            _processes, contexts, _unknown, _complete = darwin_observer._native_scan(ENV, {501}, same_boot)
        self.assertIn(newest, contexts)

    def test_same_second_stays_deferred_and_is_not_absence(self):
        owner_start = 1_000
        newer = 70
        same = 71
        earlier = 72
        pids = [newer, same, earlier]

        def info(pid, lib=None):
            sec = {newer: owner_start + 5, same: owner_start, earlier: owner_start - 5}[pid]
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": sec, "usec": 4}

        def args(_libc, pid, _capacity):
            seen.append(pid)
            if pid == earlier:
                return pack_args(1, [b"old"], [b"PATH=/bin"])
            body = json.dumps(
                {"schema_version": 1, "execution_ids": [f"exec-{pid}"]}, separators=(",", ":"))
            return pack_args(1, [b"python"], [b"PATH=/bin", (ENV + "=" + body).encode()])

        def inputs():
            return pids, True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [{
            "execution_id": "exec-w", "boot_id": BOOT, "pid": 11,
            "start_ticks": owner_start, "uid": 501,
        }]
        seen = []
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", inputs), \
                patch.object(darwin_observer, "_bsdinfo", info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT), \
                patch("subprocess.run", side_effect=AssertionError("observer spawned")):
            _processes, contexts, unknown, complete = darwin_observer._native_scan(ENV, {501}, origins)
        self.assertEqual(seen, [newer, same, earlier])
        self.assertIn(newer, contexts)
        self.assertIn(same, contexts)
        self.assertNotIn(earlier, contexts)
        self.assertNotIn(same, unknown)
        self.assertNotIn(earlier, unknown)
        self.assertTrue(complete)
        limit = darwin_observer.SCAN_CONTEXT_LIMIT
        crowded = [pid for pid in range(1, limit + 2) if pid != same]
        self.assertEqual(len(crowded), limit)
        pids[:] = crowded + [same]

        def crowded_info(pid, lib=None):
            if pid == same:
                return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": owner_start, "usec": 1}
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": owner_start + 10 + pid, "usec": 1}

        def crowded_args(_libc, pid, _capacity):
            seen.append(pid)
            if pid != same:
                return pack_args(1, [b"x"], [b"PATH=/bin"])
            body = json.dumps(
                {"schema_version": 1, "execution_ids": ["exec-same"]}, separators=(",", ":"))
            return pack_args(1, [b"python"], [b"PATH=/bin", (ENV + "=" + body).encode()])

        def crowded_inputs():
            return list(pids), True, darwin_observer.ARGMAX_CAP, object(), object()

        seen = []
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", crowded_inputs), \
                patch.object(darwin_observer, "_bsdinfo", crowded_info), \
                patch.object(darwin_observer, "_procargs", crowded_args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT):
            _processes, contexts, unknown, complete = darwin_observer._native_scan(ENV, {501}, origins)
        self.assertNotIn(same, seen)
        self.assertNotIn(same, contexts)
        self.assertNotIn(same, unknown)
        self.assertFalse(complete)
        seen = []
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", crowded_inputs), \
                patch.object(darwin_observer, "_bsdinfo", crowded_info), \
                patch.object(darwin_observer, "_procargs", crowded_args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT):
            _processes, contexts, unknown, complete = darwin_observer._native_scan(ENV, {501}, origins)
        self.assertIn(same, seen)
        self.assertIn(same, contexts)
        self.assertFalse(complete)

    def test_unknown_origin_is_not_cut_off_by_another_start(self):
        def info(pid, lib=None):
            uid, sec = {
                40: (501, 50), 41: (501, 50), 60: (502, 50),
            }.get(pid, (501, 500))
            return {"pid": pid, "ppid": 1, "uid": uid, "status": 2, "sec": sec, "usec": 1}

        def args(_libc, pid, _capacity):
            seen.append(pid)
            if pid not in (40, 41, 60):
                return pack_args(1, [b"x"], [b"PATH=/bin"])
            execution = {40: "exec-b", 41: "exec-a", 60: "exec-c"}[pid]
            body = json.dumps(
                {"schema_version": 1, "execution_ids": [execution]}, separators=(",", ":"))
            return pack_args(1, [b"python"], [b"PATH=/bin", (ENV + "=" + body).encode()])

        def inputs():
            return [40, 41, 60, 70], True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [
            {"execution_id": "exec-a", "boot_id": BOOT, "pid": 10, "start_ticks": 100, "uid": 501},
            {"execution_id": "exec-b", "boot_id": BOOT, "pid": 11, "start_ticks": None, "uid": 501},
            {"execution_id": "exec-c", "boot_id": BOOT, "pid": 12, "start_ticks": None, "uid": 502},
        ]
        entries = [
            {"execution_id": "exec-a", "process": origins[0]},
            {"execution_id": "exec-b", "process": origins[1]},
            {"execution_id": "exec-c", "process": origins[2]},
        ]
        seen = []
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", inputs), \
                patch.object(darwin_observer, "_bsdinfo", info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT), \
                patch("factory.platform_identity.darwin", return_value=True):
            found = lifecycle._descendants(entries, {})
        self.assertIn(40, seen)
        self.assertIn(60, seen)
        self.assertEqual({item["pid"] for item in found["exec-b"]["processes"]}, {40})
        self.assertEqual({item["pid"] for item in found["exec-a"]["processes"]}, {41})
        self.assertEqual({item["pid"] for item in found["exec-c"]["processes"]}, {60})
        limit = darwin_observer.SCAN_CONTEXT_LIMIT
        crowded = list(range(1000, 1000 + limit))

        def crowded_inputs():
            return crowded + [40], True, darwin_observer.ARGMAX_CAP, object(), object()

        def crowded_info(pid, lib=None):
            if pid == 40:
                return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": 50, "usec": 1}
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": 500 + pid, "usec": 1}

        seen = []
        with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                patch.object(darwin_observer, "_native_inputs", crowded_inputs), \
                patch.object(darwin_observer, "_bsdinfo", crowded_info), \
                patch.object(darwin_observer, "_procargs", args), \
                patch("factory.platform_identity.boot_id", return_value=BOOT), \
                patch("factory.platform_identity.darwin", return_value=True):
            found = lifecycle._descendants(entries[:2], {})
        self.assertNotIn(40, seen)
        self.assertEqual(found["exec-b"]["processes"], [])
        self.assertFalse(found["exec-b"]["complete"])

    def test_saved_hand_is_read_after_it_arrives(self):
        """Newer A, older B, then newer C. One context read each observation.

        The failed premise is that one timestamp can both rank arrivals and
        name the pending process. Call 1 reads A and leaves the hand on B.
        C arrives. Call 2 must read B. Call 3 must read C even though C is
        still newer than A and no further process arrives.
        """
        clock = {"now": 0}
        present = [1, 2]
        seen = []
        sec = {1: 30, 2: 20, 3: 40}

        def mono():
            return clock["now"]

        def info(pid, lib=None):
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": sec[pid], "usec": 1}

        def args(_libc, pid, _capacity):
            clock["now"] += darwin_observer.SCAN_DEADLINE_SECONDS + 1
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return list(present), True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [{
            "execution_id": "exec-a", "boot_id": BOOT, "pid": 11,
            "start_ticks": 0, "uid": 501,
        }]

        def once():
            clock["now"] = 0
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=BOOT), \
                    patch("factory.darwin_observer.time.monotonic", mono):
                darwin_observer._native_scan(ENV, {501}, origins)

        once()
        present.append(3)
        once()
        once()
        self.assertEqual(seen, [1, 2, 3])

    def test_mixed_uid_hand_follows_pending_order(self):
        clock = {"now": 0}
        seen = []

        def mono():
            return clock["now"]

        def info(pid, lib=None):
            if pid == 3:
                return {"pid": 3, "ppid": 1, "uid": 502, "status": 2, "sec": 500, "usec": 1}
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": 50, "usec": 1}

        def args(_libc, pid, _capacity):
            clock["now"] += darwin_observer.SCAN_DEADLINE_SECONDS + 1
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return [2, 3], True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [
            {"execution_id": "exec-a", "boot_id": BOOT, "pid": 10, "start_ticks": 10, "uid": 501},
            {"execution_id": "exec-b", "boot_id": BOOT, "pid": 12, "start_ticks": None, "uid": 502},
        ]

        def once():
            clock["now"] = 0
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=BOOT), \
                    patch("factory.darwin_observer.time.monotonic", mono):
                darwin_observer._native_scan(ENV, {501, 502}, origins)

        once()
        once()
        self.assertEqual(seen, [2, 3])

    def test_missing_hand_continues_at_the_next_pending_process(self):
        clock = {"now": 0}
        seen = []
        rows = {2: (80, 501), 1: (50, 501), 3: (500, 502)}
        present = [2, 1, 3]

        def mono():
            return clock["now"]

        def info(pid, lib=None):
            sec, uid = rows[pid]
            return {"pid": pid, "ppid": 1, "uid": uid, "status": 2, "sec": sec, "usec": 1}

        def args(_libc, pid, _capacity):
            clock["now"] += darwin_observer.SCAN_DEADLINE_SECONDS + 1
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return list(present), True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [
            {"execution_id": "exec-a", "boot_id": BOOT, "pid": 10, "start_ticks": 10, "uid": 501},
            {"execution_id": "exec-b", "boot_id": BOOT, "pid": 12, "start_ticks": None, "uid": 502},
        ]

        def once():
            clock["now"] = 0
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=BOOT), \
                    patch("factory.darwin_observer.time.monotonic", mono):
                darwin_observer._native_scan(ENV, {501, 502}, origins)

        once()
        present.remove(1)
        once()
        self.assertEqual(seen, [2, 3])

        darwin_observer._resume = {}
        seen = []
        present = [2, 1, 3]
        rows[1] = (50, 501)
        once()
        rows[1] = (900, 501)
        once()
        self.assertEqual(seen, [2, 3])

    def test_deadline_advances_only_attempted_reads(self):
        count = 512
        clock = {"now": 0}
        seen = []

        def mono():
            return clock["now"]

        def info(pid, lib=None):
            return {"pid": pid, "ppid": 1, "uid": 501, "status": 2, "sec": pid, "usec": 1}

        def args(_libc, pid, _capacity):
            clock["now"] += darwin_observer.SCAN_DEADLINE_SECONDS + 1
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return list(range(1, count + 1)), True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [{
            "execution_id": "exec-a", "boot_id": BOOT, "pid": 11,
            "start_ticks": 0, "uid": 501,
        }]
        for _step in range(count):
            clock["now"] = 0
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=BOOT), \
                    patch("factory.darwin_observer.time.monotonic", mono):
                _processes, _contexts, _unknown, complete = darwin_observer._native_scan(
                    ENV, {501}, origins)
            self.assertFalse(complete)
        self.assertEqual(seen, list(range(count, 0, -1)))

    def test_arrivals_cannot_consume_the_continuation(self):
        limit = darwin_observer.SCAN_CONTEXT_LIMIT
        owner_start = 1_000
        oldest = 1
        pids = list(range(oldest, limit + 2))
        seen = []

        def info(pid, lib=None):
            return {
                "pid": pid, "ppid": 1, "uid": 501, "status": 2,
                "sec": owner_start + pid, "usec": 1,
            }

        def args(_libc, pid, _capacity):
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return list(pids), True, darwin_observer.ARGMAX_CAP, object(), object()

        origins = [{
            "execution_id": "exec-a", "boot_id": BOOT, "pid": 11,
            "start_ticks": owner_start, "uid": 501,
        }]

        def run():
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=BOOT):
                return darwin_observer._native_scan(ENV, {501}, origins)

        run()
        births = list(range(10_000, 10_000 + limit))
        pids.extend(births)
        seen = []
        run()
        arrivals = [pid for pid in seen if pid in births]
        self.assertIn(oldest, seen)
        self.assertEqual(seen[0], oldest)
        self.assertEqual(len(arrivals), limit - 1)
        self.assertLess(len(arrivals), limit)

    def test_interleaved_scopes_keep_separate_hands(self):
        limit = darwin_observer.SCAN_CONTEXT_LIMIT
        owner_start = 1_000
        pids = list(range(1, limit + 45))
        seen = []

        def info(pid, lib=None):
            return {
                "pid": pid, "ppid": 1, "uid": 501, "status": 2,
                "sec": owner_start + pid, "usec": 1,
            }

        def args(_libc, pid, _capacity):
            seen.append(pid)
            return pack_args(1, [b"x"], [b"PATH=/bin"])

        def inputs():
            return list(pids), True, darwin_observer.ARGMAX_CAP, object(), object()

        def run(execution_id):
            origins = [{
                "execution_id": execution_id, "boot_id": BOOT, "pid": 11,
                "start_ticks": owner_start, "uid": 501,
            }]
            with patch.object(darwin_observer, "abi_facts", return_value=SUPPORTED_FACTS), \
                    patch.object(darwin_observer, "_native_inputs", inputs), \
                    patch.object(darwin_observer, "_bsdinfo", info), \
                    patch.object(darwin_observer, "_procargs", args), \
                    patch("factory.platform_identity.boot_id", return_value=BOOT):
                return darwin_observer._native_scan(ENV, {501}, origins)

        run("exec-a")
        first = list(seen)
        seen = []
        run("exec-b")
        self.assertEqual(seen[0], pids[-1])
        seen = []
        run("exec-a")
        self.assertEqual(set(first).intersection(seen), set())
        self.assertIn(1, seen)


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
