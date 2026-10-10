"""Pure, bounded projection of selected cases and retained runtime flow evidence."""
from __future__ import annotations

from datetime import datetime

from factory.runtime_events import EVENT_LIMIT, IDENTITY, LOCK_LIMIT

CASE_LIMIT = 100
NOTICE_LIMIT = 64
FLOW_CASE_LIMIT = CASE_LIMIT + EVENT_LIMIT


def _rows(value: object, key: str, limit: int) -> tuple[list[dict], bool, int]:
    rows = value.get(key) if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return [], False, 0
    return [row for row in rows[:limit] if isinstance(row, dict)], len(rows) > limit, len(rows)


def _number(value: object) -> int | None:
    return value if type(value) is int and 0 < value < 2**63 else None


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _latest(*values: object) -> str | None:
    valid = [(parsed, value) for value in values if (parsed := _time(value)) is not None]
    return max(valid)[1] if valid else next((value for value in values if isinstance(value, str) and value), None)


def _seconds(start: object, end: object) -> float | None:
    left, right = _time(start), _time(end)
    if left is None or right is None or right < left:
        return None
    return round((right - left).total_seconds(), 6)


def _terminal(case: dict) -> bool:
    return (
        str(case.get("state") or "").casefold() == "closed"
        or str(case.get("stage") or "").casefold() in {"closed", "merged", "done"}
    )


def _identity(row: dict) -> tuple | None:
    values = tuple(row.get(field) for field in IDENTITY)
    return values if all(value is None or type(value) in (str, int) for value in values) else None


def _started(execution: dict) -> bool:
    return isinstance(execution.get("entered_at"), str) and bool(execution["entered_at"])


def _open(execution: dict) -> bool:
    return execution.get("ended_at") is None and execution.get("state") in {"active", "unknown"}


def _active(execution: dict) -> bool:
    return execution.get("ended_at") is None and execution.get("state") == "active"


def _execution_rows(runtime: object) -> tuple[list[dict], bool, int]:
    rows, clipped, supplied = _rows(runtime, "executions", EVENT_LIMIT)
    result = []
    seen = set()
    for row in rows:
        execution_id = row.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id or execution_id in seen:
            continue
        seen.add(execution_id)
        result.append(row)
    return result, clipped, supplied


def _case_row(case: dict | None, number: int, executions: list[dict]) -> dict:
    active = [row for row in executions if _active(row)]
    attempts = sorted({row["attempt"] for row in executions
                       if type(row.get("attempt")) is int and row["attempt"] >= 0})
    rounds = sorted({row["review_round"] for row in executions
                     if type(row.get("review_round")) is int and row["review_round"] >= 0})
    states: dict[str, int] = {}
    for row in executions:
        state = row.get("state") if isinstance(row.get("state"), str) else "unknown"
        states[state] = states.get(state, 0) + 1
    selected = case is not None
    return {
        "number": number,
        "title": case.get("title") if selected else None,
        "url": case.get("url") if selected else None,
        "stage": case.get("stage") if selected else None,
        "selection": "selected" if selected else "runtime_only",
        "selected_nonterminal": selected,
        "known_started_wip": any(_started(row) for row in executions),
        "active_execution": bool(active),
        "execution_ids": [row["execution_id"] for row in executions],
        "active_execution_ids": [row["execution_id"] for row in active],
        "execution_states": {key: states[key] for key in sorted(states)},
        "attempts": {
            "observed": attempts,
            "highest": max(attempts, default=None),
            "review_rounds": rounds,
            "highest_review_round": max(rounds, default=None),
            "rework_observed": any(value > 1 for value in attempts + rounds),
        },
        "current_wait_ids": [],
    }


def _wait_payload(row: dict) -> dict:
    wait = row.get("wait") if isinstance(row.get("wait"), dict) else {}
    return {
        "reason": wait.get("reason") if isinstance(wait.get("reason"), str) else None,
        "mode": wait.get("mode") if isinstance(wait.get("mode"), str) else None,
        "resource": wait.get("resource") if isinstance(wait.get("resource"), dict) else None,
        "details": wait.get("details") if isinstance(wait.get("details"), dict) else {},
    }


def _wait_row(start: dict, end: dict | None, *, runtime_at: str | None) -> dict:
    payload = _wait_payload(start)
    event_id = start.get("event_id")
    started_at = start.get("at") if isinstance(start.get("at"), str) else None
    ended_at = end.get("at") if end and isinstance(end.get("at"), str) else None
    return {
        "event_id": event_id,
        "execution_id": start.get("execution_id"),
        "number": _number(start.get("ticket")),
        "stage": start.get("stage") if isinstance(start.get("stage"), str) else None,
        "status": "completed" if end else "current",
        **payload,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": _seconds(started_at, ended_at) if end else None,
        "observed_for_seconds": _seconds(started_at, runtime_at) if end is None else None,
        "source_event_ids": [value for value in (event_id, end.get("event_id") if end else None)
                             if isinstance(value, str)],
    }


def _waits(events: list[dict], executions: list[dict], runtime_at: str | None,
           excluded: set[int]) -> tuple[list[dict], list[dict], dict]:
    starts: dict[tuple, dict] = {}
    ends: list[dict] = []
    for event in events:
        identity = _identity(event)
        if identity is None:
            continue
        if event.get("kind") == "wait" and isinstance(event.get("event_id"), str):
            starts.setdefault((*identity, event["event_id"]), event)
        elif event.get("kind") == "wait_end" and isinstance(event.get("wait_event_id"), str):
            ends.append(event)

    matched: dict[tuple, dict] = {}
    orphan_ends = 0
    for end in ends:
        identity = _identity(end)
        key = (*identity, end["wait_event_id"]) if identity is not None else None
        start = starts.get(key) if key is not None else None
        start_sequence, end_sequence = (start or {}).get("sequence"), end.get("sequence")
        if (start is None or key in matched or type(start_sequence) is not int
                or type(end_sequence) is not int or end_sequence <= start_sequence):
            orphan_ends += 1
            continue
        matched[key] = end

    completed = []
    for key, end in matched.items():
        start = starts[key]
        if _number(start.get("ticket")) not in excluded:
            completed.append(_wait_row(start, end, runtime_at=runtime_at))

    current = []
    current_keys = set()
    current_without_event = 0
    for execution in executions:
        wait = execution.get("wait")
        if not _active(execution) or not isinstance(wait, dict) or not isinstance(wait.get("event_id"), str):
            continue
        identity = _identity(execution)
        key = (*identity, wait["event_id"]) if identity is not None else None
        start = starts.get(key) if key is not None else None
        if key in matched:
            continue
        if start is None:
            current_without_event += 1
            start = {
                **{field: execution.get(field) for field in IDENTITY},
                "event_id": wait["event_id"], "execution_id": execution.get("execution_id"),
                "ticket": execution.get("ticket"), "stage": execution.get("stage"),
                "at": wait.get("at"), "wait": {key: value for key, value in wait.items()
                                                   if key not in {"event_id", "at"}},
            }
        number = _number(start.get("ticket"))
        if number in excluded:
            continue
        current.append(_wait_row(start, None, runtime_at=runtime_at))
        if key is not None:
            current_keys.add(key)

    unpaired_starts = sum(key not in matched and key not in current_keys for key in starts)
    return current[:EVENT_LIMIT], completed[:EVENT_LIMIT], {
        "orphan_end_count": orphan_ends,
        "unpaired_start_count": unpaired_starts,
        "current_without_event_count": current_without_event,
    }


def _resource_sources(runtime: object) -> dict[str, dict]:
    resources, _, _ = _rows(runtime, "resources", LOCK_LIMIT)
    result = {}
    for row in resources:
        resource = row.get("resource")
        resource_id = resource.get("id") if isinstance(resource, dict) else None
        if not isinstance(resource_id, str) or resource_id in result:
            continue
        result[resource_id] = {
            "kind": "resource_observation", "resource_id": resource_id,
            "event_id": row.get("event_id"), "observed_at": row.get("observed_at"),
            "observation": row.get("observation"), "state": row.get("state"),
            "ownership": row.get("ownership"),
        }
    return result


def _constraints(waits: list[dict], cases: dict[int, dict], runtime: object) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for wait in waits:
        reason, mode = wait.get("reason"), wait.get("mode")
        if not isinstance(reason, str) or not isinstance(mode, str):
            continue
        resource = wait.get("resource")
        resource_id = resource.get("id") if isinstance(resource, dict) and isinstance(resource.get("id"), str) else None
        grouped.setdefault((reason, mode, resource_id), []).append(wait)
    resource_sources = _resource_sources(runtime)
    result = []
    for reason, mode, resource_id in sorted(grouped, key=lambda value: tuple(part or "" for part in value)):
        rows = grouped[(reason, mode, resource_id)]
        numbers = sorted({row["number"] for row in rows if row.get("number") is not None})
        sources = [{
            "kind": "wait", "event_id": row["event_id"],
            "wait_end_event_id": row["source_event_ids"][1] if len(row["source_event_ids"]) > 1 else None,
            "execution_id": row["execution_id"],
        } for row in rows]
        if resource_id in resource_sources:
            sources.append(resource_sources[resource_id])
        sources.extend({"kind": "case", "number": number, "url": cases[number]["url"]}
                       for number in numbers if number in cases and cases[number].get("url"))
        result.append({
            "reason": reason, "mode": mode,
            "resource": rows[0].get("resource"),
            "observed_waits": len(rows),
            "current_waits": sum(row["status"] == "current" for row in rows),
            "completed_waits": sum(row["status"] == "completed" for row in rows),
            "case_numbers": numbers,
            "sources": sources[:EVENT_LIMIT],
        })
    return result[:EVENT_LIMIT]


def project(observation: dict, runtime: dict) -> dict:
    """Project bounded evidence without I/O, persistence, policy, or outcome inference."""
    observation = observation if isinstance(observation, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}
    source_cases, cases_clipped, cases_supplied = _rows(observation, "cases", CASE_LIMIT)
    executions, executions_clipped, executions_supplied = _execution_rows(runtime)
    events, events_clipped, events_supplied = _rows(runtime, "events", EVENT_LIMIT)

    executions_by_ticket: dict[int, list[dict]] = {}
    for execution in executions:
        if (number := _number(execution.get("ticket"))) is not None:
            executions_by_ticket.setdefault(number, []).append(execution)

    selected: dict[int, dict] = {}
    excluded = set()
    for case in source_cases:
        number = _number(case.get("number"))
        if number is None or number in selected or number in excluded:
            continue
        if _terminal(case):
            excluded.add(number)
        else:
            selected[number] = case

    rows = [_case_row(case, number, executions_by_ticket.get(number, []))
            for number, case in selected.items()]
    represented = set(selected) | excluded
    runtime_only = sorted(
        number for number, grouped in executions_by_ticket.items()
        if number not in represented and any(_started(row) and _open(row) for row in grouped)
    )
    rows.extend(_case_row(None, number, executions_by_ticket[number]) for number in runtime_only)
    rows = rows[:FLOW_CASE_LIMIT]
    by_number = {row["number"]: row for row in rows}

    runtime_resources = runtime.get("resources") if isinstance(runtime.get("resources"), list) else []
    runtime_at = _latest(
        runtime.get("generated_at"),
        *[row.get("observed_at") for row in executions],
        *[row.get("observed_at") for row in runtime_resources[:LOCK_LIMIT] if isinstance(row, dict)],
    )
    current_waits, completed_waits, wait_coverage = _waits(events, executions, runtime_at, excluded)
    for wait in current_waits:
        if wait["number"] in by_number:
            by_number[wait["number"]]["current_wait_ids"].append(wait["event_id"])

    observation_coverage = observation.get("coverage") if isinstance(observation.get("coverage"), dict) else {}
    case_status = observation_coverage.get("status") if isinstance(observation_coverage.get("status"), str) else "unavailable"
    history = runtime.get("history") if isinstance(runtime.get("history"), dict) else {}
    history_complete = history.get("complete") is True and history.get("truncated") is not True
    open_unknown = any(_started(row) and _open(row) and row.get("state") == "unknown" for row in executions)
    case_total_known = case_status in {"complete", "bounded"} and not cases_clipped
    runtime_total_known = history_complete and not executions_clipped and not events_clipped and not open_unknown
    waits_complete = runtime_total_known and not any(wait_coverage.values())

    selected_count = sum(row["selected_nonterminal"] for row in rows)
    started_count = sum(row["known_started_wip"] for row in rows)
    active_case_count = sum(row["active_execution"] for row in rows)
    active_execution_count = sum(len(row["active_execution_ids"]) for row in rows)
    usable = bool(source_cases or executions or events)
    if case_total_known and runtime_total_known:
        status = "bounded"
    elif usable or case_status != "unavailable" or history.get("status") in {"available", "empty"}:
        status = "partial"
    else:
        status = "unavailable"

    notices = [notice for notice in observation_coverage.get("notices", [])[:NOTICE_LIMIT]
               if isinstance(notice, str)] if isinstance(observation_coverage.get("notices"), list) else []
    notices.append("Counts cover the returned bounded case selection and retained runtime window, not a repository-wide total.")
    if not case_total_known:
        notices.append("Case selection is incomplete; unlisted nonterminal cases remain unknown.")
    if not runtime_total_known:
        notices.append("Runtime history or current execution state is incomplete; absence of start or activity is not evidence of absence.")
    if not waits_complete:
        notices.append("Wait totals are incomplete because retained starts, exact closures, or current execution state are missing.")
    notices = list(dict.fromkeys(notices))[:NOTICE_LIMIT]

    case_observed_at = observation.get("observed_at") if isinstance(observation.get("observed_at"), str) else None
    return {
        "schema_version": 1,
        "observed_at": _latest(case_observed_at, runtime_at),
        "time": {
            "case_observed_at": case_observed_at,
            "runtime_observed_at": runtime_at,
            "history_start_at": history.get("start_at") if isinstance(history.get("start_at"), str) else None,
            "history_end_at": history.get("end_at") if isinstance(history.get("end_at"), str) else None,
        },
        "coverage": {
            "status": status,
            "cases": {
                "status": case_status, "complete": case_total_known,
                "supplied": cases_supplied, "retained": len(source_cases),
                "limit": CASE_LIMIT, "input_clipped": cases_clipped,
            },
            "runtime": {
                "status": history.get("status") if isinstance(history.get("status"), str) else "unavailable",
                "complete": runtime_total_known, "history_complete": history_complete,
                "truncated": history.get("truncated") is True,
                "gaps": [gap for gap in history.get("gaps", [])[:EVENT_LIMIT] if isinstance(gap, str)]
                if isinstance(history.get("gaps"), list) else [],
                "executions_supplied": executions_supplied, "executions_retained": len(executions),
                "events_supplied": events_supplied, "events_retained": len(events),
                "event_limit": EVENT_LIMIT,
                "input_clipped": executions_clipped or events_clipped,
            },
            "waits": {"complete": waits_complete, **wait_coverage},
            "notices": notices,
        },
        "denominator": {
            "scope": "returned bounded case selection plus retained runtime journal",
            "selected_cases": len(selected),
            "selected_nonterminal_cases": selected_count,
            "runtime_executions": len(executions),
            "runtime_events": len(events),
            "history_start_at": history.get("start_at") if isinstance(history.get("start_at"), str) else None,
            "history_end_at": history.get("end_at") if isinstance(history.get("end_at"), str) else None,
            "history_complete": history_complete,
            "history_truncated": history.get("truncated") is True,
            "byte_limit": history.get("byte_limit") if type(history.get("byte_limit")) is int else None,
            "event_limit": history.get("event_limit") if type(history.get("event_limit")) is int else EVENT_LIMIT,
        },
        "counts": {
            "selected_nonterminal_cases": {"observed": selected_count,
                                             "total": selected_count if case_total_known else None},
            "known_started_wip": {"observed": started_count,
                                  "total": started_count if case_total_known and runtime_total_known else None},
            "active_execution_cases": {"observed": active_case_count,
                                       "total": active_case_count if case_total_known and runtime_total_known else None},
            "active_executions": {"observed": active_execution_count,
                                  "total": active_execution_count if case_total_known and runtime_total_known else None},
        },
        "cases": rows,
        "waits": {"current": current_waits, "completed": completed_waits},
        "constraint_candidates": _constraints(current_waits + completed_waits, by_number, runtime),
    }
