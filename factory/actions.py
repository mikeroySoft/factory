"""Named human decisions: one schema, precondition set and effect per action.

Every caller (dashboard button, `factory mcp` tool) compiles an action through
`prepare` into the existing `decisions` request ops, so validation, locks,
journaling and receipts come from one path.
"""
from __future__ import annotations

from factory import decisions, dispatch
from factory.config import LABEL_AGENT, LABEL_HUMAN, LABEL_INFO, LABEL_TRIAGE, Config
from factory.decisions import DecisionError

PIPELINE_LABELS = frozenset({LABEL_TRIAGE, LABEL_AGENT, LABEL_HUMAN, LABEL_INFO})
NUMBER = {"type": "integer", "minimum": 1, "maximum": 2**31 - 1}
COMMENT = {"type": "string", "maxLength": decisions.COMMENT_CAP}


def _schema(required: dict | None = None, optional: dict | None = None) -> dict:
    properties = {"number": NUMBER, **(required or {}), "comment": COMMENT, **(optional or {})}
    return {"type": "object", "properties": properties,
            "required": ["number", *(required or {})], "additionalProperties": False}


def _validate(schema: dict, args: object) -> dict:
    """Check `args` against the subset of JSON Schema the action schemas use."""
    if not isinstance(args, dict):
        raise DecisionError("args must be an object", "invalid_args")
    properties = schema["properties"]
    unknown = set(args) - set(properties)
    if unknown:
        raise DecisionError(f"unknown args: {sorted(unknown)}", "invalid_args")
    missing = [key for key in schema["required"] if key not in args]
    if missing:
        raise DecisionError(f"missing args: {missing}", "invalid_args")
    for key, value in args.items():
        spec = properties[key]
        if spec["type"] == "integer":
            ok = type(value) is int and spec["minimum"] <= value <= spec["maximum"]
        else:
            ok = (isinstance(value, str) and "\x00" not in value
                  and len(value) <= spec.get("maxLength", len(value))
                  and value in spec.get("enum", (value,)))
        if not ok:
            raise DecisionError(f"{key} is invalid", "invalid_args")
    return dict(args)


def _open(target: dict) -> None:
    if target["state"] != "OPEN":
        raise DecisionError("issue is not open", "precondition_failed")


def _requeue_check(target: dict) -> None:
    _open(target)
    if LABEL_HUMAN not in target["labels"]:
        raise DecisionError(f"issue is not {LABEL_HUMAN}", "precondition_failed")


def _triage_check(target: dict) -> None:
    _open(target)
    held = sorted(PIPELINE_LABELS & set(target["labels"]))
    if held:
        raise DecisionError(f"issue is already in the pipeline: {held}", "precondition_failed")
    if target["title"].startswith(dispatch.SYNC_TITLE):
        raise DecisionError("upstream-sync issues are maintainer operations, not triage", "routing_refused")


def _request(args: dict, **effect: object) -> list[dict]:
    request = {"op": "issue", "number": args["number"], **effect}
    if args.get("comment", "").strip():
        request["comment"] = args["comment"]
    return [request]


# Sync refusal for requeue_agent comes from decisions._sync_routing on the add of ready-for-agent.
ACTIONS = {
    "requeue_agent": {
        "description": f"Move an open {LABEL_HUMAN} issue back to {LABEL_AGENT} and unassign the actor; refuses upstream-sync issues.",
        "schema": _schema(),
        "check": _requeue_check,
        "compile": lambda args: _request(args, add=[LABEL_AGENT], remove=[LABEL_HUMAN], unassign=True),
    },
    "resolve_and_close": {
        "description": "Close an open issue with a recorded reason.",
        "schema": _schema(required={"reason": {"type": "string", "enum": sorted(decisions.CLOSE_REASONS)}}),
        "check": _open,
        "compile": lambda args: _request(args, close=args["reason"]),
    },
    "add_needs_triage": {
        "description": f"Add {LABEL_TRIAGE} to an open issue carrying no pipeline label; refuses upstream-sync issues.",
        "schema": _schema(),
        "check": _triage_check,
        "compile": lambda args: _request(args, add=[LABEL_TRIAGE]),
    },
}


def prepare(cfg: Config, name: object, args: object, *, caller: str) -> dict:
    """Validate, compile and store one named action as a confirmable decision proposal."""
    action = ACTIONS.get(name) if isinstance(name, str) else None
    if action is None:
        raise DecisionError(f"unknown action; expected one of {sorted(ACTIONS)}", "unknown_action")
    args = _validate(action["schema"], args)
    return decisions.prepare(
        cfg, action["compile"](args), caller=caller, action={"name": name, "args": args}, guard=action["check"],
    )
