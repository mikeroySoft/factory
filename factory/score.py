"""`factory score`: rubric-score worker attempts from persisted OMP-compatible transcripts.

Every finished ticket's `attempt` rows name a session file (`.factory/transcripts/<n>/*.jsonl`);
each gets one `score` row: deterministic counts from the JSONL, and a 0-1 compliance
judgement from the local model that is null, never guessed, when the model is unreachable.
"""

from __future__ import annotations

import argparse
import json
import re
from itertools import pairwise
from pathlib import Path

from factory import config, dispatch, lifecycle, results, triage

MAX_ATTEMPTS = 50  # attempts scored per run
MAX_TRANSCRIPT = 2_000_000  # bytes read per transcript file
MAX_HANDOFF = 4_000  # chars of the worker handoff shown to the judge
EDIT_TOOLS = {"edit", "write"}
EXCERPT_CHARS = 3000  # per-session excerpt handed to `factory learn`
RESULT_LINES = 10  # lines of a failing tool result kept in the excerpt
FAILED = re.compile(r"(?i:error)|Traceback|FAIL")
JUDGE_SYSTEM = (
    "You grade whether a coding agent delivered a ticket. You are given the ticket's scope and "
    "exit gate, then the agent's final message and handoff note. Answer with one number between "
    "0 and 1: 1 means every scoped item and exit-gate criterion is addressed, 0 means none. "
    "Output only the number."
)
_NUMBER = re.compile(r"0(?:\.\d+)?|1(?:\.0+)?|\.\d+")
_LINE_SELECTOR = re.compile(r":(?:raw(?::-?\d[\d,+-]*)?|-?\d[\d,+-]*(?::raw)?)$")
_SKILL_FILE = re.compile(r"^\[Skill file: (.+/SKILL\.md)\]$", re.MULTILINE)


def _transcript_rows(path: Path) -> list[dict]:
    """JSON objects from the bounded OMP session; a truncated final line is dropped."""
    with path.open("rb") as handle:
        data = handle.read(MAX_TRANSCRIPT)
    out = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _tool_calls(row: dict, parts: list[dict]) -> list[dict]:
    calls = []
    for part in parts:
        if part.get("type") != "toolCall":
            continue
        arguments = part.get("arguments")
        calls.append({
            "id": part.get("id"),
            "name": part.get("name"),
            "arguments": arguments if isinstance(arguments, dict) else {},
            "at": row.get("timestamp") or row.get("at"),
        })
    return calls


def _message(row: dict) -> tuple[dict, list[dict], str] | None:
    message = row.get("message")
    if row.get("type") != "message" or not isinstance(message, dict):
        return None
    parts, text = _parts(message)
    return message, parts, text


def parse_rows(rows: list[dict]) -> dict:
    """Pure projection of transcript rows into prompt, calls, results and final text."""
    calls, prompt, final = [], "", ""
    results: dict[object, tuple[bool, str]] = {}
    for row in rows:
        projected = _message(row)
        if projected is None:
            continue
        message, parts, text = projected
        if message.get("role") == "user":
            prompt = prompt or text
        elif message.get("role") == "assistant":
            final = text or final
            calls += _tool_calls(row, parts)
        elif message.get("role") == "toolResult":
            results[message.get("toolCallId")] = (bool(message.get("isError")), text)
    for call in calls:
        call["is_error"], call["result"] = results.get(call["id"], (False, ""))
    return {"calls": calls, "prompt": prompt, "final": final}


def _canonical_calls(parsed: dict) -> list[tuple[str, str]]:
    return [(call["name"], json.dumps(call["arguments"], sort_keys=True)) for call in parsed["calls"]]


def _skill(path: object) -> tuple[str, str, bool] | None:
    if not isinstance(path, str):
        return None
    base = _LINE_SELECTOR.sub("", path)
    partial = base != path
    if base.startswith("skill://"):
        name = base.removeprefix("skill://").split("/", 1)[0]
    else:
        skill_path = Path(base)
        name = skill_path.parent.name if skill_path.name == "SKILL.md" else ""
    return (name, base, partial) if name else None


def _skill_tokens(root: Path, path: str, result: str) -> int:
    if path.startswith("skill://"):
        match = _SKILL_FILE.search(result)
        source = Path(match[1]) if match else None
    else:
        source = root / path
    try:
        return source.stat().st_size // 4 if source is not None and source.is_file() else 0
    except OSError:
        return 0


def skill_loads(parsed: dict, root: Path, attempt: dict) -> list[dict]:
    """Skill-read rows from one parsed transcript; non-read mentions never qualify."""
    loads = []
    prompt = parsed["prompt"]
    for index, call in enumerate(parsed["calls"]):
        arguments = call["arguments"]
        found = _skill(arguments.get("path")) if call["name"] == "read" else None
        if found is None:
            continue
        name, base, selected = found
        result = call["result"]
        error = next((line for line in result.splitlines() if line.strip()), None) if call["is_error"] else None
        loads.append({
            "ticket": attempt["ticket"],
            "attempt": attempt["attempt"],
            "session": attempt["transcript"],
            "harness": attempt.get("worker"),
            "skill": name,
            "path": arguments["path"],
            "ok": not call["is_error"],
            "error": error,
            "partial": selected or "offset" in arguments or "limit" in arguments,
            "call_index": index,
            "explicit": re.search(
                rf"(?<![\w-]){re.escape(name)}(?![\w-])", prompt, re.IGNORECASE,
            ) is not None,
            "tokens": _skill_tokens(root, base, result),
            "at": call["at"] if isinstance(call["at"], str) else attempt.get("at"),
        })
    return loads


def _parsed(path: Path) -> dict:
    return parse_rows(_transcript_rows(path))


def _record(event: str, row: dict, dry_run: bool) -> None:
    print(json.dumps({"event": event, **row}))
    if not dry_run:
        dispatch.record(event, **row)


def _score_failures(parsed: dict) -> list[dict]:
    return [{"call_index": index, "text": call["result"][:EXCERPT_CHARS]}
            for index, call in enumerate(parsed["calls"]) if call["is_error"]]


def _coverage(attempt: dict) -> dict:
    return {
        "ticket": attempt["ticket"], "attempt": attempt["attempt"],
        "harness": attempt.get("worker"), "reason": "no transcript", "at": attempt.get("at"),
    }


def _coverage_key(row: dict) -> tuple:
    return row.get("ticket"), row.get("attempt"), row.get("harness"), row.get("at")


def _load_key(row: dict) -> tuple:
    return row.get("session"), row.get("call_index")
def _record_health(
    cfg: config.Config,
    attempt: dict,
    parsed: dict | None,
    recorded_loads: set[tuple],
    recorded_coverage: set[tuple],
    dry_run: bool,
) -> None:
    if attempt.get("transcript") is None:
        coverage = _coverage(attempt)
        key = _coverage_key(coverage)
        if key not in recorded_coverage:
            _record("skill-coverage", coverage, dry_run)
            recorded_coverage.add(key)
    if parsed is None:
        return
    for load in skill_loads(parsed, cfg.root, attempt):
        key = _load_key(load)
        if key not in recorded_loads:
            _record("skill-load", load, dry_run)
            recorded_loads.add(key)


def _attempt_score(cfg: config.Config, attempt: dict, parsed: dict | None) -> dict:
    row = {
        "ticket": attempt["ticket"], "attempt": attempt["attempt"],
        "transcript": attempt.get("transcript"),
    }
    if parsed is None:
        return {**row, "skipped": "missing"}
    calls, prompt, final = _canonical_calls(parsed), parsed["prompt"], parsed["final"]
    handoff = dispatch.FACTORY / f"wt-{attempt['ticket']}" / ".factory" / f"handoff-{attempt['ticket']}.md"
    if handoff.is_file():
        text = handoff.read_text()
    else:  # the worktree is removed after a clean merge; the accepted handoff is retained
        prior = results.latest_result(cfg, attempt["ticket"])
        text = prior.get("text", "") if prior else ""
    compliance = judge(scope(prompt), final, text[-MAX_HANDOFF:])
    return {
        **row,
        "gate_first_pass": attempt.get("gate") == "PASS" and attempt["attempt"] == 1,
        **metrics(calls),
        "call_errors": [bool(call["is_error"]) for call in parsed["calls"]],
        "failures": _score_failures(parsed),
        "compliance": compliance,
        "judge_model": triage.LLM_MODEL if compliance is not None else None,
    }


def _finished_tickets(rows: list[dict], last: int | None) -> set[int]:
    finished_at = {row["ticket"]: str(row.get("at", "")) for row in rows
                   if row.get("event") in ("merged", "escalate") and type(row.get("ticket")) is int}
    return set(sorted(finished_at, key=finished_at.get)[-last:] if last else finished_at)


def _attempts(rows: list[dict], chosen: set[int]) -> dict[tuple, dict]:
    # Attempt numbers restart on each claim; identical sessions keep their last row.
    return {(row["ticket"], row["attempt"], row.get("transcript")): row for row in rows
            if row.get("event") == "attempt" and row.get("ticket") in chosen
            and type(row.get("attempt")) is int}






def _messages(path: Path) -> list[dict]:
    """`message` payloads from the bounded OMP-compatible session."""
    return [row["message"] for row in _transcript_rows(path)
            if row.get("type") == "message" and isinstance(row.get("message"), dict)]


def _parts(message: dict) -> tuple[list[dict], str]:
    parts = [p for p in message.get("content") or [] if isinstance(p, dict)]
    return parts, "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def parse(path: Path) -> tuple[list[tuple[str, str]], str, str]:
    """(tool calls as (name, canonical arguments), first user text, last assistant text)."""
    parsed = _parsed(path)
    return _canonical_calls(parsed), parsed["prompt"], parsed["final"]





def excerpt(path: Path, max_chars: int = EXCERPT_CHARS) -> str:
    """Where the session spun, for `factory learn`: every call repeated back to back, the final
    assistant message, then every tool call whose result mentioned an error/Traceback/FAIL (the
    call plus the result's first RESULT_LINES lines); cut to `max_chars`. The repeated calls come
    first because the flagged list routinely overflows the cap on real sessions."""
    calls: list[tuple[str, str, str]] = []  # (call id, canonical call, rendered call)
    results: dict[str, tuple[bool, str]] = {}
    final = ""
    for message in _messages(path):
        parts, text = _parts(message)
        if message.get("role") == "assistant":
            final = text or final
            for p in parts:
                if p.get("type") == "toolCall":
                    key = f"{p.get('name')} {json.dumps(p.get('arguments'), sort_keys=True)}"
                    calls.append((p.get("id"), key, key[:300]))
        elif message.get("role") == "toolResult":
            results[message.get("toolCallId")] = (bool(message.get("isError")), text)
    failed = []
    for call_id, _, shown in calls:
        is_error, text = results.get(call_id, (False, ""))
        if is_error or FAILED.search(text):
            failed.append(f"- {shown}\n" + "".join(f"  {line}\n" for line in text.splitlines()[:RESULT_LINES]))
    repeated = [f"- {b[2]}\n" for a, b in pairwise(calls) if a[1] == b[1]]
    out = "Repeated tool calls:\n" + "".join(repeated) if repeated else ""
    out += f"Final assistant message:\n{final or '(none)'}\n"
    if failed:
        out += "Tool calls whose result mentioned an error:\n" + "".join(failed)
    return out[:max_chars]


def metrics(calls: list[tuple[str, str]]) -> dict:
    retries = sum(1 for a, b in pairwise(calls) if a == b)
    return {
        "tool_calls": len(calls),
        "edits": sum(1 for name, _ in calls if name in EDIT_TOOLS),
        "retries": retries,
        "efficiency": 1 / (1 + retries + max(0, len(calls) - 40) / 40),
    }


def scope(prompt: str) -> str:
    """The ticket's `## Scope` and `## Exit gate` sections of the worker prompt; the issue text
    before the standing instructions when it has neither."""
    found = re.findall(r"^## (?:Scope|Exit gate)\n.*?(?=^## |\Z)", prompt, flags=re.MULTILINE | re.DOTALL)
    return "\n".join(s.strip() for s in found) if found else prompt.split("\n## Instructions\n", 1)[0][:6000]


def judge(ticket: str, final: str, handoff: str) -> float | None:
    """0-1 compliance from the local model; None when it is unreachable or answers with
    anything but a number."""
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": f"## Ticket scope and exit gate\n\n{ticket}\n\n## Agent's final message\n\n"
                                    f"{final or '(none)'}\n\n## Agent's handoff\n\n{handoff or '(none)'}"},
    ]
    try:
        answer = re.sub(r"<think>.*?</think>", "", triage.call_llm(messages), flags=re.DOTALL).strip()
    except (SystemExit, ValueError, LookupError, TypeError):
        return None
    return float(answer) if _NUMBER.fullmatch(answer) else None


def _event_keys(rows: list[dict], event: str, key) -> set[tuple]:
    return {key(row) for row in rows if row.get("event") == event}


def _score_keys(rows: list[dict]) -> set[tuple]:
    return {(row.get("ticket"), row.get("attempt"), row.get("transcript"))
            for row in rows if row.get("event") == "score"}


def _unscored(attempts: dict[tuple, dict], scored: set[tuple]) -> set[tuple]:
    keys = [key for key in attempts if key not in scored]
    return set(keys[-MAX_ATTEMPTS:])


def run(cfg: config.Config, last: int | None = None, dry_run: bool = False) -> list[dict]:
    """Score attempts and journal idempotent skill health rows for their transcripts."""
    triage.configure(cfg)
    rows = lifecycle.read_events(dispatch.EVENTS)
    chosen = _finished_tickets(rows, last)
    scored = _score_keys(rows)
    recorded_loads = _event_keys(rows, "skill-load", _load_key)
    recorded_coverage = _event_keys(rows, "skill-coverage", _coverage_key)
    attempts = _attempts(rows, chosen)
    out = []
    to_score = _unscored(attempts, scored)
    for key, attempt in attempts.items():
        transcript = attempt.get("transcript")
        path = cfg.root / transcript if isinstance(transcript, str) else None
        parsed = _parsed(path) if path is not None and path.is_file() else None
        _record_health(cfg, attempt, parsed, recorded_loads, recorded_coverage, dry_run)
        if key not in to_score:
            continue
        row = _attempt_score(cfg, attempt, parsed)
        _record("score", row, dry_run)
        out.append(row)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory score",
                                     description="Score finished tickets' worker attempts from their transcripts.")
    parser.add_argument("--last", type=int, help="finished tickets to score (default: all unscored)")
    parser.add_argument("--dry-run", action="store_true", help="print the rows; do not journal them")
    args = parser.parse_args(argv)
    cfg = config.load()
    dispatch.configure(cfg)
    run(cfg, args.last, args.dry_run)
    return 0
