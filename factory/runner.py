"""Runner storage admission and conservative recovery of owned scratch roots."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from factory import lifecycle

MIN_FREE_BYTES = 1024**3
MIN_FREE_INODES = 10_000
OWNER = ".factory-owner.json"
TEMP_ENV = "FACTORY_TEMP_ROOT"


@contextmanager
def temporary_directory(prefix: str):
    # Do not use TemporaryDirectory's unconditional finalizer: a surviving child
    # may still be using this directory after an interrupted command returns.
    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    (root / OWNER).write_text(json.dumps(lifecycle._identity(os.getpid())))
    try:
        yield str(root)
    finally:
        if not _has_users(root, exclude=os.getpid()):
            shutil.rmtree(root, ignore_errors=True)


def _has_users(root: Path, *, exclude: int | None = None) -> bool:
    """Retain owned roots when descendant visibility is incomplete.

    Inspect inherited environments as well as cwd/open files: children can be
    reparented, and may not have opened their next scratch file yet.
    """
    try:
        marker = root / OWNER
        owner = json.loads(marker.read_text()) if marker.exists() else None
    except (OSError, ValueError):
        return True
    if sys.platform != "linux" or owner is None:
        try:
            result = subprocess.run(
                ["ps", "axeww", "-o", "pid=,stat=,command="],
                capture_output=True, text=True, check=True,
            )
            for line in result.stdout.splitlines():
                pid, state, command = line.split(None, 2)
                if int(pid) != exclude and not state.startswith("Z") and str(root) in command:
                    return True
            return False
        except (OSError, ValueError, subprocess.SubprocessError):
            return True
    try:
        if type(owner.get("start_ticks")) is not int:
            return True
        boot = lifecycle._boot_id()
        seen = set()
        # Follow newly forked/reparented children across bounded snapshots.
        # Unrelated process churn alone is not evidence that this root is used.
        for _ in range(3):
            processes = {p for p in Path("/proc").iterdir() if p.name.isdigit()} - seen
            if not processes:
                return False
            seen.update(processes)
            for process in processes:
                try:
                    if process.stat().st_uid != os.getuid():
                        continue
                    fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
                    if int(process.name) == exclude or fields[0] in {"Z", "X"}:
                        continue
                    if owner.get("boot_id") == boot and int(fields[19]) < owner["start_ticks"]:
                        continue  # older processes cannot be this owner's descendants
                    if os.fsencode(str(root)) in (process / "environ").read_bytes():
                        return True
                    references = [process / "cwd", *(process / "fd").iterdir()]
                    for reference in references:
                        try:
                            target = Path(os.readlink(reference))
                        except (FileNotFoundError, ProcessLookupError):
                            continue
                        if target == root or root in target.parents:
                            return True
                except (FileNotFoundError, ProcessLookupError):
                    continue  # exited while inspecting
        return False
    except (OSError, ValueError, IndexError):
        return True


def abandoned_temporary_roots():
    cutoff = time.time() - 24 * 60 * 60
    parent = Path(tempfile.gettempdir()).resolve()
    for pattern in ("factory-review-*", "factory-gate-*"):
        for root in parent.glob(pattern):
            try:
                if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
                    continue
                marker = root / OWNER
                if marker.exists():
                    owner = json.loads(marker.read_text())
                    state = lifecycle._process_state(owner)
                    if state == "unknown" and sys.platform != "linux":
                        try:
                            os.kill(owner["pid"], 0)
                        except ProcessLookupError:
                            state = "dead"
                    if state != "dead":
                        continue
                elif root.stat().st_mtime >= cutoff:
                    continue  # legacy unmarked roots retain the existing age floor
                if not _has_users(root):
                    yield root
            except (OSError, ValueError, TypeError, KeyError):
                continue


def storage_low(checkout: Path) -> list[dict]:
    low = []
    for path in (checkout, Path(tempfile.gettempdir())):
        status = os.statvfs(path)
        free_bytes = status.f_bavail * status.f_frsize
        # Filesystems without an inode limit report f_files == 0 (e.g. APFS).
        if free_bytes < MIN_FREE_BYTES or (status.f_files and status.f_favail < MIN_FREE_INODES):
            low.append({"path": str(path), "free_bytes": free_bytes,
                        "free_inodes": status.f_favail})
    return low
