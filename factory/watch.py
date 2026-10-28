"""`factory watch`: file engine failures and persistent host conditions in the engine repo.

One pass reads lifecycle failures, host-condition streaks, failed `factory-*` systemd
units, `dashboard --runtime-json` failures, and escalation codes repeating across
REPEAT_MIN tickets within REPEAT_DAYS days. It fingerprints each by reason, unit,
journal source or code, not by stage, reader code or install. Engine failures file as
`bug`; host conditions file as `bug,host` only after their uninterrupted streak reaches
`[watch].host_failure_minutes`. An open engine issue with the same fingerprint gets this
install's newest evidence as a comment, at most once per QUIET_PERIOD, and the install is
added to its `Observed by` list. Filings and comments are journaled as `watch` rows so the
next pass can skip, comment, close or refile.
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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev

from factory import config, dispatch, lifecycle, runtime_events, stats

LABELS = "bug,needs-triage"
MAX_ISSUES = 5
HOST_LABELS = f"bug,{config.LABEL_HOST},{config.LABEL_TRIAGE}"
QUIET_PERIOD = timedelta(hours=24)
CLOSABLE = {config.LABEL_TRIAGE, config.LABEL_AGENT}
MAX_ROWS = 20
MAX_LOG_LINES = 40
UNKNOWN_REASONS = ("check_timeout",)
# Host conditions are ignored until their current uninterrupted failure streak crosses
# `[watch].host_failure_minutes`.
HOST_REASONS = ("worker_exit:", "triage_endpoint_", "github_command_failed")
REPEAT_MIN = 3  # distinct tickets one escalation code must hit...
REPEAT_DAYS = 7  # ...inside this trailing window to count as a factory defect
# Ticket outcomes, not factory defects: `budget` means a ticket exhausted its configured
# overall runtime; `gate_failed` reports a ticket's project gate; `ci_failed` reports a
# PR's own failed checks; `pr_feedback` reports feedback on that PR; an already-fixed
# ticket may produce `no_change`; `review_revise` means review still requests changes
# after bounded review rounds, a manager FIX, or a merge-stage refresh/rebase.
OUTCOME_CODES = {"budget", "gate_failed", "ci_failed", "pr_feedback", "no_change", "review_revise"}
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
    if reason.startswith(HOST_REASONS):
        return False
    return row.get("outcome") == "mechanism_failure" or (
        row.get("outcome") == "unknown" and reason.startswith(UNKNOWN_REASONS)
    )


def _host_reason(row: dict) -> str | None:
    if row.get("event") != "lifecycle" or row.get("kind") != "exit":
        return None
    reason = str(row.get("reason") or "")
    return reason if reason.startswith(HOST_REASONS) else None


def _host_family(row: dict) -> str:
    return str(row.get("stage") or "").split("-", 1)[0]


def persistent_host_conditions(events: list[dict], minutes: int) -> dict[str, list[dict]]:
    """Current uninterrupted host-failure streaks old enough to file."""
    threshold = timedelta(minutes=minutes)
    streaks: dict[str, list[dict]] = {}
    for row in events:
        if reason := _host_reason(row):
            rows = streaks.setdefault(reason, [])
            if rows:
                # Idle successes can leave no journal row; a long gap is not continuity.
                try:
                    gap = datetime.fromisoformat(str(row["at"])) - datetime.fromisoformat(str(rows[-1]["at"]))
                    if gap > threshold:
                        rows.clear()
                except (KeyError, TypeError, ValueError):
                    rows.clear()
            rows.append(row)
            continue
        if (row.get("event") != "lifecycle" or row.get("kind") != "exit"
                or row.get("outcome") not in {"completed", "product_feedback"}
                or row.get("reason") == "no_tickets"):
            continue
        family = _host_family(row)
        for reason, rows in streaks.items():
            if rows and any(_host_family(failure) == family for failure in rows):
                streaks[reason] = []
    persistent = {}
    for reason, rows in streaks.items():
        if len(rows) < 2:
            continue
        try:
            duration = datetime.fromisoformat(str(rows[-1]["at"])) - datetime.fromisoformat(str(rows[0]["at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if duration >= threshold:
            persistent[reason] = rows
    return persistent


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

def _day(row: dict) -> date | None:
    try:
        return datetime.fromisoformat(str(row["at"])).date()
    except (KeyError, TypeError, ValueError):
        return None


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def metric_series(events: list[dict], start: date, end: date) -> dict[str, dict[str, dict]]:
    """Configured watch inputs as daily values plus the rows supporting each point."""
    days = _days(start, end)
    sparse_names = {
        "gate_failure_rate",
        "ready_for_human_rate",
        "claim_to_merge_hours",
        "rework_rounds",
    }
    names = set(sparse_names)
    failed = [
        row for row in events
        if row.get("event") == "lifecycle" and row.get("kind") == "result"
        and row.get("stage") == "gate-check" and row.get("passed") is False
        and isinstance(row.get("check"), str)
    ]
    names.update(f"gate_failures_by_check:{row['check']}" for row in failed)
    names.update(
        f"gate_failures_by_test:{test}"
        for row in failed for test in row.get("failed_tests", [])
        if isinstance(test, str) and test
    )
    risk_by_ticket = {
        row["ticket"]: row["tier"] for row in events
        if row.get("event") == "risk" and row.get("tier") in {"Low", "Medium", "High"}
        and row.get("ticket") is not None
    }
    names.update(f"revert_rate_by_tier:{tier}" for tier in set(risk_by_ticket.values()))
    by_day: dict[date, list[dict]] = {}
    for row in events:
        parsed = _day(row)
        if parsed is not None and start <= parsed <= end:
            by_day.setdefault(parsed, []).append(row)
    series = {
        name: {
            day.isoformat(): {"value": None if name in sparse_names else 0.0, "rows": []}
            for day in days
        }
        for name in names
    }
    claims: dict[object, dict] = {}
    reviews: dict[object, list[dict]] = {}
    for row in events:
        ticket = row.get("ticket")
        if row.get("event") == "claimed" and ticket is not None and _day(row) is not None:
            previous = claims.get(ticket)
            if previous is None or str(row["at"]) < str(previous["at"]):
                claims[ticket] = row
        elif (
            row.get("event") == "review" and ticket is not None
            and row.get("role") in {None, "primary"} and _day(row) is not None
        ):
            reviews.setdefault(ticket, []).append(row)
    merged_by_tier: dict[str, dict[object, dict]] = {}
    escaped_by_tier: dict[str, dict[object, dict]] = {}

    for day in days:
        key = day.isoformat()
        today = by_day.get(day, [])
        attempts = [row for row in today if row.get("event") == "attempt" and row.get("gate") in {"PASS", "FAIL"}]
        if attempts:
            series["gate_failure_rate"][key] = {
                "value": sum(row["gate"] == "FAIL" for row in attempts) / len(attempts),
                "rows": attempts,
            }
        terminal = [
            row for row in today
            if row.get("ticket") is not None and row.get("event") in {"merged", "escalate"}
        ]
        terminal_tickets = {row["ticket"] for row in terminal}
        if terminal_tickets:
            escalated = {row["ticket"] for row in terminal if row["event"] == "escalate"}
            series["ready_for_human_rate"][key] = {
                "value": len(escalated) / len(terminal_tickets),
                "rows": terminal,
            }
            rounds = []
            round_rows = list(terminal)
            for ticket in terminal_tickets:
                end_at = max(str(row["at"]) for row in terminal if row["ticket"] == ticket)
                ticket_reviews = [row for row in reviews.get(ticket, []) if str(row["at"]) <= end_at]
                rounds.append(stats.rework_rounds(ticket_reviews))
                round_rows.extend(ticket_reviews)
            series["rework_rounds"][key] = {
                "value": sum(rounds) / len(rounds),
                "rows": round_rows,
            }
        for row in today:
            ticket = row.get("ticket")
            tier = risk_by_ticket.get(ticket)
            if tier is None:
                continue
            if row.get("event") == "merged":
                merged_by_tier.setdefault(tier, {})[ticket] = row
            elif row.get("event") == "escape":
                escaped_by_tier.setdefault(tier, {})[ticket] = row
        for tier in set(risk_by_ticket.values()):
            merged = merged_by_tier.get(tier, {})
            if merged:
                escaped = {
                    ticket: row for ticket, row in escaped_by_tier.get(tier, {}).items()
                    if ticket in merged
                }
                series[f"revert_rate_by_tier:{tier}"][key] = {
                    "value": len(escaped) / len(merged),
                    "rows": [*merged.values(), *escaped.values()],
                }
        cycles, cycle_rows = [], []
        for merged in (row for row in today if row.get("event") == "merged"):
            claim = claims.get(merged.get("ticket"))
            if claim is None:
                continue
            try:
                hours = (
                    datetime.fromisoformat(str(merged["at"]))
                    - datetime.fromisoformat(str(claim["at"]))
                ).total_seconds() / 3600
            except (KeyError, TypeError, ValueError):
                continue
            if hours >= 0:
                cycles.append(hours)
                cycle_rows.extend((claim, merged))
        if cycles:
            series["claim_to_merge_hours"][key] = {
                "value": sum(cycles) / len(cycles),
                "rows": cycle_rows,
            }
        for row in today:
            if not (
                row.get("event") == "lifecycle" and row.get("kind") == "result"
                and row.get("stage") == "gate-check" and row.get("passed") is False
                and isinstance(row.get("check"), str)
            ):
                continue
            check = series[f"gate_failures_by_check:{row['check']}"][key]
            check["value"] += 1
            check["rows"].append(row)
            for test in row.get("failed_tests", []):
                name = f"gate_failures_by_test:{test}"
                if name in series:
                    series[name][key]["value"] += 1
                    series[name][key]["rows"].append(row)
    return series


def fired_rules(
    rules: tuple[config.BandRule, ...], *, mean: float, deviation: float, values: list[float],
) -> list[config.BandRule]:
    fired = []
    for rule in rules:
        recent = values[-rule.span:]
        if len(recent) < rule.span:
            continue
        if rule.kind == "sigma":
            distance = rule.sigma * deviation
            high = sum(value > mean + distance for value in recent)
            low = sum(value < mean - distance for value in recent)
        else:
            high = sum(value > mean for value in recent)
            low = sum(value < mean for value in recent)
        if max(high, low) >= rule.count:
            fired.append(rule)
    return fired


def _rule_name(rule: config.BandRule) -> str:
    if rule.kind == "sigma":
        return f"{rule.count} of {rule.span} beyond {rule.sigma:g} sigma"
    return f"{rule.count} of {rule.span} on one side of the mean"


def _history_days(events: list[dict], today: date) -> int:
    dates = [parsed for row in events if (parsed := _day(row)) is not None and parsed <= today]
    return max(0, (today - min(dates)).days) if dates else 0


def baseline_status(
    cfg: config.Config, events: list[dict], now: datetime | None = None,
) -> str | None:
    if not cfg.watch_bands:
        return None
    today = (now or datetime.now(UTC)).date()
    available = _history_days(events, today)
    missing = [band for band in cfg.watch_bands if available < band.window_days]
    if not missing:
        return None
    needed = max(band.window_days for band in missing)
    return f"insufficient baseline: {available} of {needed} trailing days available"


def _unchanged_test_rows(test_id: str, rows: list[dict]) -> list[dict]:
    module = None
    source = None
    if "::tests::" in test_id:
        module = test_id.split("::tests::", 1)[0].rsplit("::", 1)[-1]
    elif "::" in test_id:
        source = test_id.split("::", 1)[0]
        if not source.endswith(".py"):
            return []
    else:
        if "." not in test_id:
            return []
        parts = test_id.split(".")
        stop = next((index for index, part in enumerate(parts) if part[:1].isupper()), len(parts) - 1)
        source = "/".join(parts[:stop]) + ".py"
    source_parts = Path(source).parts if source else ()
    def changed(path: Path) -> bool:
        return (
            module in {path.stem, path.parent.name}
            if module
            else bool(source_parts) and path.parts[-len(source_parts):] == source_parts
        )
    return [
        row for row in rows
        if row.get("ticket") is not None
        and isinstance(row.get("changed_paths"), list)
        and all(isinstance(path, str) for path in row["changed_paths"])
        and not any(changed(Path(path)) for path in row["changed_paths"])
    ]


def band_signals(
    cfg: config.Config, events: list[dict], now: datetime | None = None,
) -> list[dict]:
    if not cfg.watch_bands:
        return []
    current = now or datetime.now(UTC)
    today = current.date()
    available = _history_days(events, today)
    start = today - timedelta(days=max(band.window_days for band in cfg.watch_bands))
    metrics = metric_series(events, start, today)
    pr_by_ticket = {
        row.get("ticket"): row.get("pr") for row in events
        if row.get("event") == "pr-opened" and isinstance(row.get("pr"), int)
    }
    alarms = []
    for band in cfg.watch_bands:
        if available < band.window_days:
            continue
        names = [
            name for name in metrics
            if name == band.metric or name.startswith(f"{band.metric}:")
        ]
        for name in sorted(names):
            points = list(metrics[name].values())[-(band.window_days + 1):]
            point = points[-1]
            if point["value"] is None:
                continue
            baseline = [point["value"] for point in points[:-1] if point["value"] is not None]
            if not baseline:
                continue
            average, deviation = mean(baseline), pstdev(baseline)
            values = [point["value"] for point in points if point["value"] is not None]
            rows = list(point["rows"])
            key = name.split(":", 1)[1] if ":" in name else name
            for rule in fired_rules(
                band.rules, mean=average, deviation=deviation, values=values,
            ):
                rule_rows = rows
                if band.metric == "gate_failures_by_test" and rule.tier == "act":
                    recent_rows = [
                        row for recent in points[-rule.span:] for row in recent["rows"]
                    ]
                    rule_rows = _unchanged_test_rows(key, recent_rows)
                    if len({row["ticket"] for row in rule_rows}) < rule.min_tickets:
                        continue
                tickets = {
                    row.get("ticket") for row in rule_rows if row.get("ticket") is not None
                }
                related = [
                    row for row in events
                    if row.get("ticket") in tickets
                    and row.get("event") in {"pr-opened", "escalate"}
                ]
                evidence_rows = rule_rows + [row for row in related if row not in rule_rows]
                prs = sorted({
                    pr_by_ticket[ticket] for ticket in tickets if ticket in pr_by_ticket
                })
                rule_name = _rule_name(rule)
                fp = f"band:{fingerprint(name, rule_name)}"
                pr_text = f"\nAffected PRs: {', '.join(f'PR #{pr}' for pr in prs)}." if prs else ""
                excerpt = (
                    f"Metric: {name}\nCurrent: {point['value']:g}\n"
                    f"Baseline ({band.window_days} days): mean {average:g}, "
                    f"standard deviation {deviation:g}\nRule: {rule_name}.{pr_text}"
                )
                alarms.append({
                    "fingerprint": fp,
                    "kind": "band",
                    "stage": "control-band",
                    "key": key,
                    "metric": name,
                    "rule": rule_name,
                    "tier": rule.tier,
                    "rows": evidence_rows,
                    "excerpt": excerpt,
                    "log": None,
                    "latest": max(
                        (str(row.get("at") or "") for row in evidence_rows),
                        default=datetime.combine(today, datetime.min.time(), UTC).isoformat(),
                    ),
                    "command": "factory watch --dry-run",
                    "prs": prs,
                })
    return alarms


def _diagnose(cfg: config.Config, signal: dict) -> None:
    from factory import triage

    triage.configure(cfg)
    summary = triage.call_llm([
        {
            "role": "system",
            "content": (
                "Diagnose this deterministic Factory metric alarm from the supplied evidence only. "
                "You have no tools and must not request or perform writes. Return a concise summary."
            ),
        },
        {"role": "user", "content": signal["excerpt"]},
    ]).strip()
    signal["diagnosis"] = summary
    signal["excerpt"] += f"\nDiagnosis: {summary}"




def signals(
    cfg: config.Config, events: list[dict], now: datetime | None = None,
) -> list[dict]:
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
    for reason, rows in persistent_host_conditions(events, cfg.watch_host_failure_minutes).items():
        key = fingerprint("host", reason)
        stages = dict.fromkeys(str(row.get("stage")) for row in rows)
        found[key] = {
            "fingerprint": key, "kind": "host", "stage": stages, "key": reason,
            "rows": rows, "excerpt": None, "log": None,
            "latest": max(str(row.get("at") or "") for row in rows),
            "command": "zcat -f .factory/events.jsonl* | grep -F "
            f"{shlex.quote(json.dumps({'reason': reason}, separators=(',', ':'))[1:-1])}",
        }
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
    for alarm in band_signals(cfg, events, now):
        found[alarm["fingerprint"]] = alarm
    for signal in found.values():
        signal["rows"] = signal["rows"][-MAX_ROWS:]
        if signal["kind"] in {"lifecycle", "host"}:
            signal["stage"] = ", ".join(signal["stage"])
    return list(found.values())


def title(signal: dict) -> str:
    if signal["kind"] == "band":
        return f"factory control-band alarm: {signal['metric']} ({signal['rule']})"[:200]
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
        (
            f"`factory watch` observed metric `{signal['metric']}` outside its configured control band "
            f"under `{signal['rule']}` at tier `{signal['tier']}`. "
            if signal["kind"] == "band" else
            "`factory watch` observed a persistent host condition from local evidence: "
            if signal["kind"] == "host" else
            "`factory watch` observed a runtime failure of the factory itself from local evidence: "
        ),
        (
            "Filed automatically from deterministic journal metrics; no human has looked at it yet."
            if signal["kind"] == "band" else
            f"signal `{signal['kind']}`, stage `{signal['stage']}`, `{signal['key']}`. "
            "Filed automatically; no human has looked at it yet."
        ),
        "",
        OBSERVED, f"- `{install}`", "",
        "## Evidence", *evidence(signal),
        "## Reproduce", f"In the `{install}` checkout:", "```", signal["command"], "factory watch --dry-run", "```",
        "## Expected behavior",
        (
            f"Metric `{signal['metric']}` returns inside its configured control band, and "
            f"`factory watch --dry-run` no longer reports fingerprint `{signal['fingerprint']}`."
            if signal["kind"] == "band" else
            f"The factory pass completes without `{signal['key']}` at stage `{signal['stage']}`, and "
            f"`factory watch --dry-run` no longer reports fingerprint `{signal['fingerprint']}`."
        ),
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


def run_pass(
    cfg: config.Config, dry_run: bool = False, now: datetime | None = None,
) -> list[dict]:
    """Log, diagnose, file/comment, and close configured signals."""
    events = lifecycle.read_events(dispatch.EVENTS)
    if any(band.metric == "revert_rate_by_tier" for band in cfg.watch_bands):
        stats.configure(cfg)
        try:
            events.extend(stats.escape_events(stats.escapes()))
        except (
            OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, SystemExit,
        ) as exc:
            dispatch.log(f"watch: escape attribution unavailable ({type(exc).__name__})")
    rows = [r for r in events if r.get("event") == "watch"]
    filings = {r["fingerprint"]: r for r in rows}
    # Issues this install opened; only the opener closes one (rows without `repo` predate routing).
    opened = {(r["fingerprint"], r["issue"], r.get("repo", dispatch.REPO))
              for r in rows if not r.get("commented") and not r.get("closed")}
    engine, install = cfg.watch_engine_repo, cfg.repo
    current = signals(cfg, events, now)
    observations = {
        row["fingerprint"]: row for row in events
        if row.get("event") == "watch-band" and isinstance(row.get("fingerprint"), str)
    }
    observed_signals = []
    actionable = []
    for signal in current:
        tier = signal.get("tier", "act")
        if tier == "act":
            actionable.append(signal)
            continue
        previous_observation = observations.get(signal["fingerprint"])
        if (
            previous_observation is not None
            and signal["latest"] <= previous_observation.get("latest", previous_observation["at"])
        ):
            observed_signals.append(signal)
            continue
        if tier == "diagnose":
            try:
                _diagnose(cfg, signal)
            except (SystemExit, OSError, ValueError, KeyError, TypeError) as exc:
                signal["diagnosis"] = f"unavailable ({type(exc).__name__})"
        dispatch.log(
            f"watch: {tier} {signal['fingerprint']} ({title(signal)})"
            + (f": {signal['diagnosis']}" if signal.get("diagnosis") else "")
        )
        if not dry_run:
            dispatch.record(
                "watch-band", fingerprint=signal["fingerprint"], tier=tier,
                latest=signal["latest"], diagnosis=signal.get("diagnosis"),
            )
        observed_signals.append(signal)
    latest_by_fingerprint = {
        signal["fingerprint"]: signal["latest"] for signal in actionable
    }
    listing = None
    filed = []
    for signal in actionable:
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
                "--label", HOST_LABELS if signal["kind"] == "host" else LABELS,
                "--body", body(signal, install),
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
    filed = observed_signals + filed

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
    if not args.dry_run:
        lifecycle.record_stage_success(dispatch.EVENTS, "watch")
    return 0
