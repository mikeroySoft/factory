"""Native macOS identity probes shared by lifecycle and passive runtime readers.

Linux keeps its existing procfs readers. Probe failures are not evidence of death.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
import ctypes
import ctypes.util


def darwin() -> bool:
    return sys.platform == "darwin"


def _probe(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2, check=False,
                              env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise OSError("identity probe unavailable") from exc


def _sysctl_string(name: str) -> str | None:
    """Non-spawning Darwin sysctlbyname string lookup; None on any failure."""
    if not darwin():
        return None
    try:
        libname = ctypes.util.find_library("System") or ctypes.util.find_library("c")
        if not libname:
            return None
        libc = ctypes.CDLL(libname)
        fn = getattr(libc, "sysctlbyname", None)
        if fn is None:
            return None
        fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_size_t),
                       ctypes.c_void_p, ctypes.c_size_t]
        fn.restype = ctypes.c_int
        needed = ctypes.c_size_t(0)
        if fn(name.encode("utf-8"), None, ctypes.byref(needed), None, 0) != 0:
            return None
        if needed.value == 0 or needed.value > 1024:
            return None
        buf = ctypes.create_string_buffer(needed.value)
        if fn(name.encode("utf-8"), buf, ctypes.byref(needed), None, 0) != 0:
            return None
        return buf.raw[:needed.value].rstrip(b"\x00").decode("utf-8", errors="strict").strip() or None
    except Exception:
        return None


def boot_id() -> str | None:
    # Prefer non-spawning sysctl when possible (fixtures may forbid external commands).
    value = _sysctl_string("kern.bootsessionuuid")
    if isinstance(value, str):
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return None
    try:
        result = _probe("/usr/sbin/sysctl", "-n", "kern.bootsessionuuid")
        return str(uuid.UUID(result.stdout.strip())) if result.returncode == 0 else None
    except (OSError, ValueError):
        return None


def host_id() -> str | None:
    # Prefer non-spawning IOKit/CoreFoundation lookup; fall back to ioreg only when needed.
    if darwin():
        try:
            cf_name = ctypes.util.find_library("CoreFoundation")
            io_name = ctypes.util.find_library("IOKit")
            if cf_name and io_name:
                CF = ctypes.CDLL(cf_name)
                IO = ctypes.CDLL(io_name)
                CFStringRef = ctypes.c_void_p
                CFMutableDictionaryRef = ctypes.c_void_p
                io_service_t = ctypes.c_uint
                IOServiceMatching = IO.IOServiceMatching
                IOServiceMatching.argtypes = [ctypes.c_char_p]
                IOServiceMatching.restype = CFMutableDictionaryRef
                IOServiceGetMatchingService = IO.IOServiceGetMatchingService
                IOServiceGetMatchingService.argtypes = [ctypes.c_uint, CFMutableDictionaryRef]
                IOServiceGetMatchingService.restype = io_service_t
                IOObjectRelease = IO.IOObjectRelease
                IOObjectRelease.argtypes = [io_service_t]
                IOObjectRelease.restype = ctypes.c_int
                CFStringCreateWithCString = CF.CFStringCreateWithCString
                CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
                CFStringCreateWithCString.restype = CFStringRef
                IORegistryEntryCreateCFProperty = IO.IORegistryEntryCreateCFProperty
                IORegistryEntryCreateCFProperty.argtypes = [io_service_t, CFStringRef, ctypes.c_void_p, ctypes.c_uint32]
                IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
                CFGetTypeID = CF.CFGetTypeID
                CFGetTypeID.argtypes = [ctypes.c_void_p]
                CFGetTypeID.restype = ctypes.c_ulong
                CFStringGetTypeID = CF.CFStringGetTypeID
                CFStringGetTypeID.argtypes = []
                CFStringGetTypeID.restype = ctypes.c_ulong
                CFStringGetCStringPtr = CF.CFStringGetCStringPtr
                CFStringGetCStringPtr.argtypes = [CFStringRef, ctypes.c_uint32]
                CFStringGetCStringPtr.restype = ctypes.c_char_p
                CFStringGetCString = CF.CFStringGetCString
                CFStringGetCString.argtypes = [CFStringRef, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
                CFStringGetCString.restype = ctypes.c_bool
                CFRelease = CF.CFRelease
                CFRelease.argtypes = [ctypes.c_void_p]
                kUTF8 = 0x08000100  # kCFStringEncodingUTF8
                matching = IOServiceMatching(b"IOPlatformExpertDevice")
                service = IOServiceGetMatchingService(0, matching)
                try:
                    if service:
                        key = CFStringCreateWithCString(ctypes.c_void_p(0), b"IOPlatformUUID", kUTF8)
                        try:
                            cf = IORegistryEntryCreateCFProperty(service, key, ctypes.c_void_p(0), 0)
                        finally:
                            CFRelease(key)
                        if cf:
                            try:
                                if CFGetTypeID(cf) == CFStringGetTypeID():
                                    s = CFStringGetCStringPtr(cf, kUTF8)
                                    if not s:
                                        buf = ctypes.create_string_buffer(128)
                                        if CFStringGetCString(cf, buf, 128, kUTF8):
                                            s = buf.value
                                    if s:
                                        machine = str(uuid.UUID(s.decode("utf-8").strip())).upper()
                                        return str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:" + machine))
                            finally:
                                CFRelease(cf)
                finally:
                    if service:
                        IOObjectRelease(service)
        except Exception:
            pass
    # Fallback: spawn ioreg (kept for compatibility and tests)
    try:
        result = _probe("/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice")
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]+)"', result.stdout)
        if result.returncode != 0 or match is None:
            return None
        machine = str(uuid.UUID(match[1])).upper()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:" + machine))
    except (OSError, ValueError):
        return None


def process(pid: int) -> dict:
    result = _probe("/bin/ps", "-o", "lstart=,ppid=,uid=,stat=", "-p", str(pid))
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        raise ProcessLookupError(pid)
    if result.returncode != 0:
        raise OSError("process probe failed")
    fields = result.stdout.split()
    if len(fields) != 8:
        raise ValueError("invalid process identity")
    # lstart is an absolute start time, not elapsed process age; ps reports seconds.
    start = datetime.strptime(" ".join(fields[:5]), "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    return {"pid": pid, "boot_id": boot_id(), "start_ticks": int(start.timestamp()),
            "pid_namespace": "darwin", "ppid": int(fields[5]), "uid": int(fields[6]),
            "state": fields[7][0]}


def process_state(identity: dict | None) -> str:
    if (not isinstance(identity, dict) or type(identity.get("pid")) is not int or identity["pid"] <= 0
            or type(identity.get("start_ticks")) is not int or identity["start_ticks"] < 0
            or not isinstance(identity.get("boot_id"), str) or not identity["boot_id"]
            or identity.get("pid_namespace") != "darwin"):
        return "unknown"
    boot = boot_id()
    if boot is None:
        return "unknown"
    if boot != identity["boot_id"]:
        return "dead"
    try:
        current = process(identity["pid"])
    except ProcessLookupError:
        # Absence is meaningful only when ps can see this observer.
        try:
            observer = process(os.getpid())
        except (OSError, ValueError, IndexError):
            return "unknown"
        if observer["boot_id"] != boot:
            return "unknown"
        return "dead"
    except (OSError, ValueError, IndexError):
        return "unknown"
    if current["boot_id"] != boot:
        return "unknown"
    if current["start_ticks"] != identity["start_ticks"] or current["state"] in {"Z", "X"}:
        return "dead"
    if identity.get("uid") is not None and current["uid"] != identity["uid"]:
        return "unknown"
    return "alive"


def lock_holders(lock: dict) -> list[int] | None:
    """lsof's lock field identifies owners; merely opening the file is not ownership."""
    try:
        path = Path(lock["path"])
        expected = (lock["device"], lock["inode"])
        before = path.stat()
        if (before.st_dev, before.st_ino) != expected:
            return None
        result = _probe("/usr/sbin/lsof", "-nP", "-Fpfl", "--", str(path))
        after = path.stat()
        if result.returncode != 0 or result.stderr.strip() or (after.st_dev, after.st_ino) != expected:
            return None
        holders = set()
        pid = None
        for field in result.stdout.splitlines():
            if field.startswith("p"):
                pid = int(field[1:])
                if pid <= 0:
                    return None
            elif field in {"lR", "lW"}:
                if pid is None:
                    return None
                holders.add(pid)
        # Some Darwin lsof versions emit blank lock fields even for held flocks.
        return sorted(holders) if holders else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
