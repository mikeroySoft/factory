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


def _messages(path: Path) -> list[dict]:
    """`message` rows from the first MAX_TRANSCRIPT bytes of an OMP-compatible session;
    a truncated final line is dropped."""
    with path.open("rb") as handle:
        data = handle.read(MAX_TRANSCRIPT)
    out = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") == "message" and isinstance(row.get("message"), dict):
            out.append(row["message"])
    return out


def _parts(message: dict) -> tuple[list[dict], str]:
    parts = [p for p in message.get("content") or [] if isinstance(p, dict)]
    return parts, "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def parse(path: Path) -> tuple[list[tuple[str, str]], str, str]:
    """(tool calls as (name, canonical arguments), first user text, last assistant text)."""
    calls, prompt, final = [], "", ""
    for message in _messages(path):
        parts, text = _parts(message)
        if message.get("role") == "user":
            prompt = prompt or text
        elif message.get("role") == "assistant":
            calls += [(p.get("name"), json.dumps(p.get("arguments"), sort_keys=True))
                      for p in parts if p.get("type") == "toolCall"]
            final = text or final
    return calls, prompt, final


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


def run(cfg: config.Config, last: int | None = None, dry_run: bool = False) -> list[dict]:
    """Score unscored attempts of the newest `last` finished tickets (all when None), at most
    MAX_ATTEMPTS per run; journal one `score` row each unless `dry_run`. Returns the rows."""
    triage.configure(cfg)
    rows = lifecycle.read_events(dispatch.EVENTS)
    finished_at: dict[int, str] = {}
    for r in rows:
        if r.get("event") in ("merged", "escalate") and type(r.get("ticket")) is int:
            finished_at[r["ticket"]] = str(r.get("at", ""))
    chosen = set(sorted(finished_at, key=finished_at.get)[-last:] if last else finished_at)
    scored = {(r.get("ticket"), r.get("attempt"), r.get("transcript")) for r in rows if r.get("event") == "score"}
    # Attempt numbers restart on each claim; deduplicate identical sessions, keeping the last row.
    attempts = {(r["ticket"], r["attempt"], r.get("transcript")): r for r in rows if r.get("event") == "attempt"
                and r.get("ticket") in chosen and type(r.get("attempt")) is int}
    attempts = [r for key, r in attempts.items() if key not in scored]
    out = []
    for a in attempts[-MAX_ATTEMPTS:]:
        n, k, transcript = a["ticket"], a["attempt"], a.get("transcript")
        row = {"ticket": n, "attempt": k, "transcript": transcript}
        path = cfg.root / transcript if isinstance(transcript, str) else None
        if path is None or not path.is_file():
            row["skipped"] = "missing"
        else:
            calls, prompt, final = parse(path)
            handoff = dispatch.FACTORY / f"wt-{n}" / ".factory" / f"handoff-{n}.md"
            if handoff.is_file():
                text = handoff.read_text()
            else:  # the worktree is removed after a clean merge; the accepted handoff is retained
                prior = results.latest_result(cfg, n)
                text = prior.get("text", "") if prior else ""
            compliance = judge(scope(prompt), final, text[-MAX_HANDOFF:])
            row.update(gate_first_pass=a.get("gate") == "PASS" and k == 1, **metrics(calls),
                       compliance=compliance, judge_model=triage.LLM_MODEL if compliance is not None else None)
        print(json.dumps({"event": "score", **row}))
        if not dry_run:
            dispatch.record("score", **row)
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
