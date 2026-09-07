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
from datetime import date
from pathlib import Path
from typing import Callable

from factory import config, dispatch, lifecycle, manage, triage
from factory.config import LABEL_CHORE, LESSONS_NAME

MAX_LESSONS = 10
LOG_TAIL = 40  # lines of a failing attempt log shown to the model
MAX_EVIDENCE = 24_000  # chars; keeps the prompt inside a local model's window


def evidence(last: int) -> tuple[list[int], str]:
    """Evidence per ticket from events.jsonl, newest `last` tickets that finished."""
    by_ticket: dict[int, list[dict]] = {}
    for row in lifecycle.read_events(dispatch.EVENTS):
        if (
            row.get("event") == "lifecycle"
            or not isinstance(row.get("event"), str)
            or type(row.get("ticket")) is not int
            or not isinstance(row.get("at"), str)
        ):
            continue
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
            if e["event"] == "attempt" and e.get("gate") == "FAIL" and isinstance(e.get("log"), str) and e["log"]:
                tail = "\n".join(Path(e["log"]).read_text(errors="replace").splitlines()[-LOG_TAIL:]) if Path(e["log"]).exists() else ""
                if tail:
                    parts.append(f"\nAttempt {e.get('attempt', '?')} log tail (gate FAILED after it):\n```\n{tail}\n```")
        review = dispatch.FACTORY / f"review-{n}.md"
        if review.exists():
            parts.append(f"\nLatest reviewer findings:\n```\n{review.read_text()[-3000:]}\n```")
        parts.append("")
    return chosen, "\n".join(parts)[-MAX_EVIDENCE:]


def manager_llm(cfg: config.Config) -> Callable[[list[dict]], str]:
    """Chat-shaped adapter over `manager.command`: the transcript is flattened into one prompt."""
    def ask(messages: list[dict]) -> str:
        prompt = "\n\n".join(m["content"] for m in messages)
        return dispatch.run(cfg.manager_cmd(prompt, cfg.root), cwd=cfg.root).stdout
    return ask


def propose(existing: str, evidence_md: str, repo: str, ask: Callable[[list[dict]], str]) -> list[str]:
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
        reply = ask(messages)
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
    if cfg.manager:
        notes = cfg.factory / manage.NOTES_NAME
        if notes.is_file():
            evidence_md += f"\n## {notes.name}\n\n{notes.read_text()}"
    path = cfg.root / LESSONS_NAME
    existing = path.read_text() if path.exists() else ""
    ask = manager_llm(cfg) if cfg.manager else triage.call_llm
    lessons = propose(existing, evidence_md, cfg.repo, ask)
    body = "".join(f"- {lesson}\n" for lesson in lessons)
    print(f"learned from tickets {', '.join(f'#{n}' for n in tickets)}:\n{body}", end="")
    if args.dry_run:
        return 0
    content = f"<!-- Written by `factory learn` from {len(tickets)} finished tickets; edit freely and commit. -->\n{body}"
    if not cfg.manager:
        path.write_text(content)
        dispatch.record("learn", tickets=tickets, lessons=len(lessons))
        print(f"wrote {path}; review and commit it")
        return 0
    branch = f"agent/lessons-{date.today().isoformat()}"
    wt = cfg.factory / f"wt-{branch.removeprefix('agent/')}"
    dispatch.run(["git", "fetch", "origin", cfg.main], cwd=cfg.root)
    dispatch.run(["git", "worktree", "add", "--force", "-B", branch, str(wt), f"origin/{cfg.main}"], cwd=cfg.root)
    try:
        (wt / LESSONS_NAME).write_text(content)
        dispatch.run(["git", "add", LESSONS_NAME], cwd=wt)
        flags = ["-s"] if cfg.signoff else []
        dispatch.run(["git", "commit", *flags, "-m", f"{branch}: update {LESSONS_NAME}"], cwd=wt)
        pr_body = f"`factory learn` distilled tickets {', '.join(f'#{n}' for n in tickets)} via the manager.\n\n{body}"
        dispatch.push_and_pr(wt, branch, f"{branch}: update {LESSONS_NAME}", pr_body, label=LABEL_CHORE)
    finally:
        dispatch.run(["git", "worktree", "remove", "--force", str(wt)], cwd=cfg.root, check=False)
    dispatch.record("learn", tickets=tickets, lessons=len(lessons), branch=branch)
    print(f"opened a {LABEL_CHORE} PR from {branch}")
    return 0
