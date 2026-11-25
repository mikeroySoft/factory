"""Host-wide pause metadata shared by admission, CLI, MCP and diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def pause_path() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "factory" / "factory.pause"


def read_status(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    value = {"paused": False, "owner": None, "reason": None, "created_at": None,
             "age_s": None, "unowned": False}
    flag = pause_path()
    try:
        metadata = os.lstat(flag)
    except FileNotFoundError:
        return value
    except OSError:
        return {**value, "paused": True, "unowned": True}
    value.update(paused=True, unowned=True, age_s=max(0.0, now.timestamp() - metadata.st_mtime))
    if not stat.S_ISREG(metadata.st_mode):
        return value
    try:
        # Never block on a FIFO or follow a symlink swapped in after lstat.
        fd = os.open(flag, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict):
            return value
        for key in ("owner", "reason", "created_at"):
            field = data.get(key)
            if isinstance(field, str) and field.strip():
                value[key] = field
        if any(value[key] is None for key in ("owner", "reason", "created_at")):
            return value
        created = datetime.fromisoformat(value["created_at"])
        if created.utcoffset() is None:
            return value
        value.update(unowned=False, age_s=max(0.0, (now - created).total_seconds()))
    except (OSError, UnicodeError, ValueError, OverflowError, RecursionError):
        pass  # The marker still pauses; unreadable or malformed attribution is unowned.
    return value


def set_pause(owner: str, reason: str) -> dict:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must be a nonblank string")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a nonblank string")
    flag = pause_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    data = {"owner": owner, "reason": reason, "created_at": datetime.now(UTC).isoformat()}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=flag.parent,
                                         prefix=".factory.pause-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, flag)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return read_status()


def resume() -> dict:
    pause_path().unlink(missing_ok=True)
    return read_status()


def describe(value: dict) -> str:
    if not value["paused"]:
        return "not paused"
    age = f"{value['age_s']:.0f}s" if value["age_s"] is not None else "unknown"
    ownership = "unowned" if value["unowned"] else "owned"
    return (f"paused ({ownership}); owner: {value['owner'] or 'unknown'}; "
            f"reason: {value['reason'] or 'unknown'}; age: {age}")


def main(argv: list[str], *, command: str) -> int:
    parser = argparse.ArgumentParser(prog=f"factory {command}")
    if command == "pause":
        parser.add_argument("--owner", required=True)
        parser.add_argument("--reason", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = (set_pause(args.owner, args.reason) if command == "pause" else
                 resume() if command == "resume" else read_status())
    except ValueError as exc:
        parser.error(str(exc))
    except OSError as exc:
        print(f"factory {command}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(value) if args.json else describe(value))
    return 0


def pause_main(argv: list[str]) -> int:
    return main(argv, command="pause")


def resume_main(argv: list[str]) -> int:
    return main(argv, command="resume")


def status_main(argv: list[str]) -> int:
    return main(argv, command="status")
