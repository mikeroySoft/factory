"""Odd experimental and even quality-ratcheted public patch releases."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factory import config, lifecycle

SEMVER = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
JSON_BAR = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
BAR = (("human_rate", "lower"), ("first_pass", "higher"), ("escape_rate", "lower"))
WORST = {"human_rate": 1.0, "first_pass": 0.0, "escape_rate": 1.0}
PENDING = ("pending_tickets", "pending_human_touches", "pending_first_passes", "pending_attempts")


def wilson(k: int, n: int) -> tuple[float, float] | None:
    """Return the 95% Wilson score interval, or no estimate for an empty sample."""
    if n == 0:
        return None
    z = 1.959963984540054
    observed = k / n
    denominator = 1 + z * z / n
    center = (observed + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(observed * (1 - observed) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def next_bar(
    bar: dict[str, float | int] | None,
    metrics: dict[str, float | int | None],
    *,
    bar_min_tickets: int,
    bar_floor_human_rate: float,
    bar_floor_first_pass: float,
    bar_floor_escape_rate: float = 0.05,
) -> dict[str, float | int]:
    """Pool small windows and tighten halfway toward conservative confidence bounds."""
    old = bar or WORST

    tickets = int(metrics["tickets"])
    touches = int(metrics["human_touches"])
    first_passes = int(metrics["first_passes"])
    attempts = int(metrics["attempts"])
    escapes = int(metrics.get("escapes") or 0)
    pooled = {
        "pending_tickets": int(old.get("pending_tickets", 0)) + tickets,
        "pending_human_touches": int(old.get("pending_human_touches", 0)) + touches,
        "pending_first_passes": int(old.get("pending_first_passes", 0)) + first_passes,
        "pending_attempts": int(old.get("pending_attempts", 0)) + attempts,
    }
    has_escape = "escape_rate" in old or metrics.get("escape_rate") is not None
    if has_escape:
        pooled["pending_escapes"] = int(old.get("pending_escapes", 0)) + escapes
    result: dict[str, float | int] = {
        "human_rate": float(old["human_rate"]),
        "first_pass": float(old["first_pass"]),
        "tickets": tickets,
        **pooled,
    }
    if has_escape:
        result["escape_rate"] = float(old.get("escape_rate", 1.0))
    if bar is None or "pending_tickets" not in bar:
        result["human_ticket_rate"] = float(metrics["human_ticket_rate"])
    if pooled["pending_tickets"] < bar_min_tickets:
        return result

    human_bound = wilson(min(pooled["pending_human_touches"], pooled["pending_tickets"]),
                         pooled["pending_tickets"])
    first_bound = wilson(pooled["pending_first_passes"], pooled["pending_attempts"])
    if human_bound is not None and human_bound[1] < result["human_rate"]:
        result["human_rate"] = round(
            max((result["human_rate"] + human_bound[1]) / 2, bar_floor_human_rate), 6
        )
    if first_bound is not None and first_bound[0] > result["first_pass"]:
        result["first_pass"] = round(
            min((result["first_pass"] + first_bound[0]) / 2, bar_floor_first_pass), 6
        )
    if has_escape:
        escape_bound = wilson(int(pooled["pending_escapes"]), pooled["pending_tickets"])
        if escape_bound is not None and escape_bound[1] < result["escape_rate"]:
            result["escape_rate"] = round(
                max((result["escape_rate"] + escape_bound[1]) / 2, bar_floor_escape_rate), 6
            )
        result["pending_escapes"] = 0
    result.update({key: 0 for key in PENDING})
    return result


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=check)


def _at(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def measure(cfg: config.Config, since: str | datetime) -> dict[str, float | int | None]:
    """Measure the journal window strictly after ``since``."""
    start = _at(since)
    rows = [row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
            if isinstance(row.get("at"), str) and _at(row["at"]) > start]
    tickets = {row["ticket"] for row in rows
               if row.get("event") == "merged" and type(row.get("ticket")) is int}
    human_tickets = {row["ticket"] for row in rows
                     if row.get("event") == "manage" and row.get("decision") == "HUMAN"
                     and type(row.get("ticket")) is int}
    human_decisions = {
        ("decision", row["decision_id"]) if row.get("decision_id") else ("row", index)
        for index, row in enumerate(rows)
        if row.get("event") == "human-decision"
    }
    human_touches = len(human_decisions) + sum(
        row.get("event") == "manage" and row.get("decision") == "HUMAN"
        for row in rows
    )
    attempts = [row for row in rows if row.get("event") == "attempt" and row.get("attempt") == 1]
    first_passes = sum(row.get("gate") == "PASS" for row in attempts)
    denominator = max(len(tickets), 1)
    return {
        "human_rate": round(human_touches / denominator, 6),
        "human_touches": human_touches,
        "human_ticket_rate": round(min(len(human_tickets) / denominator, 1.0), 6),
        "first_pass": round(first_passes / len(attempts), 6) if attempts else None,
        "first_passes": first_passes,
        "attempts": len(attempts),
        "tickets": len(tickets),
    }

def measure_window(
    cfg: config.Config, until: str | datetime | None = None,
) -> dict[str, float | int | None]:
    """Measure the newest configured merged-ticket window and its matching event span."""
    rows = [
        row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
        if isinstance(row.get("at"), str)
        and (until is None or _at(row["at"]) <= _at(until))
    ]
    merged: dict[int, dict] = {}
    for row in rows:
        if row.get("event") == "merged" and type(row.get("ticket")) is int:
            merged[row["ticket"]] = row
    selected_rows = sorted(merged.values(), key=lambda row: _at(row["at"]))[-cfg.release_window_tickets:]
    selected = {row["ticket"] for row in selected_rows}
    start = min((_at(row["at"]) for row in selected_rows), default=None)
    window = [row for row in rows if start is None or _at(row["at"]) >= start]
    human_tickets = {
        row["ticket"] for row in window
        if row.get("event") == "manage" and row.get("decision") == "HUMAN"
        and type(row.get("ticket")) is int
    }
    human_decisions = {
        ("decision", row["decision_id"]) if row.get("decision_id") else ("row", index)
        for index, row in enumerate(window)
        if row.get("event") == "human-decision"
    }
    human_touches = len(human_decisions) + sum(
        row.get("event") == "manage" and row.get("decision") == "HUMAN"
        for row in window
    )
    attempts = [
        row for row in window
        if row.get("event") == "attempt" and row.get("attempt") == 1
    ]
    first_passes = sum(row.get("gate") == "PASS" for row in attempts)
    from factory import stats

    try:
        escaped = {
            candidate["ticket"]
            for row in stats.escapes()
            for candidate in row.get("candidates", [])
            if type(candidate.get("ticket")) is int
        } & selected
    except (
        OSError, RuntimeError, ValueError, KeyError, TypeError,
        subprocess.SubprocessError, SystemExit,
    ):
        escaped = None
    denominator = max(len(selected), 1)
    return {
        "human_rate": round(human_touches / denominator, 6),
        "human_touches": human_touches,
        "human_ticket_rate": round(min(len(human_tickets & selected) / denominator, 1.0), 6),
        "first_pass": round(first_passes / len(attempts), 6) if attempts else None,
        "first_passes": first_passes,
        "attempts": len(attempts),
        "escape_rate": round(len(escaped) / denominator, 6) if escaped is not None else None,
        "escapes": len(escaped) if escaped is not None else None,
        "tickets": len(selected),
    }


def soak_metrics(
    cfg: config.Config,
    odd: dict[str, Any],
    previous_odd: dict[str, Any] | None,
    previous_bar: dict[str, float | int] | None,
    *,
    now: datetime | None = None,
) -> dict[str, float | int | None]:
    """Evidence that the candidate commit ran across a soak without regressions."""
    now = now or datetime.now(UTC)
    rows = [
        row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
        if isinstance(row.get("at"), str) and _at(row["at"]) <= now
    ]

    def passes(candidate: dict[str, Any]) -> list[dict]:
        return [
            row for row in rows
            if row.get("event") == "lifecycle"
            and row.get("stage") == "dispatcher"
            and row.get("kind") == "enter"
            and row.get("engine_commit") == candidate["sha"]
        ]

    candidate_passes = passes(odd)
    start = min((_at(row["at"]) for row in candidate_passes), default=None)
    active = [
        row for row in rows
        if start is not None and start <= _at(row["at"]) <= now
    ]
    terminals = {
        row["ticket"] for row in active
        if row.get("event") in {"merged", "escalate"} and type(row.get("ticket")) is int
    }
    escalated = {
        row["ticket"] for row in active
        if row.get("event") == "escalate" and type(row.get("ticket")) is int
    }
    escalation_rate = len(escalated) / len(terminals) if terminals else 0.0
    previous_rate = (
        float(previous_bar["escalation_rate"])
        if previous_bar and "escalation_rate" in previous_bar
        else None
    )
    if previous_rate is None and previous_odd is not None:
        old_passes = passes(previous_odd)
        old_start = min((_at(row["at"]) for row in old_passes), default=None)
        old_end = start or now
        old_active = [
            row for row in rows
            if old_start is not None and old_start <= _at(row["at"]) < old_end
        ]
        old_terminals = {
            row["ticket"] for row in old_active
            if row.get("event") in {"merged", "escalate"} and type(row.get("ticket")) is int
        }
        old_escalated = {
            row["ticket"] for row in old_active
            if row.get("event") == "escalate" and type(row.get("ticket")) is int
        }
        if old_terminals:
            previous_rate = len(old_escalated) / len(old_terminals)
    watch_bugs = sum(
        row.get("event") == "watch"
        and not row.get("commented")
        and not row.get("closed")
        for row in active
    )
    return {
        "soak_hours": round((now - start).total_seconds() / 3600, 6) if start else 0.0,
        "soak_passes": len(candidate_passes),
        "watch_filed_bugs": watch_bugs,
        "escalation_rate": round(escalation_rate, 6),
        "previous_escalation_rate": (
            round(previous_rate, 6) if previous_rate is not None else None
        ),
    }


def _latest(
    cfg: config.Config,
    parity: int | None = None,
    before: tuple[int, int, int] | None = None,
) -> dict[str, Any] | None:
    tags = _git(
        cfg.root,
        "tag",
        *(("--merged", f"origin/{cfg.main}") if parity is None else ()),
        "--list",
        "v*",
    ).stdout.splitlines()
    remote = {
        ref.removesuffix("^{}").removeprefix("refs/tags/")
        for line in _git(cfg.root, "ls-remote", "--tags", "origin").stdout.splitlines()
        if len(line.split(maxsplit=1)) == 2
        for ref in [line.split(maxsplit=1)[1]]
    }
    versions = [
        (tuple(map(int, match.groups())), tag)
        for tag in tags
        if tag in remote
        and (match := SEMVER.fullmatch(tag))
        and (parity is None or int(match.group(3)) % 2 == parity)
        and (before is None or tuple(map(int, match.groups())) < before)
    ]
    if not versions:
        return None
    version, tag = max(versions)
    kind = _git(cfg.root, "cat-file", "-t", tag).stdout.strip()
    message = (
        _git(cfg.root, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}").stdout
        if kind == "tag" else ""
    )
    date = _git(
        cfg.root, "for-each-ref", "--format=%(taggerdate:iso-strict)", f"refs/tags/{tag}"
    ).stdout.strip()
    if not date:
        date = _git(cfg.root, "show", "-s", "--format=%cI", tag).stdout.strip()
    return {
        "tag": tag,
        "version": version,
        "sha": _git(cfg.root, "rev-parse", f"{tag}^{{}}").stdout.strip(),
        "date": date,
        "message": message,
    }


def _bar(message: str) -> dict[str, float | int] | None:
    match = JSON_BAR.search(message)
    if not match:
        return None
    value = json.loads(match.group(1))
    required = {"human_rate", "first_pass", "tickets"}
    optional_rates = {
        "human_ticket_rate", "escape_rate", "soak_hours",
        "escalation_rate", "previous_escalation_rate",
    }
    optional_counts = {"pending_escapes", "watch_filed_bugs", "soak_passes"}
    allowed = required | set(PENDING) | optional_rates | optional_counts
    if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
        raise ValueError("release tag JSON bar has invalid fields")
    pending = set(PENDING) & set(value)
    if pending and pending != set(PENDING):
        raise ValueError("release tag JSON bar has incomplete pending counts")
    if (
        type(value["human_rate"]) not in (int, float)
        or type(value["first_pass"]) not in (int, float)
        or type(value["tickets"]) is not int
        or any(type(value[key]) is not int for key in pending | (optional_counts & set(value)))
        or any(type(value[key]) not in (int, float) for key in optional_rates & set(value))
    ):
        raise ValueError("release tag JSON bar has invalid metric types")
    result: dict[str, float | int] = {
        "human_rate": float(value["human_rate"]),
        "first_pass": float(value["first_pass"]),
        "tickets": value["tickets"],
    }
    result.update({key: value[key] for key in PENDING if key in value})
    result.update({key: value[key] for key in optional_counts if key in value})
    result.update({key: float(value[key]) for key in optional_rates if key in value})
    return result


def _reset_legacy_bar(
    bar: dict[str, float | int] | None, cfg: config.Config
) -> tuple[dict[str, float | int] | None, bool]:
    if bar is None or "pending_tickets" in bar:
        return bar, False
    return {
        **bar,
        "human_rate": max(float(bar["human_rate"]), cfg.release_bar_floor_human_rate),
        "first_pass": min(float(bar["first_pass"]), cfg.release_bar_floor_first_pass),
        "escape_rate": max(
            float(bar.get("escape_rate", cfg.release_bar_floor_escape_rate)),
            cfg.release_bar_floor_escape_rate,
        ),
    }, True


def _gh(cfg: config.Config, endpoint: str, *args: str) -> Any:
    proc = subprocess.run(["gh", "api", *args, endpoint], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"gh api {endpoint} failed")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def _pulls(cfg: config.Config, branch: str) -> list[dict[str, Any]]:
    return _gh(
        cfg,
        f"repos/{cfg.repo}/pulls?state=open&base={cfg.main}&head={cfg.repo.split('/')[0]}:{branch}",
    )

def _pull(cfg: config.Config, number: int) -> dict[str, Any]:
    return _gh(cfg, f"repos/{cfg.repo}/pulls/{number}")


def _open_pull(cfg: config.Config, branch: str, title: str, body: str) -> dict[str, Any]:
    return _gh(
        cfg, f"repos/{cfg.repo}/pulls", "-X", "POST",
        "-f", f"title={title}", "-f", f"head={branch}", "-f", f"base={cfg.main}", "-f", f"body={body}",
    )


def _merge_pull(cfg: config.Config, number: int, title: str) -> dict[str, Any]:
    return _gh(
        cfg, f"repos/{cfg.repo}/pulls/{number}/merge", "-X", "PUT",
        "-f", "merge_method=squash", "-f", f"commit_title={title}",
    )


def _version_at(cfg: config.Config, ref: str) -> tuple[int, int, int]:
    text = _git(cfg.root, "show", f"{ref}:{cfg.release_version_file}").stdout
    match = next(
        (match for line in text.splitlines() if "version" in line.lower()
         if (match := re.search(r"(?<![0-9.])(\d+)\.(\d+)\.(\d+)(?![0-9.])", line))),
        None,
    )
    if not match:
        raise ValueError(f"{cfg.release_version_file} has no semantic version at {ref}")
    return tuple(map(int, match.groups()))


def ci_passes(cfg: config.Config, sha: str) -> bool:
    """True when every reported status and check run for ``sha`` passed."""
    prefix = f"repos/{cfg.repo}/commits/{sha}"
    status = _gh(cfg, f"{prefix}/status?per_page=100")
    pages = _gh(cfg, f"{prefix}/check-runs?per_page=100", "--paginate", "--slurp")
    runs = [run for page in pages for run in page.get("check_runs", [])]
    statuses = status.get("statuses", [])
    return bool(statuses or runs) and (not statuses or status.get("state") == "success") and all(
        row.get("state") == "success" for row in statuses
    ) and all(row.get("status") == "completed"
              and row.get("conclusion") in {"success", "skipped", "neutral"} for row in runs)


def gate_passes(worktree: Path) -> bool:
    return subprocess.run(
        [sys.executable, "-m", "factory", "gate", "--tier", "release"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0


def _reason(condition: str, metrics: dict[str, float | int | None] | None,
            bar: dict[str, float | int] | None) -> str:
    return f"{condition}; measured={json.dumps(metrics, sort_keys=True)} bar={json.dumps(bar, sort_keys=True)}"


def _record(cfg: config.Config, outcome: str, reason: str, metrics: dict[str, float | int | None] | None,
            bar: dict[str, float | int] | None, **fields: object) -> None:
    from factory import dispatch

    if getattr(dispatch, "cfg", None) is not cfg:
        dispatch.configure(cfg)
    dispatch.record("release", outcome=outcome, reason=reason, metrics=metrics, bar=bar, **fields)


def _record_hold(
    cfg: config.Config,
    reason: str,
    metrics: dict[str, float | int | None],
    bar: dict[str, float | int],
) -> None:
    duplicate = any(
        row.get("event") == "release"
        and row.get("outcome") == "held"
        and row.get("bar") == bar
        for row in lifecycle.read_events(cfg.factory / "events.jsonl")
    )
    if not duplicate:
        _record(cfg, "held", reason, metrics, bar)


def _finish(cfg: config.Config, dry_run: bool, reason: str, metrics: dict[str, float | int | None] | None,
            bar: dict[str, float | int] | None, latest: str | None, *, released: bool = False) -> str:
    if dry_run:
        print(json.dumps({"metrics": metrics, "bar": bar, "latest": latest, "verdict": reason}, sort_keys=True))
    elif released:
        _record(cfg, "released", reason, metrics, bar, tag=latest)
    return reason


def _gh_release(cfg: config.Config, *args: str, notes: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", "release", *args, "--repo", cfg.repo], input=notes,
                          text=True, capture_output=True, check=False)


def _notes(cfg: config.Config, tag: str) -> str:
    """The tag's ``## X.Y.Z`` section of its own CHANGELOG.md, empty when it has none."""
    text = _git(cfg.root, "show", f"{tag}:CHANGELOG.md", check=False).stdout
    match = re.search(rf"(?ms)^## {re.escape(tag.removeprefix('v'))}(?![0-9.]).*?\n(.*?)(?=^## |\Z)", text)
    return match.group(1).strip() if match else ""


def _unpublished(cfg: config.Config, tag: str) -> bool:
    outcomes = {row.get("outcome") for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "release" and row.get("tag") == tag}
    return "released" in outcomes and "published" not in outcomes


def _publish(cfg: config.Config, tag: str, *, experimental: bool = False) -> str:
    """Create the GitHub Release for a pushed tag unless one exists."""
    try:
        if _gh_release(cfg, "view", tag).returncode == 0:
            reason = f"GitHub Release {tag} already exists"
        else:
            channel = ("--prerelease", "--latest=false") if experimental else ("--latest",)
            proc = _gh_release(
                cfg, "create", tag, "--verify-tag", *channel, "--title", tag,
                "--notes-file", "-", notes=_notes(cfg, tag),
            )
            if proc.returncode:
                raise RuntimeError(proc.stderr.strip() or f"gh release create {tag} failed")
            reason = f"created GitHub Release {tag}"
    except Exception as exc:  # noqa: BLE001 - journaled and retried next pass
        reason = f"GitHub Release {tag} failed: {type(exc).__name__}: {exc}"
        _record(cfg, "publish_failed", reason, None, None, tag=tag)
        return reason
    _record(cfg, "published", reason, None, None, tag=tag)
    return reason


def _replace_once(text: str, pattern: str, replacement: str, spot: str) -> str:
    changed, count = re.subn(pattern, lambda match: match.expand(replacement), text)
    if count != 1:
        raise ValueError(f"{spot} must appear exactly once (found {count})")
    return changed


def _update_experimental_files(
    cfg: config.Config, worktree: Path, old: str, new: str,
) -> None:
    """Bump an odd release without changing public release documentation."""
    version = worktree / cfg.release_version_file
    version_text, count = re.subn(
        rf"(?<![0-9.]){re.escape(old)}(?![0-9.])", new, version.read_text(), count=1
    )
    if count != 1:
        raise ValueError(f"{cfg.release_version_file} does not contain version {old}")
    changelog = worktree / "CHANGELOG.md"
    changelog_text, count = re.subn(
        r"(?m)^## Unreleased[ \t]*\n(?:\n)?",
        f"## Unreleased\n\n## {new} — {datetime.now(UTC).date().isoformat()}\n\n",
        changelog.read_text(),
        count=1,
    )
    if count != 1:
        raise ValueError("CHANGELOG.md has no ## Unreleased heading")
    version.write_text(version_text)
    changelog.write_text(changelog_text)


def _update_release_files(cfg: config.Config, worktree: Path, old: str, new: str) -> None:
    version = worktree / cfg.release_version_file
    version_text, count = re.subn(
        rf"(?<![0-9.]){re.escape(old)}(?![0-9.])", new, version.read_text(), count=1
    )
    if count != 1:
        raise ValueError(f"{cfg.release_version_file} does not contain version {old}")

    released = datetime.now(UTC).date()
    changelog = worktree / "CHANGELOG.md"
    changelog_text, count = re.subn(
        r"(?m)^## Unreleased[ \t]*\n(?:\n)?",
        f"## Unreleased\n\n## {new} — {released.isoformat()}\n\n",
        changelog.read_text(),
        count=1,
    )
    if count != 1:
        raise ValueError("CHANGELOG.md has no ## Unreleased heading")

    readme = worktree / "README.md"
    page = worktree / "docs" / "index.html"
    readme_text = _replace_once(
        readme.read_text() if readme.exists() else "",
        r"(?m)^(uv tool install git\+https://github\.com/mikeroySoft/factory@)"
        r"v\d+\.\d+\.\d+([ \t]+# a specific release)$",
        rf"\g<1>v{new}\g<2>",
        "README.md specific-release install pin",
    )
    page_text = page.read_text() if page.exists() else ""
    page_text = _replace_once(
        page_text,
        r"(uv tool install git\+https://github\.com/mikeroySoft/factory@)"
        r"v\d+\.\d+\.\d+(</code>)",
        rf"\g<1>v{new}\g<2>",
        "docs/index.html install-command version pin",
    )
    display_date = f"{released:%B} {released.day}, {released.year}"
    page_text = _replace_once(
        page_text,
        r"Latest release: v\d+\.\d+\.\d+ · [A-Z][a-z]+ \d{1,2}, \d{4}",
        f"Latest release: v{new} · {display_date}",
        "docs/index.html latest-release line",
    )
    section = re.search(
        rf"(?ms)^## {re.escape(new)} — \d{{4}}-\d{{2}}-\d{{2}}\n\n(.*?)(?=^## |\Z)",
        changelog_text,
    )
    notes = "\n".join(
        f"                <li>{html.escape(line.removeprefix('- '))}</li>"
        for line in section.group(1).splitlines()
        if line
    )
    entry = (
        '          <li class="release">\n'
        '            <div class="release-meta">\n'
        '              <span class="chip chip-success">Latest release</span>\n'
        f'              <a href="https://github.com/mikeroySoft/factory/releases/tag/v{new}">'
        f"v{new}</a>\n"
        f'              <time datetime="{released.isoformat()}">{display_date}</time>\n'
        "            </div>\n"
        "            <div>\n"
        f"              <h3>v{new} release notes.</h3>\n"
        "              <ul>\n"
        f"{notes}\n"
        "              </ul>\n"
        "            </div>\n"
        "          </li>\n"
    )
    page_text = _replace_once(
        page_text,
        r'<span class="chip chip-success">Latest release</span>',
        '<span class="chip">Release</span>',
        "docs/index.html changelog history",
    )
    history = '<ol class="history">\n'
    count = page_text.count(history)
    if count != 1:
        raise ValueError(f"docs/index.html changelog history must appear exactly once (found {count})")
    page_text = page_text.replace(history, f"{history}{entry}", 1)

    version.write_text(version_text)
    changelog.write_text(changelog_text)
    readme.write_text(readme_text)
    page.write_text(page_text)


def _text(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def _next_odd(odd: dict[str, Any] | None, even: dict[str, Any]) -> tuple[int, int, int]:
    highest = max(
        (tag["version"] for tag in (odd, even) if tag is not None),
        default=even["version"],
    )
    patch = highest[2] + 1
    if patch % 2 == 0:
        patch += 1
    return (*highest[:2], patch)


def _branch_sha(cfg: config.Config, branch: str) -> str | None:
    result = _git(cfg.root, "rev-parse", "--verify", f"origin/{branch}", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _has_changes(cfg: config.Config, base: dict[str, Any]) -> bool:
    subjects = _git(
        cfg.root, "log", "--format=%s", f"{base['tag']}..origin/{cfg.main}"
    ).stdout.splitlines()
    return any(not re.match(r"^(?:Release|Promote) v?\d+\.\d+\.\d+\b", subject) for subject in subjects)


def _release_base(
    cfg: config.Config, odd: dict[str, Any] | None, even: dict[str, Any],
) -> dict[str, Any]:
    on_main = _git(
        cfg.root, "merge-base", "--is-ancestor", even["tag"], f"origin/{cfg.main}",
        check=False,
    ).returncode == 0
    return even if on_main else odd or even


def _process_pull(
    cfg: config.Config,
    branch: str,
    title: str,
    metrics: dict[str, float | int | None],
    bar: dict[str, float | int] | None,
) -> str | None:
    pulls = _pulls(cfg, branch)
    if not pulls:
        return None
    pull = _pull(cfg, pulls[0]["number"])
    number = pull["number"]
    state, mergeable = pull.get("mergeable_state"), pull.get("mergeable")
    if mergeable is None or state == "unknown":
        return _reason("waiting for GitHub mergeability", metrics, bar)
    if state == "behind":
        _gh(cfg, f"repos/{cfg.repo}/pulls/{number}/update-branch", "-X", "PUT")
        return _reason(f"updated release PR #{number} from main", metrics, bar)
    if state == "dirty" or mergeable is False:
        _gh(cfg, f"repos/{cfg.repo}/pulls/{number}", "-X", "PATCH", "-f", "state=closed")
        try:
            _gh(cfg, f"repos/{cfg.repo}/git/refs/heads/{branch}", "-X", "DELETE")
        except RuntimeError as exc:
            if "404" not in str(exc) and "422" not in str(exc):
                raise
        return _reason(f"closed conflicting release PR #{number}; next pass rebuilds it", metrics, bar)
    if not ci_passes(cfg, pull["head"]["sha"]):
        return _reason("waiting for CI", metrics, bar)
    try:
        merged = _merge_pull(cfg, number, title)
    except RuntimeError as exc:
        if "405" in str(exc) or "409" in str(exc):
            return _reason(str(exc), metrics, bar)
        raise
    if not merged.get("merged"):
        return _reason(
            str(merged.get("message") or f"release PR #{number} merge refused"), metrics, bar
        )
    return _reason(f"merged release PR #{number}", metrics, bar)


def _temporary_worktree(cfg: config.Config, ref: str) -> tuple[Path, Path]:
    cfg.factory.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="release-", dir=cfg.factory))
    worktree = temporary / "worktree"
    _git(cfg.root, "worktree", "prune", check=False)
    _git(cfg.root, "worktree", "add", "--detach", str(worktree), ref)
    return temporary, worktree


def _remove_worktree(cfg: config.Config, temporary: Path, worktree: Path) -> None:
    _git(cfg.root, "worktree", "remove", "--force", str(worktree), check=False)
    shutil.rmtree(temporary, ignore_errors=True)


def _open_experimental_pr(
    cfg: config.Config,
    head: str,
    old: str,
    version: str,
    branch: str,
    metrics: dict[str, float | int | None],
    bar: dict[str, float | int] | None,
) -> str:
    temporary, worktree = _temporary_worktree(cfg, head)
    try:
        _update_experimental_files(cfg, worktree, old, version)
        if not gate_passes(worktree):
            return _reason("factory gate --tier release failed", metrics, bar)
        _git(worktree, "add", cfg.release_version_file, "CHANGELOG.md")
        _git(worktree, "commit", "-m", f"Release {version}")
        pushed = _git(
            worktree, "push", "--force", "origin", f"HEAD:refs/heads/{branch}", check=False
        )
        if pushed.returncode:
            detail = pushed.stderr.strip().splitlines()[-1] if pushed.stderr.strip() else "git push failed"
            return _reason(f"release branch push failed: {detail}", metrics, bar)
        body = (
            f"Automated experimental patch release from v{old}.\n\n"
            f"Window metrics (informational): `{json.dumps(metrics, sort_keys=True)}`"
        )
        pull = _open_pull(cfg, branch, f"Release {version}", body)
        return f"opened experimental release PR #{pull['number']} for v{version}"
    finally:
        _remove_worktree(cfg, temporary, worktree)


def _promotion_evidence(
    cfg: config.Config,
    odd: dict[str, Any],
    previous_odd: dict[str, Any] | None,
    even: dict[str, Any],
) -> tuple[
    dict[str, float | int | None],
    dict[str, float | int] | None,
    dict[str, float | int] | None,
    str | None,
]:
    metrics = measure_window(cfg)
    bar = _bar(even["message"])
    comparison, _ = _reset_legacy_bar(bar, cfg)
    soak = soak_metrics(cfg, odd, previous_odd, comparison)
    metrics.update(soak)
    required_tickets = max(cfg.release_min_tickets, cfg.release_bar_min_tickets)
    if metrics["tickets"] < required_tickets:
        hold = f"tickets {metrics['tickets']} below promotion minimum {required_tickets}"
    elif metrics["first_pass"] is None:
        hold = "no first-attempt evidence in rolling window"
    elif metrics["escape_rate"] is None:
        hold = "escape evidence is unavailable"
    elif metrics["soak_passes"] == 0:
        hold = f"{odd['tag']} has no real factory passes"
    elif metrics["soak_hours"] < cfg.release_soak_hours:
        hold = (
            f"soak {metrics['soak_hours']}h below soak_hours {cfg.release_soak_hours}"
        )
    elif metrics["watch_filed_bugs"]:
        hold = f"watch filed {metrics['watch_filed_bugs']} engine bug(s) during soak"
    elif (
        metrics["previous_escalation_rate"] is not None
        and metrics["escalation_rate"] > metrics["previous_escalation_rate"]
    ):
        hold = (
            f"escalation_rate {metrics['escalation_rate']} rose above "
            f"{metrics['previous_escalation_rate']}"
        )
    else:
        hold = None
    if hold is None and comparison:
        for metric, direction in BAR:
            if metric not in comparison or metrics.get(metric) is None:
                continue
            worse = (
                metrics[metric] > comparison[metric]
                if direction == "lower" else metrics[metric] < comparison[metric]
            )
            if worse:
                hold = (
                    f"{metric} {metrics[metric]} is worse than "
                    f"{comparison[metric]} ({direction} is better)"
                )
                break
    return metrics, bar, comparison, hold


def _build_promotion(
    cfg: config.Config,
    odd: dict[str, Any],
    version: str,
    branch: str,
    metrics: dict[str, float | int | None],
    bar: dict[str, float | int] | None,
) -> str:
    temporary, worktree = _temporary_worktree(cfg, odd["sha"])
    try:
        _update_release_files(
            cfg, worktree, odd["tag"].removeprefix("v"), version
        )
        if not gate_passes(worktree):
            return _reason("factory gate --tier release failed", metrics, bar)
        _git(
            worktree, "add", cfg.release_version_file, "CHANGELOG.md",
            "README.md", "docs/index.html",
        )
        _git(worktree, "commit", "-m", f"Promote v{version}")
        pushed = _git(
            worktree, "push", "--force", "origin", f"HEAD:refs/heads/{branch}", check=False
        )
        if pushed.returncode:
            detail = pushed.stderr.strip().splitlines()[-1] if pushed.stderr.strip() else "git push failed"
            return _reason(f"promotion branch push failed: {detail}", metrics, bar)
        return _reason(f"built promotion branch {branch}; waiting for CI", metrics, bar)
    finally:
        _remove_worktree(cfg, temporary, worktree)


def _tag_experimental(
    cfg: config.Config,
    base: dict[str, Any],
    current: tuple[int, int, int],
    metrics: dict[str, float | int | None],
) -> str:
    version, tag = _text(current), f"v{_text(current)}"
    commits = _git(
        cfg.root, "log", "--reverse", "--format=%H",
        f"{base['tag']}..origin/{cfg.main}", f"-G{re.escape(version)}",
        "--", cfg.release_version_file,
    ).stdout.splitlines()
    if not commits:
        raise ValueError(f"cannot find commit that set version {version}")
    commit = commits[0]
    message = (
        f"Experimental release {version}: window metrics for information only.\n\n"
        f"Metrics: {json.dumps(metrics, sort_keys=True, separators=(',', ':'))}"
    )
    _git(cfg.root, "tag", "-d", tag, check=False)
    _git(cfg.root, "tag", "-a", tag, commit, "-m", message)
    pushed = _git(cfg.root, "push", "origin", tag, check=False)
    if pushed.returncode:
        _git(cfg.root, "tag", "-d", tag, check=False)
        detail = pushed.stderr.strip().splitlines()[-1] if pushed.stderr.strip() else "git push failed"
        return _reason(f"tag push failed: {detail}", metrics, None)
    released = _finish(cfg, False, f"released {tag}", metrics, None, tag, released=True)
    return f"{released}; {_publish(cfg, tag, experimental=True)}"


def _tag_public(
    cfg: config.Config,
    odd: dict[str, Any],
    even: dict[str, Any],
    version: str,
    commit: str,
    metrics: dict[str, float | int | None],
    bar: dict[str, float | int] | None,
    comparison: dict[str, float | int] | None,
) -> str:
    updated = next_bar(
        comparison,
        metrics,
        bar_min_tickets=cfg.release_bar_min_tickets,
        bar_floor_human_rate=cfg.release_bar_floor_human_rate,
        bar_floor_first_pass=cfg.release_bar_floor_first_pass,
        bar_floor_escape_rate=cfg.release_bar_floor_escape_rate,
    )
    for key in ("soak_hours", "soak_passes", "watch_filed_bugs", "escalation_rate"):
        updated[key] = metrics[key]
    if metrics["previous_escalation_rate"] is not None:
        updated["previous_escalation_rate"] = metrics["previous_escalation_rate"]
    tag = f"v{version}"
    message = (
        f"Public release {version}, promoted from {odd['tag']}.\n\n"
        f"```json\n{json.dumps(updated, separators=(',', ':'))}\n```"
    )
    _git(cfg.root, "tag", "-d", tag, check=False)
    _git(cfg.root, "tag", "-a", tag, commit, "-m", message)
    pushed = _git(
        cfg.root, "push", "--atomic", "origin", tag,
        f"{commit}:refs/heads/stable", check=False,
    )
    if pushed.returncode:
        _git(cfg.root, "tag", "-d", tag, check=False)
        detail = pushed.stderr.strip().splitlines()[-1] if pushed.stderr.strip() else "git push failed"
        return _reason(f"atomic push failed: {detail}", metrics, bar)
    released = _finish(cfg, False, f"released {tag}", metrics, bar, tag, released=True)
    return f"{released}; {_publish(cfg, tag)}"


def _public_on_main(cfg: config.Config, even: dict[str, Any]) -> bool:
    readme = _git(
        cfg.root, "show", f"origin/{cfg.main}:README.md", check=False
    ).stdout
    return f"factory@{even['tag']}" in readme


def _open_promotion_pull(
    cfg: config.Config,
    odd: dict[str, Any],
    even: dict[str, Any],
    branch: str,
) -> str:
    temporary, worktree = _temporary_worktree(cfg, even["tag"])
    try:
        version = worktree / cfg.release_version_file
        text, count = re.subn(
            rf"(?<![0-9.]){re.escape(_text(even['version']))}(?![0-9.])",
            _text(odd["version"]),
            version.read_text(),
            count=1,
        )
        if count != 1:
            raise ValueError(f"{cfg.release_version_file} does not contain {_text(even['version'])}")
        version.write_text(text)
        _git(worktree, "add", cfg.release_version_file)
        _git(worktree, "commit", "-m", f"Promote {even['tag']}")
        _git(worktree, "push", "--force", "origin", f"HEAD:refs/heads/{branch}")
        pull = _open_pull(
            cfg, branch, f"Promote {even['tag']}",
            f"Carry {even['tag']} public pins and release notes back to main.",
        )
        return f"opened promotion PR #{pull['number']} for {even['tag']}"
    finally:
        _remove_worktree(cfg, temporary, worktree)


def _dry_run_channels(
    cfg: config.Config,
    odd: dict[str, Any] | None,
    even: dict[str, Any],
) -> str:
    metrics = measure_window(cfg)
    current = _version_at(cfg, f"origin/{cfg.main}")
    base = _release_base(cfg, odd, even)
    highest = max((tag for tag in (odd, even) if tag), key=lambda tag: tag["version"])
    next_odd = _next_odd(odd, even)
    odd_pulls = _pulls(cfg, f"release/v{_text(next_odd)}")
    if current > highest["version"] and current[2] % 2:
        odd_action = f"would tag v{_text(current)} as experimental"
    elif odd_pulls:
        odd_action = f"experimental release PR #{odd_pulls[0]['number']} is in flight"
    elif not _has_changes(cfg, base):
        odd_action = f"no changes since {base['tag']}"
    elif odd and (
        datetime.now(UTC) - _at(odd["date"])
    ).total_seconds() < cfg.release_experimental_min_interval_hours * 3600:
        odd_action = "experimental interval has not elapsed"
    else:
        odd_action = f"would open experimental release PR for v{_text(next_odd)}"
    even_report: dict[str, Any] = {"candidate": None, "verdict": "no newer odd tag"}
    if odd and odd["version"] > even["version"]:
        evidence, bar, _, hold = _promotion_evidence(
            cfg, odd, _latest(cfg, 1, odd["version"]), even
        )
        next_even = f"v{_text((*odd['version'][:2], odd['version'][2] + 1))}"
        branch = f"release/{next_even}"
        commit = _branch_sha(cfg, branch)
        if hold:
            verdict = hold
        elif commit is None:
            verdict = "would build promotion branch"
        elif ci_passes(cfg, commit):
            verdict = "would promote"
        else:
            verdict = "waiting for CI on promotion commit"
        even_report = {
            "candidate": odd["tag"],
            "next": next_even,
            "metrics": evidence,
            "bar": bar,
            "verdict": verdict,
        }
    print(json.dumps({
        "odd": {"next": f"v{_text(next_odd)}", "metrics": metrics, "verdict": odd_action},
        "even": even_report,
    }, sort_keys=True))
    return f"{odd_action}; even: {even_report['verdict']}"


def _run_channels(cfg: config.Config, dry_run: bool, open_pr: bool) -> str:
    odd, even = _latest(cfg, 1), _latest(cfg, 0)
    assert even is not None
    if dry_run:
        return _dry_run_channels(cfg, odd, even)
    publication = []
    for tag, experimental in ((odd, True), (even, False)):
        if tag and _unpublished(cfg, tag["tag"]):
            publication.append(_publish(cfg, tag["tag"], experimental=experimental))

    current = _version_at(cfg, f"origin/{cfg.main}")
    base = _release_base(cfg, odd, even)
    highest = max((tag for tag in (odd, even) if tag), key=lambda tag: tag["version"])
    if (
        current <= highest["version"]
        and not (odd and odd["version"] > even["version"])
        and (
            not even["message"].startswith("Public release ")
            or _public_on_main(cfg, even)
        )
        and not _has_changes(cfg, base)
    ):
        reason = f"no changes since {even['tag']}"
        return f"{reason}; {'; '.join(publication)}" if publication else reason
    metrics = measure_window(cfg)
    if current > highest["version"] and current[2] % 2:
        return _tag_experimental(cfg, highest, current, metrics)

    if odd and odd["version"] > even["version"]:
        evidence, bar, comparison, hold = _promotion_evidence(
            cfg, odd, _latest(cfg, 1, odd["version"]), even
        )
        if hold:
            reason = _reason(hold, evidence, comparison)
            _record_hold(cfg, reason, evidence, comparison or WORST)
            return reason
        next_even = (*odd["version"][:2], odd["version"][2] + 1)
        version, branch = _text(next_even), f"release/v{_text(next_even)}"
        commit = _branch_sha(cfg, branch)
        if commit is None:
            return _build_promotion(cfg, odd, version, branch, evidence, bar)
        if not ci_passes(cfg, commit):
            return _reason("waiting for CI on promotion commit", evidence, bar)
        return _tag_public(cfg, odd, even, version, commit, evidence, bar, comparison)

    if (
        odd
        and even["message"].startswith("Public release ")
        and even["version"] == (*odd["version"][:2], odd["version"][2] + 1)
        and not _public_on_main(cfg, even)
    ):
        branch = f"release/{even['tag']}"
        pending = _process_pull(cfg, branch, f"Promote {even['tag']}", metrics, _bar(even["message"]))
        if pending:
            return pending
        if not open_pr:
            return _reason("no promotion PR open", metrics, _bar(even["message"]))
        return _open_promotion_pull(cfg, odd, even, branch)

    next_odd = _next_odd(odd, even)
    version, branch = _text(next_odd), f"release/v{_text(next_odd)}"
    pending = _process_pull(cfg, branch, f"Release {version}", metrics, _bar(even["message"]))
    if pending:
        return pending
    if not open_pr:
        return _reason("no release PR open", metrics, _bar(even["message"]))
    if not _has_changes(cfg, base):
        return _reason(f"no changes since {base['tag']}", metrics, _bar(even["message"]))
    if odd:
        elapsed = (datetime.now(UTC) - _at(odd["date"])).total_seconds() / 3600
        if elapsed < cfg.release_experimental_min_interval_hours:
            return _reason(
                f"experimental interval {elapsed:.3f}h below "
                f"{cfg.release_experimental_min_interval_hours}h",
                metrics, _bar(even["message"]),
            )
    head = _git(cfg.root, "rev-parse", f"origin/{cfg.main}").stdout.strip()
    if not ci_passes(cfg, head):
        return _reason(
            f"CI statuses/check-runs are not all successful for {head}",
            metrics, _bar(even["message"]),
        )
    return _open_experimental_pr(
        cfg, head, _text(current), version, branch, metrics, _bar(even["message"])
    )


def _run_legacy(
    cfg: config.Config,
    dry_run: bool = False,
    open_pr: bool = True,
    *,
    fetched: bool = False,
) -> str:
    """Advance the release PR, merge, tag, and stable branch from remote state. With
    `open_pr=False` (the dispatcher's start-of-pass call) a new release PR is never opened."""
    metrics: dict[str, float | int | None] | None = None
    bar: dict[str, float | int] | None = None
    comparison_bar: dict[str, float | int] | None = None
    latest_tag: str | None = None
    bar_reset = False
    try:
        if not fetched:
            _git(cfg.root, "fetch", "--quiet", "--tags", "origin")
        latest = _latest(cfg)
        if latest is None:
            return _finish(cfg, dry_run, _reason("no semver release tag reachable from origin/main", None, None),
                           None, None, None)
        latest_tag = latest["tag"]
        if not dry_run and _unpublished(cfg, latest_tag):
            _publish(cfg, latest_tag)
        bar = _bar(latest["message"])
        comparison_bar, bar_reset = _reset_legacy_bar(bar, cfg)
        current = _version_at(cfg, f"origin/{cfg.main}")
        if current > latest["version"]:
            version = ".".join(map(str, current))
            tag = f"v{version}"
            metrics = measure(cfg, latest["date"])
            commits = _git(
                cfg.root, "log", "--reverse", "--format=%H",
                f"{latest_tag}..origin/{cfg.main}", f"-G{re.escape(version)}",
                "--", cfg.release_version_file,
            ).stdout.splitlines()
            commit = commits[0] if commits else ""
            if not commit:
                raise ValueError(f"cannot find commit that set version {version}")
            reason = _reason(
                f"would tag {tag} and advance stable" if dry_run else f"released {tag}",
                metrics, comparison_bar)
            if dry_run:
                return _finish(cfg, True, reason, metrics, bar, latest_tag)
            reset_note = (
                f"Release bar reset to floors: human rate {cfg.release_bar_floor_human_rate}, "
                f"first pass {cfg.release_bar_floor_first_pass}.\n\n"
                if bar_reset else ""
            )
            updated_bar = next_bar(
                comparison_bar,
                metrics,
                bar_min_tickets=cfg.release_bar_min_tickets,
                bar_floor_human_rate=cfg.release_bar_floor_human_rate,
                bar_floor_first_pass=cfg.release_bar_floor_first_pass,
            )
            message = (f"Release {version}: {metrics['tickets']} tickets, human rate "
                       f"{metrics['human_rate']}, first pass {metrics['first_pass']}.\n\n"
                       f"{reset_note}```json\n{json.dumps(updated_bar, separators=(',', ':'))}\n```")
            _git(cfg.root, "tag", "-d", tag, check=False)
            _git(cfg.root, "tag", "-a", tag, commit, "-m", message)
            pushed = False
            try:
                result = _git(cfg.root, "push", "--atomic", "origin", tag,
                              f"{commit}:refs/heads/stable", check=False)
                pushed = result.returncode == 0
                if not pushed:
                    detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "git push failed"
                    return _finish(cfg, False, _reason(f"atomic push failed: {detail}", metrics, bar),
                                   metrics, bar, latest_tag)
                released = _finish(cfg, False, f"released {tag}", metrics, bar, tag, released=True)
                return f"{released}; {_publish(cfg, tag)}"
            finally:
                if not pushed:
                    _git(cfg.root, "tag", "-d", tag, check=False)

        metrics = measure(cfg, latest["date"])
        head = _git(cfg.root, "rev-parse", f"origin/{cfg.main}").stdout.strip()
        if head == latest["sha"] or not _git(
                cfg.root, "rev-list", "--count", f"{latest_tag}..origin/{cfg.main}").stdout.strip().lstrip("0"):
            return _finish(cfg, dry_run, _reason(f"no changes since {latest_tag}", metrics, bar),
                           metrics, bar, latest_tag)
        next_version = ".".join(map(str, (*latest["version"][:2], latest["version"][2] + 1)))
        branch = f"release/v{next_version}"
        pulls = _pulls(cfg, branch)
        if pulls:
            pull = _pull(cfg, pulls[0]["number"])
            number = pull["number"]
            state = pull.get("mergeable_state")
            mergeable = pull.get("mergeable")
            if state == "behind":
                action = f"updated release PR #{number} from main"
            elif state == "dirty" or mergeable is False:
                action = f"closed conflicting release PR #{number}; next pass rebuilds it"
            elif mergeable is None or state == "unknown":
                return _finish(cfg, dry_run, _reason("waiting for GitHub mergeability", metrics, bar),
                               metrics, bar, latest_tag)
            elif not ci_passes(cfg, pull["head"]["sha"]):
                return _finish(cfg, dry_run, _reason("waiting for CI", metrics, bar),
                               metrics, bar, latest_tag)
            else:
                action = f"would squash-merge release PR #{number}"
            if dry_run:
                return _finish(cfg, True, _reason(action, metrics, bar), metrics, bar, latest_tag)
            if state == "behind":
                _gh(cfg, f"repos/{cfg.repo}/pulls/{number}/update-branch", "-X", "PUT")
                return _finish(cfg, False, _reason(action, metrics, bar), metrics, bar, latest_tag)
            if state == "dirty" or mergeable is False:
                _gh(cfg, f"repos/{cfg.repo}/pulls/{number}", "-X", "PATCH", "-f", "state=closed")
                try:
                    _gh(cfg, f"repos/{cfg.repo}/git/refs/heads/{branch}", "-X", "DELETE")
                except RuntimeError as exc:
                    if "404" not in str(exc) and "422" not in str(exc):
                        raise
                return _finish(cfg, False, _reason(action, metrics, bar), metrics, bar, latest_tag)
            try:
                merged = _merge_pull(cfg, number, f"Release {next_version}")
            except RuntimeError as exc:
                if "405" in str(exc) or "409" in str(exc):
                    return _finish(cfg, False, _reason(str(exc), metrics, bar),
                                   metrics, bar, latest_tag)
                raise
            if not merged.get("merged"):
                return _finish(cfg, False, _reason(
                    str(merged.get("message") or f"release PR #{number} merge refused"), metrics, bar),
                    metrics, bar, latest_tag)
            return _finish(cfg, False, _reason(f"merged release PR #{number}", metrics, bar),
                           metrics, bar, latest_tag)

        if not open_pr:
            return _finish(cfg, dry_run, _reason("no release PR open", metrics, bar), metrics, bar, latest_tag)

        if metrics["tickets"] < cfg.release_min_tickets:
            return _finish(cfg, dry_run, _reason(
                f"tickets {metrics['tickets']} below min_tickets {cfg.release_min_tickets}", metrics, bar),
                metrics, bar, latest_tag)
        if metrics["first_pass"] is None:
            return _finish(cfg, dry_run, _reason(
                f"no first-attempt evidence since {latest_tag}", metrics, bar), metrics, bar, latest_tag)
        if comparison_bar:
            for metric, direction in BAR:
                if metric not in metrics or metric not in comparison_bar:
                    continue
                worse = (metrics[metric] > comparison_bar[metric] if direction == "lower"
                         else metrics[metric] < comparison_bar[metric])
                if worse:
                    reason = _reason(
                        f"{metric} {metrics[metric]} is worse than "
                        f"{comparison_bar[metric]} ({direction} is better)",
                        metrics, comparison_bar)
                    if not dry_run:
                        _record_hold(cfg, reason, metrics, comparison_bar)
                    return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)
        if not ci_passes(cfg, head):
            return _finish(cfg, dry_run, _reason(
                f"CI statuses/check-runs are not all successful for {head}", metrics, bar),
                metrics, bar, latest_tag)
        if dry_run:
            return _finish(cfg, True, _reason(
                f"would open release PR for v{next_version} (release gate is not run by --dry-run)",
                metrics, bar), metrics, bar, latest_tag)

        cfg.factory.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="release-", dir=cfg.factory))
        worktree = temporary / "worktree"
        added = False
        try:
            _git(cfg.root, "worktree", "prune", check=False)
            _git(cfg.root, "worktree", "add", "--detach", str(worktree), head)
            added = True
            _update_release_files(cfg, worktree, latest_tag.removeprefix("v"), next_version)
            if not gate_passes(worktree):
                return _finish(cfg, False, _reason("factory gate --tier release failed", metrics, bar),
                               metrics, bar, latest_tag)
            _git(
                worktree,
                "add",
                cfg.release_version_file,
                "CHANGELOG.md",
                "README.md",
                "docs/index.html",
            )
            _git(worktree, "commit", "-m", f"Release {next_version}")
            pushed = _git(worktree, "push", "--force", "origin",
                          f"HEAD:refs/heads/{branch}", check=False)
            if pushed.returncode:
                detail = pushed.stderr.strip().splitlines()[-1] if pushed.stderr.strip() else "git push failed"
                return _finish(cfg, False, _reason(f"release branch push failed: {detail}", metrics, bar),
                               metrics, bar, latest_tag)
            body = (f"Automated patch release from {latest_tag}.\n\n"
                    f"Metrics: `{json.dumps(metrics, sort_keys=True)}`\n\n"
                    f"Bar: `{json.dumps(bar, sort_keys=True)}`")
            pull = _open_pull(cfg, branch, f"Release {next_version}", body)
            return _finish(cfg, False, f"opened release PR #{pull['number']} for v{next_version}",
                           metrics, bar, latest_tag)
        finally:
            if added:
                _git(cfg.root, "worktree", "remove", "--force", str(worktree), check=False)
            shutil.rmtree(temporary, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - the dispatcher must hold, journal, and retry next pass
        reason = _reason(f"release check failed: {type(exc).__name__}: {exc}", metrics, bar)
        return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)


def run(cfg: config.Config, dry_run: bool = False, open_pr: bool = True) -> str:
    """Advance odd experimental and even public release channels."""
    try:
        _git(cfg.root, "fetch", "--quiet", "--tags", "origin")
        if _latest(cfg, 0) is None:
            return _run_legacy(cfg, dry_run, open_pr, fetched=True)
        return _run_channels(cfg, dry_run, open_pr)
    except Exception as exc:  # noqa: BLE001 - dispatcher holds and retries the next pass
        reason = f"release check failed: {type(exc).__name__}: {exc}"
        if dry_run:
            print(json.dumps({
                "odd": {"verdict": reason},
                "even": {"verdict": reason},
            }, sort_keys=True))
        return reason


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory release", description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print odd and even channel metrics/actions as JSON; change nothing",
    )
    args = parser.parse_args(argv)
    cfg = config.load()
    reason = run(cfg, args.dry_run)
    if not args.dry_run:
        print(reason)
    return 0
