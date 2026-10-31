"""Runner filesystem preflight and run-scoped temporary directories."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from factory import lifecycle

EX_TEMPFAIL = 75
RUN_PREFIXES = ("factory-worker-", "factory-gate-")
OWNER_FILE = ".factory-owner.json"


def cleanup_abandoned() -> None:
    """Remove run roots whose recorded owning process is no longer alive."""
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
            if lifecycle._process_state(owner) == "dead":
                shutil.rmtree(directory, ignore_errors=True)




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
    lifecycle.append(cfg.factory / "events.jsonl", {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "runner_storage_low",
        "phase": phase,
        "thresholds": value["thresholds"],
        "filesystems": value["filesystems"],
    })


def require(cfg, phase: str) -> dict:
    cleanup_abandoned()
    value = status(cfg)
    if not value["ok"]:
        record_low(cfg, phase, value)
        raise RunnerStorageLow(phase, value)
    return value


@contextmanager
def temporary_environment(prefix: str):
    """Give one run an isolated temp root recoverable after an owner crash."""
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        Path(directory, OWNER_FILE).write_text(json.dumps(lifecycle._identity(os.getpid())))
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
