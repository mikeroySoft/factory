"""Native macOS identity probes shared by lifecycle and passive runtime readers.

Linux keeps its existing procfs readers. Probe failures are not evidence of death.
The guarded Darwin path never spawns ps, lsof, sysctl, or ioreg. Lock-holder
attribution has no non-spawning source here, so it is unknown unless a test
installs a backend that supplies holders.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import uuid

from factory import darwin_observer


def darwin() -> bool:
    return sys.platform == "darwin"


def _sysctl_string(name: str) -> str | None:
    """Non-spawning sysctlbyname string. None on any failure, including a missing libc."""
    if not darwin():
        return None
    try:
        libname = ctypes.util.find_library("System") or ctypes.util.find_library("c")
        if not libname:
            return None
        libc = ctypes.CDLL(libname, use_errno=True)
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
    backend = darwin_observer.installed()
    if backend is not None:
        return backend.boot_id()
    value = _sysctl_string("kern.bootsessionuuid")
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _cf_string(cf, text: bytes):
    """Owned CFString, or None. Caller releases only a non-NULL result."""
    k_utf8 = 0x08000100  # kCFStringEncodingUTF8
    created = cf.CFStringCreateWithCString(None, text, k_utf8)
    return created or None


def _cf_utf8(cf, value) -> str | None:
    k_utf8 = 0x08000100
    if not value:
        return None
    if cf.CFGetTypeID(value) != cf.CFStringGetTypeID():
        return None
    pointer = cf.CFStringGetCStringPtr(value, k_utf8)
    if pointer:
        return pointer.decode("utf-8", errors="strict").strip() or None
    buf = ctypes.create_string_buffer(128)
    if not cf.CFStringGetCString(value, buf, 128, k_utf8):
        return None
    return buf.value.decode("utf-8", errors="strict").strip() or None


def _iokit_host_id() -> str | None:
    """IOPlatformUUID via IOKit. NULL CoreFoundation objects are never released or queried."""
    cf_name = ctypes.util.find_library("CoreFoundation")
    io_name = ctypes.util.find_library("IOKit")
    if not cf_name or not io_name:
        return None
    cf = ctypes.CDLL(cf_name)
    io = ctypes.CDLL(io_name)
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFStringGetTypeID.restype = ctypes.c_ulong
    cf.CFStringGetCStringPtr.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    cf.CFStringGetCStringPtr.restype = ctypes.c_char_p
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    io.IOServiceMatching.argtypes = [ctypes.c_char_p]
    io.IOServiceMatching.restype = ctypes.c_void_p
    io.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    io.IOServiceGetMatchingService.restype = ctypes.c_uint
    io.IOObjectRelease.argtypes = [ctypes.c_uint]
    io.IORegistryEntryCreateCFProperty.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    io.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
    matching = io.IOServiceMatching(b"IOPlatformExpertDevice")
    if not matching:
        return None
    service = io.IOServiceGetMatchingService(0, matching)
    if not service:
        return None
    key = None
    prop = None
    try:
        key = _cf_string(cf, b"IOPlatformUUID")
        if not key:
            return None
        prop = io.IORegistryEntryCreateCFProperty(service, key, None, 0)
        if not prop:
            return None
        text = _cf_utf8(cf, prop)
        if not text:
            return None
        machine = str(uuid.UUID(text)).upper()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:" + machine))
    finally:
        if prop:
            cf.CFRelease(prop)
        if key:
            cf.CFRelease(key)
        io.IOObjectRelease(service)


def host_id() -> str | None:
    backend = darwin_observer.installed()
    if backend is not None:
        return backend.host_id()
    if not darwin():
        return None
    try:
        return _iokit_host_id()
    except Exception:
        return None


def process(pid: int) -> dict:
    backend = darwin_observer.installed()
    if backend is not None:
        return backend.process(pid)
    return darwin_observer.native_process(pid, boot_id())


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
        # Absence is meaningful only when this observer itself is visible.
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
    """Holders only when an installed backend supplies them. Native attribution is unavailable."""
    backend = darwin_observer.installed()
    if backend is None:
        return None
    try:
        return backend.lock_holders(lock)
    except (OSError, ValueError, KeyError, TypeError):
        return None
