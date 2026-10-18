"""Quality-ratcheted patch releases from the CI-green main branch."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factory import config, lifecycle, stats

SEMVER = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
JSON_BAR = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
BAR = (("human_rate", "lower"), ("first_pass", "higher"))


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=check)


def _at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def measure(cfg: config.Config, since: str | datetime) -> dict[str, float | int]:
    """Measure the journal window strictly after ``since``."""
    start = _at(since)
    rows = [row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
            if isinstance(row.get("at"), str) and _at(row["at"]) > start]
    tickets = {row["ticket"] for row in rows
               if row.get("event") == "merged" and type(row.get("ticket")) is int}
    humans = sum(row.get("event") == "manage" and row.get("decision") == "HUMAN" for row in rows)
    audit = stats.audit_by_ticket(cfg.factory / "events.jsonl", rows)
    claimed = {ticket: events for ticket, events in audit.items()
               if any(event.get("event") == "claimed" for event in events)}
    brief_rows = stats.brief_metrics(claimed)
    attempts = sum(row["attempts"] for row in brief_rows)
    passed = sum((row["first_pass"] or 0) * row["attempts"] for row in brief_rows)
    return {
        "human_rate": round(humans / max(len(tickets), 1), 6),
        "first_pass": round(passed / attempts, 6) if attempts else 0.0,
        "tickets": len(tickets),
    }


def _latest(cfg: config.Config) -> dict[str, Any] | None:
    tags = _git(cfg.root, "tag", "--merged", f"origin/{cfg.main}", "--list", "v*").stdout.splitlines()
    versions = [(tuple(map(int, match.groups())), tag) for tag in tags if (match := SEMVER.fullmatch(tag))]
    if not versions:
        return None
    version, tag = max(versions)
    kind = _git(cfg.root, "cat-file", "-t", tag).stdout.strip()
    message = _git(cfg.root, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}").stdout if kind == "tag" else ""
    date = _git(cfg.root, "for-each-ref", "--format=%(taggerdate:iso-strict)", f"refs/tags/{tag}").stdout.strip()
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
    if not isinstance(value, dict) or set(value) != {"human_rate", "first_pass", "tickets"}:
        raise ValueError("release tag JSON bar must contain human_rate, first_pass, and tickets")
    if (type(value["human_rate"]) not in (int, float)
            or type(value["first_pass"]) not in (int, float)
            or type(value["tickets"]) is not int):
        raise ValueError("release tag JSON bar has invalid metric types")
    return {"human_rate": float(value["human_rate"]), "first_pass": float(value["first_pass"]),
            "tickets": value["tickets"]}


def _gh(cfg: config.Config, endpoint: str) -> Any:
    proc = subprocess.run(["gh", "api", endpoint], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"gh api {endpoint} failed")
    return json.loads(proc.stdout)


def ci_passes(cfg: config.Config, sha: str) -> bool:
    """True when every reported status and check run for ``sha`` succeeded."""
    prefix = f"repos/{cfg.repo}/commits/{sha}"
    status = _gh(cfg, f"{prefix}/status?per_page=100")
    checks = _gh(cfg, f"{prefix}/check-runs?per_page=100")
    statuses = status.get("statuses", [])
    runs = checks.get("check_runs", [])
    return bool(statuses or runs) and (not statuses or status.get("state") == "success") and all(
        row.get("state") == "success" for row in statuses
    ) and all(row.get("status") == "completed" and row.get("conclusion") == "success" for row in runs)


def gate_passes(worktree: Path) -> bool:
    return subprocess.run(
        [sys.executable, "-m", "factory", "gate", "--tier", "release"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0


def _reason(condition: str, metrics: dict[str, float | int] | None,
            bar: dict[str, float | int] | None) -> str:
    return f"{condition}; measured={json.dumps(metrics, sort_keys=True)} bar={json.dumps(bar, sort_keys=True)}"


def _record(cfg: config.Config, outcome: str, reason: str, metrics: dict[str, float | int] | None,
            bar: dict[str, float | int] | None, **fields: object) -> None:
    from factory import dispatch

    if getattr(dispatch, "cfg", None) is not cfg:
        dispatch.configure(cfg)
    dispatch.record("release", outcome=outcome, reason=reason, metrics=metrics, bar=bar, **fields)


def _finish(cfg: config.Config, dry_run: bool, reason: str, metrics: dict[str, float | int] | None,
            bar: dict[str, float | int] | None, latest: str | None, *, released: bool = False) -> str:
    if dry_run:
        print(json.dumps({"metrics": metrics, "bar": bar, "latest": latest, "verdict": reason}, sort_keys=True))
    else:
        _record(cfg, "released" if released else "held", reason, metrics, bar,
                **({"tag": latest} if released else {}))
    return reason


def _update_release_files(cfg: config.Config, worktree: Path, old: str, new: str) -> None:
    version = worktree / cfg.release_version_file
    text = version.read_text()
    changed, count = re.subn(rf"(?<![0-9.]){re.escape(old)}(?![0-9.])", new, text, count=1)
    if count != 1:
        raise ValueError(f"{cfg.release_version_file} does not contain version {old}")
    version.write_text(changed)
    changelog = worktree / "CHANGELOG.md"
    text = changelog.read_text()
    heading = f"## {new} — {datetime.now(UTC).date().isoformat()}"
    changed, count = re.subn(r"(?m)^## Unreleased[ \t]*\n(?:\n)?", f"## Unreleased\n\n{heading}\n\n", text, count=1)
    if count != 1:
        raise ValueError("CHANGELOG.md has no ## Unreleased heading")
    changelog.write_text(changed)


def run(cfg: config.Config, dry_run: bool = False) -> str:
    """Release the next patch when the ticket window clears the previous quality bar."""
    metrics: dict[str, float | int] | None = None
    bar: dict[str, float | int] | None = None
    latest_tag: str | None = None
    try:
        latest = _latest(cfg)
        if latest is None:
            return _finish(cfg, dry_run, _reason("no semver release tag reachable from origin/main", None, None),
                           None, None, None)
        latest_tag = latest["tag"]
        metrics = measure(cfg, latest["date"])
        bar = _bar(latest["message"])
        head = _git(cfg.root, "rev-parse", f"origin/{cfg.main}").stdout.strip()
        if head == latest["sha"] or not _git(
                cfg.root, "rev-list", "--count", f"{latest_tag}..origin/{cfg.main}").stdout.strip().lstrip("0"):
            reason = _reason(f"no changes since {latest_tag}", metrics, bar)
            return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)
        if metrics["tickets"] < cfg.release_min_tickets:
            reason = _reason(f"tickets {metrics['tickets']} below min_tickets {cfg.release_min_tickets}", metrics, bar)
            return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)
        # ponytail: a lucky release window can freeze the bar; reset it by cutting a tag without a JSON block.
        if bar:
            for metric, direction in BAR:
                worse = metrics[metric] > bar[metric] if direction == "lower" else metrics[metric] < bar[metric]
                if worse:
                    reason = _reason(
                        f"{metric} {metrics[metric]} is worse than {bar[metric]} ({direction} is better)", metrics, bar)
                    return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)
        if not ci_passes(cfg, head):
            reason = _reason(f"CI statuses/check-runs are not all successful for {head}", metrics, bar)
            return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)
        new_version = ".".join(map(str, (*latest["version"][:2], latest["version"][2] + 1)))
        new_tag = f"v{new_version}"
        if dry_run:
            reason = _reason(f"would release {new_tag} (release gate is not run by --dry-run)", metrics, bar)
            return _finish(cfg, True, reason, metrics, bar, latest_tag)

        cfg.factory.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="release-", dir=cfg.factory))
        worktree = temporary / "worktree"
        added = created_tag = pushed = False
        try:
            _git(cfg.root, "worktree", "add", "--detach", str(worktree), head)
            added = True
            _update_release_files(cfg, worktree, latest_tag.removeprefix("v"), new_version)
            if not gate_passes(worktree):
                reason = _reason("factory gate --tier release failed", metrics, bar)
                return _finish(cfg, False, reason, metrics, bar, latest_tag)
            _git(worktree, "add", cfg.release_version_file, "CHANGELOG.md")
            _git(worktree, "commit", "-m", f"Release {new_version}")
            message = (f"Release {new_version}: {metrics['tickets']} tickets, human rate "
                       f"{metrics['human_rate']}, first pass {metrics['first_pass']}.\n\n"
                       f"```json\n{json.dumps(metrics, separators=(',', ':'))}\n```")
            _git(worktree, "tag", "-a", new_tag, "-m", message)
            created_tag = True
            pushed_result = _git(worktree, "push", "--atomic", "origin",
                                 f"HEAD:refs/heads/{cfg.main}", new_tag, "HEAD:refs/heads/stable", check=False)
            if pushed_result.returncode:
                detail = pushed_result.stderr.strip().splitlines()[-1] if pushed_result.stderr.strip() else "git push failed"
                reason = _reason(f"atomic push failed: {detail}", metrics, bar)
                return _finish(cfg, False, reason, metrics, bar, latest_tag)
            pushed = True
            reason = _reason(f"released {new_tag}", metrics, bar)
            return _finish(cfg, False, reason, metrics, bar, new_tag, released=True)
        finally:
            if created_tag and not pushed:
                _git(cfg.root, "tag", "-d", new_tag, check=False)
            if added:
                _git(cfg.root, "worktree", "remove", "--force", str(worktree), check=False)
            shutil.rmtree(temporary, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - the dispatcher must hold, journal, and retry next pass
        reason = _reason(f"release check failed: {type(exc).__name__}: {exc}", metrics, bar)
        return _finish(cfg, dry_run, reason, metrics, bar, latest_tag)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory release", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print metrics, bar, and verdict as JSON; change nothing")
    args = parser.parse_args(argv)
    cfg = config.load()
    reason = run(cfg, args.dry_run)
    if not args.dry_run:
        print(reason)
    return 0
