"""`factory watch`: file the factory's own runtime failures as `bug` issues from local evidence.

One pass reads four local signals (lifecycle exit rows that name a mechanism failure,
failed `factory-*` systemd units, `dashboard --runtime-json` source errors, one escalation
code repeating across REPEAT_MIN tickets within REPEAT_DAYS days), fingerprints each by
reason, unit or journal source (not by stage or reader code: one defect is one issue), and
opens one `bug,needs-triage` issue per fingerprint not already covered by an open issue.
Filing is journaled as a `watch` row so the next pass can skip or refile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory import config, dispatch, lifecycle

LABELS = "bug,needs-triage"
MAX_ISSUES = 5
MAX_ROWS = 20
MAX_LOG_LINES = 40
UNKNOWN_REASONS = ("check_timeout",)
# Host conditions the dispatcher already retries or escalates and `factory doctor` reports:
# the worker process exiting, the local triage model, GitHub. Not factory defects.
ENVIRONMENT_REASONS = ("worker_exit:", "triage_endpoint_", "github_command_failed")
REPEAT_MIN = 3  # distinct tickets one escalation code must hit...
REPEAT_DAYS = 7  # ...inside this trailing window to count as a factory defect
# Ticket outcomes, not factory defects: a worker that finds its ticket already fixed commits
# nothing, and filing that would hand the next pass another already-fixed ticket.
OUTCOME_CODES = {"no_change"}


def fingerprint(kind: str, key: str) -> str:
    return hashlib.sha256(f"{kind}\n{key}".encode()).hexdigest()[:12]


def _failing(row: dict) -> bool:
    if row.get("event") != "lifecycle" or row.get("kind") != "exit":
        return False
    reason = row.get("reason") or ""
    if reason.startswith(ENVIRONMENT_REASONS):
        return False
    return row.get("outcome") == "mechanism_failure" or (
        row.get("outcome") == "unknown" and reason.startswith(UNKNOWN_REASONS)
    )


def failed_units(cfg: config.Config) -> list[tuple[str, str]]:
    """This repository's failed user units as (name, `systemctl status` excerpt).

    The pass unit runs `watch` from ExecStopPost=, so it is never listed as failed while
    watch looks; systemd reports its result through $SERVICE_RESULT instead.
    """
    try:
        listed = subprocess.run(
            ["systemctl", "--user", "list-units", "--failed", "--plain", "--no-legend", "factory-*"],
            capture_output=True, text=True, check=False,
        ).stdout
    except OSError:
        return []
    names = [line.split()[0] for line in listed.splitlines() if line.split()]
    result = os.environ.get("SERVICE_RESULT")
    own = ""
    if result and result != "success":
        names.append(f"{cfg.unit}.service")
        own = " ".join(f"{k}={os.environ.get(k, '')}" for k in ("SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS")) + "\n"
    units = []
    for name in names:
        if name in (f"{cfg.unit}.service", f"{cfg.unit}-dashboard.service"):
            status = subprocess.run(["systemctl", "--user", "status", "--no-pager", "-n", "20", name],
                                    capture_output=True, text=True, check=False)
            prefix = own if name == f"{cfg.unit}.service" else ""
            units.append((name, (prefix + status.stdout + status.stderr).strip()))
    return units


# Expected, not failures: unit mid-state, journal clipped, `dispatch.record` audit rows
# sharing events.jsonl with lifecycle rows (always reported as `unsupported_record`), and
# the passive runtime reader never enumerating /proc, so every open execution that launched
# a child reports `descendants_not_scanned`.
STEADY_CODES = {"transitioning", "byte_limit", "event_limit", "unsupported_record", "descendants_not_scanned"}


def runtime_errors(cfg: config.Config) -> list[dict]:
    """`errors` from one `factory dashboard --runtime-json`, minus steady states; a broken reader is itself an error."""
    proc = subprocess.run([sys.executable, "-P", "-m", "factory", "dashboard", "--runtime-json"],
                          cwd=cfg.root, capture_output=True, text=True, check=False)
    try:
        errors = json.loads(proc.stdout)["errors"] if proc.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        errors = None
    if errors is None:
        return [{"source": "dashboard", "scope": "runtime-json", "code": f"exit:{proc.returncode}"}]
    return [e for e in errors if isinstance(e, dict) and e.get("code") not in STEADY_CODES]


def _tail(path: str | None) -> str | None:
    if not path:
        return None
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    return "\n".join(lines[-MAX_LOG_LINES:])


def repeated_escalations(events: list[dict]) -> dict[str, list[dict]]:
    """Escalation rows per code that fired on >= REPEAT_MIN distinct tickets in the last REPEAT_DAYS days."""
    cutoff = (datetime.now(UTC) - timedelta(days=REPEAT_DAYS)).isoformat().replace("+00:00", "Z")
    by_code: dict[str, list[dict]] = {}
    for row in events:
        if (row.get("event") == "escalate" and "code" in row and row["code"] not in OUTCOME_CODES
                and str(row.get("at") or "") >= cutoff):
            by_code.setdefault(row["code"], []).append(row)  # rows without a code are `unknown`: never filed
    return {code: rows for code, rows in by_code.items()
            if len({r.get("ticket") for r in rows}) >= REPEAT_MIN}


def signals(cfg: config.Config, events: list[dict]) -> list[dict]:
    """Every observed failure, newest journal evidence first within each fingerprint."""
    found: dict[str, dict] = {}
    logs = {(r.get("ticket"), r.get("attempt")): r.get("log") for r in events if r.get("event") == "attempt"}
    for row in events:
        if not _failing(row):
            continue
        stage, reason = str(row.get("stage")), str(row.get("reason"))
        key = fingerprint("lifecycle", reason)
        signal = found.setdefault(key, {
            "fingerprint": key, "kind": "lifecycle", "stage": {}, "key": reason, "rows": [],
            "excerpt": None, "log": None, "latest": "",
            "command": "zcat -f .factory/events.jsonl* | grep -F "
            f"{shlex.quote(json.dumps({'reason': reason}, separators=(',', ':'))[1:-1])}",
        })
        signal["stage"][stage] = None  # ordered set of every stage the reason hit
        signal["rows"].append(row)
        signal["latest"] = max(signal["latest"], str(row.get("at") or ""))
        signal["log"] = logs.get((row.get("ticket"), row.get("attempt"))) or signal["log"]
    for name, status in failed_units(cfg):
        key = fingerprint("unit", name)
        found[key] = {"fingerprint": key, "kind": "unit", "stage": "systemd", "key": name, "rows": [],
                      "excerpt": status, "log": None, "latest": None,
                      "command": f"systemctl --user status {shlex.quote(name)}"}
    by_source: dict[str, list[dict]] = {}
    for error in runtime_errors(cfg):
        by_source.setdefault(str(error.get("source")), []).append(error)
    for source, errors in by_source.items():
        key = fingerprint("runtime", source)
        found[key] = {"fingerprint": key, "kind": "runtime", "stage": source, "rows": [],
                      "key": ", ".join(sorted({str(e.get("code")) for e in errors})),
                      "excerpt": json.dumps(errors, indent=2), "log": None, "latest": None,
                      "command": "factory dashboard --runtime-json | jq .errors"}
    for code, rows in repeated_escalations(events).items():
        key = f"escalation:{code}"
        found[key] = {"fingerprint": key, "kind": "escalation", "stage": "repeated", "key": code,
                      "rows": rows, "log": None,
                      "latest": max(str(r.get("at") or "") for r in rows),
                      "excerpt": "\n".join(f"- #{r.get('ticket')}: {r.get('reason')} ({r.get('packet')})" for r in rows),
                      "command": "zcat -f .factory/events.jsonl* | grep -F "
                      f"{shlex.quote(json.dumps({'code': code}, separators=(',', ':'))[1:-1])}"}
    for signal in found.values():
        signal["rows"] = signal["rows"][-MAX_ROWS:]
        if signal["kind"] == "lifecycle":
            signal["stage"] = ", ".join(signal["stage"])
    return list(found.values())


def title(signal: dict) -> str:
    return f"factory runtime failure: {signal['kind']} {signal['stage']} {signal['key']}"[:200]


def body(signal: dict) -> str:
    parts = [
        f"Fingerprint: {signal['fingerprint']}",
        "",
        (f"`factory watch` observed a runtime failure of the factory itself from local evidence: "
         f"signal `{signal['kind']}`, stage `{signal['stage']}`, `{signal['key']}`. "
         "Filed automatically; no human has looked at it yet."),
        "",
        "## Evidence",
    ]
    if signal["rows"]:
        parts += [f"### Journal rows ({len(signal['rows'])} newest)", "```json",
                  *(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in signal["rows"]), "```"]
    tail = _tail(signal["log"])
    if tail is not None:
        parts += [f"### Attempt log tail (`{signal['log']}`, last {MAX_LOG_LINES} lines)", "```", tail, "```"]
    if signal["excerpt"]:
        parts += ["### Observation", "```", signal["excerpt"], "```"]
    parts += [
        "## Reproduce", "```", signal["command"], "factory watch --dry-run", "```",
        "## Expected behavior",
        (f"The factory pass completes without `{signal['key']}` at stage `{signal['stage']}`, and "
         f"`factory watch --dry-run` no longer reports fingerprint `{signal['fingerprint']}`."),
    ]
    return "\n".join(parts) + "\n"


def _closed_before(issue: int, latest: str | None) -> bool:
    """True when the issue is closed and the newest evidence (None: still present) postdates the close."""
    state = dispatch.gh_json(["issue", "view", str(issue), "--repo", dispatch.REPO, "--json", "state,closedAt"])
    if state["state"].upper() == "OPEN":
        return False
    # ponytail: gh reports closedAt to the second; a recurrence inside that second refiles once.
    return latest is None or datetime.fromisoformat(latest) > datetime.fromisoformat(state["closedAt"])


def run_pass(cfg: config.Config, dry_run: bool = False) -> list[dict]:
    """File at most MAX_ISSUES new fingerprints; return the signals filed (or, dry, would be)."""
    events = lifecycle.read_events(dispatch.EVENTS)
    filings = {r["fingerprint"]: r for r in events if r.get("event") == "watch"}
    filed = []
    for signal in signals(cfg, events):
        if len(filed) >= MAX_ISSUES:
            dispatch.log(f"watch: deferring {signal['fingerprint']} to the next pass (cap {MAX_ISSUES})")
            break
        previous = filings.get(signal["fingerprint"])
        if previous is not None and signal["latest"] is not None and signal["latest"] <= previous["at"]:
            continue  # already filed; nothing newer than that filing
        if dry_run:
            verb = f"re-check #{previous['issue']} for" if previous else "would file"
            print(f"[watch] {verb} {signal['fingerprint']}: {title(signal)}")
            filed.append(signal)
            continue
        if previous is not None and not _closed_before(previous["issue"], signal["latest"]):
            continue
        url = dispatch.run([
            "gh", "issue", "create", "--repo", dispatch.REPO, "--title", title(signal),
            "--label", LABELS, "--body", body(signal),
        ]).stdout.strip().splitlines()[-1]
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        dispatch.record("watch", fingerprint=signal["fingerprint"], issue=number, at=lifecycle._now())
        dispatch.log(f"watch: filed #{number} for {signal['fingerprint']} ({title(signal)})")
        filed.append(signal)
    return filed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory watch", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be filed; no gh call, no journal row")
    args = parser.parse_args(argv)
    dispatch.configure(config.load())
    with nullcontext() if args.dry_run else lifecycle.scope(dispatch.EVENTS, "watch", lazy=True):
        run_pass(dispatch.cfg, args.dry_run)
    return 0
