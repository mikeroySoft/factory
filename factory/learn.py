"""`factory learn`: distil recent ticket outcomes into `.factory-lessons.md`.

The traces -> eval -> improve loop, sized for a repo: evidence is the event
log (gate results, verdicts, escalation reasons), the latest review findings,
and the tail of failing attempt logs. The model proposes a short list of
repo-specific lessons; the file is committed and every worker prompt carries
it. The eval signal is the dashboard's first-gate pass rate before and after.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factory import config, dispatch, triage
from factory.config import LESSONS_NAME

MAX_LESSONS = 10
LOG_TAIL = 40  # lines of a failing attempt log shown to the model
MAX_EVIDENCE = 24_000  # chars; keeps the prompt inside a local model's window


def evidence(last: int) -> tuple[list[int], str]:
    """Evidence per ticket from events.jsonl, newest `last` tickets that finished."""
    rows = []
    for line in dispatch.EVENTS.read_text().splitlines() if dispatch.EVENTS.exists() else []:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    by_ticket: dict[int, list[dict]] = {}
    for row in rows:
        if "ticket" in row:
            by_ticket.setdefault(row["ticket"], []).append(row)
    finished = [n for n, evs in by_ticket.items() if any(e["event"] in ("merged", "escalate") for e in evs)]
    chosen = sorted(finished, key=lambda n: by_ticket[n][-1]["at"])[-last:]
    parts = []
    for n in chosen:
        evs = by_ticket[n]
        title = next((e.get("title") for e in evs if e["event"] == "claimed"), "")
        parts.append(f"### Ticket #{n}: {title}")
        for e in evs:
            fields = {k: v for k, v in e.items() if k not in ("ticket", "title", "log")}
            parts.append(f"- {json.dumps(fields)}")
        for e in evs:
            if e["event"] == "attempt" and e.get("gate") == "FAIL" and e.get("log"):
                tail = "\n".join(Path(e["log"]).read_text(errors="replace").splitlines()[-LOG_TAIL:]) if Path(e["log"]).exists() else ""
                if tail:
                    parts.append(f"\nAttempt {e['attempt']} log tail (gate FAILED after it):\n```\n{tail}\n```")
        review = dispatch.FACTORY / f"review-{n}.md"
        if review.exists():
            parts.append(f"\nLatest reviewer findings:\n```\n{review.read_text()[-3000:]}\n```")
        parts.append("")
    return chosen, "\n".join(parts)[-MAX_EVIDENCE:]


def propose(existing: str, evidence_md: str, repo: str) -> list[str]:
    system = (
        f"You maintain a short list of lessons for coding agents working tickets in the {repo} "
        f"repository. From the evidence, extract only lessons that would have prevented a gate "
        f"failure, a REVISE verdict, or an escalation, and that will recur: repo conventions the "
        f"agent missed, commands that must be run, files that must be updated together, "
        f"traps in the test suite. Each lesson is one imperative sentence, specific to this "
        f"repository, with the concrete file/command/check when known. Merge with the existing "
        f"list: keep lessons still supported, drop ones the evidence contradicts, never exceed "
        f"{MAX_LESSONS}. Respond with strict JSON only: {{\"lessons\": [\"...\"]}}"
    )
    user = f"Existing lessons:\n{existing or '(none)'}\n\nEvidence:\n{evidence_md}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(2):
        reply = triage.call_llm(messages)
        text = reply.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            obj = json.loads(text)
            lessons = [str(x).strip() for x in obj["lessons"] if str(x).strip()]
            return lessons[:MAX_LESSONS]
        except (ValueError, KeyError, TypeError):
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": "That was not valid JSON of the form {\"lessons\": [...]}. Reply with ONLY that JSON."},
            ]
    raise SystemExit("factory learn: model returned unparseable JSON twice")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory learn",
        description=f"Distil recent ticket outcomes into {LESSONS_NAME} (committed; read by every worker).",
    )
    parser.add_argument("--last", type=int, default=10, help="finished tickets to learn from (default 10)")
    parser.add_argument("--dry-run", action="store_true", help="print the proposed lessons; do not write")
    args = parser.parse_args(argv)
    cfg = config.load()
    dispatch.configure(cfg)
    triage.configure(cfg)

    tickets, evidence_md = evidence(args.last)
    if not tickets:
        print("factory learn: no finished tickets in .factory/events.jsonl yet")
        return 0
    path = cfg.root / LESSONS_NAME
    existing = path.read_text() if path.exists() else ""
    lessons = propose(existing, evidence_md, cfg.repo)
    body = "".join(f"- {lesson}\n" for lesson in lessons)
    print(f"learned from tickets {', '.join(f'#{n}' for n in tickets)}:\n{body}", end="")
    if args.dry_run:
        return 0
    path.write_text(
        f"<!-- Written by `factory learn` from {len(tickets)} finished tickets; edit freely and commit. -->\n{body}"
    )
    dispatch.record("learn", tickets=tickets, lessons=len(lessons))
    print(f"wrote {path}; review and commit it")
    return 0
