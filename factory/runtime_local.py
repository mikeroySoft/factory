"""Bounded, read-only local inputs for the runtime JSON projection.

At most five local commands (git and platform service/lock queries), each limited to
0.5s and 64 KiB of stdout. Configuration reads are limited to 256 KiB per file;
admission observation examines at most 1024 directory entries and 256 KiB of
kernel locks on Linux, or lsof lock fields on macOS.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from . import config, launchd
from .config import Config, ConfigError

COMMAND_TIMEOUT = 0.5
COMMAND_BYTE_LIMIT = 64 * 1024
CONFIG_BYTE_LIMIT = 256 * 1024
LOCK_BYTE_LIMIT = 256 * 1024
LOCK_ENTRY_LIMIT = 1024


class _Unavailable(Exception):
    """Only fixed diagnostic codes cross the observation boundary."""


def _read(path: Path, limit: int, *, kernel: bool = False) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb", buffering=0) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise _Unavailable("unsupported_file")
            if info.st_size > limit:
                raise _Unavailable("byte_limit")
            payload = bytearray()
            while len(payload) < limit:
                chunk = handle.read(limit - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
            if (kernel and len(payload) == limit) or os.fstat(handle.fileno()).st_size > limit:
                raise _Unavailable("byte_limit")
            return bytes(payload)
    except FileNotFoundError:
        raise _Unavailable("missing") from None
    except OSError:
        raise _Unavailable("unreadable") from None


def _command(operation: str, *, root: Path | None = None, unit: str = "") -> str:
    """Only the fixed local queries below may be executed; stderr is discarded."""
    if operation == "root":
        argv = ["git", "--no-optional-locks", "rev-parse", "--path-format=absolute", "--git-common-dir"]
    elif operation == "slug":
        argv = ["git", "--no-optional-locks", "remote", "get-url", "origin"]
    elif operation == "launchd":
        try:
            argv = ["launchctl", "print", launchd.target(unit)]
        except ValueError:
            raise _Unavailable("invalid_unit") from None
    elif operation == "locks":
        if root is None or not root.is_absolute() or root.name != "locks":
            raise _Unavailable("invalid_locks")
        argv = ["lsof", "-nP", "-F0fln", "+d", str(root)]
    elif operation in {"service", "timer", "schedule"}:
        if not re.fullmatch(r"factory-[A-Za-z0-9_.-]+", unit):
            raise _Unavailable("invalid_unit")
        argv = ["systemctl", "--user", "--no-pager"]
        if operation == "schedule":
            argv += ["list-timers", "--all", "--output=json", "--", f"{unit}.timer"]
        else:
            argv += ["show", "--property=LoadState,ActiveState", "--", f"{unit}.{operation}"]
    else:
        raise _Unavailable("unsupported_command")

    env = dict(os.environ)
    env.update(LC_ALL="C", TZ="UTC", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               SYSTEMD_PAGER="cat", SYSTEMD_COLORS="0")
    # Never let an inherited TCP D-Bus address turn a local probe into networking.
    runtime = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if not runtime.is_absolute():
        runtime = Path(f"/run/user/{os.getuid()}")
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + quote(str(runtime / "bus"), safe="/")
    deadline = time.monotonic() + COMMAND_TIMEOUT
    proc = None
    try:
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE if operation == "locks" else subprocess.DEVNULL)
        data, diagnostics = bytearray(), bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, data)
            if proc.stderr is not None:
                selector.register(proc.stderr, selectors.EVENT_READ, diagnostics)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                events = selector.select(max(0, remaining))
                if remaining <= 0 or not events:
                    raise _Unavailable("timeout")
                for key, _ in events:
                    size = len(data) + len(diagnostics)
                    chunk = os.read(key.fd, min(8192, COMMAND_BYTE_LIMIT - size))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk)
                    if size + len(chunk) == COMMAND_BYTE_LIMIT:
                        raise _Unavailable("output_limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _Unavailable("timeout")
        status = proc.wait(timeout=remaining)
        if diagnostics or (status and not (operation == "locks" and status == 1)):
            raise _Unavailable("command_failed")
        return data.decode("utf-8")
    except subprocess.TimeoutExpired:
        raise _Unavailable("timeout") from None
    except (OSError, UnicodeError):
        raise _Unavailable("command_unavailable") from None
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=COMMAND_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                pass  # The observation already failed; cleanup must not expose raw diagnostics.
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(_read(path, CONFIG_BYTE_LIMIT).decode("utf-8"))
    except _Unavailable as exc:
        if str(exc) == "missing":
            return {}
        raise


def load(start: Path | None = None) -> Config:
    """Use the ordinary host/repo layering without its unbounded IO helpers."""
    try:
        common = Path(_command("root", root=start).strip())
        if not common.is_absolute():
            raise ValueError
        root = common.parent
        raw = _toml(root / config.CONFIG_NAME)
        slug = raw.get("repo", {}).get("slug")
        if not slug:
            url = _command("slug", root=root).strip()
            if "github.com" not in url:
                raise ValueError
            slug = url.rsplit("github.com", 1)[-1].strip(":/").removesuffix(".git")
        if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
            raise ValueError
        host = _toml(config.host_config_path())
        layered = config.merge(config.host_filter(host.get("defaults", {})),
                               config.host_filter(host.get("repo", {}).get(slug, {})))
        raw = config.merge(layered, raw)
        cfg = Config(root=root, repo=slug)
        cfg.max_active = raw.get("dispatch", {}).get("max_active", cfg.max_active)
        if type(cfg.max_active) is not int or cfg.max_active < 0:
            raise ValueError
        gate = raw.get("gate", {})
        if not isinstance(gate, dict):
            raise ValueError  # noqa: TRY004 — invalid configuration shares the fallback path
        if "lock" in gate:
            lock = gate["lock"]
            if not isinstance(lock, str) or not lock or "\x00" in lock:
                raise ValueError
            cfg.lock = Path(lock)
        return cfg
    except (_Unavailable, ConfigError, OSError, ValueError, TypeError, KeyError, AttributeError,
            RecursionError, OverflowError):
        raise ConfigError("runtime configuration unavailable or invalid") from None


def _capacity(cfg: Config) -> int:
    """Count actual numeric ticket FLOCKs, never lifecycle stage counts."""
    inodes = set()
    paths = {}
    try:
        with os.scandir(cfg.factory / "locks") as entries:
            for index, entry in enumerate(entries):
                if index + 1 >= LOCK_ENTRY_LIMIT:
                    raise _Unavailable("entry_limit")
                if not re.fullmatch(r"[0-9]+\.lock", entry.name):
                    continue
                info = entry.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise _Unavailable("unsupported_file")
                inodes.add((os.major(info.st_dev), os.minor(info.st_dev), info.st_ino))
                if sys.platform == "darwin":
                    paths[str(Path(entry.path).resolve())] = info.st_ino
    except FileNotFoundError:
        # A disappearing entry is not evidence that every claim disappeared.
        if inodes:
            raise _Unavailable("changed") from None
        try:
            (cfg.factory / "locks").stat()
        except FileNotFoundError:
            return 0
        except OSError:
            pass
        raise _Unavailable("changed") from None
    except OSError:
        raise _Unavailable("unreadable") from None
    if not inodes:
        return 0
    if sys.platform == "darwin":
        text = _command("locks", root=cfg.factory / "locks")
        held = set()
        locked = False
        for field in text.split("\0"):
            field = field.lstrip("\n")
            if field.startswith("f"):
                locked = False
            elif field.startswith("l"):
                status = field[1:]
                if status not in {" ", "R", "W", "r", "w", "u"}:
                    raise _Unavailable("invalid_kernel_locks")
                locked = status in {"R", "W"}
                if status in {"r", "w", "u"}:
                    raise _Unavailable("invalid_kernel_locks")
            elif field.startswith("n") and field[1:] in paths:
                if not locked:
                    # Darwin lsof can expose the descriptor without exposing its flock.
                    raise _Unavailable("lock_state_unavailable")
                held.add(paths[field[1:]])
        return len(held)

    held = set()
    try:
        rows = _read(Path("/proc/locks"), LOCK_BYTE_LIMIT, kernel=True).decode("ascii").splitlines()
        for row in rows:
            fields = row.split()
            if len(fields) < 2:
                raise ValueError
            if fields[1] != "FLOCK":
                continue  # Blocked rows have -> here, and POSIX locks are not admission claims.
            if len(fields) < 8 or fields[2] != "ADVISORY" or fields[3] not in {"READ", "WRITE"}:
                raise ValueError
            major, minor, inode = fields[5].split(":")
            key = (int(major, 16), int(minor, 16), int(inode))
            if key in inodes:
                held.add(key)
    except (ValueError, UnicodeError):
        raise _Unavailable("invalid_kernel_locks") from None
    return len(held)


def dispatcher(cfg: Config, executions: list[dict], history: dict) -> tuple[dict, list[dict]]:
    """Observe units and admission slots without running the legacy dashboard."""
    errors = []

    def error(source: str, scope: str, code: str) -> None:
        errors.append({"source": source, "scope": scope, "code": code})

    def active(kind: str) -> bool | None:
        try:
            text = _command(kind, unit=cfg.unit)
            props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            if props.get("LoadState") != "loaded":
                raise _Unavailable("unit_unavailable")
            state = props.get("ActiveState")
            if state == "active":
                return True
            if state in {"activating", "reloading", "refreshing", "deactivating"}:
                raise _Unavailable("transitioning")
            if state == "inactive":
                return False
            if state == "failed":
                error("systemctl", kind, "unit_failed")
                return False
            raise _Unavailable("invalid_state")
        except _Unavailable as exc:
            error("systemctl", kind, str(exc))
            return None

    extra = {}
    following = None
    if sys.platform == "darwin":
        service_active = timer_active = None
        extra = {"next_at_code": "not_applicable", "last_exit_status": None, "run_interval": None}
        try:
            props = launchd.parse(_command("launchd", unit=cfg.unit))
            interval = launchd.number(props, "run interval")
            extra.update(last_exit_status=launchd.number(props, "last exit code"), run_interval=interval)
            timer_active = interval is not None and interval > 0
            state = props["state"]
            if state == "running":
                service_active = True
            elif state in {"not running", "exited"}:
                service_active = False
            else:
                raise _Unavailable("transitioning" if state in {"spawn scheduled", "waiting"} else "invalid_state")
        except (_Unavailable, ValueError) as exc:
            error("launchctl", "service", str(exc))
    else:
        service_active, timer_active = active("service"), active("timer")
        try:
            rows = json.loads(_command("schedule", unit=cfg.unit))
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError
            matches = [row for row in rows if row.get("unit") == f"{cfg.unit}.timer"]
            if len(matches) > 1 or (not matches and timer_active is True):
                raise ValueError
            if matches:
                value = matches[0].get("next")
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError
                if value not in (None, 0, 2**64 - 1):
                    following = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)
        except _Unavailable as exc:
            error("systemctl", "schedule", str(exc))
        except (ValueError, TypeError, OverflowError, OSError, RecursionError):
            error("systemctl", "schedule", "invalid_schedule")

    active_count = None
    try:
        active_count = _capacity(cfg)
    except _Unavailable as exc:
        error("admission", "repository", str(exc))
    observed = datetime.now(UTC)
    next_at = (following.isoformat(timespec="microseconds").replace("+00:00", "Z")
               if timer_active is True and following is not None and following > observed else None)
    run_ids = sorted({execution["dispatcher_run_id"] for execution in executions
                      if execution.get("stage") == "dispatcher" and execution.get("state") == "active"
                      and isinstance(execution.get("dispatcher_run_id"), str)
                      and execution["dispatcher_run_id"]})
    observation = "partial" if errors else "fresh"
    if service_active is None and timer_active is None and active_count is None:
        observation = "unavailable"
    return {
        "service_active": service_active, "timer_active": timer_active, "next_at": next_at,
        **extra,
        "paused": None,
        "observed_at": observed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "observation": observation,
        "capacity": {"configured": cfg.max_active, "active": active_count, "complete": active_count is not None},
        "run_ids": run_ids,
        "latest_transition": None,  # The caller derives this from the retained supported events.
    }, errors
