"""`factory learn`: distil recent ticket outcomes into `.factory-lessons.md`.

The traces -> eval -> improve loop, sized for a repo: evidence is the event
log (gate results, verdicts, escalation reasons), the latest review findings,
each ticket's lowest-scored session excerpt (`factory score`; the tail of a
failing attempt log for tickets without one), and candidate escaped defects
(closed bugs blamed back to a merged agent ticket). The model proposes a short
list of repo-specific lessons; the file is committed and every worker prompt
carries it. The eval signal is the dashboard's first-gate pass rate before and after.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from factory import config, dispatch, lifecycle, manage, promotion, score, stats, triage
from factory.config import LABEL_CHORE, LESSONS_NAME

MAX_LESSONS = 10
LOG_TAIL = 40  # lines of a failing attempt log shown to the model when the ticket has no score row
MAX_EVIDENCE = 24_000  # chars; keeps the prompt inside a local model's window
MAX_ESCAPES = 10  # most recent candidate-escape bugs carried into evidence
AUTO_EVERY = 10  # finished tickets between automatic runs at the end of a dispatcher pass
LESSONS_BRANCH = re.compile(r"agent/(lessons|curate)-")

@dataclass(frozen=True)
class ReplayCase:
    ticket: int
    base: str
    title: str
    labels: tuple[str, ...]
    issue: dict


@dataclass(frozen=True)
class Candidate:
    kind: str
    content: str


def worst_session(evs: list[dict]) -> tuple[dict, dict] | None:
    """(score row, its attempt row) for the ticket's lowest-efficiency scored session: ties go
    to the lowest compliance (unknown last), then to a gate FAIL; None when nothing was scored."""
    attempts = {(e.get("attempt"), e.get("transcript")): e for e in evs if e["event"] == "attempt"}
    scored = [(s, attempts.get((s.get("attempt"), s.get("transcript")), {}))
              for s in evs if s["event"] == "score" and isinstance(s.get("efficiency"), (int, float))]
    if not scored:
        return None
    return min(scored, key=lambda pair: (pair[0]["efficiency"], pair[0].get("compliance") is None,
                                         pair[0].get("compliance") or 0, pair[1].get("gate") != "FAIL"))


def evidence(last: int) -> tuple[list[int], str]:
    """Evidence per ticket from events.jsonl, newest `last` tickets that finished, then recent candidate escapes."""
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
            fields = {k: v for k, v in e.items() if k not in ("ticket", "title", "log", "issue")}
            parts.append(f"- {json.dumps(fields)}")
        worst = worst_session(evs)
        transcript = Path(worst[0]["transcript"]) if worst and isinstance(worst[0].get("transcript"), str) else None
        if transcript is not None and transcript.is_file():
            row, attempt = worst
            parts.append(f"\nAttempt {row.get('attempt', '?')} session excerpt (efficiency {row['efficiency']:.2f}, "
                         f"compliance {row.get('compliance')}, gate {attempt.get('gate')}):\n```\n"
                         f"{score.excerpt(transcript)}\n```")
        else:
            for e in evs:
                if e["event"] == "attempt" and e.get("gate") == "FAIL" and isinstance(e.get("log"), str) and e["log"]:
                    tail = "\n".join(Path(e["log"]).read_text(errors="replace").splitlines()[-LOG_TAIL:]) if Path(e["log"]).exists() else ""
                    if tail:
                        parts.append(f"\nAttempt {e.get('attempt', '?')} log tail (gate FAILED after it):\n```\n{tail}\n```")
        review = dispatch.FACTORY / f"review-{n}.md"
        if review.exists():
            parts.append(f"\nLatest reviewer findings:\n```\n{review.read_text()[-3000:]}\n```")
        parts.append("")
    recent = sorted((row for row in stats.escapes() if row["candidates"]), key=lambda row: row["closed_at"])
    if recent:
        parts.append("## Candidate escapes\n\nClosed bugs whose fix changed lines an earlier agent ticket "
                     "introduced (SZZ-style blame: candidates, not causes).")
        parts += [f"- Bug #{row['bug']}: {row['title']} <- candidate #{c['ticket']}: {c['title']} "
                  f"({row['source']}; {', '.join(c['paths']) or 'no blamed paths'})"
                  for row in recent[-MAX_ESCAPES:] for c in row["candidates"]]
    return chosen, "\n".join(parts)[-MAX_EVIDENCE:]

def replay_cases(tickets: list[int], maximum: int) -> list[ReplayCase]:
    """Newest finished tickets with a frozen issue snapshot and exact pre-work revision."""
    by_ticket: dict[int, list[dict]] = {}
    for row in lifecycle.read_events(dispatch.EVENTS):
        if type(row.get("ticket")) is int:
            by_ticket.setdefault(row["ticket"], []).append(row)
    cases = []
    for ticket in tickets:
        claimed = next((
            row for row in by_ticket.get(ticket, [])
            if row.get("event") == "claimed"
            and isinstance(row.get("base"), str)
            and re.fullmatch(r"[0-9a-f]{40}", row["base"])
            and isinstance(row.get("issue"), dict)
            and isinstance(row.get("labels"), list)
        ), None)
        if claimed is not None:
            cases.append(ReplayCase(
                ticket=ticket,
                base=claimed["base"],
                title=str(claimed.get("title") or claimed["issue"].get("title") or f"ticket {ticket}"),
                labels=tuple(str(label) for label in claimed["labels"]),
                issue=claimed["issue"],
            ))
    return cases[-maximum:]


def _tokens(usage: dict | None) -> int | None:
    if not usage:
        return None
    values = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    return sum(value or 0 for value in values) if any(isinstance(value, (int, float)) for value in values) else None


def _replay_prompt(cfg: config.Config, case: ReplayCase, wt: Path, extra: str = "") -> str:
    issue = case.issue
    parts = [f"# Issue #{case.ticket}: {issue.get('title') or case.title}", "", issue.get("body") or "(no body)"]
    for comment in issue.get("comments") or []:
        author = (comment.get("author") or {}).get("login", "unknown")
        parts += ["", f"## Comment by {author}", "", comment.get("body", "")]
    parts.append(dispatch.STANDING_INSTRUCTIONS.format(
        n=case.ticket,
        commit_flag=" -s" if cfg.signoff else "",
        main=cfg.main,
        python=sys.executable,
    ))
    lessons = wt / LESSONS_NAME
    if lessons.is_file():
        parts += ["", "## Lessons from previous tickets in this repository", "", lessons.read_text()]
    parts += [
        "",
        "## Learn evaluation replay",
        "",
        (
            "This is an isolated replay at the ticket's recorded base revision. Never access or mutate "
            "the original GitHub issue, labels, comments, branches, or pull requests."
        ),
    ]
    if extra:
        parts += ["", extra]
    return "\n".join(parts) + "\n"


def _no_push_env(directory: Path) -> dict[str, str]:
    hooks = directory / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\necho 'factory learn replay: pushes are disabled' >&2\nexit 1\n")
    hook.chmod(0o755)
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hooks),
    }


def _pr_checks(wt: Path) -> bool:
    path = wt / config.CONFIG_NAME
    if not path.is_file():
        return False
    try:
        checks = tomllib.loads(path.read_text()).get("gate", {}).get("check", [])
    except (tomllib.TOMLDecodeError, AttributeError):
        return False
    return any(isinstance(check, dict) and check.get("when") == "pr" for check in checks)


def _finding_count(findings: str) -> int:
    required_level = 0
    count = 0
    for line in findings.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if required_level:
            if heading and len(heading.group(1)) <= required_level:
                break
            if re.match(r"^(?:[-*+]|\d+[.)])\s+", line):
                count += 1
        elif heading and heading.group(2).strip("*_ :").casefold() == "required fixes":
            required_level = len(heading.group(1))
    return count


def _candidate_base(wt: Path, state: Path, candidate: Candidate | None) -> str:
    if candidate is None:
        return dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
    if candidate.kind == "lessons":
        (wt / LESSONS_NAME).write_text(candidate.content)
        paths = [LESSONS_NAME]
    elif candidate.kind == "curate":
        patch = state / "candidate.patch"
        patch.write_text(candidate.content if candidate.content.endswith("\n") else candidate.content + "\n")
        paths = apply_curate_change(wt, patch)
    else:
        raise ValueError(f"unknown learn candidate {candidate.kind!r}")
    dispatch.run(["git", "add", "--", *paths], cwd=wt)
    dispatch.run([
        "git", "-c", "user.name=Factory replay", "-c", "user.email=factory@localhost",
        "commit", "-m", f"factory learn {candidate.kind} candidate",
    ], cwd=wt)
    return dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()


def replay_ticket(
    cfg: config.Config,
    case: ReplayCase,
    candidate: Candidate | None,
    deadline: float,
) -> dict:
    """Replay one frozen ticket arm in an isolated checkout; no GitHub or push operation is available."""
    cfg.factory.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix=f"learn-{case.ticket}-", dir=cfg.factory))
    wt = state / "worktree"
    started = time.monotonic()
    tokens: list[int | None] = []
    costs: list[float] = []
    first_gate_pass = False
    review_rounds = revise_findings = 0
    approved = False
    try:
        dispatch.run(["git", "clone", "--quiet", "--no-checkout", "--shared", str(cfg.root), str(wt)])
        dispatch.run(["git", "checkout", "--quiet", "--detach", case.base], cwd=wt)
        dispatch.run(["git", "config", "user.name", "Factory replay"], cwd=wt)
        dispatch.run(["git", "config", "user.email", "factory@localhost"], cwd=wt)
        base = _candidate_base(wt, state, candidate)
        isolated = _no_push_env(state)
        extra = ""
        attempt = 0
        report = ""
        gate_head = base
        promptfile = wt / ".factory-prompt.md"

        for attempt in range(1, cfg.max_attempts + 1):
            logfile = state / f"worker-{attempt}.log"
            promptfile.write_text(_replay_prompt(cfg, case, wt, extra))
            wall = time.time()
            argv, session_dir = dispatch._with_transcript(
                cfg.worker(set(case.labels), promptfile, wt),
                f"learn-{case.ticket}-{state.name}-{candidate.kind if candidate else 'baseline'}-{attempt}",
            )
            _, fired = dispatch.run_worker(
                argv, wt, logfile, wt / ".factory" / f"handoff-{case.ticket}.md",
                deadline, journal=False, env=isolated,
            )
            transcript = dispatch._transcript(session_dir, wall)
            usage = dispatch.worker_usage(
                logfile, cfg.worker_usage.get(cfg.worker_key(set(case.labels))), transcript,
            )
            tokens.append(_tokens(usage))
            if isinstance(usage.get("cost"), (int, float)):
                costs.append(usage["cost"])
            dispatch.commit_leftovers(wt, case.ticket, case.title)
            gate_head = dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
            if fired or time.monotonic() >= deadline:
                report = f"worker replay stopped by {fired or 'budget_min'}"
                ok = False
            else:
                ok, report = dispatch.run_gate(
                    wt, f"learn-{case.ticket}-{attempt}", base=base,
                    timeout=max(0.001, deadline - time.monotonic()),
                )
            if attempt == 1:
                first_gate_pass = ok
            if ok:
                break
            extra = f"## Previous gate report (attempt {attempt} failed)\n\n{report}"
        else:
            ok = False

        for bounce in range(cfg.review_rounds + 1):
            if not ok or time.monotonic() >= deadline:
                break
            review_report = report
            if _pr_checks(wt):
                pr_ok, pr_report = dispatch.run_gate(
                    wt, f"learn-{case.ticket}-pr-{bounce + 1}", tier="pr", base=base,
                    timeout=max(0.001, deadline - time.monotonic()),
                )
                review_report = f"{report}\n{pr_report}"
                if not pr_ok:
                    revise_findings += 1
                    ok = False
                    break
            context = (
                "## Learn evaluation evidence\n\n"
                f"Ticket #{case.ticket} is replayed at recorded base `{case.base}`. "
                "Judge only this diff and the gate report above."
            )
            result = dispatch.run_reviewer(
                wt,
                dispatch.review_prompt(case.ticket, review_report, base_ref=base, context=context),
                gate_head,
                env=isolated,
                subject=f"learn replay #{case.ticket}",
                timeout=max(0.001, deadline - time.monotonic()),
            )
            review_rounds += 1
            tokens.append(_tokens(result["usage"]))
            if isinstance((result["usage"] or {}).get("cost"), (int, float)):
                costs.append(result["usage"]["cost"])
            if result["verdict"] == "APPROVE":
                approved = True
                break
            revise_findings += max(1, _finding_count(result["findings"]))
            if bounce == cfg.review_rounds:
                break
            promptfile.write_text(_replay_prompt(
                cfg, case, wt,
                f"## Reviewer findings, round {bounce + 1}\n\n"
                "Address required fixes only; optional suggestions are not requirements.\n\n"
                f"{result['findings']}",
            ))
            logfile = state / f"worker-review-{bounce + 1}.log"
            wall = time.time()
            argv, session_dir = dispatch._with_transcript(
                cfg.worker(set(case.labels), promptfile, wt),
                f"learn-{case.ticket}-{state.name}-{candidate.kind if candidate else 'baseline'}-review-{bounce + 1}",
            )
            _, fired = dispatch.run_worker(
                argv, wt, logfile, wt / ".factory" / f"handoff-{case.ticket}.md",
                deadline, journal=False, env=isolated,
            )
            transcript = dispatch._transcript(session_dir, wall)
            usage = dispatch.worker_usage(
                logfile, cfg.worker_usage.get(cfg.worker_key(set(case.labels))), transcript,
            )
            tokens.append(_tokens(usage))
            if isinstance(usage.get("cost"), (int, float)):
                costs.append(usage["cost"])
            dispatch.commit_leftovers(wt, case.ticket, case.title)
            gate_head = dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
            ok, report = (False, f"worker replay stopped by {fired or 'budget_min'}") if (
                fired or time.monotonic() >= deadline
            ) else dispatch.run_gate(
                wt, f"learn-{case.ticket}-review-{bounce + 1}", base=base,
                timeout=max(0.001, deadline - time.monotonic()),
            )

        return {
            "first_gate_pass": first_gate_pass,
            "approved": approved,
            "review_rounds": review_rounds,
            "revise_findings": revise_findings,
            "tokens": sum(value for value in tokens if value is not None) if all(
                value is not None for value in tokens
            ) else None,
            "reported_tokens": sum(value for value in tokens if value is not None),
            "cost": round(sum(costs), 4) if costs else None,
            "seconds": round(time.monotonic() - started, 3),
        }
    finally:
        shutil.rmtree(state, ignore_errors=True)


def _aggregate(rows: list[dict], cfg: config.Config) -> dict:
    return {
        "first_gate_pass": sum(bool(row["first_gate_pass"]) for row in rows),
        "review_rounds": sum(
            row["review_rounds"] if row["approved"] else cfg.review_rounds + 2 for row in rows
        ),
        "revise_findings": sum(row["revise_findings"] for row in rows),
    }


def _better(baseline: dict, variant: dict) -> bool:
    no_worse = (
        variant["first_gate_pass"] >= baseline["first_gate_pass"]
        and variant["review_rounds"] <= baseline["review_rounds"]
        and variant["revise_findings"] <= baseline["revise_findings"]
    )
    return no_worse and variant != baseline


def _evaluation_cost(baseline: list[dict], variant: list[dict]) -> dict:
    rows = baseline + variant
    tokens = [row.get("tokens") for row in rows]
    costs = [row.get("cost") for row in rows if isinstance(row.get("cost"), (int, float))]
    return {
        "tokens": sum(tokens) if all(isinstance(value, int) for value in tokens) else None,
        "cost": round(sum(costs), 4) if costs else None,
        "seconds": round(sum(float(row.get("seconds", 0)) for row in rows), 3),
    }


def evaluation_markdown(result: dict) -> str:
    lines = [
        "## Learn evaluation evidence",
        "",
        f"Disposition: **{result['status']}**"
        + (f" — {result['reason']}" if result.get("reason") else ""),
        "",
        "| Ticket | Recorded base | Arm | First gate | Approved | Review rounds | REVISE findings |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for arm in ("baseline", "variant"):
        for row in result.get(arm, []):
            lines.append(
                f"| #{row['ticket']} | `{row['base'][:12]}` | {arm} | "
                f"{'yes' if row['first_gate_pass'] else 'no'} | "
                f"{'yes' if row['approved'] else 'no'} | {row['review_rounds']} | "
                f"{row['revise_findings']} |"
            )
    cost = result.get("cost", {})
    tokens = f"{cost['tokens']} tokens" if cost.get("tokens") is not None else "tokens not reported"
    dollars = f", ${cost['cost']:.4f}" if cost.get("cost") is not None else ""
    lines += ["", f"Cost: {tokens}{dollars}, {cost.get('seconds', 0):g} seconds."]
    return "\n".join(lines)


def evaluate_candidates(
    cfg: config.Config,
    cases: list[ReplayCase],
    candidates: list[Candidate],
) -> dict[str, dict]:
    """Paired baseline/variant replay; journal one terminal outcome per candidate."""
    deadline = time.monotonic() + cfg.learn_budget_min * 60
    used_tokens = 0
    baseline: list[dict] = []
    baseline_error = ""
    if not cases:
        baseline_error = "no recent ticket has a recorded replay base and frozen issue snapshot"
    else:
        for case in cases:
            try:
                row = replay_ticket(cfg, case, None, deadline)
            except Exception as exc:  # noqa: BLE001 - an unavailable replay is evidence, not escalation
                baseline_error = f"baseline replay failed for #{case.ticket}: {type(exc).__name__}: {exc}"
                break
            baseline.append({"ticket": case.ticket, "base": case.base, **row})
            reported_tokens = row.get("reported_tokens", row.get("tokens"))
            if isinstance(reported_tokens, (int, float)):
                used_tokens += reported_tokens
            if used_tokens > cfg.learn_max_tokens:
                baseline_error = f"learn token budget exceeded ({used_tokens} > {cfg.learn_max_tokens})"
                break
            if time.monotonic() >= deadline:
                baseline_error = f"learn wall-time budget exhausted ({cfg.learn_budget_min} min)"
                break

    results = {}
    for candidate in candidates:
        variant: list[dict] = []
        reason = baseline_error
        if not reason:
            for case in cases:
                if used_tokens >= cfg.learn_max_tokens:
                    reason = f"learn token budget exhausted ({used_tokens} >= {cfg.learn_max_tokens})"
                    break
                if time.monotonic() >= deadline:
                    reason = f"learn wall-time budget exhausted ({cfg.learn_budget_min} min)"
                    break
                try:
                    row = replay_ticket(cfg, case, candidate, deadline)
                except Exception as exc:  # noqa: BLE001 - report and skip the proposal
                    reason = f"variant replay failed for #{case.ticket}: {type(exc).__name__}: {exc}"
                    break
                variant.append({"ticket": case.ticket, "base": case.base, **row})
                reported_tokens = row.get("reported_tokens", row.get("tokens"))
                if isinstance(reported_tokens, (int, float)):
                    used_tokens += reported_tokens
                if used_tokens > cfg.learn_max_tokens:
                    reason = f"learn token budget exceeded ({used_tokens} > {cfg.learn_max_tokens})"
                    break
                if time.monotonic() >= deadline:
                    reason = f"learn wall-time budget exhausted ({cfg.learn_budget_min} min)"
                    break
        if reason:
            status = "not_tested"
        else:
            status = "kept" if _better(_aggregate(baseline, cfg), _aggregate(variant, cfg)) else "rejected"
            reason = "" if status == "kept" else "variant was not no-worse on every metric and better on one"
        result = {
            "candidate": candidate.kind,
            "status": status,
            "reason": reason,
            "tickets": [case.ticket for case in cases],
            "baseline": baseline,
            "variant": variant,
            "cost": _evaluation_cost(baseline, variant),
        }
        results[candidate.kind] = result
        dispatch.record(
            "learn-evaluation", candidate=candidate.kind, status=status, reason=reason,
            tickets=result["tickets"], baseline=baseline, variant=variant, cost=result["cost"],
        )
    return results


def manager_llm(cfg: config.Config) -> Callable[[list[dict]], str]:
    """Chat-shaped adapter over `manager.command`: the transcript is flattened into one prompt."""
    def ask(messages: list[dict]) -> str:
        cfg.factory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=cfg.factory, prefix="manager-prompt-", suffix=".md") as prompt:
            prompt.write("\n\n".join(m["content"] for m in messages))
            prompt.flush()
            return dispatch.run(cfg.manager_cmd(Path(prompt.name), cfg.root), cwd=cfg.root).stdout
    return ask


CURATE_OFFER = (
    "Optionally, after the JSON, add a line `DECISION: CURATE` followed by a unified diff "
    "(`git apply` format, against the current main branch) that puts each lesson in its strongest "
    "enforceable form. When the failure is mechanically detectable, prefer a gate check (append a new "
    f"`[[gate.check]]` table to `{config.CONFIG_NAME}`) or a regression test under a `[gate].protected_paths` "
    "or `[gate].add_only_paths` path such as `tests/`; otherwise a standards entry in `AGENTS.md`; a prose "
    f"lesson only as a last resort. Allowed paths: {', '.join(manage.CURATE_PREFIXES)}, `{config.CONFIG_NAME}`, "
    "and main's `[gate].protected_paths` and `[gate].add_only_paths`. Changes are add-only: removing or editing an existing gate check or "
    "protected path, changing `[leak_scan]`, deleting or renaming a protected file, or touching `.github/` "
    "or any other path rejects the whole diff. It is opened as a separate chore PR citing the evidence, "
    "merged only by a human."
)
HUMAN_MERGE = "Harness change from the meta loop — human merge required."


def require_add_only(base: dict, new: dict) -> None:
    """Raise unless `new` is `base` plus appended `[[gate.check]]` tables (new names) and appended
    `[gate].protected_paths` entries."""
    name = config.CONFIG_NAME
    base_gate, new_gate = base.get("gate", {}), new.get("gate", {})
    old, cur = base_gate.get("check", []), new_gate.get("check", [])
    for i, check in enumerate(old):
        if cur[i:i + 1] != [check]:
            kept = any(c.get("name") == check.get("name") for c in cur)
            raise ValueError(f"{name} {'edits' if kept else 'removes'} gate check {check.get('name')!r}")
    names = [c.get("name") for c in old]
    for check in cur[len(old):]:
        if check.get("name") in names:
            raise ValueError(f"{name} gate check {check.get('name')!r} is not a new name")
        names.append(check.get("name"))
    old, cur = base_gate.get("protected_paths", []), new_gate.get("protected_paths", [])
    if missing := [p for p in old if p not in cur]:
        raise ValueError(f"{name} removes protected path {missing[0]!r}")
    if cur[:len(old)] != old:
        raise ValueError(f"{name} reorders [gate].protected_paths")
    if base.get("leak_scan") != new.get("leak_scan"):
        raise ValueError(f"{name} changes [leak_scan]")

    def rest(doc: dict) -> dict:
        gate = {k: v for k, v in doc.get("gate", {}).items() if k not in ("check", "protected_paths")}
        return {**doc, "gate": gate}

    if rest(base) != rest(new):
        raise ValueError(f"{name} changes more than appended gate checks and protected paths")


def propose(existing: str, evidence_md: str, repo: str, ask: Callable[[list[dict]], str],
            curate: bool = False) -> tuple[list[str], str | None]:
    system = (
        f"You maintain a short list of lessons for coding agents working tickets in the {repo} "
        f"repository. From the evidence, extract only lessons that would have prevented a gate "
        f"failure, a REVISE verdict, or an escalation, and that will recur: repo conventions the "
        f"agent missed, commands that must be run, files that must be updated together, "
        f"traps in the test suite. Each lesson is one imperative sentence, specific to this "
        f"repository, with the concrete file/command/check when known. Merge with the existing "
        f"list: keep lessons still supported, drop ones the evidence contradicts, never exceed "
        f"{MAX_LESSONS}. Each session's failure excerpts show where the worker spun; prefer lessons "
        f"that remove the cause of a repeated tool call. Respond with strict JSON only: {{\"lessons\": [\"...\"]}}"
    )
    if curate:
        system += " " + CURATE_OFFER
    user = f"Existing lessons:\n{existing or '(none)'}\n\nEvidence:\n{evidence_md}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(2):
        reply = ask(messages)
        text, diff = manage.split_curate(reply) if curate else (reply, None)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            obj = json.loads(text)
            lessons = [str(x).strip() for x in obj["lessons"] if str(x).strip()]
            return lessons[:MAX_LESSONS], diff
        except (ValueError, KeyError, TypeError):
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": "That was not valid JSON of the form {\"lessons\": [...]}. Reply with ONLY that JSON."},
            ]
    raise ValueError("model returned unparseable JSON twice")


def branch_name(kind: str, tickets: list[int]) -> str:
    """`agent/<kind>-<date>-t<N>`, N the newest included ticket: same-day runs never collide,
    and the merge stage's `agent/(\\d+)` never matches."""
    return f"agent/{kind}-{datetime.now().astimezone().date().isoformat()}-t{tickets[-1]}"


def chore_pr(cfg: config.Config, branch: str, edit: Callable[[Path], list[str]], title: str, pr_body: str) -> None:
    """Branch from origin/main in a throwaway worktree, let `edit` return the paths it changed, commit, open the PR."""
    wt = cfg.factory / f"wt-{branch.removeprefix('agent/')}"
    dispatch.run(["git", "fetch", "origin", cfg.main], cwd=cfg.root)
    dispatch.run(["git", "worktree", "add", "--force", "-B", branch, str(wt), f"origin/{cfg.main}"], cwd=cfg.root)
    try:
        paths = edit(wt)
        dispatch.run(["git", "add", "--", *paths], cwd=wt)
        flags = ["-s"] if cfg.signoff else []
        dispatch.run(["git", "commit", *flags, "-m", title], cwd=wt)
        dispatch.push_and_pr(wt, branch, title, pr_body, label=LABEL_CHORE)
    finally:
        dispatch.run(["git", "worktree", "remove", "--force", str(wt)], cwd=cfg.root, check=False)


def apply_curate_change(wt: Path, patch: Path) -> list[str]:
    """Apply and validate one manager CURATE patch in a disposable checkout."""
    toml = wt / config.CONFIG_NAME
    base = tomllib.loads(toml.read_text()) if toml.is_file() else {}
    gate = base.get("gate", {})
    protected = gate.get("protected_paths", []) + gate.get("add_only_paths", [])
    applied = dispatch.run(["git", "apply", str(patch)], cwd=wt, check=False)
    if applied.returncode:
        raise ValueError(f"diff does not apply: {applied.stderr.strip()}")
    # Worktree-only apply (no --index): every touched path shows as ` M`/` D`/`??`; a rename is ` D` + `??`.
    status = dispatch.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=wt).stdout
    entries = [(entry[:2], entry[3:]) for entry in status.split("\0") if entry]
    paths = [path for _, path in entries]
    bad = [path for path in paths if not manage.curate_allowed(path, protected)]
    if bad:
        raise ValueError(", ".join(bad) + " outside " + ", ".join(manage.CURATE_PREFIXES)
                         + f", {config.CONFIG_NAME}, [gate].protected_paths and [gate].add_only_paths")
    if not paths:
        raise ValueError("empty diff")
    deleted = [
        path for code, path in entries
        if "D" in code and any(fnmatch.fnmatchcase(path, glob) for glob in protected)
    ]
    if deleted:
        raise ValueError(
            f"deletes or renames {', '.join(deleted)} under "
            "[gate].protected_paths or [gate].add_only_paths"
        )
    if config.CONFIG_NAME in paths:
        try:
            new = tomllib.loads(toml.read_text()) if toml.is_file() else {}
            require_add_only(base, new)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{config.CONFIG_NAME}: {exc}") from exc
        except (AttributeError, TypeError) as exc:
            raise ValueError(f"{config.CONFIG_NAME}: malformed [gate]: {exc}") from exc
    return paths


def curate(
    cfg: config.Config,
    diff: str,
    tickets: list[int],
    notes: str,
    evaluation: str = "",
) -> str:
    """Apply the manager's CURATE diff on `agent/curate-<date>-t<N>` and open its chore PR.

    Returns the branch, or `rejected: <reason>` when the diff does not apply or leaves the allowlist."""
    branch = branch_name("curate", tickets)
    patch = cfg.factory / f"{branch.removeprefix('agent/')}.patch"
    patch.write_text(diff if diff.endswith("\n") else diff + "\n")

    def edit(wt: Path) -> list[str]:
        return apply_curate_change(wt, patch)

    cited = ", ".join(f"#{n}" for n in tickets)
    pr_body = (
        f"{HUMAN_MERGE}\n\n`factory learn` CURATE proposed by the manager from tickets {cited}.\n\n"
        + (f"{evaluation.strip()}\n\n" if evaluation.strip() else "")
        + f"## Manager notes\n\n{notes or '(none)'}"
    )
    try:
        chore_pr(cfg, branch, edit, f"{branch}: curate harness context", pr_body)
    except ValueError as exc:
        return f"rejected: {exc}"
    finally:
        patch.unlink(missing_ok=True)
    return branch


def run(
    cfg: config.Config,
    last: int = 10,
    dry_run: bool = False,
    evaluate: bool | None = None,
    **fields: object,
) -> dict | None:
    """One `factory learn` over the newest `last` finished tickets; `fields` extend its `learn` event.

    Returns the recorded event's fields, or None when nothing was recorded (no finished tickets,
    or a dry run). Raises on a model, `git` or `gh` failure."""
    triage.configure(cfg)
    stats.configure(cfg)
    tickets, evidence_md = evidence(last)
    if not tickets:
        print("factory learn: no finished tickets in .factory/events.jsonl yet")
        return None
    notes_md = ""
    if cfg.manager:
        notes = cfg.factory / manage.NOTES_NAME
        if notes.is_file():
            notes_md = notes.read_text()
            evidence_md += f"\n## {notes.name}\n\n{notes_md}"
    path = cfg.root / LESSONS_NAME
    existing = path.read_text() if path.exists() else ""
    ask = manager_llm(cfg) if cfg.manager else triage.call_llm
    lessons, diff = propose(existing, evidence_md, cfg.repo, ask, curate=bool(cfg.manager))
    body = "".join(f"- {lesson}\n" for lesson in lessons)
    print(f"learned from tickets {', '.join(f'#{n}' for n in tickets)}:\n{body}", end="")
    if diff:
        print(f"manager CURATE diff:\n{diff}", end="")
    if dry_run:
        return None
    content = f"<!-- Written by `factory learn` from {len(tickets)} finished tickets; edit freely and commit. -->\n{body}"
    enabled = cfg.learn_evaluate if evaluate is None else evaluate
    evaluations: dict[str, dict] = {}
    if enabled:
        candidates = []
        if content != existing:
            candidates.append(Candidate("lessons", content))
        if diff:
            candidates.append(Candidate("curate", diff))
        evaluations = evaluate_candidates(
            cfg, replay_cases(tickets, cfg.learn_max_tickets), candidates,
        )
        for kind, result in evaluations.items():
            print(f"{kind} evaluation: {result['status']}"
                  + (f" ({result['reason']})" if result["reason"] else ""))

    keep_lessons = not enabled or evaluations.get("lessons", {}).get("status") == "kept"
    keep_curate = bool(diff) and (
        not enabled or evaluations.get("curate", {}).get("status") == "kept"
    )
    if not cfg.manager:
        if keep_lessons:
            path.write_text(content)
            print(f"wrote {path}; review and commit it")
        event = {"tickets": tickets, "lessons": len(lessons), **fields}
        if enabled:
            event["evaluation"] = {
                kind: result["status"] for kind, result in evaluations.items()
            }
        dispatch.record("learn", **event)
        return event

    branch = None
    if keep_lessons:
        branch = branch_name("lessons", tickets)

        def edit(wt: Path) -> list[str]:
            (wt / LESSONS_NAME).write_text(content)
            return [LESSONS_NAME]

        if enabled:
            pr_body = (
                f"{HUMAN_MERGE}\n\n`factory learn` distilled tickets "
                f"{', '.join(f'#{n}' for n in tickets)} via the manager.\n\n"
                f"{evaluation_markdown(evaluations['lessons'])}\n\n{body}"
            )
        else:
            pr_body = (
                f"{HUMAN_MERGE}\n\n`factory learn` distilled tickets "
                f"{', '.join(f'#{n}' for n in tickets)} via the manager.\n\n{body}"
            )
        chore_pr(cfg, branch, edit, f"{branch}: update {LESSONS_NAME}", pr_body)
        print(f"opened a {LABEL_CHORE} PR from {branch}")
    curated = (
        curate(
            cfg, diff, tickets, notes_md,
            evaluation_markdown(evaluations["curate"]) if enabled else "",
        )
        if keep_curate else None
    )
    if curated:
        print(f"CURATE {curated}" if curated.startswith("rejected") else f"opened a {LABEL_CHORE} PR from {curated}")
    event = {
        "tickets": tickets,
        "lessons": len(lessons),
        "branch": branch,
        "curate": curated,
        **fields,
    }
    if enabled:
        event["evaluation"] = {
            kind: result["status"] for kind, result in evaluations.items()
        }
    dispatch.record("learn", **event)
    return event


def finished_since_learn() -> int:
    """Tickets whose latest `merged`/`escalate` row is newer than the newest non-failed `learn` row
    (any, if none): failed runs keep the count, so the next pass retries."""
    rows = [r for r in lifecycle.read_events(dispatch.EVENTS) if isinstance(r.get("at"), str)]
    since = max((r["at"] for r in rows if r.get("event") == "learn" and r.get("status") != "failed"), default="")
    return len({r["ticket"] for r in rows
                if r.get("event") in ("merged", "escalate") and type(r.get("ticket")) is int and r["at"] > since})


def learn_pass(dry_run: bool = False) -> None:
    """End of a dispatcher pass: `factory learn --last 10` once 10 tickets finished since the last `learn`.

    Needs a manager (without one, learn only writes an uncommitted file) and no open lessons or
    curate PR (lessons rewrite the whole file). A failure is journaled, never raised."""
    cfg = dispatch.cfg
    count = finished_since_learn()
    if count < AUTO_EVERY:
        return
    if not cfg.manager:
        dispatch.log("learn: skipped (no manager)")
        return
    try:
        prs = dispatch.gh_json(["pr", "list", "--repo", cfg.repo, "--state", "open", "--limit", "1000",
                                "--json", "number,headRefName"])
        if pr := next((p for p in prs if LESSONS_BRANCH.match(p["headRefName"])), None):
            dispatch.log(f"learn: skipped (open lessons PR #{pr['number']})")
            return
        if dry_run:
            dispatch.log(f"learn: would run --last {AUTO_EVERY} ({count} tickets finished since the last learn)")
            return
        dispatch.log(f"learn: {count} tickets finished since the last learn; running --last {AUTO_EVERY}")
        score.run(cfg, AUTO_EVERY)  # unscored attempts of the same tickets, so evidence has session excerpts
        run(cfg, AUTO_EVERY, trigger="auto")
    except Exception as exc:  # noqa: BLE001 - isolates learn failures from the dispatcher pass
        reason = f"{type(exc).__name__}: {exc}"
        dispatch.log(f"learn: failed ({reason})")
        if not dry_run:
            dispatch.record("learn", trigger="auto", status="failed", reason=reason)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory learn",
        description=f"Distil recent ticket outcomes into {LESSONS_NAME} (committed; read by every worker).",
    )
    parser.add_argument("--last", type=int, default=10, help="finished tickets to learn from (default 10)")
    parser.add_argument("--dry-run", action="store_true", help="print the proposed lessons; do not write")
    parser.add_argument(
        "--evaluate", action="store_true",
        help="replay recorded tickets before proposing changes (also enabled by [learn] evaluate = true)",
    )
    parser.add_argument("--promotion", metavar="CLAIM.json",
                        help="judge and record an eval promotion claim (docs/eval-promotion-contract.md) instead")
    args = parser.parse_args(argv)
    cfg = config.load()
    dispatch.configure(cfg)
    if args.promotion:
        claim = json.loads(Path(args.promotion).read_text())
        try:
            disposition, reason = promotion.decide(claim)
        except ValueError as exc:
            raise SystemExit(f"factory learn: invalid promotion claim: {exc}")
        print(f"{disposition}: {reason}")
        if not args.dry_run:
            dispatch.record("promotion", stage=claim["stage"], change=claim["change"],
                            baseline=claim["baseline"], candidate=claim["candidate"],
                            splits=claim["splits"], disposition=disposition, reason=reason)
        return 0
    try:
        run(cfg, args.last, args.dry_run, True if args.evaluate else None)
    except ValueError as exc:
        raise SystemExit(f"factory learn: {exc}")
    return 0
