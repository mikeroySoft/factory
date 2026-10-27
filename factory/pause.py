"""Durable per-repository pause state and CLI commands."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config, lifecycle
from .config import Config

STATE_NAME = "pause.json"
LOCK_NAME = "pause.lock"
MAX_BYTES = 16 * 1024
DEFAULT_REASON = "no reason given"
_DURATION = re.compile(r"([1-9][0-9]*)([smhdw])\Z", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _root(value: Config | Path) -> Path:
    return value.root if isinstance(value, Config) else Path(value)


def _factory(value: Config | Path) -> Path:
    return _root(value) / ".factory"


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601 with a timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def parse_until(value: str, *, now: datetime | None = None) -> str:
    """Normalize a compact duration (s/m/h/d/w) or an ISO timestamp to UTC."""
    if match := _DURATION.fullmatch(value):
        start = now or datetime.now(UTC)
        return _format(start + timedelta(seconds=int(match[1]) * _UNITS[match[2].lower()]))
    return _format(_timestamp(value))


def _decode(payload: bytes) -> dict:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid pause state") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"reason", "at", "who", "until"}
        or not isinstance(value["reason"], str)
        or not value["reason"]
        or not isinstance(value["at"], str)
        or not isinstance(value["who"], str)
        or not value["who"]
        or value["until"] is not None and not isinstance(value["until"], str)
    ):
        raise ValueError("invalid pause state")
    _timestamp(value["at"])
    if value["until"] is not None:
        _timestamp(value["until"])
    return value


def read(value: Config | Path) -> dict | None:
    """Read the bounded atomic state file; malformed state fails closed."""
    path = _factory(value) / STATE_NAME
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("invalid pause state")
        payload = bytearray()
        while len(payload) <= MAX_BYTES:
            chunk = os.read(fd, min(4096, MAX_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_BYTES:
            raise ValueError("invalid pause state")
        return _decode(bytes(payload))
    finally:
        os.close(fd)


@contextmanager
def _locked(value: Config | Path) -> Iterator[None]:
    directory = _factory(value)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / LOCK_NAME).open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _save(value: Config | Path, state: dict) -> None:
    directory = _factory(value)
    path = directory / STATE_NAME
    temporary = directory / f".{STATE_NAME}.{os.getpid()}.tmp"
    with temporary.open("w") as handle:
        json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(directory, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove(value: Config | Path) -> None:
    path = _factory(value) / STATE_NAME
    path.unlink(missing_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(value: Config | Path) -> str:
    user = getpass.getuser()
    try:
        cfg = value if isinstance(value, Config) else config.load(_root(value))
        from .onboard import gh_login

        login = gh_login(config.dashboard_env(cfg))
    except (OSError, SystemExit, ValueError):
        login = ""
    return f"{user} (@{login})" if login else user


def _event(value: Config | Path, event: str, state: dict, who: str, at: str) -> None:
    row = {"event": event, "who": who, "reason": state["reason"], "at": at}
    if state["until"] is not None:
        row["until"] = state["until"]
    lifecycle.append(_factory(value) / "events.jsonl", row)


def _expire_locked(value: Config | Path, state: dict | None, instant: datetime) -> dict | None:
    if state is None or state["until"] is None or _timestamp(state["until"]) > instant:
        return state
    at = _format(instant)
    _remove(value)
    _event(value, "resume", state, "expiry", at)
    return None


def activate(
    value: Config | Path,
    reason: str | None = None,
    *,
    until: str | None = None,
    who: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Set the pause once. A later pause command never rewrites its state or journal row."""
    instant = now or datetime.now(UTC)
    with _locked(value):
        if _expire_locked(value, read(value), instant) is not None:
            return False
        at = _format(instant)
        state = {
            "reason": reason or DEFAULT_REASON,
            "at": at,
            "who": who or _identity(value),
            "until": until,
        }
        if until is not None:
            _timestamp(until)
        _save(value, state)
        _event(value, "pause", state, state["who"], at)
        return True


def resume(
    value: Config | Path,
    *,
    who: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Clear the pause once and retain its reason/expiry in the resume row."""
    if read(value) is None:
        return False
    with _locked(value):
        state = read(value)
        if state is None:
            return False
        at = _format(now or datetime.now(UTC))
        actor = who or _identity(value)
        _remove(value)
        _event(value, "resume", state, actor, at)
        return True


def current(
    value: Config | Path,
    *,
    expire: bool = False,
    now: datetime | None = None,
) -> dict | None:
    """Return active state; a pass may atomically consume an elapsed expiry."""
    state = read(value)
    instant = now or datetime.now(UTC)
    if not expire or state is None or state["until"] is None or _timestamp(state["until"]) > instant:
        return state
    with _locked(value):
        return _expire_locked(value, read(value), instant)


def message(state: dict) -> str:
    expiry = f", until {state['until']}" if state["until"] else ""
    return f"paused ({state['reason']}, since {state['at']}, by {state['who']}{expiry})"


def guard(value: Config | Path, logger=print) -> bool:
    """Log one line and return True while paused; consume elapsed expiry."""
    state = current(value, expire=True)
    if state is None:
        return False
    logger(message(state))
    return True


def main_pause(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory pause", description="Pause every Factory stage for this repository.")
    parser.add_argument("--reason", help="operator-visible reason")
    parser.add_argument("--until", help="auto-resume after DURATION (for example 30m or 2h) or at an ISO timestamp")
    args = parser.parse_args(argv)
    cfg = config.load()
    try:
        until = parse_until(args.until) if args.until else None
    except ValueError as exc:
        parser.error(str(exc))
    created = activate(cfg, args.reason, until=until)
    state = read(cfg)
    print(message(state) if created else f"already {message(state)}")
    return 0


def main_resume(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory resume", description="Resume every Factory stage for this repository.")
    parser.parse_args(argv)
    cfg = config.load()
    state = read(cfg)
    changed = resume(cfg)
    print(f"resumed ({state['reason']})" if changed else "not paused")
    return 0
