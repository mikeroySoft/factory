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

from factory import config, lifecycle

SEMVER = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
JSON_BAR = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
BAR = (("human_rate", "lower"), ("first_pass", "higher"))
WORST = {"human_rate": 1.0, "first_pass": 0.0}


def next_bar(bar: dict[str, float | int] | None, metrics: dict[str, float | int]) -> dict[str, float | int]:
    """The bar moves halfway from the old bar (or the worst value) toward the measured release,
    so it only rises, never jumps to one lucky window, and stays reachable."""
    old = bar or WORST
    return {**{metric: round((old[metric] + metrics[metric]) / 2, 6) for metric, _ in BAR},
            "tickets": metrics["tickets"]}


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
    humans = {row["ticket"] for row in rows
              if row.get("event") == "manage" and row.get("decision") == "HUMAN"
              and type(row.get("ticket")) is int}
    attempts = [row for row in rows if row.get("event") == "attempt" and row.get("attempt") == 1]
    return {
        "human_rate": round(min(len(humans) / max(len(tickets), 1), 1.0), 6),
        "first_pass": round(sum(row.get("gate") == "PASS" for row in attempts) / len(attempts), 6)
        if attempts else None,
        "tickets": len(tickets),
    }


def _latest(cfg: config.Config) -> dict[str, Any] | None:
    tags = _git(cfg.root, "tag", "--merged", f"origin/{cfg.main}", "--list", "v*").stdout.splitlines()
    remote = {
        ref.removesuffix("^{}").removeprefix("refs/tags/")
        for line in _git(cfg.root, "ls-remote", "--tags", "origin").stdout.splitlines()
        if len(line.split(maxsplit=1)) == 2
        for ref in [line.split(maxsplit=1)[1]]
    }
    versions = [(tuple(map(int, match.groups())), tag) for tag in tags
                if tag in remote and (match := SEMVER.fullmatch(tag))]
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


def _gh(cfg: config.Config, endpoint: str, *args: str) -> Any:
    proc = subprocess.run(["gh", "api", *args, endpoint], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"gh api {endpoint} failed")
    return json.loads(proc.stdout)

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


def _finish(cfg: config.Config, dry_run: bool, reason: str, metrics: dict[str, float | int | None] | None,
            bar: dict[str, float | int] | None, latest: str | None, *, released: bool = False) -> str:
    if dry_run:
        print(json.dumps({"metrics": metrics, "bar": bar, "latest": latest, "verdict": reason}, sort_keys=True))
    elif released:
        _record(cfg, "released", reason, metrics, bar, tag=latest)
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
    """Advance the release PR, merge, tag, and stable branch from remote state."""
    metrics: dict[str, float | int | None] | None = None
    bar: dict[str, float | int] | None = None
    latest_tag: str | None = None
    try:
        _git(cfg.root, "fetch", "--quiet", "--tags", "origin")
        latest = _latest(cfg)
        if latest is None:
            return _finish(cfg, dry_run, _reason("no semver release tag reachable from origin/main", None, None),
                           None, None, None)
        latest_tag = latest["tag"]
        bar = _bar(latest["message"])
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
                f"would tag {tag} and advance stable" if dry_run else f"released {tag}", metrics, bar)
            if dry_run:
                return _finish(cfg, True, reason, metrics, bar, latest_tag)
            message = (f"Release {version}: {metrics['tickets']} tickets, human rate "
                       f"{metrics['human_rate']}, first pass {metrics['first_pass']}.\n\n"
                       f"```json\n{json.dumps(next_bar(bar, metrics), separators=(',', ':'))}\n```")
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
                return _finish(cfg, False, f"released {tag}", metrics, bar, tag, released=True)
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

        if metrics["tickets"] < cfg.release_min_tickets:
            return _finish(cfg, dry_run, _reason(
                f"tickets {metrics['tickets']} below min_tickets {cfg.release_min_tickets}", metrics, bar),
                metrics, bar, latest_tag)
        if metrics["first_pass"] is None:
            return _finish(cfg, dry_run, _reason(
                f"no first-attempt evidence since {latest_tag}", metrics, bar), metrics, bar, latest_tag)
        if bar:
            for metric, direction in BAR:
                worse = metrics[metric] > bar[metric] if direction == "lower" else metrics[metric] < bar[metric]
                if worse:
                    return _finish(cfg, dry_run, _reason(
                        f"{metric} {metrics[metric]} is worse than {bar[metric]} ({direction} is better)",
                        metrics, bar), metrics, bar, latest_tag)
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
            _git(worktree, "add", cfg.release_version_file, "CHANGELOG.md")
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory release", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print metrics, bar, and verdict as JSON; change nothing")
    args = parser.parse_args(argv)
    cfg = config.load()
    reason = run(cfg, args.dry_run)
    if not args.dry_run:
        print(reason)
    return 0
