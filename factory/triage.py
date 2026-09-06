"""Triage needs-triage issues using the local model endpoint.

Stateless worker: reads issues via gh, asks the local model for a decision,
applies labels/comments via gh. wontfix is only ever proposed, never applied.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import nullcontext

from factory import config, lifecycle
from factory.config import (
    LABEL_AGENT,
    LABEL_APPROVED,
    LABEL_CHORE,
    LABEL_HUMAN,
    LABEL_INFO,
    LABEL_TRIAGE,
    Config,
)

cfg: Config
LLM_URL = os.environ.get("FACTORY_LLM_URL", config.DEFAULT_LLM_URL)
LLM_MODEL = os.environ.get("FACTORY_LLM_MODEL", config.DEFAULT_LLM_MODEL)


def configure(c: Config) -> None:
    global cfg, LLM_URL, LLM_MODEL
    cfg = c
    LLM_URL = os.environ.get("FACTORY_LLM_URL", cfg.llm_url)
    LLM_MODEL = os.environ.get("FACTORY_LLM_MODEL", cfg.llm_model)


LABEL_TABLE = "\n".join(
    [
        "| Label | Meaning |",
        "|-------|---------|",
        *(
            f"| {label} | {meaning} |"
            for label, (_color, meaning) in config.LABELS.items()
            if label not in (LABEL_APPROVED, LABEL_CHORE)
        ),
        "| wontfix | Will not be actioned |",
    ]
)

DECISIONS = (LABEL_AGENT, LABEL_INFO, LABEL_HUMAN, "wontfix-proposal")

ACCEPTANCE_HINTS = re.compile(
    r"acceptance|exit gate|verification|expected behavior|steps to reproduce",
    re.IGNORECASE,
)


def deterministic_needs_info(body: str, comments: str) -> str | None:
    """Cheap lint before the LLM: obviously under-specified -> needs-info."""
    if len(body.strip()) < 80:
        return (
            "The issue body is too short to act on. Describe the problem and "
            "add acceptance criteria (an observable done-condition) plus the "
            "command that verifies it."
        )
    text = f"{body}\n{comments}"
    if not ACCEPTANCE_HINTS.search(text) and "```" not in text:
        return (
            "No acceptance criteria found. Add an observable done-condition "
            "and the exact command that verifies it."
        )
    return None


def gh(*args: str, execution=None) -> str:
    command = ["gh", *args, "--repo", cfg.repo]
    if execution:
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=execution.env()
        ) as proc:
            try:
                execution.child(proc.pid)
                out, err = proc.communicate()
            except BaseException:
                proc.kill()
                proc.wait()
                raise
            finally:
                if proc.poll() is not None:
                    execution.child_done(proc.pid)
    else:
        proc = subprocess.run(command, capture_output=True, text=True)
        out, err = proc.stdout, proc.stderr
    if execution:
        execution.emit("result", command="gh", returncode=proc.returncode)
    if proc.returncode != 0:
        if execution:
            execution.outcome = "unknown"
            execution.reason = "github_command_failed"
        print(f"gh {' '.join(args)} failed: {err.strip()}", file=sys.stderr)
        raise SystemExit(1)
    return out


def system_prompt() -> str:
    return f"""You are the triage bot for the {cfg.repo} issue tracker.

Label reference:

{LABEL_TABLE}

Choose exactly one decision for the issue:
- "ready-for-agent": the issue is fully specified — it has a problem statement AND acceptance criteria or an observable done-condition. Also fill "brief": a short restatement of the acceptance criteria and the exact verification command, for the implementing agent.
- "needs-info": information is missing; state the specific missing information as a question.
- "ready-for-human": needs design judgment, touches release, signing, or security policy, or has blast radius beyond this repository.
- "wontfix-proposal": the issue should not be actioned; explain why.

Respond with strict JSON only, no markdown, no prose outside the JSON:
{{"decision": "<one of ready-for-agent|needs-info|ready-for-human|wontfix-proposal>", "rationale": "<one short paragraph>", "question": "<the question for the reporter, or empty string if decision is not needs-info>", "brief": "<agent brief for ready-for-agent, else empty string>"}}"""


def call_llm(messages: list[dict], execution=None) -> str:
    payload = json.dumps(
        {
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.1,
        }
    ).encode()
    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        if execution:
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
            )
            execution.outcome = "unknown" if timed_out else "mechanism_failure"
            execution.reason = "triage_endpoint_timeout" if timed_out else "triage_endpoint_unavailable"
            execution.emit("result", timed_out=timed_out, timeout_seconds=60)
        print(
            f"cannot reach local model at {LLM_URL}: {exc}\n"
            "Is the model server running? Set [triage].url in .factory.toml if the endpoint differs.",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return body["choices"][0]["message"]["content"]


def parse_decision(text: str) -> dict | None:
    # ponytail: naive fence strip; structured-output API if the model misbehaves
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("decision") not in DECISIONS:
        return None
    obj.setdefault("rationale", "")
    obj.setdefault("question", "")
    obj.setdefault("brief", "")
    return obj


def fetch_issue(number: int, execution=None) -> dict:
    raw = gh(
        "issue",
        "view",
        str(number),
        "--json",
        "number,title,body,comments,labels,state",
        execution=execution,
    )
    return json.loads(raw)


def triage_issue(issue: dict, execution=None) -> dict | None:
    """Ask the model for a decision. One retry on bad JSON, then None."""
    comments = "\n\n".join(
        f"Comment by {c.get('author', {}).get('login', '?')}:\n{c.get('body', '')}"
        for c in issue.get("comments", [])
    )
    body = issue.get("body") or ""
    question = deterministic_needs_info(body, comments)
    if question:
        return {
            "decision": LABEL_INFO,
            "rationale": "Deterministic pre-check: under-specified for the factory.",
            "question": question,
            "brief": "",
        }
    user_msg = (
        f"Issue #{issue['number']}: {issue['title']}\n\n"
        f"Body:\n{body or '(empty)'}\n\n"
        f"Comments:\n{comments or '(none)'}"
    )
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_msg},
    ]
    for _attempt in range(2):
        reply = call_llm(messages, execution=execution)
        decision = parse_decision(reply)
        if decision is not None:
            return decision
        messages.append({"role": "assistant", "content": reply})
        messages.append(
            {
                "role": "user",
                "content": "That was not valid JSON matching the required schema. "
                "Reply with ONLY the JSON object.",
            }
        )
    return None


def apply_decision(number: int, decision: dict, dry_run: bool, execution=None) -> None:
    label = decision["decision"]
    rationale = decision["rationale"]
    if label == LABEL_INFO and decision["question"]:
        comment = f"Triage: {rationale}\n\nQuestion: {decision['question']}"
    elif label == LABEL_AGENT and decision.get("brief"):
        comment = f"Triage: {rationale}\n\nAgent brief: {decision['brief']}"
    elif label == "wontfix-proposal":
        comment = f"Triage proposal: wontfix — {rationale}"
    else:
        comment = f"Triage: {rationale}"

    if dry_run:
        print(f"#{number} [dry-run] decision: {json.dumps(decision)}")
        print(f"#{number} [dry-run] comment: {comment}")
        return

    gh("issue", "comment", str(number), "--body", comment, execution=execution)
    if label == "wontfix-proposal":
        return  # never apply wontfix; leave needs-triage for a human
    gh(
        "issue",
        "edit",
        str(number),
        "--remove-label",
        LABEL_TRIAGE,
        "--add-label",
        label,
        execution=execution,
    )


def list_needs_triage(execution=None) -> list[int]:
    raw = gh(
        "issue",
        "list",
        "--label",
        LABEL_TRIAGE,
        "--state",
        "open",
        "--json",
        "number",
        execution=execution,
    )
    return [item["number"] for item in json.loads(raw)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory triage",
        description="Triage needs-triage issues with the local model.",
    )
    parser.add_argument("--issue", type=int, help="triage a single issue")
    parser.add_argument(
        "--replay", help="comma-separated issue numbers: print decisions, no writes"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print decisions instead of applying"
    )
    args = parser.parse_args(argv)
    configure(config.load())

    recording = not (args.replay or args.dry_run)
    with (
        lifecycle.scope(cfg.factory / "events.jsonl", "triage") if recording else nullcontext()
    ) as execution:
        return execute(args, execution)


def execute(args: argparse.Namespace, execution) -> int:
    replay = bool(args.replay)
    if replay:
        numbers = [int(n) for n in args.replay.split(",")]
    elif args.issue:
        numbers = [args.issue]
    else:
        numbers = list_needs_triage(execution=execution)

    if not numbers:
        if execution:
            execution.reason = "no_tickets"
        print("no needs-triage issues")
        return 0

    exit_code = 0
    outcome, reason = "completed", None
    # One local model serves every repo on this host; hold the host lock so
    # simultaneous timer passes queue on it instead of hammering the endpoint.
    with open(cfg.lock, "w") as host_lock:  # noqa: SIM115
        fcntl.flock(host_lock, fcntl.LOCK_EX)
        if execution:
            execution.emit("lock_acquired", lock=str(cfg.lock))
        try:
            for number in numbers:
                ticket_execution = None
                try:
                    with (
                        lifecycle.scope(
                            cfg.factory / "events.jsonl", "triage-ticket", ticket=number, lock=cfg.lock
                        ) if execution else nullcontext()
                    ) as ticket_execution:
                        issue = fetch_issue(number, execution=ticket_execution)
                        decision = triage_issue(issue, execution=ticket_execution)
                        if decision is None:
                            if ticket_execution:
                                ticket_execution.outcome = "unknown"
                                ticket_execution.reason = "unparseable_decision"
                            outcome, reason = "unknown", "unparseable_decision"
                            print(
                                f"#{number}: model returned unparseable JSON twice; skipping",
                                file=sys.stderr,
                            )
                            exit_code = 1
                            continue
                        if replay:
                            print(f"#{number}: {json.dumps(decision)}")
                        else:
                            apply_decision(number, decision, args.dry_run, execution=ticket_execution)
                        if ticket_execution:
                            ticket_execution.reason = decision["decision"]
                            if decision["decision"] != LABEL_AGENT:
                                ticket_execution.outcome = "product_feedback"
                                if outcome == "completed":
                                    outcome, reason = "product_feedback", "triage_feedback"
                except BaseException:
                    if execution and ticket_execution:
                        execution.outcome = ticket_execution.outcome
                        execution.reason = ticket_execution.reason
                    raise
        finally:
            # Closing the existing host lock still releases it on every exit.
            host_lock.close()
            if execution:
                execution.emit("lock_released", lock=str(cfg.lock))
    if execution:
        execution.outcome, execution.reason = outcome, reason
    return exit_code
