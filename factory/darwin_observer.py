"""Private Darwin observer used by lifecycle and passive runtime reads.

The only supported receipt is macOS 27.2, arm64, 64-bit pointers, little-endian,
SDK 27.2. The struct layout below was measured on that receipt. It is not a
stable public ABI. Any other platform, architecture, pointer width, or byte
order is unknown and is not read.

  sizeof(proc_bsdinfo) = 136, PROC_PIDTBSDINFO = 3
  pbi_status @ 4 u32, pbi_pid @ 12 i32, pbi_ppid @ 16 i32, pbi_uid @ 20 u32
  pbi_start_tvsec @ 120 u64, pbi_start_tvusec @ 128 u64
  SZOMB = 5, CTL_KERN = 1, KERN_ARGMAX = 8, KERN_PROCARGS2 = 49
  KERN_ARGMAX on that host was 1048576 bytes

proc_listallpids takes a byte count and returns a pid count. A buffer that
comes back full is grown a bounded number of times and then reported incomplete.
Context uses one KERN_ARGMAX allocation, capped at 1 MiB. A buffer that comes
back full is truncated and unknown, not a short read to grow.

Identity reads cover the verified host's 1061-pid population, up to 4096.
Context reads stay capped at 256. A same-uid process that started after that
uid's known process-start second is read newest first. The same second and any
earlier start stay deferred at lower priority; they are not dropped. An
unknown origin keeps that uid deferred, and another uid's start is not a
cutoff. A private per-scope hand records only context reads that were started.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import platform
import struct
import sys
import time

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
LIST_SLACK = 64
LIST_CEILING = 100_000
SCAN_IDENTITY_LIMIT = 4096
SCAN_CONTEXT_LIMIT = 256
SCAN_CONTINUATION_RESERVE = 64
SCAN_DEADLINE_SECONDS = 2.0
_RESUME_SCOPES = 8
ESRCH = 3
POINTER = 8
EXEC_PREFIX = "executable_path="
SUPPORTED_PLATFORM = "darwin"
SUPPORTED_MACHINE = "arm64"
SUPPORTED_POINTER = 8
SUPPORTED_BYTEORDER = "little"
SUPPORTED_MAC = (27, 2)

_installed = None
_libproc = None
_libc = None
_resume = {}


def install(backend) -> None:
    global _installed
    _installed = backend


def reset() -> None:
    global _installed, _resume
    _installed = None
    _resume = {}


def installed():
    return _installed


def abi_facts() -> tuple:
    """Runtime facts compared with the receipt before any native read."""
    return (
        sys.platform, platform.machine(), struct.calcsize("P"), sys.byteorder,
        _mac_release(platform.mac_ver()[0]),
    )


def abi_supported(facts: tuple | None = None) -> bool:
    return (facts if facts is not None else abi_facts()) == (
        SUPPORTED_PLATFORM, SUPPORTED_MACHINE, SUPPORTED_POINTER,
        SUPPORTED_BYTEORDER, SUPPORTED_MAC)


def scan(context_env: str, uids=(), origins=()):
    """(processes, contexts, unknown_context_pids, complete)."""
    backend = _installed
    if backend is not None:
        return backend.scan()
    if not abi_supported():
        return {}, {}, set(), False
    return _native_scan(context_env, uids, origins)


def native_process(pid: int, boot_id: str | None) -> dict:
    """One identity. Two reads must agree on start time, parent, and uid.

    A live status change (running versus sleeping) is the same process.
    The returned state is the second read, so a final zombie is dead.
    """
    if not abi_supported():
        raise OSError("unsupported darwin ABI")
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
    """('tagged', dict) | ('absent', None) | ('unknown', None)."""
    if not isinstance(data, (bytes, bytearray)) or capacity <= 0 or capacity > ARGMAX_CAP:
        return "unknown", None
    if len(data) >= capacity or len(data) < 4:
        return "unknown", None
    try:
        kind, value = _parse_procargs(bytes(data), context_env.encode("ascii") + b"=")
    except RecursionError:
        return "unknown", None
    if kind == "tagged":
        return "tagged", value
    if kind == "absent":
        return "absent", None
    return "unknown", None


def procargs_padding(path_with_nul: int) -> int:
    """NUL pad after the returned exec path, for this receipt's pointer width.

    The kernel aligns ``executable_path=`` plus the path. That prefix is 16
    bytes, a multiple of 8, so stripping it leaves the pad of the path itself.
    """
    if path_with_nul < 1:
        raise ValueError("exec path is empty")
    if len(EXEC_PREFIX) % POINTER != 0:
        raise ValueError("exec prefix is not pointer aligned")
    return (POINTER - (path_with_nul % POINTER)) % POINTER


def _mac_release(text: str):
    parts = (text or "").split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _same_start(first: dict, second: dict) -> bool:
    return (first["pid"] == second["pid"] and first["sec"] == second["sec"]
            and first["usec"] == second["usec"] and first["ppid"] == second["ppid"]
            and first["uid"] == second["uid"])


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
    end = buf.find(b"\x00", 4)
    if end < 0:
        return "malformed", None
    path_with_nul = end - 4 + 1
    pad = procargs_padding(path_with_nul)
    argv_at = end + 1 + pad
    if argv_at > len(buf) or buf[end + 1:argv_at] != b"\x00" * pad:
        return "malformed", None
    pos = argv_at
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
        except (ValueError, UnicodeError, RecursionError):
            return "malformed", None
        if (not isinstance(parsed, dict) or parsed.get("schema_version") != 1
                or not isinstance(parsed.get("execution_ids"), list)
                or not all(isinstance(item_id, str) and item_id
                           for item_id in parsed["execution_ids"])):
            return "malformed", None
        found = parsed
    if found is not None:
        return "tagged", found
    if count == 0:
        return "unknown", None
    return "absent", None


def _symbol(lib, name):
    try:
        return getattr(lib, name)
    except AttributeError as exc:
        raise OSError(f"{name} is unavailable") from exc


def _lib():
    global _libproc, _libc
    if not abi_supported():
        raise OSError("unsupported darwin ABI")
    if _libproc is None:
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError as exc:
            raise OSError("libproc is unavailable") from exc
        pidinfo = _symbol(lib, "proc_pidinfo")
        listpids = _symbol(lib, "proc_listallpids")
        pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        pidinfo.restype = ctypes.c_int
        listpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        listpids.restype = ctypes.c_int
        _libproc = lib
    if _libc is None:
        name = ctypes.util.find_library("System") or ctypes.util.find_library("c")
        if not name:
            raise OSError("sysctl is unavailable")
        try:
            libc = ctypes.CDLL(name, use_errno=True)
        except OSError as exc:
            raise OSError("sysctl is unavailable") from exc
        sysctl = _symbol(libc, "sysctl")
        sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        sysctl.restype = ctypes.c_int
        _libc = libc
    return _libproc, _libc


def _bsdinfo(pid: int, lib=None) -> dict:
    if not abi_supported():
        raise OSError("unsupported darwin ABI")
    if type(pid) is not int or pid <= 0:
        raise ProcessLookupError(pid)
    if lib is None:
        lib, _ = _lib()
    fn = _symbol(lib, "proc_pidinfo")
    buf = ctypes.create_string_buffer(PROC_BSDINFO_SIZE)
    ctypes.set_errno(0)
    try:
        size = fn(pid, PROC_PIDTBSDINFO, 0, buf, PROC_BSDINFO_SIZE)
    except AttributeError as exc:
        raise OSError("proc_pidinfo is unavailable") from exc
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
    return {
        "pid": got, "ppid": ppid, "uid": uid, "status": status,
        "sec": int(sec), "usec": int(usec),
    }


def _list_pids(lib):
    """(pids, complete). A full buffer is grown, not treated as the whole table."""
    if not abi_supported():
        raise OSError("unsupported darwin ABI")
    query = _symbol(lib, "proc_listallpids")
    ctypes.set_errno(0)
    needed = query(None, 0)
    if ctypes.get_errno() not in (0, None) or needed < 0:
        return None, False
    if needed == 0:
        return [], True
    count = int(needed)
    last = None
    width = ctypes.sizeof(ctypes.c_int)
    for _ in range(LIST_RETRIES):
        cap = count + LIST_SLACK
        if cap <= 0 or cap > LIST_CEILING:
            return None, False
        nbytes = cap * width
        buf = (ctypes.c_int * cap)()
        ctypes.set_errno(0)
        got = query(ctypes.cast(buf, ctypes.c_void_p), nbytes)
        err = ctypes.get_errno()
        if err not in (0, None) or type(got) is not int or got < 0:
            return None, False
        if got == 0:
            return [], True
        if got > cap:
            if got > LIST_CEILING:
                return None, False
            count = got
            continue
        pids = []
        dropped = False
        for index in range(got):
            pid = int(buf[index])
            if pid > 0:
                pids.append(pid)
            else:
                dropped = True
        if got < cap:
            return pids, not dropped
        last = pids
        count = cap + LIST_SLACK
    return last, False


def _argmax(libc) -> int | None:
    sysctl = _symbol(libc, "sysctl")
    mib = (ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX)
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    ctypes.set_errno(0)
    if sysctl(mib, 2, ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        return None
    if value.value <= 0 or value.value > ARGMAX_CAP or size.value != ctypes.sizeof(value):
        return None
    return int(value.value)


def _procargs(libc, pid: int, capacity: int) -> bytes | None:
    sysctl = _symbol(libc, "sysctl")
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    buf = ctypes.create_string_buffer(capacity)
    size = ctypes.c_size_t(capacity)
    ctypes.set_errno(0)
    if sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    got = int(size.value)
    if got <= 0 or got >= capacity:
        return None
    return buf.raw[:got]


def _stamp(info: dict) -> tuple:
    return (info["sec"], info["usec"], info["pid"])


def _scope_key(origins, boot: str) -> tuple:
    rows = []
    for origin in origins if isinstance(origins, (list, tuple)) else ():
        if not isinstance(origin, dict):
            continue
        execution = origin.get("execution_id")
        origin_boot = origin.get("boot_id")
        pid = origin.get("pid")
        start = origin.get("start_ticks")
        rows.append((
            execution if isinstance(execution, str) else "",
            origin_boot if isinstance(origin_boot, str) else "",
            pid if type(pid) is int else -1,
            start if type(start) is int else -1,
        ))
    return (boot, tuple(sorted(rows)))


def _uid_starts(origins, boot: str) -> dict:
    """uid -> start seconds, or None when that uid has an unknown origin.

    Journal wall time is not a cutoff. A missing start or a different boot is
    unknown for that uid only. Another uid's start is not a cutoff.
    """
    known = {}
    for origin in origins if isinstance(origins, (list, tuple)) else ():
        if not isinstance(origin, dict):
            continue
        uid = origin.get("uid")
        if type(uid) is not int:
            continue
        start = origin.get("start_ticks")
        usable = origin.get("boot_id") == boot and type(start) is int
        if known.get(uid) is None and uid in known:
            continue
        if not usable:
            known[uid] = None
        else:
            known.setdefault(uid, []).append(start)
    return known


def _priority(info: dict, known: dict) -> bool:
    starts = known.get(info["uid"])
    if not starts:
        return False
    return any(info["sec"] > start for start in starts)


def _hand_index(sequence: list, hand: tuple) -> int:
    for index, info in enumerate(sequence):
        if _stamp(info) <= hand:
            return index
    return 0


def _resume_row(scope: tuple, boot: str):
    if not isinstance(_resume, dict):
        return None
    row = _resume.get(scope)
    if not isinstance(row, dict) or row.get("boot") != boot:
        return None
    return row


def _plan_reads(priority: list, deferred: list, scope: tuple, boot: str) -> dict:
    """Choose context reads. The hand moves only after an attempt starts.

    Priority is newest first. Deferred follows, also newest first, and includes
    the same second and any earlier start. When a hand exists, new arrivals
    cannot take the whole budget: SCAN_CONTINUATION_RESERVE stays with the hand.
    """
    sequence = priority + deferred
    row = _resume_row(scope, boot)
    hand = row.get("hand") if row else None
    newest = row.get("newest") if row else None
    arrivals = []
    if newest is not None:
        arrivals = [info for info in sequence if _stamp(info) > newest]
    if hand is None:
        chosen = list(sequence[:SCAN_CONTEXT_LIMIT])
        return {
            "sequence": sequence, "chosen": chosen, "cont_start": 0,
            "hand": None, "newest": newest, "scope": scope, "boot": boot,
        }
    start = _hand_index(sequence, hand)
    continuation = []
    arrival_ids = {_stamp(info) for info in arrivals}
    for info in sequence[start:]:
        if _stamp(info) not in arrival_ids:
            continuation.append(info)
    if arrivals:
        reserve = min(SCAN_CONTINUATION_RESERVE, len(continuation))
        picked_arrivals = arrivals[:SCAN_CONTEXT_LIMIT - reserve]
    else:
        picked_arrivals = []
    room = SCAN_CONTEXT_LIMIT - len(picked_arrivals)
    chosen = continuation[:room] + picked_arrivals
    return {
        "sequence": sequence, "chosen": chosen, "cont_start": start,
        "hand": hand, "newest": newest, "scope": scope, "boot": boot,
    }


def _commit_reads(plan: dict, attempted: list) -> bool:
    """Remember the next unattempted candidate. Returns whether any remain."""
    global _resume
    sequence = plan["sequence"]
    scope = plan["scope"]
    if not isinstance(_resume, dict):
        _resume = {}
    else:
        _resume.pop(scope, None)
    attempted_ids = set(attempted)
    if not sequence or all(_stamp(info) in attempted_ids for info in sequence):
        return False
    start = 0 if plan["hand"] is None else plan["cont_start"]
    pending = None
    progressed = False
    for info in sequence[start:]:
        if _stamp(info) in attempted_ids:
            progressed = True
            continue
        pending = _stamp(info)
        break
    if pending is None and not progressed:
        pending = plan["hand"]
    elif pending is None:
        for info in sequence:
            if _stamp(info) not in attempted_ids:
                pending = _stamp(info)
                break
    newest = plan["newest"]
    if newest is None:
        if sequence and _stamp(sequence[0]) in attempted_ids:
            newest = _stamp(sequence[0])
    else:
        arrivals = [info for info in sequence if _stamp(info) > newest]
        attempted_arrivals = [info for info in arrivals if _stamp(info) in attempted_ids]
        if attempted_arrivals:
            newest = max(_stamp(info) for info in attempted_arrivals)
    if pending is None:
        return True
    _resume[scope] = {"boot": plan["boot"], "hand": pending, "newest": newest}
    while len(_resume) > _RESUME_SCOPES:
        _resume.pop(next(iter(_resume)))
    return True


def _native_inputs():
    lib, libc = _lib()
    capacity = _argmax(libc)
    if capacity is None:
        raise OSError("argmax is unavailable")
    listed, list_complete = _list_pids(lib)
    if listed is None:
        raise OSError("pid list is unavailable")
    return listed, list_complete, capacity, lib, libc


def _native_scan(context_env: str, uids=(), origins=()):
    if not abi_supported():
        return {}, {}, set(), False
    valid = {uid for uid in uids if type(uid) is int}
    if not valid:
        return {}, {}, set(), False
    try:
        listed, list_complete, capacity, lib, libc = _native_inputs()
    except OSError:
        return {}, {}, set(), False
    from factory import platform_identity
    boot = platform_identity.boot_id()
    if not boot:
        return {}, {}, set(), False
    complete = list_complete
    if len(listed) > SCAN_IDENTITY_LIMIT:
        complete = False
        listed = listed[:SCAN_IDENTITY_LIMIT]
    infos = []
    deadline = time.monotonic() + SCAN_DEADLINE_SECONDS
    for pid in listed:
        if time.monotonic() > deadline:
            complete = False
            break
        try:
            infos.append(_bsdinfo(pid, lib))
        except ProcessLookupError:
            continue
        except OSError:
            complete = False
    relevant = [info for info in infos if info["uid"] in valid and info["status"] != SZOMB]
    known = _uid_starts(origins, boot)
    priority = []
    deferred = []
    for info in relevant:
        if _priority(info, known):
            priority.append(info)
        else:
            deferred.append(info)
    priority.sort(key=_stamp, reverse=True)
    deferred.sort(key=_stamp, reverse=True)
    plan = _plan_reads(priority, deferred, _scope_key(origins, boot), boot)
    processes = {info["pid"]: _public(info, boot) for info in relevant}
    contexts = {}
    unknown: set[int] = set()
    attempted = []
    for info in plan["chosen"]:
        if time.monotonic() > deadline:
            complete = False
            break
        attempted.append(_stamp(info))
        pid = info["pid"]
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
            again = _bsdinfo(pid, lib)
        except (OSError, ProcessLookupError):
            processes.pop(pid, None)
            complete = False
            continue
        if not _same_start(info, again):
            processes.pop(pid, None)
            complete = False
            continue
        if again["status"] == SZOMB:
            processes.pop(pid, None)
            continue
        processes[pid] = _public(again, boot)
        contexts[pid] = parsed
    if _commit_reads(plan, attempted):
        complete = False
    return processes, contexts, unknown, complete
