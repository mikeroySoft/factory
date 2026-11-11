"""Host storage admission and crash-recoverable runner scratch directories."""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path

MIN_FREE_BYTES = 1024**3
MIN_FREE_INODES = 10_000


def _within(value: str, root: Path) -> bool:
    return value == str(root) or value.startswith(str(root) + os.sep)


def _users(root: Path) -> bool:
    """Only confirmed live process use protects an unlocked root, never unknowns."""
    root = root.resolve()
    if sys.platform != "linux":
        # macOS has no /proc. lsof covers cwd/open files; ps covers inherited
        # scratch paths even when the child has changed cwd and closed its files.
        with suppress(OSError, subprocess.TimeoutExpired):
            result = subprocess.run(
                ["lsof", "-t", "+D", str(root)], capture_output=True, text=True, timeout=10, check=False,
            )
            if any(pid.isdecimal() and int(pid) != os.getpid() for pid in result.stdout.split()):
                return True
        with suppress(OSError, subprocess.TimeoutExpired):
            result = subprocess.run(
                ["ps", "axeww", "-o", "pid=,command="], capture_output=True, text=True, timeout=10, check=False,
            )
            for line in result.stdout.splitlines():
                parts = line.split(None, 1)
                if (len(parts) == 2 and parts[0].isdecimal() and int(parts[0]) != os.getpid()
                        and any(f"{key}={root}" in parts[1] for key in ("TMPDIR", "GH_CONFIG_DIR"))):
                    return True
        return False
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return False
    for process in processes:
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            stat = (process / "stat").read_bytes()
            if stat[stat.rfind(b")") + 2:].split()[0] in {b"Z", b"X"}:
                continue
        except (OSError, IndexError):
            continue
        with suppress(OSError):
            if _within(os.readlink(process / "cwd"), root):
                return True
        with suppress(OSError):
            for item in (process / "environ").read_bytes().split(b"\0"):
                key, _, value = item.partition(b"=")
                if key in {b"TMPDIR", b"GH_CONFIG_DIR"} and _within(os.fsdecode(value), root):
                    return True
        with suppress(OSError):
            for descriptor in (process / "fd").iterdir():
                with suppress(OSError):
                    if _within(os.readlink(descriptor), root):
                        return True
    return False


def _remove(root: Path) -> None:
    # Crashed reviewers can leave a read-only snapshot. Only directory write
    # permission is needed to unlink files; never chmod through symlinks.
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink() and path.is_dir():
            path.chmod(path.stat().st_mode | 0o700)
    shutil.rmtree(root)


def reap() -> None:
    for root in Path(tempfile.gettempdir()).glob("factory-runner-*"):
        try:
            info = root.lstat()
            if root.is_symlink() or not root.is_dir() or info.st_uid != os.geteuid():
                continue
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if not _users(root):
                    _remove(root)
            finally:
                os.close(descriptor)
        except OSError:
            continue  # Held owner, concurrent reap, or unavailable directory.


@contextmanager
def temp_root(kind: str):
    while True:
        root = Path(tempfile.mkdtemp(prefix=f"factory-runner-{kind}-")).resolve()
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue  # A preflight won the create/lock race; publish a fresh root.
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if root.exists():
            break
        os.close(descriptor)
    try:
        yield str(root)
    finally:
        try:
            if not _users(root):
                _remove(root)
        finally:
            os.close(descriptor)


def preflight(repository: Path) -> dict | None:
    reap()
    for path in (repository, Path(tempfile.gettempdir())):
        try:
            stats = os.statvfs(path)
        except OSError as exc:
            return {"reason": "runner_storage_unavailable", "path": str(path), "detail": str(exc)}
        available = {"bytes": stats.f_bavail * stats.f_frsize, "inodes": stats.f_favail}
        for resource, minimum in (("bytes", MIN_FREE_BYTES), ("inodes", MIN_FREE_INODES)):
            if resource > "inodes" and stats.f_files == 0:
                continue  # Filesystems without an inode limit report zero total inodes.
            if available[resource] < minimum:
                return {"reason": "runner_storage_low", "path": str(path), "resource": resource,
                        "available": available[resource], "minimum": minimum}
    return None
