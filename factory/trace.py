"""Structured routing decisions and the read-only explanation projection."""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from factory import config, lifecycle, runtime_events
from factory.decisions import _SECRET

STAGES = ("triage", "binding", "admission", "worker_select", "claim")
ALL_STAGES = (
    *STAGES,
    "plan", "gate", "review", "manager", "merge", "wontfix", "escalate", "learn", "curate",
)
RELATIONS = frozenset({"follows", "retries", "blocked_by", "split_from", "unblocked_by", "overrides"})
ACTORS = frozenset({"deterministic", "model", "human", "worker"})
INPUT_LIMIT = 16
CAUSE_LIMIT = 16
OPTION_LIMIT = 512
TEXT_LIMIT = 256
RATIONALE_LIMIT = 2_000
LEGACY = frozenset({"claimed", "attempt", "review", "approved", "merged", "escalate"})
_SPLIT = re.compile(r"(?i)(?:\(|\b)split from\s+#([1-9][0-9]*)")
_BLOCKED = re.compile(r"(?im)^blocked by:(.*)$")
_REF = re.compile(r"#([1-9][0-9]*)")


def _text(value: object, limit: int = TEXT_LIMIT) -> str:
    text = "".join(character if ord(character) >= 32 else " " for character in str(value or ""))
    text = _SECRET.sub("[credential withheld]", text)
    return text[:limit]


def _input(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    return {
        "kind": _text(value.get("kind") or "unknown", 64),
        "ref": _text(value.get("ref") or ".factory/events.jsonl"),
        "summary": _text(value.get("summary") or "unspecified"),
    }


def _option(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    return {
        "option": _text(value.get("option") or "unknown"),
        "eligible": value.get("eligible") is True,
        "reason": _text(value.get("reason") or "not selected"),
    }


def _cause(value: object) -> dict | None:
    value = value if isinstance(value, dict) else {}
    relation = value.get("relation")
    if relation not in RELATIONS:
        return None
    if type(value.get("ticket")) is int and value["ticket"] > 0:
        return {"ticket": value["ticket"], "relation": relation}
    decision_id = _text(value.get("decision_id"))
    if decision_id:
        return {"decision_id": decision_id, "relation": relation}
    return None


def _actor(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    kind = value.get("kind") if value.get("kind") in ACTORS else "deterministic"
    return {
        "kind": kind,
        "name": _text(value.get("name") or "factory"),
        "model": _text(value["model"]) if value.get("model") is not None else None,
        "version": _text(value["version"]) if value.get("version") is not None else None,
    }


def _cost(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens"):
        number = value.get(key, 0)
        result[key] = number if type(number) is int and number >= 0 else 0
    seconds = value.get("seconds", 0)
    result["seconds"] = seconds if type(seconds) in (int, float) and seconds >= 0 else 0
    return result


def decision(
    *,
    ticket: int,
    stage: str,
    inputs: list[dict],
    options: list[dict],
    chosen: str,
    rationale: str,
    causes: list[dict] | None,
    cost: dict | None,
    actor: dict,
    pr: int | None = None,
    at: str | None = None,
    decision_id: str | None = None,
) -> dict:
    """Build one bounded schema-1 decision payload for ``dispatch.record``."""
    normalized_options = [_option(value) for value in options[:OPTION_LIMIT]]
    chosen = _text(chosen)
    if chosen and not any(value["option"] == chosen for value in normalized_options):
        normalized_options.append({"option": chosen, "eligible": True, "reason": "selected"})
    row = {
        "schema_version": 1,
        "decision_id": decision_id or str(uuid.uuid4()),
        "ticket": ticket,
        "stage": stage,
        "at": at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": [_input(value) for value in inputs[:INPUT_LIMIT]],
        "options": normalized_options,
        "chosen": chosen,
        "rationale": _text(rationale, RATIONALE_LIMIT),
        "causes": [cause for value in (causes or [])[:CAUSE_LIMIT] if (cause := _cause(value)) is not None],
        "cost": _cost(cost),
        "actor": _actor(actor),
    }
    if type(pr) is int and pr > 0:
        row["pr"] = pr
    selected_option = next(value for value in normalized_options if value["option"] == chosen)
    envelope = {
        "event": "decision",
        **row,
        "execution_id": "00000000-0000-4000-8000-000000000000",
        "dispatcher_run_id": "00000000-0000-4000-8000-000000000000",
    }
    while len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()) + 1 \
            > runtime_events.ROW_LIMIT:
        removable = next((
            index for index in range(len(normalized_options) - 1, -1, -1)
            if normalized_options[index] is not selected_option
        ), None)
        if removable is None:
            raise ValueError("decision record exceeds journal row limit")
        normalized_options.pop(removable)
    validate({"event": "decision", **row})
    return row


def _utc(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def _uuid4(value: object) -> bool:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _bounded_text(value: object, limit: int = TEXT_LIMIT) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and not _SECRET.search(value)


def validate(row: dict) -> dict:
    """Validate one complete journal decision row; return it unchanged."""
    required = {
        "event", "schema_version", "decision_id", "ticket", "stage", "at", "inputs", "options",
        "chosen", "rationale", "causes", "cost", "actor",
    }
    if not isinstance(row, dict) or not required <= row.keys() or row.get("event") != "decision":
        raise ValueError("invalid decision record fields")
    if row["schema_version"] != 1 or not _uuid4(row["decision_id"]):
        raise ValueError("invalid decision record identity")
    if type(row["ticket"]) is not int or row["ticket"] <= 0 or row["stage"] not in ALL_STAGES or not _utc(row["at"]):
        raise ValueError("invalid decision ticket, stage, or timestamp")
    if "pr" in row and (type(row["pr"]) is not int or row["pr"] <= 0):
        raise ValueError("invalid decision PR")
    if not isinstance(row["inputs"], list) or len(row["inputs"]) > INPUT_LIMIT or any(
        not isinstance(value, dict) or set(value) != {"kind", "ref", "summary"}
        or not all(_bounded_text(value[key], 64 if key == "kind" else TEXT_LIMIT) for key in value)
        for value in row["inputs"]
    ):
        raise ValueError("invalid decision inputs")
    if not isinstance(row["options"], list) or not row["options"] or len(row["options"]) > OPTION_LIMIT + 1 or any(
        not isinstance(value, dict) or set(value) != {"option", "eligible", "reason"}
        or not _bounded_text(value["option"]) or type(value["eligible"]) is not bool
        or not _bounded_text(value["reason"])
        for value in row["options"]
    ):
        raise ValueError("invalid decision options")
    if not _bounded_text(row["chosen"]) or not any(value["option"] == row["chosen"] for value in row["options"]):
        raise ValueError("invalid chosen decision option")
    if not isinstance(row["rationale"], str) or len(row["rationale"]) > RATIONALE_LIMIT or _SECRET.search(row["rationale"]):
        raise ValueError("invalid decision rationale")
    if not isinstance(row["causes"], list) or len(row["causes"]) > CAUSE_LIMIT or any(
        not isinstance(value, dict) or value.get("relation") not in RELATIONS
        or (set(value) == {"ticket", "relation"} and (type(value["ticket"]) is not int or value["ticket"] <= 0))
        or (set(value) == {"decision_id", "relation"} and not _uuid4(value["decision_id"]))
        or set(value) not in ({"ticket", "relation"}, {"decision_id", "relation"})
        for value in row["causes"]
    ):
        raise ValueError("invalid decision causes")
    if row["cost"] is not None and (
        not isinstance(row["cost"], dict)
        or set(row["cost"]) != {"prompt_tokens", "completion_tokens", "cached_tokens", "seconds"}
        or any(type(row["cost"][key]) is not int or row["cost"][key] < 0
               for key in ("prompt_tokens", "completion_tokens", "cached_tokens"))
        or type(row["cost"]["seconds"]) not in (int, float) or row["cost"]["seconds"] < 0
    ):
        raise ValueError("invalid decision cost")
    actor = row["actor"]
    if not isinstance(actor, dict) or set(actor) != {"kind", "name", "model", "version"} \
            or actor["kind"] not in ACTORS or not _bounded_text(actor["name"]) \
            or any(actor[key] is not None and not _bounded_text(actor[key]) for key in ("model", "version")):
        raise ValueError("invalid decision actor")
    return row


def ticket_causes(title: object = "", body: object = "", *, parent: int | None = None) -> list[dict]:
    """Extract the cross-ticket relations already present in a ticket or manager receipt."""
    text = f"{title or ''}\n{body or ''}"
    split = {int(number) for number in _SPLIT.findall(text)}
    if type(parent) is int and parent > 0:
        split.add(parent)
    blocked = {int(number) for line in _BLOCKED.findall(str(body or "")) for number in _REF.findall(line)}
    return ([{"ticket": number, "relation": "split_from"} for number in sorted(split)]
            + [{"ticket": number, "relation": "blocked_by"} for number in sorted(blocked)])[:CAUSE_LIMIT]


def _legacy(row: dict, index: int) -> dict:
    event = row["event"]
    details = []
    for key in ("worker", "attempt", "gate", "verdict", "reason", "pr"):
        if row.get(key) is not None:
            details.append(f"{key}={row[key]}")
    return {
        "decision_id": f"legacy:{row['ticket']}:{index}:{event}",
        "ticket": row["ticket"],
        "stage": "claim" if event == "claimed" else event,
        "at": _text(row.get("at") or "unknown"),
        "chosen": event,
        "rationale": _text(", ".join(details) or event),
        "synthesized": True,
        "event": event,
    }


def _sum_cost(nodes: list[dict]) -> dict:
    total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "seconds": 0,
    }
    for node in nodes:
        cost = node.get("cost")
        if not isinstance(cost, dict):
            continue
        for key in total:
            value = cost.get(key, 0)
            if type(value) in (int, float) and value >= 0:
                total[key] += value
    return total


def _terminal(node: dict) -> bool:
    return (
        node.get("stage") == "wontfix"
        or node.get("stage") == "merge" and node.get("chosen") == "merge now"
        or node.get("event") == "merged"
    )


def _frontier_stage(node: dict) -> str | None:
    stage = node.get("stage")
    chosen = node.get("chosen")
    if _terminal(node):
        return None
    if stage in {"admission", "worker_select"}:
        return "gate"
    if stage == "claim" or node.get("event") == "claimed":
        return "plan"
    if stage == "plan":
        return "gate"
    if stage == "gate" or node.get("event") == "attempt":
        return "review" if chosen == "pass" or "PASS" in str(node.get("rationale", "")) else "gate"
    if stage == "review" or node.get("event") == "review":
        return "merge eligibility" if chosen == "APPROVE" or "APPROVE" in str(node.get("rationale", "")) else "gate"
    if stage == "manager":
        if chosen in {"RETRY", "REWRITE", "ROUTE", "AUTHORIZE", "FIX"}:
            return "gate"
        return "human" if chosen == "HUMAN" else "merge eligibility" if chosen == "APPROVE" else "manager"
    if stage == "escalate" or node.get("event") == "escalate":
        return "human"
    if stage == "merge":
        return "gate" if chosen in {"refresh branch", "conflict-requeue", "gate-requeue"} else "merge eligibility"
    if node.get("event") == "approved":
        return "merge eligibility"
    return stage


def explain(rows: list[dict], ticket: int) -> dict:
    """Project journal rows into a bounded graph without I/O or mutation."""
    nodes = []
    decisions = []
    ordered = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("ticket") != ticket:
            continue
        if row.get("event") == "decision":
            try:
                validate(row)
            except ValueError:
                continue
            node = dict(row)
            nodes.append(node)
            decisions.append(node)
            ordered.append(node)
        elif row.get("event") in LEGACY:
            node = _legacy(row, index)
            nodes.append(node)
            ordered.append(node)

    edges = []
    seen_edges = set()

    def edge(source: str, target: str, relation: str) -> None:
        key = source, target, relation
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append({"from": source, "to": target, "relation": relation})

    external = {}
    for node in decisions:
        for cause in node["causes"]:
            if "decision_id" in cause:
                source = cause["decision_id"]
            else:
                source = f"ticket:{cause['ticket']}"
                external.setdefault(source, {
                    "decision_id": source,
                    "ticket": cause["ticket"],
                    "stage": "ticket",
                    "chosen": "ticket",
                    "rationale": "cross-ticket cause",
                    "synthesized": True,
                })
            edge(source, node["decision_id"], cause["relation"])
    for previous, current in pairwise(ordered):
        edge(previous["decision_id"], current["decision_id"], "follows")

    positions = {node["decision_id"]: index for index, node in enumerate(ordered)}
    loops = []
    retry_sources = dict.fromkeys(
        value["from"] for value in edges
        if value["relation"] == "retries" and value["from"] in positions
    )
    for source in retry_sources:
        start = positions[source]
        stop = next((
            index for index in range(start + 1, len(ordered))
            if ordered[index].get("stage") in {"review", "escalate"}
            or ordered[index]["decision_id"] in retry_sources
        ), len(ordered))
        members = ordered[start:stop]
        loops.append({
            "nodes": [node["decision_id"] for node in members],
            "cost": _sum_cost(members),
        })

    nodes = [*external.values(), *nodes]
    present = {row["stage"] for row in decisions}
    flow = [node for node in ordered if node.get("stage") not in {"learn", "curate"}]
    last = flow[-1] if flow else None
    terminal = bool(last and _terminal(last))
    if terminal or not decisions:
        expected = STAGES
    else:
        last_stage = next((
            node["stage"] for node in reversed(flow) if node.get("stage") in STAGES
        ), STAGES[-1])
        expected = STAGES[:STAGES.index(last_stage) + 1]
    blocked = bool(last and last.get("stage") in {"triage", "binding"}) and any(
        cause["relation"] == "blocked_by"
        for node in decisions for cause in node["causes"]
    ) and not any(
        cause["relation"] == "unblocked_by"
        for node in decisions for cause in node["causes"]
    )
    graph = {
        "ticket": ticket,
        "nodes": nodes,
        "edges": edges,
        "loops": loops,
        "cost": _sum_cost(decisions),
        "frontier": None if terminal or last is None else last["decision_id"],
        "frontier_stage": None if terminal or last is None else "blocker" if blocked else _frontier_stage(last),
        "coverage": [stage for stage in expected if stage not in present],
    }
    while len(json.dumps(graph, ensure_ascii=False).encode()) > runtime_events.BYTE_LIMIT and graph["nodes"]:
        removed = graph["nodes"].pop(0)["decision_id"]
        graph["edges"] = [value for value in graph["edges"] if removed not in (value["from"], value["to"])]
        graph["loops"] = [
            {**loop, "nodes": [node for node in loop["nodes"] if node != removed]}
            for loop in graph["loops"] if any(node != removed for node in loop["nodes"])
        ]
        if graph["frontier"] == removed:
            graph["frontier"] = graph["nodes"][-1]["decision_id"] if graph["nodes"] else None
    return graph


def read_rows(path: Path, since: str | None = None) -> list[dict]:
    """Read the live and rotated journal, optionally from one UTC instant."""
    if since is None:
        return lifecycle.read_events(path)
    if not _utc(since):
        raise ValueError("--since must be an ISO UTC timestamp")
    threshold = datetime.fromisoformat(since)
    return [row for row in lifecycle.read_events(path)
            if _utc(row.get("at")) and datetime.fromisoformat(row["at"]) >= threshold]


def _text_tree(graph: dict) -> str:
    lines = [f"ticket #{graph['ticket']}"]
    external = {node["decision_id"] for node in graph["nodes"] if node.get("stage") == "ticket"}
    depth = 1
    for node in graph["nodes"]:
        if node["decision_id"] in external:
            continue
        marker = " (synthesized)" if node.get("synthesized") else ""
        lines.append(f"{'  ' * depth}{node['stage']}: {node['chosen']}{marker} — {node['rationale']}")
        depth += 1
    for value in graph["edges"]:
        if value["from"] in external:
            lines.append(f"  {value['relation']}: #{value['from'].removeprefix('ticket:')}")
    if graph["coverage"]:
        lines.append("  coverage missing: " + ", ".join(graph["coverage"]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory explain", description="Explain one ticket's recorded routing decisions.")
    parser.add_argument("ticket", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--since")
    args = parser.parse_args(argv)
    if args.ticket <= 0:
        parser.error("ticket must be a positive integer")
    cfg = config.load()
    try:
        graph = explain(read_rows(cfg.factory / "events.jsonl", args.since), args.ticket)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(graph, ensure_ascii=False, separators=(",", ":")) if args.json else _text_tree(graph))
    return 0
