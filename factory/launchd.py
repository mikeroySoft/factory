"""Read-only launchd label and top-level `launchctl print` field parsing."""
from __future__ import annotations

import os
import re


def target(label: str) -> str:
    if not re.fullmatch(r"factory-[A-Za-z0-9_.-]+", label):
        raise ValueError("invalid_unit")
    return f"gui/{os.getuid()}/{label}"


def parse(text: str) -> dict[str, str]:
    """Ignore nested environments/resource policies, which can repeat field names."""
    fields = {}
    depth = 0
    for row in text.splitlines():
        row = row.strip()
        if depth == 1 and " = " in row and not row.endswith("{"):
            key, value = row.split(" = ", 1)
            fields[key] = value
        if row.endswith("{"):
            depth += 1
        elif row == "}":
            depth -= 1
    if depth != 0 or "state" not in fields:
        raise ValueError("invalid_state")
    return fields


def number(fields: dict[str, str], key: str) -> int | None:
    # launchd may render sysexits as `78: EX_CONFIG`, intervals as `600 seconds`.
    match = re.fullmatch(r"(-?\d+)(?::\s*\S+|\s+seconds)?", fields.get(key, ""))
    return int(match[1]) if match else None
