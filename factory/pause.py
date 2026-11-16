"""Local per-repository full stop, independent of scheduling pace and timers."""
from __future__ import annotations

import argparse
import contextvars
import fcntl
import getpass
import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory import config, lifecycle

_OVERRIDE = contextvars.ContextVar("factory_pause_override", default=False)


class Paused(RuntimeError):
    """A newly paused repository must park before claiming or publishing."""


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("pause timestamp must include a timezone")
    return parsed


def parse_until(value: str) -> str:
    """Positive durations (s/m/h/d/w, e.g. 1h30m), or timezone-aware ISO timestamps."""
    if re.fullmatch(r"(?:\d+(?:\.\d+)?[smhdw])+", value):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        seconds = sum(float(number) * units[unit] for number, unit in re.findall(r"(\d+(?:\.\d+)?)([smhdw])", value))
        if seconds <= 0:
            raise ValueError("pause duration must be positive")
        return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    _timestamp(value)
    return value


def actor() -> str:
    who = getpass.getuser()
    try:
        result = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                                capture_output=True, text=True, timeout=2, check=False)
        login = result.stdout.strip()
        if result.returncode == 0 and login:
            who += f" (github: {login})"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return who


def read(root: Path) -> dict | None:
    """Passive atomic snapshot; only a pass or explicit resume resolves expiry."""
    try:
        value = json.loads((root / ".factory" / "pause.json").read_text())
    except FileNotFoundError:
        return None
    try:
        if not isinstance(value, dict) or any(not isinstance(value[key], str) for key in ("reason", "at", "who")):
            raise ValueError("invalid pause state")
        _timestamp(value["at"])
        if value.get("until") is not None:
            _timestamp(value["until"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid pause state") from exc
    return {key: value.get(key) for key in ("reason", "at", "who", "until")}


@contextmanager
def _locked(root: Path):
    directory = root / ".factory"
    directory.mkdir(parents=True, exist_ok=True)
    # Keep the inode: concurrent pause, resume and expiry must serialize on one lock.
    with (directory / "pause.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield directory


def _sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def set_pause(root: Path, *, reason: str = "", until: str | None = None) -> dict:
    if until is not None:
        _timestamp(until)
    with _locked(root) as directory:
        current = read(root)
        if current is not None:
            return current
        state = {"reason": reason, "at": datetime.now(UTC).isoformat(), "who": actor(), "until": until}
        temporary = directory / "pause.json.tmp"
        try:
            with temporary.open("w") as output:
                json.dump(state, output)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / "pause.json")
            _sync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)
        lifecycle.append(directory / "events.jsonl", {"event": "pause", **state})
        return state


def _resume(directory: Path, state: dict, who: str) -> None:
    lifecycle.append(directory / "events.jsonl", {
        **state, "event": "resume", "at": datetime.now(UTC).isoformat(), "who": who,
    })
    (directory / "pause.json").unlink()
    _sync_directory(directory)


def resume(root: Path) -> bool:
    if read(root) is None:
        return False
    with _locked(root) as directory:
        state = read(root)
        if state is None:
            return False
        _resume(directory, state, actor())
        return True


def describe(state: dict) -> str:
    reason = " ".join(state["reason"].split()) or "no reason"
    expiry = f", until {state['until']}" if state.get("until") else ""
    return f"paused ({reason}, since {state['at']}, by {state['who']}{expiry})"


def check(root: Path, log=print) -> bool:
    if _OVERRIDE.get():
        return False
    state = read(root)
    if state is None:
        return False
    if state.get("until") and _timestamp(state["until"]) <= datetime.now(UTC):
        with _locked(root) as directory:
            state = read(root)
            if state is not None and state.get("until") and _timestamp(state["until"]) <= datetime.now(UTC):
                _resume(directory, state, "expiry")
                return False
        if state is None:
            return False
    log(describe(state))
    return True


def require_running(root: Path) -> None:
    if check(root):
        raise Paused("repository paused; worktree kept for resume")


@contextmanager
def override(enabled: bool):
    token = _OVERRIDE.set(enabled)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory pause", description=__doc__)
    parser.add_argument("--reason", default="", help="why all factory stages should stop")
    parser.add_argument("--until", help="positive duration (2h, 1h30m, 1d) or timestamp with timezone")
    args = parser.parse_args(argv)
    try:
        until = parse_until(args.until) if args.until is not None else None
    except (ValueError, OverflowError) as exc:
        parser.error(str(exc))
    print(describe(set_pause(config.repo_root(), reason=args.reason, until=until)))
    return 0


def resume_main(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="factory resume", description="Clear this repository's local pause.").parse_args(argv)
    print("resumed" if resume(config.repo_root()) else "not paused")
    return 0
