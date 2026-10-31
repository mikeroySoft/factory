"""Runner filesystem preflight and run-scoped temporary directories."""
from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from factory import lifecycle

EX_TEMPFAIL = 75
RUN_PREFIXES = ("factory-worker-", "factory-gate-")
OWNER_FILE = ".factory-owner.json"


def cleanup_abandoned() -> None:
    """Reclaim only roots whose owner and visible consumers have all stopped."""
    root = Path(tempfile.gettempdir())
    for prefix in RUN_PREFIXES:
        try:
            directories = list(root.glob(prefix + "*"))
        except OSError:
            continue
        for directory in directories:
            try:
                owner = json.loads((directory / OWNER_FILE).read_text())
            except (OSError, ValueError, TypeError):
                continue
            if lifecycle._process_state(owner) == "dead" and _consumers_stopped(directory, owner):
                shutil.rmtree(directory, ignore_errors=True)


def _consumers_stopped(directory: Path, owner: dict) -> bool:
    if owner.get("boot_id") != lifecycle._boot_id():
        return True  # A prior boot cannot have surviving consumers.
    if type(owner.get("uid")) is not int:
        return False
    execution_id = owner.get("execution_id")
    sessions = set()
    if execution_id:
        try:
            rows = lifecycle.read_events(Path(owner["journal"]))
            if not any(row.get("execution_id") == execution_id for row in rows):
                return False
            ids = {execution_id}
            for row in rows:
                if row.get("parent_execution_id") in ids:
                    ids.add(row.get("execution_id"))
                if row.get("execution_id") in ids and row.get("kind") == "child_start":
                    child = row.get("child_process")
                    if lifecycle._process_state(child) != "dead":
                        return False
                    sessions.add(child["pid"])
        except (OSError, KeyError, TypeError):
            return False
    # Include the Popen -> child_start race, reparented grandchildren, and checks
    # that intentionally do not inherit lifecycle context. Never infer absence
    # from an unreadable /proc entry.
    try:
        for process in Path("/proc").iterdir():
            if not process.name.isdecimal():
                continue
            try:
                if process.stat().st_uid != owner.get("uid"):
                    continue
                text = (process / "stat").read_text()
                fields = text[text.rfind(")") + 2:].split()
                # Older processes cannot have inherited this later run's TMPDIR.
                # In particular, unrelated sandboxed processes may hide environ.
                if fields[0] in {"Z", "X"} or int(fields[19]) < owner["start_ticks"]:
                    continue
                if int(fields[2]) in sessions or int(fields[3]) in sessions:
                    return False  # A check/worker's session can outlive its leader.
                environment = (process / "environ").read_bytes().split(b"\0")
                if any(key + b"=" + os.fsencode(directory) in environment
                       for key in (b"TMPDIR", b"TMP", b"TEMP")):
                    return False
            except FileNotFoundError:
                continue
            except (OSError, ValueError, IndexError):
                return False
    except OSError:
        return False
    return True




class RunnerStorageLow(RuntimeError):
    def __init__(self, phase: str, status: dict) -> None:
        self.phase = phase
        self.status = status
        super().__init__(f"runner_storage_low before {phase}: {detail(status)}")


def _existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def status(cfg) -> dict:
    """Free bytes/inodes for the temp and worktree filesystems, deduplicated by device."""
    thresholds = {
        "free_bytes": int(cfg.runner_min_free_gb * 1024**3),
        "free_inodes": cfg.runner_min_free_inodes,
    }
    grouped: dict[int | str, dict] = {}
    for purpose, requested in (("temp", Path(tempfile.gettempdir())), ("worktree", cfg.factory)):
        path = _existing(requested)
        try:
            device: int | str = path.stat().st_dev
            entry = grouped.get(device)
            if entry is None:
                info = os.statvfs(path)
                entry = {
                    "device": device,
                    "paths": [],
                    "purposes": [],
                    "free_bytes": info.f_bavail * info.f_frsize,
                    "free_inodes": info.f_favail if info.f_files else None,
                }
                grouped[device] = entry
            if str(path) not in entry["paths"]:
                entry["paths"].append(str(path))
            entry["purposes"].append(purpose)
        except OSError as exc:
            device = f"unavailable:{purpose}"
            grouped[device] = {
                "device": None,
                "paths": [str(path)],
                "purposes": [purpose],
                "error": f"{type(exc).__name__}: {exc}",
            }
    filesystems = []
    for entry in grouped.values():
        low = []
        if "error" in entry:
            low.append("unavailable")
        else:
            if entry["free_bytes"] < thresholds["free_bytes"]:
                low.append("space")
            if entry["free_inodes"] is not None and entry["free_inodes"] < thresholds["free_inodes"]:
                low.append("inodes")
        filesystems.append({**entry, "low": low})
    return {"ok": not any(row["low"] for row in filesystems), "thresholds": thresholds,
            "filesystems": filesystems}


def detail(value: dict) -> str:
    parts = []
    for row in value["filesystems"]:
        location = ", ".join(row["purposes"])
        if row["low"]:
            if "error" in row:
                observed = row["error"]
            else:
                inode_detail = (
                    f"{row['free_inodes']} inodes free"
                    if row["free_inodes"] is not None else "inode availability not reported"
                )
                observed = f"{row['free_bytes']} bytes and {inode_detail}"
            parts.append(f"{location} ({observed}; low: {', '.join(row['low'])})")
    thresholds = value["thresholds"]
    return "; ".join(parts) or (
        f"at least {thresholds['free_bytes']} bytes and {thresholds['free_inodes']} inodes free"
    )


def record_low(cfg, phase: str, value: dict) -> None:
    execution = lifecycle.current()
    if execution is not None:
        execution.defer_storage()
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "runner_storage_low",
        "phase": phase,
        "thresholds": value["thresholds"],
        "filesystems": value["filesystems"],
    }
    try:
        lifecycle.append(cfg.factory / "events.jsonl", row)
    except OSError as exc:
        if exc.errno not in {errno.ENOSPC, errno.EDQUOT}:
            raise
        print(f"runner_storage_low before {phase}: journal unavailable (storage exhausted)",
              file=sys.stderr)


def require(cfg, phase: str) -> dict:
    cleanup_abandoned()
    value = status(cfg)
    if not value["ok"]:
        record_low(cfg, phase, value)
        raise RunnerStorageLow(phase, value)
    return value


@contextmanager
def temporary_environment(prefix: str, cfg=None, phase: str = "run"):
    """Give one run an isolated temp root recoverable after an owner crash."""
    directory = None
    try:
        directory = tempfile.TemporaryDirectory(prefix=prefix)
        owner = lifecycle._identity(os.getpid())
        execution = lifecycle.current()
        if execution is not None:
            owner.update(execution_id=execution.execution_id, journal=str(execution.path))
        Path(directory.name, OWNER_FILE).write_text(json.dumps(owner))
    except OSError as exc:
        if directory is not None:
            directory.cleanup()
        if exc.errno not in {errno.ENOSPC, errno.EDQUOT}:
            raise
        value = {
            "ok": False, "thresholds": {"free_bytes": 0, "free_inodes": 0},
            "filesystems": [{
                "paths": [tempfile.gettempdir()], "purposes": ["temp"],
                "low": ["space" if exc.errno == errno.ENOSPC else "quota"],
                "error": f"{type(exc).__name__}: {exc}",
            }],
        }
        if cfg is not None:
            record_low(cfg, phase, value)
        elif lifecycle.current() is not None:
            lifecycle.current().defer_storage()
        raise RunnerStorageLow(phase, value) from exc
    with directory:
        directory = directory.name
        environment = dict.fromkeys(("TMPDIR", "TMP", "TEMP"), directory)
        previous = {key: os.environ.get(key) for key in environment}
        previous_tempdir = tempfile.tempdir
        os.environ.update(environment)
        tempfile.tempdir = directory
        try:
            yield environment
        finally:
            tempfile.tempdir = previous_tempdir
            for key, old in previous.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
