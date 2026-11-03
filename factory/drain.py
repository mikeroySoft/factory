"""One admission decision before a scheduled pass; never gate individual stages."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

DRAIN_MAX_TTL = timedelta(hours=8)  # Shared contract with District.


def _timestamp(value: str) -> datetime:
    """Accept RFC 3339 UTC, not datetime.fromisoformat's date-only/naive forms."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|\+00:00)", value):
        raise ValueError("expected RFC 3339 UTC timestamp")
    return datetime.fromisoformat(value.upper())


def main(argv: list[str]) -> int:
    # ExecCondition interprets even an accidental SystemExit as a skip. Fail open.
    try:
        now = _timestamp(os.environ["FACTORY_NOW"]) if "FACTORY_NOW" in os.environ else datetime.now(UTC)
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        flag = state / "district" / "drain"
        try:
            mtime = datetime.fromtimestamp(flag.stat().st_mtime, UTC)
        except FileNotFoundError:
            return 0
        except OSError:
            print("WARN drain flag unreadable; ignoring", file=sys.stderr)
            return 0
        if mtime > now + timedelta(seconds=60):
            print("WARN drain flag mtime in the future; ignoring", file=sys.stderr)
            return 0

        expiry = mtime + DRAIN_MAX_TTL
        reason = "malformed flag"
        try:
            data = json.loads(flag.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected JSON object")
            expiry = min(_timestamp(data["expires_at"]), expiry)
            if isinstance(data.get("reason"), str) and data["reason"]:
                reason = data["reason"]
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            print("WARN drain flag malformed", file=sys.stderr)
        until = expiry.isoformat().replace("+00:00", "Z")
        if now >= expiry:
            print(f"drain flag expired at {until}; ignoring", file=sys.stderr)
            return 0
        print(f"skipped: district drain ({reason}, until {until})", file=sys.stderr)
        return 1
    except BaseException as exc:
        print(f"ERROR drain-check {type(exc).__name__}: {exc}; ignoring", file=sys.stderr)
        return 0
