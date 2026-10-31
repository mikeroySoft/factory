"""Print read-only factory ticket metrics from GitHub and events.jsonl."""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import pstdev
from typing import Any

from factory import config, feedback, lifecycle, onboard, risk
from factory.config import LABEL_AGENT, LABEL_HUMAN, USAGE_FIELDS, Config

cfg: Config
REPOSITORY = ""
AGENT_BRANCH = re.compile(r"agent/(\d+)$")
AGENT_SUBJECT = re.compile(r"agent/(\d+):")
REGRESSED_BY = re.compile(r"^[ \t]*Regressed-by:[ \t]*#(\d+)\b", re.MULTILINE)
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? ")
WORKERS = 8  # concurrent per-ticket gh reads
NOT_REPORTED = "n/a (not reported)"


def configure(c: Config) -> None:
    global cfg, REPOSITORY
    cfg = c
    REPOSITORY = cfg.repo


def gh(*args: str) -> Any:
    """Run gh and decode its JSON output."""
    completed = subprocess.run(
        ["gh", *args, *([] if args[0] == "api" else ["--repo", REPOSITORY])],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        print(completed.stderr.strip() or "gh command failed", file=sys.stderr)
        raise SystemExit(completed.returncode)
    return json.loads(completed.stdout)


def graphql(*, query: str, variables: dict) -> dict:
    """feedback's read(query=..., variables=...) transport over `gh api graphql`."""
    return gh("api", "graphql", "-f", f"query={query}",
              *(arg for key, value in variables.items() for arg in ("-f", f"{key}={value}")))["data"]


@functools.cache
def _login(env: tuple[tuple[str, str], ...]) -> str:
    return onboard.gh_login({**os.environ, **dict(env)})


def dispatcher_login(c: Config) -> str:
    """The dispatcher's authenticated login under `[install].dispatch_env`; "" when unset,
    because the factory then shares the human's account and nothing is attributable."""
    if not c.install.get("dispatch_env"):
        return ""
    return _login(tuple({**c.install["env"], **c.install["dispatch_env"]}.items()))


def count_comments(comments: list[dict[str, Any]], text: str) -> int:
    return sum(text in comment.get("body", "") for comment in comments)


def review_findings(events: list[dict]) -> list[dict[str, int]]:
    """Machine-readable severity counts in review-round order."""
    rounds = []
    for event in events:
        counts = event.get("counts")
        if event.get("event") != "review-result" or not isinstance(counts, dict):
            continue
        if all(type(counts.get(key)) is int and counts[key] >= 0
               for key in ("important", "nit", "pre_existing")):
            rounds.append({key: counts[key] for key in ("important", "nit", "pre_existing")})
    return rounds

def _important_finding_keys(event: dict) -> set[tuple[str, tuple[tuple[str, int], ...]]] | None:
    findings = event.get("findings")
    if not isinstance(findings, str):
        return None
    blocks = re.findall(r"```json[ \t]*\r?\n(.*?)\r?\n```", findings, re.DOTALL)
    if len(blocks) != 1:
        return None
    try:
        tally = json.loads(blocks[0])
    except (json.JSONDecodeError, TypeError):
        return None
    rows = tally.get("findings") if isinstance(tally, dict) else None
    if not isinstance(rows, list):
        return None
    keys = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("severity") != "important":
            continue
        instances = row.get("instances")
        if not isinstance(row.get("class"), str) or not isinstance(instances, list):
            return None
        locations = tuple(sorted(
            (instance["path"], instance["line"])
            for instance in instances
            if isinstance(instance, dict)
            and isinstance(instance.get("path"), str)
            and type(instance.get("line")) is int
        ))
        if not locations:
            return None
        keys.add((row["class"], locations))
    count = event["counts"].get("important")
    return keys if type(count) is int and len(keys) == count else None


def review_convergence(events: list[dict]) -> dict[str, Any]:
    """Accepted ticket-review convergence metrics in ticket-round order."""
    rounds = []
    numbered = {}
    for event in events:
        counts = event.get("counts")
        if (
            event.get("event") != "review-result"
            or not isinstance(counts, dict)
            or type(counts.get("important")) is not int
            or counts["important"] < 0
            or event.get("accepted") is False
            or event.get("accepted") is None and event.get("reason") is not None
        ):
            continue
        number = event.get("review_round")
        if type(number) is int:
            current = numbered.get(number)
        elif event.get("role") == "second" and rounds:
            current = rounds[-1]
        else:
            current = None
        if current is None:
            current = {
                "approved": True,
                "repeated": 0,
                "important": set(),
                "unkeyed_important": 0,
            }
            rounds.append(current)
            if type(number) is int:
                numbered[number] = current
        current["approved"] &= event.get("verdict") == "APPROVE"
        repeated = event.get("repeated_dropped", 0)
        if type(repeated) is int and repeated >= 0:
            current["repeated"] += repeated
        keys = _important_finding_keys(event)
        if keys is None:
            current["unkeyed_important"] += counts["important"]
        else:
            current["important"].update(keys)
    approved = next(
        (index for index, review_round in enumerate(rounds, 1) if review_round["approved"]),
        None,
    )
    return {
        "rounds_to_pass": approved,
        "repeated_findings_dropped": sum(review_round["repeated"] for review_round in rounds),
        "important_findings_per_round": [
            len(review_round["important"]) + review_round["unkeyed_important"]
            for review_round in rounds
        ],
    }


def issue(number: int) -> dict[str, Any]:
    return gh(
        "issue",
        "view",
        str(number),
        "--json",
        "number,title,state,createdAt,closedAt,comments",
    )


def audit_by_ticket(path: Path, rows: list[dict] | None = None) -> dict[int, list[dict]]:
    """Group committed traces, reusing a caller's coherent journal snapshot."""
    result: dict[int, list[dict]] = {}
    for row in lifecycle.read_events(path) if rows is None else rows:
        if isinstance(row.get("ticket"), int):
            result.setdefault(row["ticket"], []).append(row)
    return result

def priced(event: dict, prices: dict[str, dict]) -> float | None:
    """Dollars for one usage event: its reported cost, else its model's price table entry, else None.

    Cached tokens (or the cache hit rate's share of prompt tokens) bill at `cached` when the entry has it.
    """
    def num(key: str) -> float | None:
        return event[key] if type(event.get(key)) in (int, float) else None

    model = event.get("model")
    price = prices.get(model) if isinstance(model, str) else None
    prompt, completion, cached, rate = map(num, ("prompt_tokens", "completion_tokens", "cached_tokens", "prefix_cache_hit_rate"))
    if num("cost") is not None or price is None or prompt is None and completion is None:
        return num("cost")
    prompt, completion = prompt or 0, completion or 0
    cached = 0 if "cached" not in price else cached if cached is not None else prompt * (rate or 0)
    return ((prompt - cached) * price["prompt"] + cached * price.get("cached", 0)
            + completion * price["completion"]) / 1_000_000


def llm_usage(events: list[dict], prices: dict[str, dict] | None = None) -> dict[str, dict]:
    """Sum tokens and dollars by stage plus a ticket `total`; None = no event reported it.

    Dollars come from a reported cost or, for token-only events, the `[prices]` table.
    """
    usage: dict[str, dict] = {}
    for event in events:
        stage = event.get("stage")
        if event.get("event") != "llm-usage" or stage not in {"triage", "review", "worker"}:
            continue
        values = {**{key: event.get(key) for key in USAGE_FIELDS}, "cost": priced(event, prices or {})}
        for totals in (usage.setdefault(stage, dict.fromkeys(USAGE_FIELDS)),
                       usage.setdefault("total", dict.fromkeys(USAGE_FIELDS))):
            for key, value in values.items():
                if type(value) in (int, float):
                    totals[key] = round((totals[key] or 0) + value, 4)
        rate = event.get("prefix_cache_hit_rate")
        if type(rate) in (int, float):
            usage[stage].setdefault("prefix_cache_hit_rates", []).append(rate)
    return usage


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_rows(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Rows whose timestamps fall inside the inclusive UTC window."""
    return [row for row in rows if (at := _timestamp(row.get("at"))) is not None and start <= at <= end]
SKILL_AFTER_CALLS = 10


def skill_coverage(rows: list[dict]) -> float | None:
    attempts = [row for row in rows if row.get("event") == "attempt"]
    return (sum(isinstance(row.get("transcript"), str) for row in attempts) / len(attempts)
            if attempts else None)


def _session_errors(rows: list[dict]) -> dict[str, list[bool]]:
    sessions = {}
    for row in rows:
        errors = row.get("call_errors")
        session = row.get("transcript")
        if row.get("event") == "score" and isinstance(session, str) and isinstance(errors, list):
            sessions[session] = [bool(value) for value in errors]
    return sessions


def _rate(samples: list[list[bool]]) -> float | None:
    count = sum(len(sample) for sample in samples)
    return sum(sum(sample) for sample in samples) / count if count else None


def _skill_samples(
    loads: list[dict], errors: dict[str, list[bool]], loaded_sessions: set[str],
) -> tuple[list[list[bool]], list[list[bool]]]:
    positioned = [(row, row["call_index"]) for row in loads if type(row.get("call_index")) is int]
    loaded = [errors.get(row["session"], [])[index + 1:index + 1 + SKILL_AFTER_CALLS]
              for row, index in positioned]
    controls = [values for session, values in errors.items() if session not in loaded_sessions]
    control = [values[index + 1:index + 1 + SKILL_AFTER_CALLS]
               for _, index in positioned for values in controls]
    return loaded, control


def _skill_rates(
    session_count: int, loaded: list[list[bool]], controls: list[list[bool]],
) -> tuple[float | None, float | None, float | None, float | None]:
    if session_count < 10:
        return None, None, None, None
    after, baseline = _rate(loaded), _rate(controls)
    if after is None or baseline is None:
        return None, None, None, None
    loaded_rates = [_rate([sample]) for sample in loaded if sample]
    control_rates = [_rate([sample]) for sample in controls if sample]
    noise = max(pstdev(loaded_rates), pstdev(control_rates))
    return after, baseline, after - baseline, noise


def _skill_metric(name: str, loads: list[dict], errors: dict[str, list[bool]]) -> dict:
    sessions = {row["session"] for row in loads}
    tickets = {row.get("ticket") for row in loads if type(row.get("ticket")) is int}
    tokens = max((row.get("tokens", 0) for row in loads
                  if type(row.get("tokens")) is int), default=0)
    loaded, controls = _skill_samples(loads, errors, sessions)
    after, baseline, excess, noise = _skill_rates(len(sessions), loaded, controls)
    failure_errors = list(dict.fromkeys(
        row["error"] for row in loads if not row.get("ok") and isinstance(row.get("error"), str)
    ))
    return {
        "skill": name,
        "loads": len(loads),
        "sessions": len(sessions),
        "tickets": len(tickets),
        "explicit_share": sum(bool(row.get("explicit")) for row in loads) / len(loads),
        "reloads_per_session": (len(loads) - len(sessions)) / len(sessions),
        "partial_share": sum(bool(row.get("partial")) for row in loads) / len(loads),
        "load_failures": sum(not row.get("ok") for row in loads),
        "load_failure_errors": failure_errors,
        "tokens": tokens,
        "context_spent": tokens * len(loads),
        "after_load_error_rate": after,
        "position_matched_baseline_error_rate": baseline,
        "error_rate_excess": excess,
        "noise_margin": noise,
    }


def skill_metrics(rows: list[dict]) -> list[dict]:
    """Pure per-skill aggregation over journal rows produced by `factory score`."""
    loads = [row for row in rows if row.get("event") == "skill-load"
             and isinstance(row.get("skill"), str) and isinstance(row.get("session"), str)]
    by_skill: dict[str, list[dict]] = {}
    for row in loads:
        by_skill.setdefault(row["skill"], []).append(row)
    errors = _session_errors(rows)
    result = [_skill_metric(name, skill_loads, errors) for name, skill_loads in by_skill.items()]
    return sorted(result, key=lambda row: (-row["context_spent"], row["skill"]))


def print_skills(rows: list[dict], coverage: float | None) -> None:
    print("\nSkills")
    if coverage is not None:
        print(f"coverage: {coverage:.1%}")
    if not rows:
        print("no skill loads recorded")
        return
    selected = rows[:5]
    selected += [row for row in rows[5:] if row["load_failures"]]
    print("skill                 loads  sessions  context  failures  after/baseline")
    for row in selected:
        rate = ("n<10" if row["sessions"] < 10 else
                f"{row['after_load_error_rate']:.1%}/{row['position_matched_baseline_error_rate']:.1%}"
                if row["after_load_error_rate"] is not None else "n/a")
        print(f"{row['skill']:<21} {row['loads']:>5}  {row['sessions']:>8}  "
              f"{row['context_spent']:>7}  {row['load_failures']:>8}  {rate}")
        for error in row["load_failure_errors"]:
            print(f"  load failure: {error}")




def weekly_metrics(
    rows: list[dict], start: datetime, end: datetime, local_model: str | None,
) -> dict[str, Any]:
    """Aggregate one already-windowed journal snapshot without external reads."""
    window = {"start": _stamp(start), "end": _stamp(end)}
    merged = {row["ticket"] for row in rows
              if row.get("event") == "merged" and type(row.get("ticket")) is int}
    human_decisions = {
        ("decision", row["decision_id"]) if row.get("decision_id") else ("row", index)
        for index, row in enumerate(rows)
        if row.get("event") == "human-decision"
    }
    touches = len(human_decisions) + sum(
        row.get("event") == "manage" and row.get("decision") == "HUMAN"
        for row in rows
    )
    escalations = [row for row in rows if row.get("event") == "escalate"]
    escalation_codes = Counter(row.get("code") or "unknown" for row in escalations)
    review_revises = escalation_codes["review_revise"]
    attempts = [row for row in rows if row.get("event") == "attempt"
                and row.get("attempt") == 1 and row.get("gate") in {"PASS", "FAIL"}]
    first_passes = sum(row["gate"] == "PASS" for row in attempts)
    tokens: dict[str, dict[str, dict[str, int | bool]]] = {}
    local_tokens = reported_tokens = usage_missing = 0
    for row in rows:
        if row.get("event") != "llm-usage":
            continue
        model = row.get("model")
        if not isinstance(model, str) or not model:
            usage_missing += 1
            continue
        stage = row.get("stage")
        if not isinstance(stage, str) or not stage:
            continue
        prompt = row.get("prompt_tokens") if type(row.get("prompt_tokens")) is int else 0
        completion = row.get("completion_tokens") if type(row.get("completion_tokens")) is int else 0
        prompt, completion = max(prompt, 0), max(completion, 0)
        local = row["local"] if type(row.get("local")) is bool else model == local_model
        by_model = tokens.setdefault(stage, {})
        if model not in by_model:
            by_model[model] = {"prompt": 0, "completion": 0, "calls": 0, "local": local}
        totals = by_model[model]
        totals["prompt"] += prompt
        totals["completion"] += completion
        totals["calls"] += 1
        totals["local"] = bool(totals["local"] and local)
        reported_tokens += prompt + completion
        if local:
            local_tokens += prompt + completion
    result = {
        "window": window,
        "merges": len(merged),
        "human_touches": touches,
        "human_touches_per_merge": touches / len(merged) if merged else None,
        "escalations_7d": len(escalations),
        "escalations_by_code": dict(escalation_codes.most_common()),
        "review_nonconvergence_share": review_revises / len(escalations) if escalations else None,
        "review_nonconvergence_share_numerator": review_revises,
        "review_nonconvergence_share_denominator": len(escalations),
        "review_nonconvergence_share_window": window,
        "first_pass": first_passes / len(attempts) if attempts else None,
        "first_pass_numerator": first_passes,
        "first_pass_denominator": len(attempts),
        "first_pass_window": window,
        "tokens": tokens,
        "local_token_share": round(local_tokens / reported_tokens, 6) if reported_tokens else None,
        "usage_missing": usage_missing,
    }
    if any(row.get("event") in {"skill-load", "skill-coverage"} for row in rows):
        result.update(skills=skill_metrics(rows), skill_coverage=skill_coverage(rows))
    return result


def stuck_over_days(now: datetime, days: int = 7) -> list[dict] | str:
    """One bounded GitHub read for stale open ready-for-human issues."""
    try:
        completed = subprocess.run(
            ["gh", "issue", "list", "--repo", REPOSITORY, "--state", "open",
             "--label", LABEL_HUMAN, "--limit", "1000", "--json", "number,title,url,updatedAt"],
            text=True, capture_output=True, check=False, timeout=10,
        )
        issues = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return "unavailable"
    if not isinstance(issues, list):
        return "unavailable"
    cutoff = now - timedelta(days=days)
    stale = []
    for issue in issues:
        updated = _timestamp(issue.get("updatedAt")) if isinstance(issue, dict) else None
        if updated is None:
            return "unavailable"
        if updated <= cutoff:
            stale.append(issue)
    return stale




def worker_metrics(audit: dict[int, list[dict]], workers: dict[str, list[str]]) -> list[dict]:
    """Claim attribution, using the recorded worker profile when available.

    First-pass denominators are attempt-1 gate outcomes per claim. All attempts,
    reported worker tokens and costs count; unclaimed history does not. Older
    claims fall back to their labels.
    """
    rows = {key: {"worker": key, "first_pass": None, "attempts": 0, "cost": None} for key in workers}
    outcomes: dict[str, list[bool]] = {key: [] for key in workers}
    for events in audit.values():
        worker = None
        for event in events:
            if event.get("event") == "claimed":
                labels = event.get("labels", [])
                recorded = event.get("worker")
                worker = recorded if recorded in workers else next(
                    (key for key in workers if key in labels), "default"
                )
            elif event.get("event") == "llm-usage" and event.get("stage") == "worker" and worker is not None:
                row = rows[worker]
                for field in USAGE_FIELDS[:-1]:
                    value = event.get(field)
                    if type(value) in (int, float):
                        row[field] = row.get(field, 0) + value
            elif event.get("event") == "attempt" and worker is not None:
                row = rows[worker]
                row["attempts"] += 1
                cost = event.get("cost")
                if type(cost) in (int, float):
                    row["cost"] = round((row["cost"] or 0) + cost, 4)
                if event.get("attempt") == 1 and event.get("gate") in ("PASS", "FAIL"):
                    outcomes[worker].append(event["gate"] == "PASS")
    for worker, gates in outcomes.items():
        if gates:
            rows[worker]["first_pass"] = sum(gates) / len(gates)
    return list(rows.values())


def print_workers(rows: list[dict]) -> None:
    """Show token and cost columns only when at least one worker reported them."""
    width = max(len("worker"), *(len(row["worker"]) for row in rows))
    costs = any(row["cost"] is not None for row in rows)
    token_fields = USAGE_FIELDS[:-1] if any(
        any(field in row for field in USAGE_FIELDS[:-1]) for row in rows
    ) else ()
    token_headings = {"prompt_tokens": "prompt tokens", "completion_tokens": "completion tokens",
                      "cached_tokens": "cached tokens"}
    print(f"{'worker':<{width}}  first-gate pass  attempts"
          + "".join(f"  {token_headings[field]}" for field in token_fields) + "  cost" * costs)
    for row in rows:
        rate = "n/a" if row["first_pass"] is None else f"{row['first_pass']:.1%}"
        tokens = "".join(f"  {row.get(field, NOT_REPORTED)}" for field in token_fields)
        cost = "n/a" if row["cost"] is None else f"${row['cost']:.2f}"
        print(f"{row['worker']:<{width}}  {rate:>15}  {row['attempts']:>8}{tokens}" + f"  {cost}" * costs)


def brief_metrics(audit: dict[int, list[dict]]) -> list[dict]:
    """First-gate pass rate split by whether attempt 1 carried a brief."""
    outcomes: dict[str, list[bool]] = {"with brief": [], "without brief": []}
    for events in audit.values():
        for event in events:
            if event.get("event") == "attempt" and event.get("attempt") == 1 and event.get("gate") in ("PASS", "FAIL"):
                outcomes["with brief" if event.get("brief") else "without brief"].append(event["gate"] == "PASS")
    return [{"tickets": key, "first_pass": sum(gates) / len(gates) if gates else None, "attempts": len(gates)}
            for key, gates in outcomes.items()]


def print_briefs(rows: list[dict]) -> None:
    print("tickets        first-gate pass  attempts")
    for row in rows:
        rate = "n/a" if row["first_pass"] is None else f"{row['first_pass']:.1%}"
        print(f"{row['tickets']:<13}  {rate:>15}  {row['attempts']:>8}")


def timeline(number: int) -> list[dict]:
    pages = gh("api", f"repos/{REPOSITORY}/issues/{number}/timeline",
               "--paginate", "--slurp")
    return [
        {"__typename": "LabeledEvent" if item["event"] == "labeled" else "UnlabeledEvent",
         "createdAt": item["created_at"], "label": item["label"],
         "actor": {"login": actor.get("login"), "__typename": actor.get("type")}}
        for page in pages for item in page
        if item.get("event") in {"labeled", "unlabeled"}
        for actor in [item.get("actor") or {}]
    ]


def human_touch(items: list[dict], audit: list[dict], end: str | None = None,
                login: str = "", cutover: str | None = None) -> dict:
    """Label intervals; removal actor, never comment author, resolves an escalation.

    Actors are attributed by feedback.attribute: Bot or the dispatcher `login` is factory,
    any other account human, anything before `cutover` unattributable.
    Trace counts supplement missing timeline history without double-counting it.
    """
    starts = []
    resolutions = []
    opened = None
    minutes = 0.0
    queued = 0
    for item in sorted(items, key=lambda e: e.get("createdAt") or ""):
        kind, at = item.get("__typename"), item.get("createdAt")
        label = (item.get("label") or {}).get("name")
        if not at or (end and at > end):
            continue
        if kind == "LabeledEvent" and label == LABEL_AGENT:
            queued += 1
        if label != LABEL_HUMAN:
            continue
        if kind == "LabeledEvent" and opened is None:
            opened = at
            starts.append(at)
        elif kind == "UnlabeledEvent" and opened is not None:
            minutes += merge_hours(opened, at) * 60
            actor = item.get("actor") or {}
            resolutions.append({
                "actor": actor.get("login"),
                "resolved_by": feedback.attribute(actor, at, login, cutover),
            })
            opened = None
    if opened:
        minutes += merge_hours(opened, end or datetime.now(UTC).isoformat()) * 60
    escalated = [(e["at"], e.get("code") or "unknown") for e in audit
                 if e.get("event") == "escalate" and e.get("reason") != "manager_failed"]
    claims = sum(e.get("event") == "claimed" for e in audit)
    return {
        "escalation_count": max(len(starts), len(escalated)),
        "escalation_times": [at for at, _ in escalated] if len(escalated) >= len(starts) else starts,
        "escalation_codes": escalated,
        "manager_failures": sum(e.get("event") == "escalate" and e.get("reason") == "manager_failed"
                                for e in audit),
        "resolutions": resolutions,
        "ready_for_human_minutes": round(minutes, 2),
        "requeue_count": max(0, queued - 1, claims - 1),
    }


def human_touch_metrics(rows: list[dict], now: datetime | None = None) -> dict:
    """Trailing seven-day escalation count, by escalation code; human share of attributed resolutions."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=7)
    resolved = [r["resolved_by"] for row in rows for r in row.get("resolutions", [])
                if r["resolved_by"] in {"human", "factory"}]
    by_code = Counter(code for row in rows for at, code in row.get("escalation_codes", [])
                      if since <= datetime.fromisoformat(at) <= now)
    return {
        "escalations_per_week": sum(
            since <= datetime.fromisoformat(at) <= now
            for row in rows for at in row.get("escalation_times", [])
        ),
        "escalations_by_code": dict(by_code.most_common()),
        "human_resolved_pct": round(100 * resolved.count("human") / len(resolved), 1) if resolved else None,
    }


def human_touched_pct(merged: list[dict]) -> float | None:
    """Share of merged agent PRs with any human correction; unattributable PRs left out."""
    keys = feedback.CORRECTION_KEYS.values()
    known = [any(pr[k] for k in keys) for pr in merged if all(pr.get(k) is not None for k in keys)]
    return round(100 * sum(known) / len(known), 1) if known else None

def rework_rounds(events: list[dict]) -> int:
    reviews = [
        row for row in events
        if row.get("event") == "review" and row.get("role") in {None, "primary"}
    ]
    return max(0, len(reviews) - 1)


def risk_tier(events: list[dict]) -> str | None:
    return next((
        row["tier"] for row in reversed(events)
        if row.get("event") == "risk" and row.get("tier") in risk.TIERS
    ), None)


def risk_metrics(
    rows: list[dict], *, escape_tickets: set[int] | None = None,
) -> dict[str, dict]:
    """Merged-PR outcomes grouped by the classifier's recorded tier."""
    result = {}
    for tier in risk.TIERS:
        merged = [
            row for row in rows
            if row.get("state") == "merged" and row.get("risk_tier") == tier
        ]
        count = len(merged)
        def ratio(value: float, denominator: int = count) -> float | None:
            return round(value / denominator, 3) if denominator else None
        disagreements = sum(
            code == "risk_disagreement"
            for row in merged for _, code in row.get("escalation_codes", [])
        )
        escaped = (
            sum(row.get("ticket") in escape_tickets for row in merged)
            if escape_tickets is not None else None
        )
        result[tier] = {
            "merged_prs": count,
            "human_interventions_per_merged_pr": ratio(sum(
                row.get("escalation_count", 0) for row in merged
            )),
            "ready_for_human_minutes_per_merged_pr": ratio(sum(
                row.get("ready_for_human_minutes", 0) for row in merged
            )),
            "rounds_to_pass": ratio(sum(row.get("rework_rounds", 0) for row in merged)),
            "risk_disagreements_per_merged_pr": ratio(disagreements),
            "escape_rate": ratio(escaped) if escaped is not None else None,
        }
    return result


def print_risk_metrics(rows: dict[str, dict]) -> None:
    print("tier    merged  human/PR  human min/PR  rounds  disagreements/PR  escape rate")
    for tier, row in rows.items():
        def value(key: str, values: dict = row) -> str:
            return "n/a" if values[key] is None else f"{values[key]:.3f}"
        print(
            f"{tier:<6}  {row['merged_prs']:>6}  "
            f"{value('human_interventions_per_merged_pr'):>8}  "
            f"{value('ready_for_human_minutes_per_merged_pr'):>12}  "
            f"{value('rounds_to_pass'):>6}  "
            f"{value('risk_disagreements_per_merged_pr'):>16}  "
            f"{value('escape_rate'):>11}"
        )


def merge_hours(created_at: str, merged_at: str | None) -> float | None:
    if not merged_at:
        return None
    created = datetime.fromisoformat(created_at)
    merged = datetime.fromisoformat(merged_at)
    return (merged - created).total_seconds() / 3600


def make_row(
    details: dict[str, Any],
    state: str,
    *,
    review_rounds: int = 0,
    merged_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ticket": details["number"],
        "title": details["title"],
        "state": state,
        "attempts": count_comments(details["comments"], "gate"),
        "review_rounds": review_rounds,
        "merge_hours": merge_hours(details["createdAt"], merged_at),
    }


def collect_rows(progress: bool = False) -> list[dict[str, Any]]:
    """One row per agent PR, open escalated/queued issue, and journaled ticket. Per-ticket
    reads (issue, PR comments, timeline) run on WORKERS threads; `progress` counts them on stderr."""
    audit = audit_by_ticket(cfg.factory / "events.jsonl")
    pulls: dict[int, dict] = {}
    for pull_request in gh("pr", "list", "--state", "all", "--limit", "1000",
                           "--json", "number,state,headRefName,mergedAt"):
        if match := AGENT_BRANCH.fullmatch(pull_request["headRefName"]):
            pulls[int(match.group(1))] = pull_request
    listed: dict[int, str | None] = dict.fromkeys(pulls)
    for label, state in ((LABEL_HUMAN, "escalated"), (LABEL_AGENT, "queued")):
        for listed_issue in gh("issue", "list", "--state", "open", "--label", label,
                               "--limit", "1000", "--json", "number"):
            listed.setdefault(listed_issue["number"], state)
    for number in audit:
        listed.setdefault(number, None)

    def read(number: int) -> tuple[dict, dict | None, list[dict]]:
        review = gh("pr", "view", str(pulls[number]["number"]), "--json", "comments") if number in pulls else None
        return issue(number), review, timeline(number)

    fetched = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(read, number): number for number in listed}
        try:
            for done, future in enumerate(as_completed(futures), 1):
                fetched[futures[future]] = future.result()
                if progress:
                    sys.stderr.write(f"\rstats: {done}/{len(futures)} tickets")
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise
    if progress and futures:
        sys.stderr.write("\n")

    rows: dict[int, dict[str, Any]] = {}
    for number, state in listed.items():
        details, review, _ = fetched[number]
        if number in pulls:
            pull_request = pulls[number]
            state = "open PR" if pull_request["state"] == "OPEN" else "merged" if pull_request["mergedAt"] else "escalated"
            rows[number] = make_row(details, state, review_rounds=count_comments(review["comments"], "VERDICT:"),
                                    merged_at=pull_request["mergedAt"])
        elif state:
            rows[number] = make_row(details, state)
        else:
            merged = next((e["at"] for e in reversed(audit[number]) if e.get("event") == "merged"), None)
            rows[number] = make_row(details, "merged" if merged else details["state"].lower(), merged_at=merged)
    pr_by_ticket = {number: pull_request["number"] for number, pull_request in pulls.items()}
    login = dispatcher_login(cfg)
    actions = feedback.correction_reads(graphql, REPOSITORY, pr_by_ticket.values()) if pr_by_ticket else {}
    cutover = feedback.first_action(login, [
        *((item.get("actor"), item.get("createdAt")) for _, _, items in fetched.values() for item in items),
        *((actor, action["at"]) for acts in actions.values() for action in acts for actor in action["actors"]),
    ])
    for number, row in rows.items():
        details, _, items = fetched[number]
        row.update(human_touch(items, audit.get(number, []), details.get("closedAt"), login, cutover))
        if number in pr_by_ticket:
            row.update(feedback.corrections(actions[pr_by_ticket[number]], login, cutover))
        row["llm_usage"] = llm_usage(audit.get(number, []), cfg.prices)
        row["review_findings"] = review_findings(audit.get(number, []))
        row["risk_tier"] = risk_tier(audit.get(number, []))
        if any(event.get("event") == "review" for event in audit.get(number, [])):
            row["rework_rounds"] = rework_rounds(audit.get(number, []))
        row.update(review_convergence(audit.get(number, [])))

    return [rows[number] for number in sorted(rows)]


def promotions(path: Path) -> list[dict]:
    """One row per `promotion-open` journal row (a merged lessons/curate PR) with its baseline means and,
    once `learn_pass` decided it, the `after` means, `decision` (`keep`/`revert`) and the revert branch."""
    rows: dict[int, dict] = {}
    for e in lifecycle.read_events(path):
        if e.get("event") == "promotion-open":
            rows[e["pr"]] = {"pr": e["pr"], "branch": e.get("branch"), "head": e.get("head"), "opened_at": e.get("at"),
                             "baseline": e.get("baseline"), "decision": None, "decided_at": None, "after": None,
                             "reason": None, "revert": None}
        elif e.get("event") in ("promotion-keep", "promotion-revert") and e.get("pr") in rows:
            rows[e["pr"]].update(decision=e["event"].removeprefix("promotion-"), decided_at=e.get("at"),
                                 after=e.get("after"), reason=e.get("reason"), revert=e.get("branch"))
    return list(rows.values())


def print_promotions(rows: list[dict]) -> None:
    def pair(r: dict, key: str) -> str:
        left, right = (r["baseline"] or {}).get(key), (r["after"] or {}).get(key)
        return f"{'n/a' if left is None else f'{left:.2f}'} -> {'n/a' if right is None else f'{right:.2f}'}"

    print(f"{'PR':>5}  {'branch':40}  {'decision':8}  efficiency        first-pass")
    for r in rows:
        print(f"#{r['pr']:<4}  {r['branch'] or '':40}  {r['decision'] or 'open':8}  "
              f"{pair(r, 'efficiency_mean'):16}  {pair(r, 'gate_first_pass_rate')}"
              + (f"  {r['revert']}" if r["revert"] else ""))


def truncate(title: str) -> str:
    return title if len(title) <= 40 else f"{title[:37]}..."


def format_hours(hours: float | None) -> str:
    return "" if hours is None else f"{hours:.1f}"


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cfg.root,
                          text=True, capture_output=True, check=True).stdout


def agent_commits() -> list[tuple[str, int, str]]:
    """(sha, ticket, title) of first-parent `agent/<n>:` commits on origin/<main>, oldest first."""
    commits = []
    for line in git("log", "--first-parent", "--reverse", "--format=%H %s", f"origin/{cfg.main}").splitlines():
        sha, _, subject = line.partition(" ")
        if match := AGENT_SUBJECT.match(subject):
            commits.append((sha, int(match.group(1)), subject[match.end():].strip()))
    return commits


def blamed_lines(fix: str) -> list[tuple[str, str]]:
    """(path, commit subject) per line `fix` modifies or deletes, blamed at its first parent."""
    lines, path, header = [], None, False
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", f"{fix}^", fix)
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("--- "):
            path = None if line == "--- /dev/null" else line[6:].rstrip("\t")  # git appends a TAB to paths with spaces
        elif match := HUNK.match(line):
            header = False
            count = 1 if match.group(2) is None else int(match.group(2))
            if path and count:
                blame = git("blame", "--line-porcelain", "-L", f"{match.group(1)},+{count}", f"{fix}^", "--", path)
                lines += [(path, row[8:]) for row in blame.splitlines() if row.startswith("summary ")]
    return lines


def present(fix: str) -> bool:
    """True when `fix` and its first parent are in this clone, after one `git fetch origin <fix>` if not."""
    def have() -> bool:
        return subprocess.run(["git", "cat-file", "-e", f"{fix}^"], cwd=cfg.root,
                              capture_output=True, check=False).returncode == 0
    if not have():
        subprocess.run(["git", "fetch", "-q", "origin", fix], cwd=cfg.root, capture_output=True, check=False)
    return have()


def escapes() -> list[dict[str, Any]]:
    """Candidate escaped defects: closed `bug` issues whose fix changed lines an earlier agent ticket wrote.
    SZZ-style and noisy: refactors yield false candidates, add-only fixes yield none."""
    bugs = gh("issue", "list", "--state", "closed", "--label", "bug", "--limit", "1000",
              "--json", "number,title,body,closedAt")
    if not bugs:
        return []
    commits = agent_commits()
    titles = {n: title for _, n, title in commits}
    pulls = None
    rows = []
    for bug in sorted(bugs, key=lambda b: b["number"]):
        number = bug["number"]
        fixes = [sha for sha, n, _ in commits if n == number]
        if not fixes:
            if pulls is None:
                pulls = gh("pr", "list", "--state", "merged", "--limit", "1000",
                           "--json", "mergeCommit,closingIssuesReferences")
            fixes = [pr["mergeCommit"]["oid"] for pr in pulls if pr.get("mergeCommit")
                     and any(ref["number"] == number for ref in pr["closingIssuesReferences"])]
        declared = sorted({int(n) for n in REGRESSED_BY.findall(bug.get("body") or "")})
        candidates: dict[int, dict] = {}
        skipped: list[str] = []
        if declared:
            status = "candidates"
            candidates = {n: {"ticket": n, "title": titles.get(n, ""), "lines": None, "paths": []} for n in declared}
        elif not fixes:
            status = "unlinked"
        else:
            skipped = [fix for fix in fixes if not present(fix)]
            for fix in skipped:
                print(f"factory stats: skipped fix {fix[:7]} of bug #{number}: not in this clone or on origin",
                      file=sys.stderr)
            blamed = [line for fix in fixes if fix not in skipped for line in blamed_lines(fix)]
            for path, subject in blamed:
                match = AGENT_SUBJECT.match(subject)
                if match and int(match.group(1)) != number:
                    n = int(match.group(1))
                    candidate = candidates.setdefault(n, {"ticket": n, "title": titles.get(n, ""), "lines": 0, "paths": []})
                    candidate["lines"] += 1
                    if path not in candidate["paths"]:
                        candidate["paths"].append(path)
            status = ("candidates" if candidates else "skipped" if len(skipped) == len(fixes)
                      else "no_candidates" if blamed else "no_blamed_lines")
        rows.append({"bug": number, "title": bug["title"], "closed_at": bug["closedAt"], "fixes": fixes,
                     "source": "declared" if declared else "blame", "status": status, "skipped": skipped,
                     "candidates": [candidates[n] for n in sorted(candidates)]})
    return rows

def escape_events(rows: list[dict[str, Any]]) -> list[dict]:
    """Project SZZ escape attribution into watch-compatible lifecycle rows."""
    return [
        {
            "event": "escape",
            "ticket": candidate["ticket"],
            "bug": row["bug"],
            "at": row["closed_at"],
        }
        for row in rows
        for candidate in row.get("candidates", [])
    ]


def print_escapes(rows: list[dict[str, Any]], merged: set[int]) -> None:
    print("bug   fix       source   candidates")
    for row in rows:
        fixes = ",".join(sha[:7] for sha in row["fixes"]) or "-"
        result = ", ".join(
            f"#{c['ticket']}" + ("" if c["lines"] is None else f" ({c['lines']} line{'s' * (c['lines'] != 1)})")
            for c in row["candidates"]) or row["status"]
        print(f"#{row['bug']:<4} {fixes:<8} {row['source']:<8} {result}")
    escaped = len({c["ticket"] for row in rows for c in row["candidates"]} & merged)
    share = f"{100 * escaped / len(merged):.1f}%" if merged else "n/a"
    print(f"\nCandidate escapes: {escaped} of {len(merged)} merged agent tickets ({share})")
    if skipped := [sha[:7] for row in rows for sha in row["skipped"]]:
        print(f"Skipped fixes: {len(skipped)} not in this clone or on origin ({', '.join(skipped)})")


def print_table(rows: list[dict[str, Any]]) -> None:
    """Dollar columns only when some row has a cost; main notes missing prices once."""
    def usage(row: dict, stage: str, key: str) -> str:
        value = row["llm_usage"].get(stage, {}).get(key)
        if isinstance(value, list):
            return ", ".join(map(str, value))
        if key == "cost" and value is not None:
            return f"${value:.4f}"
        if value is None and stage == "worker" and key != "cost":
            return NOT_REPORTED
        return "" if value is None else str(value)

    def tokens(row: dict) -> str:
        total = row["llm_usage"].get("total", {})
        known = [v for v in (total.get("prompt_tokens"), total.get("completion_tokens")) if v is not None]
        return str(sum(known)) if known else ""

    def correction(row: dict, key: str) -> str:
        return "" if key not in row else "n/a" if row[key] is None else str(row[key])

    def findings(row: dict) -> str:
        return "; ".join(
            f"{counts['important']}/{counts['nit']}/{counts['pre_existing']}"
            for counts in row.get("review_findings", [])
        )

    def important_rounds(row: dict) -> str:
        return ",".join(map(str, row.get("important_findings_per_round", [])))

    columns = (
        ("ticket#", lambda row: f"#{row['ticket']}"),
        ("title", lambda row: truncate(row["title"])),
        ("state", lambda row: row["state"]),
        ("attempts", lambda row: str(row["attempts"])),
        ("review rounds", lambda row: str(row["review_rounds"])),
        ("rounds to pass", lambda row: "" if row.get("rounds_to_pass") is None else str(row["rounds_to_pass"])),
        ("repeats dropped", lambda row: str(row.get("repeated_findings_dropped", 0))),
        ("important/round", important_rounds),
        ("review findings I/N/P", findings),
        ("hours", lambda row: format_hours(row["merge_hours"])),
        ("triage prompt", lambda row: usage(row, "triage", "prompt_tokens")),
        ("triage completion", lambda row: usage(row, "triage", "completion_tokens")),
        ("triage cache rates", lambda row: usage(row, "triage", "prefix_cache_hit_rates")),
        ("triage $", lambda row: usage(row, "triage", "cost")),
        ("review prompt", lambda row: usage(row, "review", "prompt_tokens")),
        ("review completion", lambda row: usage(row, "review", "completion_tokens")),
        ("review cache rates", lambda row: usage(row, "review", "prefix_cache_hit_rates")),
        ("review $", lambda row: usage(row, "review", "cost")),
        ("worker prompt", lambda row: usage(row, "worker", "prompt_tokens")),
        ("worker completion", lambda row: usage(row, "worker", "completion_tokens")),
        ("worker cached", lambda row: usage(row, "worker", "cached_tokens")),
        ("worker $", lambda row: usage(row, "worker", "cost")),
        ("total tokens", tokens),
        ("total $", lambda row: usage(row, "total", "cost")),
        ("escalations", lambda row: str(row["escalation_count"])),
        ("manager failures", lambda row: str(row["manager_failures"])),
        ("resolved by (actor)", lambda row: ", ".join(
            f"{r['resolved_by']} ({r['actor'] or '?'})" for r in row["resolutions"])),
        ("human minutes", lambda row: f"{row['ready_for_human_minutes']:.1f}"),
        ("re-queues", lambda row: str(row["requeue_count"])),
        ("human CRs", lambda row: correction(row, "human_change_requests")),
        ("human comments", lambda row: correction(row, "human_comments")),
        ("human commits", lambda row: correction(row, "human_commits")),
    )
    if not any(row["llm_usage"].get("total", {}).get("cost") is not None for row in rows):
        columns = tuple(column for column in columns if not column[0].endswith("$"))
    values = [[render(row) for _, render in columns] for row in rows]
    widths = [
        max([len(name), *(len(row[index]) for row in values)])
        for index, (name, _) in enumerate(columns)
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.ljust(width) for cell, width in zip(cells, widths, strict=True)
        ).rstrip()

    print(line([name for name, _ in columns]))
    print(line(["-" * width for width in widths]))
    for row in values:
        print(line(row))


def worker_tokens(rows: list[dict[str, Any]]) -> str:
    """Summed worker tokens over the tickets whose worker reported any."""
    reported = [usage for row in rows if (usage := row["llm_usage"].get("worker", {}))
                and (usage["prompt_tokens"] is not None or usage["completion_tokens"] is not None)]
    if not reported:
        return NOT_REPORTED
    prompt, completion = (sum(usage[key] or 0 for usage in reported) for key in ("prompt_tokens", "completion_tokens"))
    return f"{prompt} prompt, {completion} completion ({len(reported)} of {len(rows)} tickets reported)"


def resolve(repo: str | None) -> Config:
    """The factory root the dispatcher runs in: host `[repo."<slug>"].path` for `repo` (else this
    checkout's slug), falling back to this checkout when it is that repository."""
    try:
        local = config.load()
    except config.ConfigError as exc:
        if not repo:
            raise SystemExit(f"factory stats: {exc}; run inside a checkout or pass --repo owner/name") from exc
        local = None
    slug = repo or local.repo
    table = config.host_config().get("repo", {}).get(slug)
    if isinstance(table, dict) and table.get("path"):
        try:
            root = config.load(Path(table["path"]))
        except config.ConfigError as exc:
            raise SystemExit(f'factory stats: [repo."{slug}"].path in {config.host_config_path()}: {exc}') from exc
        if root.repo != slug:
            raise SystemExit(f'factory stats: [repo."{slug}"].path {table["path"]} is a checkout of {root.repo}')
        return root
    if local is None or local.repo != slug:
        raise SystemExit(f'factory stats: no [repo."{slug}"].path in {config.host_config_path()}')
    return local


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory stats", description=__doc__)
    parser.add_argument("--json", action="store_true", help="print metric rows as JSON")
    parser.add_argument("--week", action="store_true",
                        help="trailing seven-day journal metrics plus one bounded stuck-ticket read")
    parser.add_argument("--by-worker", action="store_true",
                        help="split gate pass rate, attempts, reported token usage and known cost by claim label")
    parser.add_argument("--by-brief", action="store_true", help="split first-gate pass rate by tickets with/without a brief")
    parser.add_argument("--stages", action="store_true",
                        help="last successful triage, dispatch and watch pass for this repository")
    parser.add_argument("--escapes", action="store_true",
                        help="candidate escaped defects: closed bugs whose fix changed lines an earlier agent ticket wrote")
    parser.add_argument("--by-risk", action="store_true",
                        help="per-tier merged outcomes and escaped-defect guardrail")
    parser.add_argument("--repo", metavar="OWNER/NAME",
                        help='read the root registered as [repo."OWNER/NAME"].path in the host config (default: this checkout\'s repository)')
    parser.add_argument("--quiet", action="store_true", help="no progress on stderr")
    parser.add_argument("--promotions", action="store_true",
                        help="merged lessons/curate PRs with their before/after score means and keep/revert decision")
    args = parser.parse_args(argv)
    configure(resolve(args.repo))
    journal = cfg.factory / "events.jsonl"
    if not args.escapes and not journal.is_file():
        raise SystemExit(f"factory stats: no journal at {journal}; this is not the dispatcher's root. "
                         f'Set [repo."{cfg.repo}"].path in {config.host_config_path()} or pass --repo owner/name')
    if args.week:
        now = _now()
        start = now - timedelta(days=7)
        journal_rows = window_rows(lifecycle.read_events(journal), start, now)
        rows = weekly_metrics(
            journal_rows, start, now,
            cfg.llm_model if config.local_endpoint(cfg.llm_url) else None,
        )
        rows.setdefault("skills", skill_metrics(journal_rows))
        rows.setdefault("skill_coverage", skill_coverage(journal_rows))
        rows["stuck_over_days"] = stuck_over_days(now)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"Window: {rows['window']['start']} to {rows['window']['end']}")
            print(f"Merges: {rows['merges']}")
            print(f"Human touches: {rows['human_touches']} ({rows['human_touches_per_merge']} per merge)")
            print(f"Escalations: {rows['escalations_7d']} ({rows['escalations_by_code']})")
            print(f"First pass: {rows['first_pass']} "
                  f"({rows['first_pass_numerator']}/{rows['first_pass_denominator']})")
            print(f"Review nonconvergence: {rows['review_nonconvergence_share']} "
                  f"({rows['review_nonconvergence_share_numerator']}/"
                  f"{rows['review_nonconvergence_share_denominator']})")
            print(f"Local token share: {rows['local_token_share']}; missing usage: {rows['usage_missing']}")
            print(f"Stuck over 7 days: {rows['stuck_over_days']}")
            print_skills(rows["skills"], rows["skill_coverage"])
        return 0
    if args.stages:
        passes = lifecycle.last_successful_passes(journal)
        result = {"repository": cfg.repo, "last_successful_pass": passes}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"repository: {cfg.repo}")
            for stage, timestamp in passes.items():
                print(f"{stage}: {timestamp or 'never'}")
        return 0
    if args.escapes:
        rows = escapes()
    elif args.promotions:
        rows = promotions(cfg.factory / "events.jsonl")
    elif args.by_worker or args.by_brief:
        audit = audit_by_ticket(journal)
        rows = worker_metrics(audit, cfg.workers) if args.by_worker else brief_metrics(audit)
    elif args.by_risk:
        ticket_rows = collect_rows(progress=not args.quiet)
        escaped = escapes()
        rows = risk_metrics(
            ticket_rows,
            escape_tickets={
                candidate["ticket"]
                for row in escaped for candidate in row.get("candidates", [])
            },
        )
    else:
        rows = collect_rows(progress=not args.quiet)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif args.escapes:
        print_escapes(rows, {n for _, n, _ in agent_commits()})
    elif args.promotions:
        print_promotions(rows)
    elif args.by_worker:
        print_workers(rows)
    elif args.by_brief:
        print_briefs(rows)
    elif args.by_risk:
        print_risk_metrics(rows)
    else:
        print_table(rows)
        totals = human_touch_metrics(rows)
        human = totals["human_resolved_pct"]
        print(f"\nWorker tokens: {worker_tokens(rows)}")
        print(f"Escalations/week (last 7 days): {totals['escalations_per_week']}")
        if totals["escalations_by_code"]:
            print("  by code: " + ", ".join(f"{c} {k}" for c, k in totals["escalations_by_code"].items()))
        print(f"Human-resolved: {str(human) + '%' if human is not None else 'n/a'} (attributed resolutions)")
        touched = human_touched_pct([row for row in rows if row["state"] == "merged"])
        print(f"Human-touched PRs: {str(touched) + '%' if touched is not None else 'n/a'} (attributed merged agent PRs)")
        if not cfg.prices:
            print("Prices: none configured, so tokens without a reported cost have no dollars "
                  '(host config [prices."<model>"], see README)')
    return 0
