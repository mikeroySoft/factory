"""Deterministic PR risk tiers from one immutable git diff."""
from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

TIERS = ("Low", "Medium", "High")

HIGH_RULES = (
    ("high:workflow", (".github/**",)),
    ("high:release, promotion, install, or signing", (
        "factory/release.py", "factory/promotion.py", "factory/onboard.py",
        "factory/templates/ci.yml",
    )),
    ("high:approval, gate, and dispatch control", (
        "factory/dispatch.py", "factory/gate.py", "factory/manage.py",
        "factory/decisions.py", "factory/config.py",
    )),
    ("high:risk policy", ("factory/risk.py",)),
    ("high:factory configuration", (".factory.toml", "factory-bands.toml")),
)

LOW_RULES = (
    "low:docs/**",
    "low:markdown",
    "low:tests/** additions",
    "low:.factory-lessons.md",
    "low:experiments/** notes",
    "low:default-off code",
)
NOTE_SUFFIXES = frozenset({".md", ".txt", ".rst", ".json", ".jsonl", ".csv"})


def _changes(base: str, head: str, cwd: Path | None) -> list[tuple[str, str]]:
    raw = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--no-renames", f"{base}..{head}"],
        cwd=cwd, check=True, capture_output=True,
    ).stdout.split(b"\0")
    changes = []
    for index in range(0, len(raw) - 1, 2):
        if raw[index] and raw[index + 1]:
            changes.append((raw[index].decode(errors="surrogateescape"),
                            raw[index + 1].decode(errors="surrogateescape")))
    return changes


def high_rule(path: str) -> str | None:
    return next((name for name, patterns in HIGH_RULES
                 if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)), None)


def _low_rule(status: str, path: str) -> str | None:
    name = Path(path).name
    if path.startswith("docs/"):
        return LOW_RULES[0]
    if path.endswith(".md") and name not in {"AGENTS.md", "REVIEW.md"}:
        return LOW_RULES[1]
    if status == "A" and path.startswith("tests/"):
        return LOW_RULES[2]
    if path == ".factory-lessons.md":
        return LOW_RULES[3]
    if path.startswith("experiments/") and Path(path).suffix in NOTE_SUFFIXES:
        return LOW_RULES[4]
    return None

def _default_off_additions(base: str, head: str, path: str, cwd: Path | None) -> bool:
    if not path.endswith(".py"):
        return False
    diff = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}..{head}", "--", path],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    if any(line.startswith("-") and not line.startswith("---") for line in diff):
        return False
    added: set[int] = set()
    for line in diff:
        if match := re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line):
            start, count = int(match[1]), int(match[2] or 1)
            added.update(range(start, start + count))
    source = subprocess.run(
        ["git", "show", f"{head}:{path}"], cwd=cwd,
        check=True, capture_output=True, text=True,
    ).stdout
    old = subprocess.run(
        ["git", "show", f"{base}:{path}"], cwd=cwd,
        check=False, capture_output=True, text=True,
    ).stdout
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    # Only whole new module-level declarations and guards qualify. Partial edits
    # to existing statements, executable annotations, else arms, and uncertain
    # syntax conservatively keep the ordinary Medium policy.
    flags: set[str] = set()
    guarded: set[str] = set()
    covered: set[int] = set()
    for node in tree.body:
        span = set(range(node.lineno, node.end_lineno + 1))
        if not span & added:
            continue
        if not span <= added:
            return False
        if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant) and node.value.value is False
        ):
            name = node.targets[0].id
            if name in flags or re.search(rf"\b{re.escape(name)}\b", old):
                return False
            flags.add(name)
        elif (
            isinstance(node, ast.If) and isinstance(node.test, ast.Name)
            and node.test.id in flags and not node.orelse
        ):
            guarded.add(node.test.id)
        else:
            return False
        covered.update(span)
    executable = {
        index for index, line in enumerate(source.splitlines(), 1)
        if index in added and line.strip() and not line.lstrip().startswith("#")
    }
    return bool(guarded) and flags == guarded and executable <= covered


def classify(
    base: str,
    head: str,
    *,
    opinion: Callable[[str, list[str]], str | None] | None = None,
    cwd: Path | None = None,
) -> tuple[str, list[str]]:
    """Return ``(tier, matched_rules)`` for ``base..head``.

    Path rules decide first. An optional model opinion may raise that result but
    cannot lower it; model failures leave the deterministic result unchanged.
    """
    changes = _changes(base, head, cwd)
    high = [rule for _, path in changes if (rule := high_rule(path))]
    if high:
        tier, rules = "High", list(dict.fromkeys(high))
    else:
        low = []
        for status, path in changes:
            rule = _low_rule(status, path)
            if rule is None and _default_off_additions(base, head, path, cwd):
                rule = LOW_RULES[5]
            low.append(rule)
        if changes and all(low):
            tier, rules = "Low", list(dict.fromkeys(low))
        else:
            tier, rules = "Medium", ["medium:other path"]
    if opinion is not None:
        try:
            proposed = opinion(tier, rules)
        except Exception:  # noqa: BLE001 - advisory model failure keeps deterministic policy.
            proposed = None
        if proposed in TIERS and TIERS.index(proposed) > TIERS.index(tier):
            tier = proposed
            rules.append(f"model:{proposed}")
    return tier, rules
