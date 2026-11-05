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


def darwin() -> bool:
    return sys.platform == "darwin"


def _probe(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2, check=False,
                              env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise OSError("identity probe unavailable") from exc


def boot_id() -> str | None:
    try:
        result = _probe("/usr/sbin/sysctl", "-n", "kern.bootsessionuuid")
        return str(uuid.UUID(result.stdout.strip())) if result.returncode == 0 else None
    except (OSError, ValueError):
        return None


def host_id() -> str | None:
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
