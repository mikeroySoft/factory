"""Repository-local operating mode, presets, and feature completion."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from factory import config, lifecycle

MODES = ("normal", "maintenance", "feature-drive")
_STATE = "mode.json"
_LOCK = "mode.lock"
_TARGET = re.compile(
    r"(?:(?:https://github\.com/)?(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"(?:/issues/|#))?(?P<number>#?[1-9][0-9]*)/?\Z"
)


def _normal() -> dict:
    return {"mode": "normal", "feature": None}


def _validate_state(value: object, path: Path) -> dict:
    if not isinstance(value, dict) or set(value) != {"mode", "feature"}:
        raise config.ConfigError(f"{path}: expected exactly mode and feature")
    mode, feature = value["mode"], value["feature"]
    if mode not in MODES:
        raise config.ConfigError(f"{path}: mode must be one of {', '.join(MODES)}")
    if mode == "feature-drive":
        if isinstance(feature, bool) or not isinstance(feature, int) or feature < 1:
            raise config.ConfigError(f"{path}: feature-drive needs a positive feature number")
    elif feature is not None:
        raise config.ConfigError(f"{path}: {mode} mode cannot name a feature")
    return {"mode": mode, "feature": feature}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _normal()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise config.ConfigError(f"{path}: {exc}") from exc
    return _validate_state(value, path)


def read(cfg: config.Config) -> dict:
    """Current local mode; a repository with no state is normal."""
    return _read(cfg.factory / _STATE)


def _atomic(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def set_mode(
    cfg: config.Config,
    mode: str,
    feature: int | None = None,
    *,
    who: str,
    expected: dict | None = None,
) -> dict:
    """Atomically change mode and append one audit row; equal and raced writes are no-ops."""
    target = _validate_state({"mode": mode, "feature": feature}, cfg.factory / _STATE)
    if not isinstance(who, str) or not who.strip():
        raise config.ConfigError("mode actor must be non-empty text")
    cfg.factory.mkdir(parents=True, exist_ok=True)
    with (cfg.factory / _LOCK).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read(cfg)
        if (expected is not None and current != expected) or current == target:
            return current
        _atomic(cfg.factory / _STATE, target)
        lifecycle.append(cfg.factory / "events.jsonl", {
            "event": "mode", "who": who, **target,
            "at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        })
        return target


def _journal_parents(cfg: config.Config) -> dict[int, int]:
    parents = {}
    for row in lifecycle.read_events(cfg.factory / "events.jsonl"):
        if (row.get("event") == "issue-created" and type(row.get("ticket")) is int
                and type(row.get("parent")) is int):
            parents[row["ticket"]] = row["parent"]
    return parents


def effective(
    cfg: config.Config,
    ticket: int | None = None,
    *,
    state: dict | None = None,
    parents: dict[int, int] | None = None,
) -> config.Config:
    """Return this mode's pace and ticket-local aggression without mutating ``cfg``."""
    state = read(cfg) if state is None else _validate_state(state, cfg.factory / _STATE)
    band = "low" if state["mode"] == "maintenance" else "normal"
    pace = cfg.throttle["pace"][band]
    max_active = pace.get("max_active", cfg.max_active)
    if cfg.manager_max_active_cap is not None:
        max_active = min(max_active, cfg.manager_max_active_cap)

    aggression_band = "normal"
    if state["mode"] == "feature-drive" and ticket is not None:
        if parents is None:
            parents = _journal_parents(cfg)
        from factory.schedule import feature_ticket

        if feature_ticket(ticket, state["feature"], parents):
            aggression_band = "high"
    values = cfg.throttle["aggression"][aggression_band]
    return replace(
        cfg,
        max_active=max_active,
        review_rounds=values.get("review_rounds", cfg.review_rounds),
        manager_rounds=values.get("manager_rounds", cfg.manager_rounds),
        max_attempts=values.get("max_attempts", cfg.max_attempts),
        budget_min=values.get("budget_min", cfg.budget_min),
    )


def _github(cfg: config.Config, endpoint: str, *, paginate: bool = False):
    command = ["gh", "api"]
    if paginate:
        command += ["--paginate", "--slurp"]
    command.append(endpoint)
    result = subprocess.run(
        command, cwd=cfg.root, text=True, capture_output=True, timeout=30, check=False,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"},
    )
    if result.returncode:
        raise config.ConfigError(result.stderr.strip() or f"GitHub read failed: {endpoint}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise config.ConfigError(f"GitHub returned invalid JSON for {endpoint}") from exc


def _issue(cfg: config.Config, number: int) -> dict:
    issue = _github(cfg, f"repos/{cfg.repo}/issues/{number}")
    if not isinstance(issue, dict) or issue.get("number") != number:
        raise config.ConfigError(f"GitHub returned an invalid issue for {cfg.repo}#{number}")
    return issue


def parse_target(cfg: config.Config, reference: str) -> int:
    match = _TARGET.fullmatch(reference.strip())
    if match is None:
        raise config.ConfigError(f"invalid feature issue reference: {reference!r}")
    repo = match["repo"]
    if repo and repo.casefold() != cfg.repo.casefold():
        raise config.ConfigError(
            f"feature issue belongs to {repo}, not configured repository {cfg.repo}"
        )
    return int(match["number"].lstrip("#"))


def validate_target(cfg: config.Config, reference: str) -> int:
    """Resolve a same-repository open ready ticket or initiative."""
    number = parse_target(cfg, reference)
    issue = _issue(cfg, number)
    if "pull_request" in issue:
        raise config.ConfigError(f"feature target #{number} is a pull request, not an issue")
    expected = f"https://github.com/{cfg.repo}/issues/{number}"
    url = issue.get("html_url")
    if not isinstance(url, str) or url.casefold() != expected.casefold():
        raise config.ConfigError(
            f"GitHub issue #{number} is not from configured repository {cfg.repo}"
        )
    labels = {
        label.get("name") for label in issue.get("labels", []) if isinstance(label, dict)
    }
    if str(issue.get("state", "")).upper() != "OPEN":
        raise config.ConfigError(f"feature issue #{number} must be open")
    if config.LABEL_AGENT not in labels and config.LABEL_INITIATIVE not in labels:
        raise config.ConfigError(
            f"feature issue #{number} needs {config.LABEL_AGENT!r} or {config.LABEL_INITIATIVE!r}"
        )
    return number


def _subissues(cfg: config.Config, number: int) -> list[int]:
    value = _github(
        cfg, f"repos/{cfg.repo}/issues/{number}/sub_issues?per_page=100", paginate=True
    )
    if not isinstance(value, list):
        raise config.ConfigError(f"GitHub returned invalid subissues for {cfg.repo}#{number}")
    items = [item for page in value for item in (page if isinstance(page, list) else [page])]
    expected_api = f"https://api.github.com/repos/{cfg.repo}".casefold()
    expected_web = f"https://github.com/{cfg.repo}/issues/".casefold()
    if any(
        not isinstance(item, dict)
        or type(item.get("number")) is not int
        or (
            str(item.get("repository_url") or "").casefold() != expected_api
            and not str(item.get("html_url") or "").casefold().startswith(expected_web)
        )
        for item in items
    ):
        raise config.ConfigError(
            f"GitHub returned invalid or foreign subissues for {cfg.repo}#{number}"
        )
    return [item["number"] for item in items]


def refresh(cfg: config.Config, *, dry_run: bool = False) -> dict:
    """Auto-normal after every native and journal-linked descendant is closed."""
    state = read(cfg)
    if state["mode"] != "feature-drive":
        return state
    feature = state["feature"]
    parents = _journal_parents(cfg)
    journal_children: dict[int, set[int]] = {}
    for child, parent in parents.items():
        journal_children.setdefault(parent, set()).add(child)

    queue, seen, states = [feature], set(), {}
    while queue:
        number = queue.pop()
        if number in seen:
            continue
        seen.add(number)
        issue = _issue(cfg, number)
        states[number] = str(issue.get("state", "")).upper()
        if states[number] not in {"OPEN", "CLOSED"}:
            raise config.ConfigError(f"GitHub returned invalid state for {cfg.repo}#{number}")
        children = set(_subissues(cfg, number)) | journal_children.get(number, set())
        queue.extend(children - seen)

    if not all(states[number] == "CLOSED" for number in seen):
        return state
    target = _normal()
    return target if dry_run else set_mode(cfg, "normal", who="auto", expected=state)


def _actor(cfg: config.Config) -> str:
    value = _github(cfg, "user")
    login = value.get("login") if isinstance(value, dict) else None
    if not isinstance(login, str) or not login.strip():
        raise config.ConfigError("GitHub did not return the authenticated login")
    return login


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="factory mode",
        description="Read or change this repository's local operating mode.",
    )
    parser.add_argument("mode", nargs="?", choices=MODES)
    parser.add_argument("feature", nargs="?")
    args = parser.parse_args(argv)
    cfg = config.load()
    if args.mode is None:
        if args.feature is not None:
            parser.error("a feature requires feature-drive")
        state = read(cfg)
    else:
        if args.mode == "feature-drive":
            if args.feature is None:
                parser.error("feature-drive requires an issue number or same-repository issue URL")
            feature = validate_target(cfg, args.feature)
        else:
            if args.feature is not None:
                parser.error(f"{args.mode} does not accept a feature")
            feature = None
        target = {"mode": args.mode, "feature": feature}
        state = target if read(cfg) == target else set_mode(
            cfg, args.mode, feature, who=_actor(cfg)
        )
    print(
        state["mode"] + (f" #{state['feature']}" if state["feature"] is not None else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
