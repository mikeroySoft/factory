"""`factory watch`: file the factory's own runtime failures as `bug` issues in the engine repo.

One pass reads four local signals (lifecycle exit rows that name a mechanism failure,
failed `factory-*` systemd units, `dashboard --runtime-json` failures, one escalation code
repeating across REPEAT_MIN tickets within REPEAT_DAYS days), fingerprints each by reason,
unit, journal source or code (not by stage, reader code or install: one defect is one
issue), and files it into `[watch].engine_repo` whatever repository it runs in: every signal
is an engine defect, and a downstream repository cannot fix engine code, so nothing is filed
locally. An open engine issue with the same fingerprint (filed by any install) gets this
install's newest evidence as a comment, at most once per QUIET_PERIOD, and the install is
added to its `Observed by` list. Filings and comments are journaled as `watch` rows so the
next pass can skip, comment or refile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory import config, dispatch, lifecycle, runtime_events

LABELS = "bug,needs-triage"
MAX_ISSUES = 5
QUIET_PERIOD = timedelta(hours=24)
CLOSABLE = {config.LABEL_TRIAGE, config.LABEL_AGENT}
MAX_ROWS = 20
MAX_LOG_LINES = 40
UNKNOWN_REASONS = ("check_timeout",)
# Host conditions the dispatcher already retries or escalates and `factory doctor` reports:
# the worker process exiting, the local triage model, GitHub. Not factory defects.
ENVIRONMENT_REASONS = ("worker_exit:", "triage_endpoint_", "github_command_failed")
REPEAT_MIN = 3  # distinct tickets one escalation code must hit...
REPEAT_DAYS = 7  # ...inside this trailing window to count as a factory defect
# Ticket outcomes, not factory defects: an already-fixed ticket may produce `no_change`;
# `review_revise` means review still requests changes after bounded review rounds, a
# manager FIX, or a merge-stage refresh/rebase.
OUTCOME_CODES = {"no_change", "review_revise"}
OBSERVED = "## Observed by"
COMMENT_PREFIX = "factory watch: observed in `"
MAX_OPEN = 200  # newest open `bug` issues of the engine repo searched for a fingerprint
FINGERPRINT = re.compile(r"Fingerprint: (\S+)")


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


def runtime_errors(cfg: config.Config) -> list[dict]:
    """Failure-severity errors of one `factory dashboard --runtime-json`; a broken reader is itself one.

    Coverage (reader bounds, clipped windows, host conditions: every code outside
    runtime_events.FAILURES) is never returned. Entries from the reader's `failures` carry
    its evidence pointers (journal `offset`, `execution_id`, `error`)."""
    proc = subprocess.run([sys.executable, "-P", "-m", "factory", "dashboard", "--runtime-json"],
                          cwd=cfg.root, capture_output=True, text=True, check=False)
    try:
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
        errors = None if data is None else [*data["errors"], *data.get("failures", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        errors = None
    if errors is None:
        stderr = "\n".join(proc.stderr.splitlines()[-MAX_LOG_LINES:])
        return [{"source": "dashboard", "scope": "runtime-json", "code": f"exit:{proc.returncode}", "stderr": stderr}]
    found = {}  # a `failures` entry replaces its bare `errors` twin
    for error in errors:
        if isinstance(error, dict) and runtime_events.failure(error):
            found[(error.get("source"), error.get("scope"), error.get("code"))] = error
    return list(found.values())


def _line_at(path: Path, offset: object) -> str | None:
    try:
        with path.open("rb") as handle:
            handle.seek(int(offset))
            return handle.readline(runtime_events.ROW_LIMIT).decode(errors="replace").rstrip("\n")
    except (OSError, ValueError, TypeError):
        return None


def _runtime_evidence(cfg: config.Config, errors: list[dict], events: list[dict]) -> tuple[str, list[dict]]:
    """(observation text, journal rows): the errors plus what they point at, never a bare code."""
    parts, rows = [json.dumps(errors, indent=2)], []
    for error in errors:
        if "offset" in error:
            line = _line_at(cfg.factory / "events.jsonl", error["offset"])
            parts.append(f"events.jsonl line at byte {error['offset']} ({error.get('code')}):\n{line}")
        if error.get("stderr"):
            parts.append(f"stderr:\n{error['stderr']}")
        if "execution_id" in error:
            rows += [r for r in events if r.get("execution_id") == error["execution_id"]]
    return "\n\n".join(parts), rows



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
        # Fingerprint the unit's role, not this install's unit name, so installs share one issue.
        key = fingerprint("unit", name.replace(cfg.unit, "factory-<repo>", 1))
        found[key] = {"fingerprint": key, "kind": "unit", "stage": "systemd", "key": name, "rows": [],
                      "excerpt": status, "log": None, "latest": None,
                      "command": f"systemctl --user status {shlex.quote(name)}"}
    by_source: dict[str, list[dict]] = {}
    for error in runtime_errors(cfg):
        by_source.setdefault(str(error.get("source")), []).append(error)
    for source, errors in by_source.items():
        key = fingerprint("runtime", source)
        excerpt, rows = _runtime_evidence(cfg, errors, events)
        found[key] = {"fingerprint": key, "kind": "runtime", "stage": source, "rows": rows,
                      "key": ", ".join(sorted({str(e.get("code")) for e in errors})),
                      "excerpt": excerpt, "log": None, "latest": None,
                      "command": "factory dashboard --runtime-json | jq '.errors, .failures'"}
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


def evidence(signal: dict) -> list[str]:
    parts = []
    if signal["rows"]:
        parts += [f"### Journal rows ({len(signal['rows'])} newest)", "```json",
                  *(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in signal["rows"]), "```"]
    tail = _tail(signal["log"])
    if tail is not None:
        parts += [f"### Attempt log tail (`{signal['log']}`, last {MAX_LOG_LINES} lines)", "```", tail, "```"]
    if signal["excerpt"]:
        parts += ["### Observation", "```", signal["excerpt"], "```"]
    return parts


def body(signal: dict, install: str) -> str:
    parts = [
        f"Fingerprint: {signal['fingerprint']}",
        "",
        (f"`factory watch` observed a runtime failure of the factory itself from local evidence: "
         f"signal `{signal['kind']}`, stage `{signal['stage']}`, `{signal['key']}`. "
         "Filed automatically; no human has looked at it yet."),
        "",
        OBSERVED, f"- `{install}`", "",
        "## Evidence", *evidence(signal),
        "## Reproduce", f"In the `{install}` checkout:", "```", signal["command"], "factory watch --dry-run", "```",
        "## Expected behavior",
        (f"The factory pass completes without `{signal['key']}` at stage `{signal['stage']}`, and "
         f"`factory watch --dry-run` no longer reports fingerprint `{signal['fingerprint']}`."),
    ]
    return "\n".join(parts) + "\n"


def comment(signal: dict, install: str) -> str:
    commit = (config.engine_commit() or "unknown")[:12]
    head = (f"{COMMENT_PREFIX}{install}` (newest evidence {signal['latest'] or 'failing now'}, "
            f"engine {commit}): fingerprint {signal['fingerprint']}, `{signal['key']}`.")
    return "\n".join([head, "", *evidence(signal)]) + "\n"


def observed(text: str, install: str) -> str | None:
    """`text` with `install` in its Observed by list; None when already listed."""
    line = f"- `{install}`"
    if line in text.splitlines():
        return None
    if f"{OBSERVED}\n" in text:
        return text.replace(f"{OBSERVED}\n", f"{OBSERVED}\n{line}\n", 1)
    return f"{text.rstrip()}\n\n{OBSERVED}\n{line}\n"


def open_filings(repo: str) -> dict[str, dict]:
    """Open `bug` issues of `repo` by `Fingerprint:` first line, newest first. The listing API,
    not search: a filing from another install a minute ago is already visible."""
    issues = dispatch.gh_json(["issue", "list", "--repo", repo, "--state", "open", "--label", "bug",
                               "--limit", str(MAX_OPEN), "--json", "number,body"])
    found: dict[str, dict] = {}
    for issue in issues:
        match = FINGERPRINT.match(issue.get("body") or "")
        if match:
            found.setdefault(match[1], issue)
    return found


def _age(at: str) -> timedelta:
    return datetime.now(UTC) - datetime.fromisoformat(at)


def run_pass(cfg: config.Config, dry_run: bool = False) -> list[dict]:
    """File/comment and close at most MAX_ISSUES fingerprints apiece; return signals filed or commented."""
    events = lifecycle.read_events(dispatch.EVENTS)
    rows = [r for r in events if r.get("event") == "watch"]
    filings = {r["fingerprint"]: r for r in rows}
    # Issues this install opened; only the opener closes one (rows without `repo` predate routing).
    opened = {(r["fingerprint"], r["issue"], r.get("repo", dispatch.REPO))
              for r in rows if not r.get("commented") and not r.get("closed")}
    engine, install = cfg.watch_engine_repo, cfg.repo
    current = signals(cfg, events)
    latest_by_fingerprint = {signal["fingerprint"]: signal["latest"] for signal in current}
    listing = None
    filed = []
    for signal in current:
        fp = signal["fingerprint"]
        if len(filed) >= MAX_ISSUES:
            dispatch.log(f"watch: deferring {fp} to the next pass (cap {MAX_ISSUES})")
            break
        previous = filings.get(fp)
        if previous is not None and signal["latest"] is not None and signal["latest"] <= previous["at"]:
            continue
        if dry_run:
            verb = f"re-check #{previous['issue']} for" if previous else "would file or comment"
            print(f"[watch] {verb} {fp} in {engine}: {title(signal)}")
            filed.append(signal)
            continue
        issue = None
        if previous is not None:
            repo = previous.get("repo", dispatch.REPO)
            state = dispatch.gh_json(["issue", "view", str(previous["issue"]), "--repo", repo,
                                      "--json", "state,closedAt,body"])
            if state["state"].upper() == "OPEN":
                if repo != engine:
                    continue  # a filing from before routing covers it until it closes
                issue = {"number": previous["issue"], "body": state.get("body")}
            # ponytail: gh reports closedAt to the second; a recurrence inside that second refiles once.
            elif signal["latest"] is not None and (
                    datetime.fromisoformat(signal["latest"]) <= datetime.fromisoformat(state["closedAt"])):
                continue  # closed by its fix or a human; nothing newer since
        if issue is None:
            listing = open_filings(engine) if listing is None else listing
            issue = listing.get(fp)
        if issue is None:
            url = dispatch.run([
                "gh", "issue", "create", "--repo", engine, "--title", title(signal),
                "--label", LABELS, "--body", body(signal, install),
            ]).stdout.strip().splitlines()[-1]
            number = int(url.rstrip("/").rsplit("/", 1)[-1])
            dispatch.record("watch", fingerprint=fp, issue=number, repo=engine,
                            latest=signal["latest"], at=lifecycle._now())
            dispatch.log(f"watch: filed {engine}#{number} for {fp} ({title(signal)})")
        else:
            if (previous is not None and (previous["issue"], previous.get("repo", dispatch.REPO)) == (issue["number"], engine)
                    and _age(previous["at"]) < QUIET_PERIOD):
                continue  # this install reported on it within the window
            number = issue["number"]
            dispatch.run(["gh", "issue", "comment", str(number), "--repo", engine, "--body", comment(signal, install)])
            # ponytail: read-modify-write; two installs editing in the same second can drop one name.
            listed = observed(issue["body"], install) if isinstance(issue.get("body"), str) else None
            if listed is not None:
                dispatch.run(["gh", "issue", "edit", str(number), "--repo", engine, "--body", listed])
            dispatch.record("watch", fingerprint=fp, issue=number, repo=engine, latest=signal["latest"],
                            commented=True, at=lifecycle._now())
            dispatch.log(f"watch: commented on {engine}#{number} for {fp}")
        filed.append(signal)

    closed = 0
    for fp, previous in filings.items():
        if closed >= MAX_ISSUES:
            break
        repo = previous.get("repo", dispatch.REPO)
        if previous.get("closed") or (fp, previous["issue"], repo) not in opened:
            continue
        # A signal stays in the journal window long after it stops happening, so judge by the
        # newest evidence, not presence; `latest is None` means it is failing right now.
        if fp in latest_by_fingerprint and latest_by_fingerprint[fp] is None:
            continue
        evidence_at = max(latest_by_fingerprint.get(fp) or "", previous.get("latest") or "", previous["at"])
        if _age(evidence_at) < QUIET_PERIOD:
            continue
        if dry_run:
            print(f"[watch] would close #{previous['issue']} in {repo} for {fp} if it is still open, unassigned, "
                  f"not with a human and quiet in every install (no newer evidence than {evidence_at})")
            closed += 1
            continue
        try:
            state = dispatch.gh_json([
                "issue", "view", str(previous["issue"]), "--repo", repo,
                "--json", "state,assignees,labels,comments",
            ])
            labels = {label["name"] for label in state.get("labels", [])}
            if state["state"].upper() != "OPEN":
                # Closed by its fix or a human: stop re-checking it every pass.
                dispatch.record("watch", fingerprint=fp, issue=previous["issue"], repo=repo, latest=evidence_at,
                                closed=True, at=lifecycle._now())
                continue
            if state.get("assignees") or not labels & CLOSABLE:
                continue  # claimed by a worker, or handed to a human
            if any(c.get("body", "").startswith(COMMENT_PREFIX) and _age(c["createdAt"]) < QUIET_PERIOD
                   for c in state.get("comments") or []):
                continue  # another install still reports it
            commit = (config.engine_commit() or "unknown")[:12]
            note = (
                f"factory watch: fingerprint {fp} has not recurred since {evidence_at} on engine "
                f"{commit}; closing. A recurrence reopens tracking with a new issue."
            )
            dispatch.run([
                "gh", "issue", "close", str(previous["issue"]), "--repo", repo,
                "--reason", "completed", "--comment", note,
            ])
            dispatch.record("watch", fingerprint=fp, issue=previous["issue"], repo=repo, latest=evidence_at,
                            closed=True, at=lifecycle._now())
            dispatch.log(f"watch: closed {repo}#{previous['issue']} for {fp}")
            closed += 1
        except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
            dispatch.log(f"watch: could not close {repo}#{previous['issue']} for {fp}: {exc}")
    return filed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory watch", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be filed or closed; no gh call, no journal row")
    args = parser.parse_args(argv)
    dispatch.configure(config.load())
    with nullcontext() if args.dry_run else lifecycle.scope(dispatch.EVENTS, "watch", lazy=True):
        run_pass(dispatch.cfg, args.dry_run)
    return 0
