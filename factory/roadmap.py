"""Shared read-only initiative roadmap and routed owner attention."""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from uuid import uuid4

from factory import binding, config, lifecycle, plan, runtime_events
from factory.briefing import CONTEXT_CAP, SOURCE_COUNT
from factory.evidence import ERROR_CAP, READ_SECONDS, EvidenceError, clean_text, failed, source

ATTENTION_CAP = plan.PAGES * plan.PAGE_SIZE
BLOCKER_CAP = plan.LINKS
HISTORY_CAP = plan.PAGE_SIZE
NOTICES = [
    "Only bounded GitHub GETs and retained local evidence are read; no issue, assignment, plan or execution state is changed.",
    "Status, owner and plan text are declarations from untrusted issue bodies; owner filters change only attention rows.",
    "A declared delivered stage, closed child or accepted baseline does not establish owner-confirmed outcome delivery.",
    "Reads are sequential rather than an atomic GitHub snapshot; unavailable evidence remains unknown rather than absent.",
]


def _revision(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {key: value.get(key) for key in ("sha256", "source_url", "observed_at")}


def _unknown_route(reason: str = "unknown", paths: list[str] | None = None) -> dict:
    return {
        "status": "unknown", "owner": None, "candidates": [], "source": None,
        "reason": reason, "paths": list(paths or []), "revision": None,
        "verification": "unknown", "provenance": [],
    }


def _error(result: dict, code: str, source_name: str, scope: str = "roadmap") -> None:
    failed(result, EvidenceError(code, "Roadmap evidence is unavailable.", source_name, scope))


def _notice(result: dict, text: str) -> None:
    if text not in result["coverage"]["notices"]:
        result["coverage"]["notices"].append(text)


def _cite(result: dict, label: str, value: object, *, url: str | None = None,
          path: str | None = None, truncated: bool = False) -> str | None:
    item = source(label, value, url=url, path=path, truncated=truncated)
    if any(row["id"] == item["id"] for row in result["sources"]):
        return item["id"]
    used = sum(len(row["text"].encode("utf-8")) for row in result["sources"])
    if len(result["sources"]) >= SOURCE_COUNT or used + len(item["text"].encode("utf-8")) > CONTEXT_CAP:
        _notice(result, f"Roadmap citations stop at {SOURCE_COUNT} sources and {CONTEXT_CAP} source-text bytes; later structured rows may lack a citation.")
        return None
    result["sources"].append(item)
    if item["truncated"]:
        _notice(result, f"{label} is a bounded prefix; omitted text is not evidence of absence.")
    return item["id"]


def _copy_errors(result: dict, scratch: dict) -> None:
    for row in scratch["errors"]:
        if row not in result["errors"] and len(result["errors"]) < ERROR_CAP:
            result["errors"].append(row)
    if scratch["errors"]:
        result["ok"] = False
    for text in scratch["coverage"]["notices"]:
        _notice(result, text)


def _route(reader: plan.Reader, result: dict, number: int, reason: str,
           paths: list[str], url: str) -> tuple[dict, list[str]]:
    try:
        reader.route(number, reason, paths)
    except EvidenceError as exc:
        failed(reader.result, exc)
        return _unknown_route(reason, paths), []
    route = reader.result["route"]
    citation = _cite(result, f"Decision route for #{number}", route, url=url)
    return route, [citation] if citation else []


def _blocker_numbers(body: str) -> list[int]:
    """The dispatcher's existing `Blocked by: #N` line syntax."""
    found = set()
    for line in re.findall(r"(?im)^blocked by:(.*)$", body):
        found.update(int(value) for value in re.findall(r"#(\d+)", line))
    return sorted(found)


def _blocker(reader: plan.Reader, result: dict, number: int) -> tuple[dict | None, bool]:
    path = f"issues/{number}"
    try:
        value = reader.fetch(f"Blocker #{number}", path)
        if not isinstance(value, dict) or type(value.get("number")) is not int:
            raise EvidenceError("invalid_response", "GitHub blocker response has an unexpected shape.", f"{reader.prefix}/{path}")
    except EvidenceError as exc:
        failed(reader.result, exc)
        return {"number": number, "unavailable": exc.code}, False
    state = str(value.get("state") or "").upper()
    if state != "OPEN":
        return None, True
    row = {
        "number": number,
        "title": clean_text(str(value.get("title") or "")),
        "state": state,
        "url": clean_text(str(value.get("html_url") or "")),
    }
    citation = _cite(result, f"Open blocker #{number}", row, url=row["url"])
    if citation:
        row["source"] = citation
    return row, True


def _child(reader: plan.Reader, result: dict, number: int) -> tuple[dict, str | None, bool]:
    path = f"issues/{number}"
    try:
        value = reader.fetch(f"Linked issue #{number}", path)
        parsed = plan.record(value, path)
    except EvidenceError as exc:
        failed(reader.result, exc)
        return {"number": number, "unavailable": exc.code}, None, False
    body = value.get("body") if isinstance(value.get("body"), str) else ""
    raw_assignees = value.get("assignees") if isinstance(value.get("assignees"), list) else []
    assignees = [
        clean_text(str(item.get("login") or ""))
        for item in raw_assignees
        if isinstance(item, dict) and item.get("login")
    ][:5]
    if len(raw_assignees) > len(assignees):
        _notice(result, f"Ticket #{number} assignees are bounded to the first five valid logins.")
    row = {key: parsed[key] for key in ("number", "title", "state", "url", "labels")}
    row["assignees"] = assignees
    blockers: list[dict] = []
    complete = True
    dependency_path = f"issues/{number}/dependencies/blocked_by"
    try:
        dependencies = reader.fetch(f"Native blockers for #{number}", dependency_path)
        if not isinstance(dependencies, list) or not all(isinstance(item, dict) for item in dependencies):
            raise EvidenceError("invalid_response", "GitHub dependency response has an unexpected shape.", f"{reader.prefix}/{dependency_path}")
        open_dependencies = []
        seen = set()
        for item in dependencies:
            dependency = item.get("number")
            if (str(item.get("state") or "").upper() != "OPEN"
                    or type(dependency) is not int or dependency in seen):
                continue
            seen.add(dependency)
            open_dependencies.append(item)
        if len(open_dependencies) > BLOCKER_CAP:
            complete = False
            _notice(result, f"Ticket #{number} blockers stop after the first {BLOCKER_CAP} records.")
        for item in open_dependencies[:BLOCKER_CAP]:
            blocker = {
                "number": item["number"],
                "title": clean_text(str(item.get("title") or "")),
                "state": "OPEN",
                "url": clean_text(str(item.get("html_url") or "")),
            }
            citation = _cite(result, f"Open blocker #{item['number']}", blocker, url=blocker["url"])
            if citation:
                blocker["source"] = citation
            blockers.append(blocker)
    except EvidenceError as exc:
        if exc.code != "github_not_found":
            failed(reader.result, exc)
            complete = False
    known = {item.get("number") for item in blockers}
    refs = [blocker for blocker in _blocker_numbers(body) if blocker not in known]
    available = max(0, BLOCKER_CAP - len(blockers))
    if len(refs) > available:
        complete = False
        _notice(result, f"Ticket #{number} blockers stop after the first {BLOCKER_CAP} records.")
    for blocker_number in refs[:available]:
        blocker, observed = _blocker(reader, result, blocker_number)
        complete = complete and observed
        if blocker is not None:
            blockers.append(blocker)
    blockers.sort(key=lambda item: item.get("number") or 0)
    row["blockers"] = blockers
    return row, body, complete


def _accepted(result: dict, cfg, ticket: int, cache: dict[int, object]) -> object:
    if ticket in cache:
        return cache[ticket]
    try:
        cache[ticket] = binding.accepted(cfg, ticket)
    except binding.BindingError as exc:
        cache[ticket] = exc
        _error(result, exc.code, ".factory/events.jsonl", f"ticket:{ticket}")
    return cache[ticket]


def _drift(result: dict, cfg, ticket: int, accepted: object, targeted: bool,
           observed: object) -> dict:
    if isinstance(accepted, binding.BindingError):
        return {
            "ticket": ticket, "status": "unavailable", "baseline": None, "observed": None,
            "changed_sections": [],
            "proposed_question": f"Ticket #{ticket} accepted binding evidence is unavailable. Can a human restore or verify it?",
            "reason": str(accepted), "sources": [],
        }
    if accepted is None:
        return {
            "ticket": ticket, "status": "unknown", "baseline": None, "observed": None,
            "changed_sections": [],
            "proposed_question": f"Ticket #{ticket} has no accepted plan binding. Should a human review and baseline a new ticket through intake?",
            "reason": "No accepted plan binding evidence is available; drift is unknown, not unchanged.",
            "sources": [],
        }
    baseline = accepted["baseline"]
    accepted_source = _cite(result, f"Accepted plan binding for #{ticket}", {
        "ticket": ticket, "initiative": baseline["initiative"], "revision": _revision(baseline),
    }, path=".factory/events.jsonl")
    if isinstance(observed, (binding.BindingError, EvidenceError)) or observed is None:
        code = observed.code if isinstance(observed, (binding.BindingError, EvidenceError)) else "source_unavailable"
        reason = str(observed) if observed is not None else f"initiative #{baseline['initiative']} source is unavailable"
        _error(result, code, f"repos/{cfg.repo}/issues/{baseline['initiative']}", f"ticket:{ticket}")
        report = {
            "status": "unavailable", "baseline": baseline, "observed": None,
            "changed_sections": [],
            "proposed_question": (
                f"Initiative #{baseline['initiative']} is unavailable. Can a human verify the source "
                "while the accepted ticket baseline remains unchanged?"
            ),
            "reason": reason,
        }
    else:
        try:
            report = binding.compare(cfg, baseline, observed)
        except binding.BindingError as exc:
            _error(result, exc.code, f"repos/{cfg.repo}/issues/{baseline['initiative']}", f"ticket:{ticket}")
            report = {
                "status": "unavailable", "baseline": baseline, "observed": None,
                "changed_sections": [],
                "proposed_question": f"Ticket #{ticket} binding comparison is unavailable. Can a human verify its accepted and current revisions?",
                "reason": str(exc),
            }
    observed_source = None
    if report["observed"] is not None:
        observed_source = _cite(result, f"Observed initiative #{baseline['initiative']} revision for ticket #{ticket}", {
            "ticket": ticket, "initiative": baseline["initiative"],
            "revision": _revision(report["observed"]), "changed_sections": report["changed_sections"],
        }, url=report["observed"]["source_url"])
    return {
        "ticket": ticket, "status": report["status"],
        "baseline": report["baseline"] if targeted else _revision(report["baseline"]),
        "observed": report["observed"] if targeted else _revision(report["observed"]),
        "changed_sections": report["changed_sections"],
        "proposed_question": report["proposed_question"], "reason": report.get("reason"),
        "sources": [item for item in (accepted_source, observed_source) if item],
    }


def _historical_candidates(result: dict, cfg) -> tuple[list[int], list[dict]]:
    try:
        events = lifecycle.read_events(cfg.factory / "events.jsonl")
    except OSError:
        _error(result, "source_unavailable", ".factory/events.jsonl", "history")
        return [], []
    candidates = []
    seen = set()
    total = 0
    for row in reversed(events):
        ticket = row.get("ticket")
        if row.get("event") != "plan-bound" or type(ticket) is not int or ticket <= 0 or ticket in seen:
            continue
        total += 1
        seen.add(ticket)
        if len(candidates) < HISTORY_CAP:
            candidates.append(ticket)
    if total > HISTORY_CAP:
        _notice(result, f"Historical binding recovery covers the latest {HISTORY_CAP} of {total} bound tickets.")
    return candidates, events


def _plan_source(result: dict, row: dict) -> None:
    sections = row.get("sections") or {}
    citation = _cite(result, f"Initiative #{row['number']} plan", {
        "number": row["number"], "title": row.get("title"), "state": row.get("state"),
        "declared_status": row.get("status"), "declared_owner": row.get("owner"),
        "historical": bool(row.get("historical")),
        "revision": row.get("revision"),
        "sections": {key: sections.get(key) for key in ("Outcome", "Areas", "Plan", "Open decisions")},
        "implementation_links": row.get("links") or [], "problems": row.get("problems") or [],
    }, url=row.get("url"))
    if citation:
        row["source"] = citation


def _canonical(reader: plan.Reader, result: dict, cfg, number: int,
               cache: dict[int, object]) -> object:
    if number in cache:
        return cache[number]
    try:
        issue = reader.fetch(f"Complete initiative #{number}", f"issues/{number}")
        cache[number] = binding.from_issue(cfg, number, issue)
    except EvidenceError as exc:
        failed(reader.result, exc)
        cache[number] = exc
    except binding.BindingError as exc:
        _error(result, exc.code, f"repos/{cfg.repo}/issues/{number}", f"initiative:{number}")
        cache[number] = exc
    return cache[number]


def _admission_readiness(cfg, body: str, accepted: object,
                         observed: object) -> tuple[bool | None, str | None]:
    try:
        initiative = binding.linked(body)
        if initiative is None:
            if isinstance(accepted, binding.BindingError):
                return None, "Accepted plan binding evidence is invalid or unavailable; admission readiness is unknown."
            if isinstance(accepted, dict):
                return None, "A previously bound ticket no longer retains its Initiative declaration and Plan baseline; admission would refuse it."
            return True, None
        pinned = binding.baseline(body, cfg.repo)
        if pinned is None:
            return None, f"Linked ticket for initiative #{initiative} has no Plan baseline; admission would refuse it."
        if isinstance(accepted, binding.BindingError):
            return None, "Accepted plan binding evidence is invalid or unavailable; admission readiness is unknown."
        if isinstance(observed, (binding.BindingError, EvidenceError)) or observed is None:
            return None, f"Initiative #{initiative} canonical source is unavailable; admission readiness is unknown."
        compared = binding.compare(cfg, pinned, observed)
        if compared["status"] == "changed":
            return None, (
                f"Initiative #{initiative} changed in {', '.join(compared['changed_sections'])}; "
                "the pinned ticket baseline would be refused."
            )
        return True, None
    except binding.BindingError as exc:
        return None, f"Plan binding is invalid; admission would refuse it: {exc}"


def collect(cfg, number: int | None = None, *, deadline: float | None = None) -> dict:
    """Collect the canonical roadmap or one detailed initiative observation."""
    result = {
        "schema_version": 1, "ok": True,
        "scope": {"repository": cfg.repo},
        "observation_id": uuid4().hex,
        "coverage": {"status": "bounded", "notices": list(NOTICES)},
        "errors": [], "sources": [], "plans": [], "attention": [],
    }
    if number is not None:
        if type(number) is not int or number <= 0:
            raise ValueError("initiative must be a positive integer")
        result["scope"]["initiative"] = number
    stop = deadline if deadline is not None else time.monotonic() + READ_SECONDS
    scratch = {"ok": True, "coverage": {"status": "bounded", "notices": []},
               "sources": [], "errors": [], "plans": [], "plan": None, "route": None}
    reader = plan.Reader(cfg, scratch)
    reader.deadline = stop
    candidates, events = _historical_candidates(result, cfg)
    accepted_cache: dict[int, object] = {}

    try:
        if time.monotonic() >= stop:
            raise EvidenceError("collection_timeout", "Roadmap collection deadline exceeded.", "roadmap")
        if number is None:
            reader.list()
            discovered = scratch["plans"]
        else:
            reader.inspect(number)
            discovered = [scratch["plan"]] if scratch["plan"] else []
    except EvidenceError as exc:
        failed(scratch, exc)
        discovered = []

    accepted_rows: dict[int, dict] = {}
    for ticket in candidates:
        accepted = _accepted(result, cfg, ticket, accepted_cache)
        if isinstance(accepted, dict):
            accepted_rows[ticket] = accepted

    by_initiative = {}
    for event in accepted_rows.values():
        by_initiative.setdefault(event["baseline"]["initiative"], event["baseline"])
    if number is not None and not discovered and number in by_initiative:
        baseline = by_initiative[number]
        discovered = [{
            "number": number, "title": "", "state": "UNKNOWN", "url": baseline["source_url"],
            "status": None, "owner": None,
            "sections": {name: text[:plan.SECTION_CAP] for name, text in baseline["sections"].items()},
            "links": [],
            "problems": ["Live initiative unavailable; retained accepted binding evidence is shown."],
            "malformed": False, "historical": True,
        }]
    elif number is None:
        live_numbers = {row["number"] for row in discovered}
        historical = [{
            "number": initiative, "title": "", "state": "UNKNOWN", "url": baseline["source_url"],
            "status": None, "owner": None,
            "sections": {name: text[:plan.SECTION_CAP] for name, text in baseline["sections"].items()},
            "links": [],
            "problems": ["Live initiative unavailable or absent from bounded list coverage; retained accepted binding evidence is shown."],
            "malformed": False, "historical": True,
        } for initiative, baseline in sorted(by_initiative.items()) if initiative not in live_numbers]
        if historical:
            discovered = [*discovered, *historical]
            _notice(result, "Retained accepted bindings supply historical initiatives absent from the live bounded list; their current state is unknown.")

    # Factory's own claim receipt (`claimed`, then `pr-opened`) is the only evidence
    # that an assignment is Factory's; `escalate` unassigns it. Same receipt-based
    # takeover detection as handoff: no receipt means a human holds the ticket.
    latest_escalation, factory_claim = {}, {}
    for event in events:
        ticket = event.get("ticket")
        if type(ticket) is not int:
            continue
        if event.get("event") == "escalate":
            latest_escalation[ticket] = event
            factory_claim.pop(ticket, None)
        elif event.get("event") in ("claimed", "pr-opened"):
            factory_claim[ticket] = event

    runtime = None
    canonical_cache: dict[int, object] = {}
    for discovered_row in discovered:
        if time.monotonic() >= stop:
            failed(scratch, EvidenceError("collection_timeout", "Roadmap collection deadline exceeded.", "roadmap"))
            _notice(result, "Roadmap enrichment stopped at its deadline; later initiatives are absent.")
            break
        initiative = discovered_row["number"]
        if discovered_row.get("historical"):
            discovered_row["problems"].extend(
                f"truncated section: {name} cut to {plan.SECTION_CAP} characters"
                for name, text in by_initiative[initiative]["sections"].items()
                if len(text) > plan.SECTION_CAP
            )
        try:
            raw = reader.fetch(f"Complete initiative #{initiative}", f"issues/{initiative}")
            observed_row = plan.record(raw, f"issues/{initiative}")
            if not observed_row["initiative"] or observed_row["pull_request"]:
                raise EvidenceError("not_initiative", f"Issue #{initiative} is not an initiative.", f"issues/{initiative}")
            discovered_row = observed_row
            canonical = binding.from_issue(cfg, initiative, raw)
        except (EvidenceError, binding.BindingError) as exc:
            canonical = exc
            if isinstance(exc, EvidenceError):
                failed(scratch, exc)
            else:
                _error(result, exc.code, f"repos/{cfg.repo}/issues/{initiative}", f"initiative:{initiative}")
        canonical_cache[initiative] = canonical
        revision = _revision(
            by_initiative.get(initiative) if discovered_row.get("historical") else canonical
        )
        row = {key: discovered_row.get(key) for key in (
            "number", "title", "state", "url", "status", "owner", "sections", "links", "problems", "malformed"
        )}
        row.update(historical=bool(discovered_row.get("historical")), revision=revision, children=[], blockers=[], drift=[], delivery={
            "status": "unknown",
            "detail": "Owner-confirmed success evidence was not established by this producer; declared stage and implementation state do not prove delivery.",
        })
        _plan_source(result, row)


        linked = list(row["links"] or [])
        for ticket, event in accepted_rows.items():
            if event["baseline"]["initiative"] == initiative and ticket not in linked:
                linked.append(ticket)
        linked = linked[:plan.LINKS]
        if len(set(row["links"] or []) | {ticket for ticket, event in accepted_rows.items()
                                        if event["baseline"]["initiative"] == initiative}) > plan.LINKS:
            _notice(result, f"Initiative #{initiative} implementation evidence stops after {plan.LINKS} tickets.")

        for ticket in linked:
            if time.monotonic() >= stop:
                failed(scratch, EvidenceError("collection_timeout", "Roadmap collection deadline exceeded.", "roadmap"))
                _notice(result, f"Initiative #{initiative} enrichment stopped at its deadline; later implementation evidence is absent.")
                break
            child, body, blockers_complete = _child(reader, result, ticket)
            accepted = _accepted(result, cfg, ticket, accepted_cache)
            bound_initiative = accepted["baseline"]["initiative"] if isinstance(accepted, dict) else initiative
            observed = _canonical(reader, result, cfg, bound_initiative, canonical_cache)
            drift = _drift(result, cfg, ticket, accepted, number is not None, observed)
            row["drift"].append(drift)
            escalation = latest_escalation.get(ticket)
            escalation_source = None
            if escalation is not None:
                escalation_source = _cite(result, f"Escalation for #{ticket}", {
                    "ticket": ticket,
                    "reason": clean_text(str(escalation.get("reason") or ""))[:1000],
                    "paths": [clean_text(value).strip("/") for value in escalation.get("paths") or []
                              if isinstance(value, str) and value][:plan.PATHS],
                }, path=".factory/events.jsonl")
            if "unavailable" in child:
                row["children"].append(child)
                ticket_url = f"https://github.com/{cfg.repo}/issues/{ticket}"
                readiness = "Ticket evidence is unavailable, so run readiness is unknown."
                if escalation is not None:
                    reason_text = clean_text(str(escalation.get("reason") or ""))[:1000]
                    paths = [clean_text(value).strip("/") for value in escalation.get("paths") or []
                             if isinstance(value, str) and value][:plan.PATHS]
                    route_reason = "ci" if "CI failed" in reason_text else "implementation"
                    result["attention"].append({
                        "id": f"initiative-{initiative}-ticket-{ticket}-escalation",
                        "initiative": initiative, "ticket": ticket,
                        "question": f"How should #{ticket} proceed after escalation: {reason_text or 'reason unavailable'}?",
                        "kind": "escalation", "route": _unknown_route(route_reason, paths),
                        "runnable": None, "readiness_reason": readiness, "url": ticket_url,
                        "sources": [escalation_source] if escalation_source else [],
                    })
                if drift["proposed_question"]:
                    result["attention"].append({
                        "id": f"initiative-{initiative}-ticket-{ticket}-drift",
                        "initiative": initiative, "ticket": ticket,
                        "question": drift["proposed_question"], "kind": "plan_drift",
                        "route": _unknown_route("requirements"), "runnable": None,
                        "readiness_reason": readiness, "url": ticket_url,
                        "sources": drift["sources"],
                    })
                continue
            child_blockers = child["blockers"]
            for blocker in child_blockers:
                if blocker.get("number") not in {item.get("number") for item in row["blockers"]}:
                    row["blockers"].append(blocker)
            labels = set(child["labels"])
            active = False
            runtime_uncertain = False
            runtime_source = None
            claim = None
            if child["assignees"]:
                if runtime is None:
                    runtime = runtime_events.project(cfg.factory / "events.jsonl")
                states = {execution.get("state") for execution in runtime["executions"]
                          if execution.get("ticket") == ticket}
                active = "active" in states
                runtime_uncertain = bool(states & {"unknown", "interrupted"}) or runtime.get("history", {}).get("complete") is not True
                if runtime_uncertain:
                    _error(result, "runtime_unavailable", ".factory/events.jsonl", f"ticket:{ticket}")
                claim = factory_claim.get(ticket)
                runtime_source = _cite(result, f"Runtime execution projection for #{ticket}", {
                    "ticket": ticket, "active_execution_observed": active,
                    "execution_states": sorted(state for state in states if state),
                    "factory_claim": {key: claim.get(key) for key in ("event", "pr", "at")} if claim else None,
                    "history": runtime.get("history"), "errors": runtime.get("errors"),
                }, path=".factory/events.jsonl")
            linked_initiative = None
            try:
                linked_initiative = binding.linked(body)
            except binding.BindingError:
                pass
            admission_observed = (
                _canonical(reader, result, cfg, linked_initiative, canonical_cache)
                if linked_initiative is not None else canonical
            )
            admission_ready, admission_reason = _admission_readiness(
                cfg, body or "", accepted, admission_observed
            )
            open_blockers = [item for item in child_blockers if item.get("state") == "OPEN"]
            if child["state"] != "OPEN":
                child["runnable"] = None
                child["readiness_reason"] = f"Ticket state is {child['state']}; it is not claimable."
            elif active:
                child["runnable"] = None
                child["readiness_reason"] = "An active Factory execution was observed; the ticket is not available for another claim."
            elif child["assignees"] and runtime_uncertain:
                child["runnable"] = None
                child["readiness_reason"] = "Assigned; the dispatcher will not claim it. Existing execution status is unknown, so a human must verify responsibility before takeover or retry."
            elif child["assignees"] and claim is not None:
                child["runnable"] = None
                pr_ref = f" #{claim['pr']}" if type(claim.get("pr")) is int else ""
                child["readiness_reason"] = (
                    f"Assigned by Factory's own claim; its PR{pr_ref} is in Factory's review/merge pipeline. This is not a human takeover."
                    if claim.get("event") == "pr-opened" else
                    "Assigned by Factory's own claim with no PR recorded yet; this is not a human takeover."
                )
            elif child["assignees"]:
                child["runnable"] = False
                child["readiness_reason"] = (
                    f"Assigned to {', '.join('@' + owner for owner in child['assignees'])}; the dispatcher skips assigned tickets. This is human takeover."
                )
            elif open_blockers:
                child["runnable"] = None
                child["readiness_reason"] = "Open blockers prevent dispatch: " + ", ".join(f"#{item['number']}" for item in open_blockers)
            elif not blockers_complete:
                child["runnable"] = None
                child["readiness_reason"] = "Blocker evidence is incomplete, so run readiness is unknown."
            elif config.LABEL_AGENT not in labels:
                child["runnable"] = None
                child["readiness_reason"] = "The ticket does not carry ready-for-agent; no execution readiness is inferred."
            elif admission_ready is not True:
                child["runnable"] = None
                child["readiness_reason"] = admission_reason or "Plan-binding admission readiness is unknown."
            else:
                child["runnable"] = True
                child["readiness_reason"] = "Open, unassigned, ready-for-agent, with no open blocker and an admissible plan binding observed."
            child_source = _cite(result, f"Implementation ticket #{ticket}", child, url=child["url"])
            if child_source:
                child["source"] = child_source
            row["children"].append(child)

            if child["assignees"] and child["state"] == "OPEN" and not active and (runtime_uncertain or claim is None):
                route, citations = _route(reader, result, ticket, "implementation", [], child["url"])
                result["attention"].append({
                    "id": f"initiative-{initiative}-ticket-{ticket}-takeover", "initiative": initiative,
                    "ticket": ticket,
                    "question": (
                        f"Who can verify whether Factory still holds #{ticket} before arranging human takeover?"
                        if runtime_uncertain else f"What should happen next on #{ticket} while its human assignment keeps Factory from claiming it?"
                    ),
                    "kind": "execution_unknown" if runtime_uncertain else "human_takeover",
                    "route": route, "runnable": child["runnable"],
                    "readiness_reason": child["readiness_reason"], "url": child["url"],
                    "sources": citations + ([child_source] if child_source else []) + ([runtime_source] if runtime_source else []),
                })
            if escalation is not None:
                reason_text = clean_text(str(escalation.get("reason") or ""))[:1000]
                paths = [clean_text(value).strip("/") for value in escalation.get("paths") or []
                         if isinstance(value, str) and value][:plan.PATHS]
                route_reason = "ci" if "CI failed" in reason_text else "implementation"
                route, citations = _route(reader, result, ticket, route_reason, paths, child["url"])
                result["attention"].append({
                    "id": f"initiative-{initiative}-ticket-{ticket}-escalation", "initiative": initiative,
                    "ticket": ticket,
                    "question": f"How should #{ticket} proceed after escalation: {reason_text or 'reason unavailable'}?",
                    "kind": "escalation", "route": route, "runnable": child["runnable"],
                    "readiness_reason": child["readiness_reason"], "url": child["url"],
                    "sources": citations + ([child_source] if child_source else []) + ([escalation_source] if escalation_source else []) + ([runtime_source] if runtime_source else []),
                })
            if drift["proposed_question"]:
                route, citations = _route(reader, result, ticket, "requirements", [], child["url"])
                result["attention"].append({
                    "id": f"initiative-{initiative}-ticket-{ticket}-drift", "initiative": initiative,
                    "ticket": ticket, "question": drift["proposed_question"], "kind": "plan_drift",
                    "route": route, "runnable": child["runnable"],
                    "readiness_reason": child["readiness_reason"], "url": child["url"],
                    "sources": list(dict.fromkeys(drift["sources"] + citations + ([child_source] if child_source else []) + ([runtime_source] if runtime_source else []))),
                })

        decisions = (row["sections"] or {}).get("Open decisions")
        if decisions:
            route, citations = _route(reader, result, initiative, "requirements", [], row["url"])
            plan_source = [row["source"]] if row.get("source") else []
            result["attention"].append({
                "id": f"initiative-{initiative}-open-decisions", "initiative": initiative, "ticket": None,
                "question": decisions, "kind": "open_decisions", "route": route, "runnable": None,
                "readiness_reason": "This is a declared initiative question; ticket run readiness does not apply.",
                "url": row["url"], "sources": plan_source + citations,
            })
        row["blockers"].sort(key=lambda item: item.get("number") or 0)
        result["plans"].append(row)

    if len(result["attention"]) > ATTENTION_CAP:
        result["attention"] = result["attention"][:ATTENTION_CAP]
        _notice(result, f"Owner attention stops after {ATTENTION_CAP} questions.")
    _copy_errors(result, scratch)
    if result["errors"]:
        result["ok"] = False
        result["coverage"]["status"] = "partial" if result["plans"] or result["sources"] else "unavailable"
    elif len(result["coverage"]["notices"]) > len(NOTICES):
        result["coverage"]["status"] = "bounded"
    else:
        result["coverage"]["status"] = "complete"
    result["observed_at"] = datetime.now(UTC).isoformat()
    return result
