"""Pre-claim storage admission and conservative cleanup of owned temp roots."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from factory import lifecycle

MIN_FREE_BYTES = 1024**3
MIN_FREE_INODES = 10_000
OWNER = ".factory-owner.json"


def _users(root: Path, owner: dict) -> bool:
    """Unknown process visibility counts as use; never infer absence from age."""
    if not Path("/proc/self/environ").exists() or type(owner.get("start_ticks")) is not int:
        return True
    needle = os.fsencode(root)
    try:
        for process in Path("/proc").iterdir():
            if not process.name.isdecimal() or int(process.name) == os.getpid():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                text = (process / "stat").read_text()
                fields = text[text.rfind(")") + 2:].split()
                # A process older than this owner cannot be its surviving child.
                # In particular, unrelated non-dumpable user daemons are not blockers.
                if int(fields[19]) < owner["start_ticks"]:
                    continue
                if fields[0] in {"Z", "X"}:
                    continue
                if needle in (process / "environ").read_bytes():
                    return True
                for link in [process / "cwd", *(process / "fd").iterdir()]:
                    try:
                        target = os.fsencode(os.readlink(link))
                    except FileNotFoundError:
                        continue
                    if target == needle or target.startswith(needle + b"/"):
                        return True
            except (FileNotFoundError, ProcessLookupError):
                continue  # Process or descriptor exited during observation.
    except (OSError, ValueError, IndexError):
        return True
    return False


def _remove(root: Path) -> None:
    # Reviewer snapshots have read-only directories; unlinking files needs parent write.
    for directory, _, _ in os.walk(root):
        path = Path(directory)
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    shutil.rmtree(root)


def reap() -> None:
    """Only this user's marked roots with dead owners and no visible users qualify."""
    for root in Path(tempfile.gettempdir()).glob("factory-runner-*"):
        try:
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                continue
            fd = os.open(root / OWNER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as owner:
                info = os.fstat(owner.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    continue
                try:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                identity = json.load(owner)
                if lifecycle._process_state(identity) != "dead" or _users(root, identity):
                    continue
                _remove(root)
        except (OSError, ValueError, TypeError):
            continue  # Malformed ownership or incomplete evidence is never deletion authority.


@contextmanager
def temp_root(kind: str):
    root = Path(tempfile.mkdtemp(prefix=f"factory-runner-{kind}-")).resolve()
    identity = lifecycle._identity(os.getpid())
    try:
        with (root / OWNER).open("x") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            json.dump(identity, owner)
            owner.flush()
            workspace = root / "tmp"
            workspace.mkdir()
            yield workspace
    finally:
        # Normal synchronous callers reap their children before leaving this context.
        # On Linux, also retain an unexpectedly surviving child for the next preflight.
        if root.exists() and (not Path("/proc/self/environ").exists() or not _users(root, identity)):
            _remove(root)


def preflight(repository: Path) -> str | None:
    reap()
    for path in {repository, Path(tempfile.gettempdir())}:
        try:
            space = os.statvfs(path)
        except OSError as exc:
            return f"cannot inspect runner storage at {path}: {exc}"
        free_bytes = space.f_bavail * space.f_frsize
        if free_bytes < MIN_FREE_BYTES:
            return f"{path}: free bytes {free_bytes} < {MIN_FREE_BYTES}"
        # Filesystems with no inode accounting report zero total inodes.
        if space.f_files and space.f_favail < MIN_FREE_INODES:
            return f"{path}: free inodes {space.f_favail} < {MIN_FREE_INODES}"
    return None
