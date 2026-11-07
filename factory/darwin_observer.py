"""Private Darwin observer used by lifecycle and passive runtime reads.

The layout is the header-derived macOS 27.2 arm64 SPI receipt, not a stable
public ABI and not a compatibility switch:

  sizeof(proc_bsdinfo) = 136, PROC_PIDTBSDINFO = 3
  pbi_status @ 4 u32, pbi_pid @ 12 i32, pbi_ppid @ 16 i32, pbi_uid @ 20 u32
  pbi_start_tvsec @ 120 u64, pbi_start_tvusec @ 128 u64
  SZOMB = 5, CTL_KERN = 1, KERN_ARGMAX = 8, KERN_PROCARGS2 = 49
  KERN_ARGMAX on that host was 1048576 bytes

A different proc_pidinfo return size fails closed. Context uses one allocation
of the reported KERN_ARGMAX (capped at 1 MiB). sysctl may return success while
truncating, so a buffer that comes back full is unknown, not a short-buffer
retry. Enumeration is capped so a ~1000-PID host cannot turn one observation
into an unbounded read loop; hitting the cap is incomplete, not absence.

Tests install a backend with ``install``. Production never inspects whether it
is under a test, and it never shells out.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import struct
import sys
import time

# Receipt offsets. Do not invent a second layout for other sizes.
PROC_PIDTBSDINFO = 3
PROC_BSDINFO_SIZE = 136
OFF_STATUS = 4
OFF_PID = 12
OFF_PPID = 16
OFF_UID = 20
OFF_START_SEC = 120
OFF_START_USEC = 128
SIDL, SRUN, SSLEEP, SSTOP, SZOMB = 1, 2, 3, 4, 5
CTL_KERN = 1
KERN_ARGMAX = 8
KERN_PROCARGS2 = 49
ARGMAX_CAP = 1 << 20
ARGC_MAX = 4096
LIST_RETRIES = 4
SCAN_PID_LIMIT = 256
SCAN_CONTEXT_LIMIT = 128
SCAN_DEADLINE_SECONDS = 2.0
ESRCH = 3
EPERM = 1

_installed = None
_libproc = None
_libc = None


def install(backend) -> None:
    global _installed
    _installed = backend


def reset() -> None:
    global _installed
    _installed = None


def installed():
    return _installed


def scan(context_env: str):
    """(processes, contexts, unknown_context_pids, complete).

    ``contexts`` holds only a positively parsed context object. Empty, omitted,
    malformed, denied, truncated, and ambiguous reads are unknown pids, and
    they make the scan incomplete. They are not evidence the pid is untagged.
    """
    backend = _installed
    if backend is not None:
        return backend.scan()
    if sys.platform != "darwin":
        return {}, {}, set(), False
    return _native_scan(context_env)


def native_process(pid: int, boot_id: str | None) -> dict:
    """One identity. Two reads must agree on start seconds and microseconds."""
    if sys.platform != "darwin":
        raise OSError("darwin process identity is unavailable")
    if not isinstance(boot_id, str) or not boot_id:
        raise OSError("boot identity is unavailable")
    first = _bsdinfo(pid)
    try:
        second = _bsdinfo(pid)
    except ProcessLookupError as exc:
        raise OSError("process identity changed during read") from exc
    if not _same_start(first, second) or first["pid"] != pid or second["pid"] != pid:
        raise OSError("process identity changed during read")
    return _public(second, boot_id)


def classify_procargs(data: bytes, *, capacity: int, context_env: str):
    """('tagged', dict) | ('absent', None) | ('unknown', None).

    A full buffer is truncated success and therefore unknown. An empty env is
    unknown, not proof the context variable is absent.
    """
    if not isinstance(data, (bytes, bytearray)) or capacity <= 0 or capacity > ARGMAX_CAP:
        return "unknown", None
    if len(data) >= capacity or len(data) < 4:
        return "unknown", None
    kind, value = _parse_procargs(bytes(data), context_env.encode("ascii") + b"=")
    if kind == "tagged":
        return "tagged", value
    if kind == "absent":
        return "absent", None
    return "unknown", None


def _same_start(first: dict, second: dict) -> bool:
    return (first["sec"] == second["sec"] and first["usec"] == second["usec"]
            and first["ppid"] == second["ppid"] and first["uid"] == second["uid"]
            and first["status"] == second["status"])


def _public(info: dict, boot_id: str) -> dict:
    return {
        "pid": info["pid"], "boot_id": boot_id, "start_ticks": info["sec"],
        "pid_namespace": "darwin", "ppid": info["ppid"], "uid": info["uid"],
        "state": "Z" if info["status"] == SZOMB else "S",
    }


def _parse_procargs(buf: bytes, prefix: bytes):
    argc = struct.unpack_from("<i", buf, 0)[0]
    if argc < 0 or argc > ARGC_MAX:
        return "malformed", None
    pos = 4
    end = buf.find(b"\x00", pos)
    if end < 0:
        return "malformed", None
    pos = end + 1
    while pos < len(buf) and buf[pos] == 0:
        pos += 1
    for _ in range(argc):
        if pos >= len(buf):
            return "malformed", None
        end = buf.find(b"\x00", pos)
        if end < 0:
            return "malformed", None
        pos = end + 1
    found = None
    count = 0
    while pos < len(buf):
        if buf[pos] == 0:
            pos += 1
            continue
        end = buf.find(b"\x00", pos)
        if end < 0:
            return "malformed", None
        item = buf[pos:end]
        count += 1
        pos = end + 1
        if not item.startswith(prefix):
            continue
        if found is not None:
            return "ambiguous", None
        try:
            parsed = json.loads(item[len(prefix):].decode("utf-8"))
        except (ValueError, UnicodeError):
            return "malformed", None
        if (not isinstance(parsed, dict) or parsed.get("schema_version") != 1
                or not isinstance(parsed.get("execution_ids"), list)
                or not all(isinstance(item_id, str) and item_id for item_id in parsed["execution_ids"])):
            return "malformed", None
        found = parsed
    if found is not None:
        return "tagged", found
    if count == 0:
        return "unknown", None
    return "absent", None


def _lib():
    global _libproc, _libc
    if _libproc is None:
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError as exc:
            raise OSError("libproc is unavailable") from exc
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.proc_listallpids.restype = ctypes.c_int
        _libproc = lib
    if _libc is None:
        name = ctypes.util.find_library("System") or ctypes.util.find_library("c")
        if not name:
            raise OSError("sysctl is unavailable")
        libc = ctypes.CDLL(name, use_errno=True)
        libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                                ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                ctypes.c_void_p, ctypes.c_size_t]
        libc.sysctl.restype = ctypes.c_int
        _libc = libc
    return _libproc, _libc


def _bsdinfo(pid: int) -> dict:
    if type(pid) is not int or pid <= 0:
        raise ProcessLookupError(pid)
    lib, _ = _lib()
    buf = ctypes.create_string_buffer(PROC_BSDINFO_SIZE)
    ctypes.set_errno(0)
    size = lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, buf, PROC_BSDINFO_SIZE)
    err = ctypes.get_errno()
    if size != PROC_BSDINFO_SIZE:
        if err == ESRCH:
            raise ProcessLookupError(pid)
        raise OSError(err or 0, "proc_pidinfo failed")
    status = struct.unpack_from("<I", buf.raw, OFF_STATUS)[0]
    got = struct.unpack_from("<i", buf.raw, OFF_PID)[0]
    ppid = struct.unpack_from("<i", buf.raw, OFF_PPID)[0]
    uid = struct.unpack_from("<I", buf.raw, OFF_UID)[0]
    sec = struct.unpack_from("<Q", buf.raw, OFF_START_SEC)[0]
    usec = struct.unpack_from("<Q", buf.raw, OFF_START_USEC)[0]
    if (got != pid or ppid < 0 or status not in {SIDL, SRUN, SSLEEP, SSTOP, SZOMB}
            or usec > 999_999 or sec > 2**63 - 1):
        raise OSError("unsupported proc_bsdinfo layout")
    return {"pid": got, "ppid": ppid, "uid": uid, "status": status, "sec": int(sec), "usec": int(usec)}


def _list_pids():
    lib, _ = _lib()
    ctypes.set_errno(0)
    needed = lib.proc_listallpids(None, 0)
    if needed <= 0:
        return None
    count = needed
    for _ in range(LIST_RETRIES):
        cap = count + 64
        if cap <= 0 or cap > 100_000:
            return None
        buf = (ctypes.c_int * cap)()
        ctypes.set_errno(0)
        got = lib.proc_listallpids(ctypes.cast(buf, ctypes.c_void_p), cap * ctypes.sizeof(ctypes.c_int))
        err = ctypes.get_errno()
        if got < 0 or err not in (0, None):
            return None
        if 0 < got <= cap:
            return [int(buf[index]) for index in range(got) if buf[index] > 0]
        if got == 0:
            return []
        count = got
    return None


def _argmax(libc) -> int | None:
    mib = (ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX)
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    ctypes.set_errno(0)
    if libc.sysctl(mib, 2, ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        return None
    if value.value <= 0 or value.value > ARGMAX_CAP or size.value != ctypes.sizeof(value):
        return None
    return int(value.value)


def _procargs(libc, pid: int, capacity: int) -> bytes | None:
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    buf = ctypes.create_string_buffer(capacity)
    size = ctypes.c_size_t(capacity)
    ctypes.set_errno(0)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    got = int(size.value)
    if got <= 0 or got > capacity:
        return None
    return buf.raw[:got]


def _native_scan(context_env: str):
    try:
        lib, libc = _lib()
        capacity = _argmax(libc)
        pids = _list_pids()
    except OSError:
        return {}, {}, set(), False
    del lib
    if capacity is None or pids is None:
        return {}, {}, set(), False
    from factory import platform_identity
    boot = platform_identity.boot_id()
    if not boot:
        return {}, {}, set(), False
    complete = True
    if len(pids) > SCAN_PID_LIMIT:
        complete = False
        pids = pids[:SCAN_PID_LIMIT]
    processes = {}
    contexts = {}
    unknown: set[int] = set()
    deadline = time.monotonic() + SCAN_DEADLINE_SECONDS
    context_reads = 0
    for pid in pids:
        if time.monotonic() > deadline:
            complete = False
            break
        try:
            info = _bsdinfo(pid)
        except ProcessLookupError:
            continue
        except OSError as exc:
            complete = False
            if getattr(exc, "errno", None) == EPERM:
                continue
            continue
        if info["status"] == SZOMB:
            continue
        processes[pid] = _public(info, boot)
        if context_reads >= SCAN_CONTEXT_LIMIT:
            complete = False
            break
        context_reads += 1
        blob = _procargs(libc, pid, capacity)
        if blob is None:
            unknown.add(pid)
            complete = False
            continue
        kind, parsed = classify_procargs(blob, capacity=capacity, context_env=context_env)
        if kind == "unknown":
            unknown.add(pid)
            complete = False
            continue
        if kind != "tagged":
            continue
        try:
            again = _bsdinfo(pid)
        except (OSError, ProcessLookupError):
            processes.pop(pid, None)
            complete = False
            continue
        if not _same_start(info, again):
            processes.pop(pid, None)
            complete = False
            continue
        contexts[pid] = parsed
    return processes, contexts, unknown, complete
