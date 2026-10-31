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
import shlex
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
LESSONS_BRANCH = dispatch.LESSONS_BRANCH
REVERT_BRANCH = "agent/revert-lessons-"  # + the reverted PR number
WORKER_CONTEXT_FILES = (LESSONS_NAME, "AGENTS.md", "CLAUDE.md")

LICENSE_MARKERS = (
    ("apache license version 2.0", "Apache-2.0"),
    ("mit license", "MIT"),
    ("mozilla public license version 2.0", "MPL-2.0"),
    ("neither the name of", "BSD-3-Clause"),
    ("redistribution and use in source and binary forms", "BSD-2-Clause"),
)


def repository_spdx(root: Path) -> str:
    """Return the repository's SPDX identifier from package metadata or a conventional licence file."""
    metadata = (
        ("pyproject.toml", "project"),
        ("Cargo.toml", "package"),
    )
    for filename, section in metadata:
        path = root / filename
        if not path.is_file():
            continue
        try:
            license_id = tomllib.loads(path.read_text()).get(section, {}).get("license")
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            continue
        if isinstance(license_id, str) and license_id.strip():
            return license_id.strip()
    package = root / "package.json"
    if package.is_file():
        try:
            license_id = json.loads(package.read_text()).get("license")
        except (OSError, ValueError, AttributeError):
            license_id = None
        if isinstance(license_id, str) and license_id.strip():
            return license_id.strip()
    for path in sorted(root.glob("LICENSE*")) + sorted(root.glob("COPYING*")):
        try:
            text = " ".join(path.read_text(errors="ignore")[:20_000].casefold().split())
        except OSError:
            continue
        for marker, license_id in LICENSE_MARKERS:
            if marker in text:
                return license_id
    return "NOASSERTION"


def lessons_content(root: Path, tickets: list[int], body: str) -> str:
    return (
        f"<!-- SPDX-License-Identifier: {repository_spdx(root)} -->\n"
        f"<!-- Written by `factory learn` from {len(tickets)} finished tickets; edit freely and commit. -->\n"
        f"{body}"
    )


def worker_context_paths(root: Path) -> set[Path]:
    """Paths whose current contents alter worker instructions."""
    paths = {Path(name) for name in WORKER_CONTEXT_FILES}
    for name in ("skills", ".omp/skills", ".agents/skills"):
        directory = root / name
        if not directory.is_dir():
            continue
        paths.update(skill.parent.relative_to(root) for skill in directory.rglob("SKILL.md"))
    return paths

@dataclass(frozen=True)
class ReplayCase:
    ticket: int
    base: str
    title: str
    labels: tuple[str, ...]
    issue: dict
    pinned_context: tuple[str, ...] = ()

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


def _context_base(wt: Path, source: Path, pinned: tuple[str, ...] = ()) -> str:
    paths = (worker_context_paths(wt) | worker_context_paths(source)) - {Path(path) for path in pinned}
    touched = []
    for relative in paths:
        target = wt / relative
        current = source / relative
        if not (target.exists() or target.is_symlink() or current.exists()):
            continue
        touched.append(relative)
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        if current.is_dir():
            shutil.copytree(current, target)
        elif current.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, target)
    if not touched:
        return dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
    dispatch.run(["git", "add", "-A", "--", *(str(path) for path in sorted(touched))], cwd=wt)
    changed = dispatch.run(["git", "diff", "--cached", "--quiet"], cwd=wt, check=False).returncode
    if not changed:
        return dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
    dispatch.run([
        "git", "-c", "user.name=Factory replay", "-c", "user.email=factory@localhost",
        "commit", "-m", "factory eval worker context",
    ], cwd=wt)
    return dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()


def replay_ticket(
    cfg: config.Config,
    case: ReplayCase,
    candidate: Candidate | None,
    deadline: float,
    context_root: Path | None = None,
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
        if context_root is not None:
            _context_base(wt, context_root, case.pinned_context)
        base = _candidate_base(wt, state, candidate)
        isolated = _no_push_env(state)
        extra = ""
        attempt = 0
        report = ""
        gate_head = base
        promptfile = wt / ".factory-prompt.md"
        worker = cfg.worker_key(set(case.labels))
        transcript_mode = cfg.worker_transcript.get(worker)
        worker_at = len(cfg.worker_wrap) if transcript_mode else None

        for attempt in range(1, cfg.max_attempts + 1):
            logfile = state / f"worker-{attempt}.log"
            promptfile.write_text(_replay_prompt(cfg, case, wt, extra))
            wall = time.time()
            argv, session_dir = dispatch._with_transcript(
                cfg.worker(set(case.labels), promptfile, wt),
                f"learn-{case.ticket}-{state.name}-{candidate.kind if candidate else 'baseline'}-{attempt}",
                transcript_mode,
                worker_at,
            )
            _, fired = dispatch.run_worker(
                argv, wt, logfile, wt / ".factory" / f"handoff-{case.ticket}.md",
                deadline, journal=False, env_overrides={
                    **cfg.worker_environment(worker, promptfile, wt), **isolated,
                },
                worker=worker, worker_version=cfg.worker_version.get(worker),
            )
            transcript = dispatch._transcript(session_dir, wall)
            usage = dispatch.worker_usage(logfile, cfg.worker_usage.get(worker), transcript)
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
                )
                review_report = f"{report}\n{pr_report}"
                if not pr_ok:
                    revise_findings += 1
                    ok = False
                    break
            context = (
                "## Learn replay context\n\n"
                f"Ticket #{case.ticket} is replayed at recorded base `{case.base}`. "
                "Judge only this diff and the gate report above."
            )
            result = dispatch.run_reviewer(
                wt,
                dispatch.review_prompt(case.ticket, review_report, context=context),
                gate_head,
                base_ref=base,
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
            counts = result.get("counts")
            revise_findings += counts["important"] if counts is not None else max(
                1, _finding_count(result["findings"]),
            )
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
                transcript_mode,
                worker_at,
            )
            _, fired = dispatch.run_worker(
                argv, wt, logfile, wt / ".factory" / f"handoff-{case.ticket}.md",
                deadline, journal=False, env_overrides={
                    **cfg.worker_environment(worker, promptfile, wt), **isolated,
                },
                worker=worker, worker_version=cfg.worker_version.get(worker),
            )
            transcript = dispatch._transcript(session_dir, wall)
            usage = dispatch.worker_usage(logfile, cfg.worker_usage.get(worker), transcript)
            tokens.append(_tokens(usage))
            if isinstance(usage.get("cost"), (int, float)):
                costs.append(usage["cost"])
            dispatch.commit_leftovers(wt, case.ticket, case.title)
            gate_head = dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
            ok, report = (False, f"worker replay stopped by {fired or 'budget_min'}") if (
                fired or time.monotonic() >= deadline
            ) else dispatch.run_gate(
                wt, f"learn-{case.ticket}-review-{bounce + 1}", base=base,
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
    if not candidates:
        return {}
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
    "or any other path rejects the whole diff. It is opened as a separate chore PR citing the evidence; "
    "it merges after a gate PASS, reviewer APPROVE and green CI, and is reverted automatically when the "
    f"next {promotion.PROMOTION_N} scored attempts fall beyond the noise of the {promotion.PROMOTION_N} before it."
)
PIPELINE = ("Harness change from the meta loop: merges after gate, reviewer APPROVE and CI; "
            f"reverted if the next {promotion.PROMOTION_N} scored attempts regress beyond noise.")


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



def _evaluation_input(result: dict) -> dict:
    cost = result.get("cost", {})
    tokens = cost.get("tokens")
    token_text = f"{tokens} tokens" if tokens is not None else "tokens not reported"
    return {
        "kind": "learn_evaluation",
        "ref": ".factory/events.jsonl",
        "summary": (
            f"{result['candidate']} replay {result['status']} on "
            f"{', '.join(f'#{ticket}' for ticket in result.get('tickets', [])) or 'no tickets'}; "
            f"{token_text}, {cost.get('seconds', 0):g}s"
        ),
    }


def _candidate_review_input(branch: str | None) -> dict | None:
    if not branch:
        return None
    row = next((
        row for row in reversed(lifecycle.read_events(dispatch.EVENTS))
        if row.get("event") == "learn-candidate-review" and row.get("branch") == branch
    ), None)
    if row is None:
        return None
    return {
        "kind": "candidate_review",
        "ref": ".factory/events.jsonl",
        "summary": (
            f"{row.get('verdict', 'REVISE')} ({row.get('reason', 'unknown')}) "
            f"at {str(row.get('head', ''))[:12]}"
        ),
    }


def record_learn_decision(
    cfg: config.Config,
    tickets: list[int],
    lessons: int,
    branch: str | None,
    *,
    curated: str | None = None,
    evaluations: dict[str, dict] | None = None,
    review_branch: str | None = None,
) -> str:
    from factory import __version__, trace

    evaluated = bool(evaluations)
    proposed = (
        bool(branch) if evaluated and cfg.manager else
        bool(lessons) and (not evaluated or evaluations.get("lessons", {}).get("status") == "kept")
    )
    review_input = _candidate_review_input(review_branch)
    inputs = [
        {
            "kind": "evidence",
            "ref": f"https://github.com/{cfg.repo}/issues/{ticket}",
            "summary": f"evidence ticket #{ticket}",
        }
        for ticket in tickets
    ]
    inputs += [_evaluation_input(result) for result in (evaluations or {}).values()]
    if review_input:
        inputs.append(review_input)
    if branch:
        inputs.append({
            "kind": "resulting_pr",
            "ref": branch,
            "summary": f"lessons PR branch {branch}",
        })
    payload = trace.decision(
        ticket=tickets[-1],
        stage="learn",
        inputs=inputs,
        options=[
            {
                "option": "propose",
                "eligible": proposed,
                "reason": (
                    f"proposed {lessons} lesson(s)" if proposed else
                    "evaluation or review blocked publication" if evaluated else
                    "no recurring lesson was found"
                ),
            },
            {
                "option": "skip",
                "eligible": not proposed,
                "reason": (
                    "evidence supported a proposal" if proposed else
                    "no publishable lesson proposal" if evaluated else
                    "no lesson was proposed"
                ),
            },
        ],
        chosen="propose" if proposed else "skip",
        rationale=(
            f"proposed {lessons} lesson(s)" + (f"; CURATE {curated}" if curated else "")
            if proposed else
            "evidence produced no publishable lesson" if evaluated else
            "evidence produced no lesson"
        ),
        causes=[{"ticket": ticket, "relation": "follows"} for ticket in tickets],
        cost=None,
        actor={"kind": "model", "name": "factory learn", "model": cfg.manager_model,
               "version": __version__},
    )
    dispatch.record("decision", **payload)
    return payload["decision_id"]

def chore_pr(
    cfg: config.Config,
    branch: str,
    edit: Callable[[Path], list[str]],
    title: str,
    pr_body: str,
    *,
    protected_override: bool = False,
    review_candidate: bool = False,
) -> None:
    """Commit, gate and publish a proposal; evaluated candidates also need a local review."""
    wt = cfg.factory / f"wt-{branch.removeprefix('agent/')}"
    dispatch.run(["git", "fetch", "origin", cfg.main], cwd=cfg.root)
    dispatch.run(["git", "worktree", "add", "--force", "-B", branch, str(wt), f"origin/{cfg.main}"], cwd=cfg.root)
    try:
        base = dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
        paths = edit(wt)
        dispatch.run(["git", "add", "--", *paths], cwd=wt)
        flags = ["-s"] if cfg.signoff else []
        dispatch.run(["git", "commit", *flags, "-m", title], cwd=wt)
        if not review_candidate:
            gate_head(wt, branch, protected_override)
        else:
            head = dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
            name = branch.removeprefix("agent/")
            ok, report = dispatch.run_gate(
                wt, name, base=base, protected_override=protected_override,
            )
            dispatch.record("gate", branch=branch, head=head, gate="PASS" if ok else "FAIL")
            if ok and _pr_checks(wt):
                ok, pr_report = dispatch.run_gate(wt, f"{name}-pr", tier="pr", base=base)
                report = f"{report}\n{pr_report}"
            result = dispatch.run_reviewer(
                wt,
                dispatch.review_prompt(None, report, proposal=pr_body),
                head,
                base_ref=base,
                subject=f"learn candidate {branch}",
            ) if ok else {
                "verdict": "REVISE", "findings": report, "accepted": False,
                "parsed": False, "reason": "gate_failed", "usage": None,
                "returncode": None, "actual_head": head,
            }
            if result["verdict"] is None:
                failure = result.get("failure_reason", "unparsed_verdict")
                result = {
                    **result,
                    "verdict": "REVISE",
                    "reason": failure,
                    "findings": (
                        f"{result['findings']}\n\n"
                        f"Factory rejected reviewer evidence: {failure}."
                    ),
                }
            dispatch.record(
                "learn-candidate-review", branch=branch, title=title, proposal=pr_body,
                base=base, head=head, gate="PASS" if ok else "FAIL", **result,
            )
            if not result["accepted"] or result["verdict"] != "APPROVE":
                raise ValueError(
                    f"candidate review {result['reason']} (see learn-candidate-review journal row)"
                )
            pr_body += f"\n\n## Candidate review\n\nHead: `{head}`\n\n{result['findings']}"
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
    status = dispatch.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=wt,
    ).stdout
    entries = [(entry[:2], entry[3:]) for entry in status.split("\0") if entry]
    paths = [path for _, path in entries]
    bad = [path for path in paths if not manage.curate_allowed(path, protected)]
    if bad:
        raise ValueError(
            ", ".join(bad) + " outside " + ", ".join(manage.CURATE_PREFIXES)
            + f", {config.CONFIG_NAME}, [gate].protected_paths and [gate].add_only_paths"
        )
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


def gate_head(wt: Path, branch: str, protected_override: bool) -> str:
    """Run the attempt gate on `wt`'s HEAD and journal it as `gate` bound to that head; a FAIL raises ValueError."""
    head = dispatch.run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
    ok, report = dispatch.run_gate(wt, branch.removeprefix("agent/"), protected_override=protected_override)
    dispatch.record("gate", branch=branch, head=head, gate="PASS" if ok else "FAIL")
    if not ok:
        raise ValueError(f"gate FAIL on {branch}:\n{report.strip()[-2000:]}")
    return head


def refresh(cfg: config.Config, n: int, branch: str, head: str) -> None:
    """Rebase a chore PR that is behind main onto it, gate the new head, push it with a lease on `head`,
    and review it: the evidence `promote_pass` reads is then bound to a head containing the main tip."""
    wt = cfg.factory / f"wt-{branch.removeprefix('agent/')}"
    dispatch.run(["git", "fetch", "origin", cfg.main, branch], cwd=cfg.root)
    dispatch.run(["git", "worktree", "add", "--force", "-B", branch, str(wt), head], cwd=cfg.root)
    try:
        dispatch.run(["git", "rebase", f"origin/{cfg.main}"], cwd=wt)
        new = gate_head(wt, branch, protected_override=True)  # the diff `chore_pr` gated, now on main
        dispatch.run(["git", "push", f"--force-with-lease={branch}:{head}", "origin", f"HEAD:{branch}"], cwd=wt)
        base = dispatch.run(["git", "rev-parse", f"origin/{cfg.main}"], cwd=wt).stdout.strip()
        dispatch.review_external_pr(n, base, new, branch)
    finally:
        dispatch.run(["git", "rebase", "--abort"], cwd=wt, check=False)
        dispatch.run(["git", "worktree", "remove", "--force", str(wt)], cwd=cfg.root, check=False)


def _record_curate_decision(
    cfg: config.Config,
    tickets: list[int],
    paths: list[str],
    allowed: dict[str, bool],
    result: str,
    *,
    evaluation: dict | None = None,
    review_branch: str | None = None,
) -> str:
    from factory import __version__, trace

    accepted = not result.startswith("rejected:")
    options = [
        {
            "option": path,
            "eligible": allowed.get(path, False),
            "reason": (
                "curate_allowed accepted this path"
                if allowed.get(path, False)
                else "curate_allowed rejected this path"
            ),
        }
        for path in paths
    ]
    options.append({
        "option": "skip", "eligible": not accepted,
        "reason": result if not accepted else "CURATE produced an eligible change",
    })
    chosen = paths[0] if accepted and paths else "skip"
    inputs = [
        {
            "kind": "evidence",
            "ref": f"https://github.com/{cfg.repo}/issues/{ticket}",
            "summary": f"evidence ticket #{ticket}",
        }
        for ticket in tickets
    ]
    if evaluation:
        inputs.append(_evaluation_input(evaluation))
    if review_input := _candidate_review_input(review_branch):
        inputs.append(review_input)
    if accepted:
        inputs.append({
            "kind": "resulting_pr",
            "ref": result,
            "summary": f"CURATE PR branch {result}",
        })
    payload = trace.decision(
        ticket=tickets[-1],
        stage="curate",
        inputs=inputs,
        options=options,
        chosen=chosen,
        rationale=result,
        causes=[{"ticket": ticket, "relation": "follows"} for ticket in tickets],
        cost=None,
        actor={"kind": "model", "name": "factory learn CURATE", "model": cfg.manager_model,
               "version": __version__},
    )
    dispatch.record("decision", **payload)
    return payload["decision_id"]


def curate(
    cfg: config.Config,
    diff: str,
    tickets: list[int],
    notes: str,
    evaluation: dict | None = None,
) -> str:
    """Apply the manager's CURATE diff on `agent/curate-<date>-t<N>` and open its chore PR.

    Returns the branch, or `rejected: <reason>` when the diff does not apply or leaves the allowlist."""
    branch = branch_name("curate", tickets)
    patch = cfg.factory / f"{branch.removeprefix('agent/')}.patch"
    patch.write_text(diff if diff.endswith("\n") else diff + "\n")
    observed_paths: list[str] = []
    allowed: dict[str, bool] = {}

    def edit(wt: Path) -> list[str]:
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
        paths = [p for _, p in entries]
        observed_paths.extend(paths)
        allowed.update((path, manage.curate_allowed(path, protected)) for path in paths)
        bad = [path for path in paths if not allowed[path]]
        if bad:
            raise ValueError(", ".join(bad) + " outside " + ", ".join(manage.CURATE_PREFIXES)
                             + f", {config.CONFIG_NAME}, [gate].protected_paths and [gate].add_only_paths")
        if not paths:
            raise ValueError("empty diff")
        if deleted := [p for code, p in entries if "D" in code and any(fnmatch.fnmatchcase(p, g) for g in protected)]:
            raise ValueError(f"deletes or renames {', '.join(deleted)} under [gate].protected_paths or [gate].add_only_paths")
        if config.CONFIG_NAME in paths:
            try:
                new = tomllib.loads(toml.read_text()) if toml.is_file() else {}
                require_add_only(base, new)
            except tomllib.TOMLDecodeError as exc:
                raise ValueError(f"{config.CONFIG_NAME}: {exc}") from exc
            except (AttributeError, TypeError) as exc:  # e.g. a non-table gate check from the model
                raise ValueError(f"{config.CONFIG_NAME}: malformed [gate]: {exc}") from exc
        return paths

    cited = ", ".join(f"#{n}" for n in tickets)
    pr_body = (
        f"{PIPELINE}\n\n`factory learn` CURATE proposed by the manager from tickets {cited}.\n\n"
        + (f"{evaluation_markdown(evaluation)}\n\n" if evaluation else "")
        + f"## Manager notes\n\n{notes or '(none)'}"
    )
    try:
        chore_pr(
            cfg,
            branch,
            edit,
            f"{branch}: curate harness context",
            pr_body,
            protected_override=True,
            **({"review_candidate": True} if evaluation else {}),
        )
        result = branch
    except ValueError as exc:
        result = f"rejected: {exc}"
    finally:
        patch.unlink(missing_ok=True)
    _record_curate_decision(
        cfg, tickets, observed_paths, allowed, result,
        evaluation=evaluation, review_branch=branch,
    )
    return result


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
    if dry_run:
        return _run(cfg, tickets, evidence_md, dry_run, evaluate, fields)
    with lifecycle.scope(dispatch.EVENTS, "learn"):
        return _run(cfg, tickets, evidence_md, dry_run, evaluate, fields)


def _run(
    cfg: config.Config,
    tickets: list[int],
    evidence_md: str,
    dry_run: bool,
    evaluate: bool | None,
    fields: dict,
) -> dict | None:
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
    content = lessons_content(cfg.root, tickets, body)
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
        record_learn_decision(cfg, tickets, len(lessons), None, evaluations=evaluations)
        return event

    branch = None
    lessons_review_branch = None
    if keep_lessons:
        branch = branch_name("lessons", tickets)
        lessons_review_branch = branch

        def edit(wt: Path) -> list[str]:
            (wt / LESSONS_NAME).write_text(content)
            return [LESSONS_NAME]

        pr_body = (
            f"{PIPELINE}\n\n`factory learn` distilled tickets "
            f"{', '.join(f'#{n}' for n in tickets)} via the manager.\n\n"
            + (f"{evaluation_markdown(evaluations['lessons'])}\n\n" if enabled else "")
            + body
        )
        try:
            chore_pr(
                cfg, branch, edit, f"{branch}: update {LESSONS_NAME}", pr_body,
                review_candidate=enabled,
            )
        except ValueError as exc:
            if not enabled:
                raise
            print(f"lessons rejected: {exc}")
            branch = None
        else:
            print(f"opened a {LABEL_CHORE} PR from {branch}")
    if keep_curate:
        curated = curate(
            cfg, diff, tickets, notes_md,
            evaluations.get("curate") if enabled else None,
        )
    elif diff and enabled:
        reason = evaluations["curate"]["reason"]
        curated = f"rejected: {reason}"
        _record_curate_decision(
            cfg, tickets, [], {}, curated,
            evaluation=evaluations["curate"],
        )
    else:
        curated = None
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
    record_learn_decision(
        cfg, tickets, len(lessons), branch,
        curated=curated, evaluations=evaluations, review_branch=lessons_review_branch,
    )
    return event


def finished_since_learn() -> int:
    """Tickets whose latest `merged`/`escalate` row is newer than the newest non-failed `learn` row
    (any, if none): failed runs keep the count, so the next pass retries."""
    rows = [r for r in lifecycle.read_events(dispatch.EVENTS) if isinstance(r.get("at"), str)]
    since = max((r["at"] for r in rows if r.get("event") == "learn" and r.get("status") != "failed"), default="")
    return len({r["ticket"] for r in rows
                if r.get("event") in ("merged", "escalate") and type(r.get("ticket")) is int and r["at"] > since})


def auto_learn(cfg: config.Config, dry_run: bool) -> None:
    count = finished_since_learn()
    if count < AUTO_EVERY:
        return
    if not cfg.manager:
        dispatch.log("learn: skipped (no manager)")
        return
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
    if reverts := decide_promotions(cfg):  # a lessons PR from this pass would conflict with the revert
        dispatch.log(f"learn: skipped (revert PR opened: {', '.join(reverts)})")
        return
    run(cfg, AUTO_EVERY, trigger="auto")


def learn_pass(dry_run: bool = False) -> None:
    """End of a dispatcher pass: `factory learn --last 10` once 10 tickets finished since the last `learn`,
    then one keep/revert decision per open promotion (`decide_promotions`).

    Learn needs a manager (without one, learn only writes an uncommitted file) and no open lessons,
    curate or revert PR (lessons rewrite the whole file). A failure is journaled, never raised."""
    cfg = dispatch.cfg
    try:
        auto_learn(cfg, dry_run)
    except Exception as exc:  # noqa: BLE001 - isolates learn failures from the dispatcher pass
        reason = f"{type(exc).__name__}: {exc}"
        dispatch.log(f"learn: failed ({reason})")
        if not dry_run:
            dispatch.record("learn", trigger="auto", status="failed", reason=reason)
    decide_promotions(cfg, dry_run)  # attempts scored by hand since the last pass


def _promotion_blocked(
    events: list[dict],
    n: int,
    branch: str,
    head: str,
    reason: str,
    dry_run: bool,
) -> None:
    if any(
        row.get("event") == "promotion-blocked"
        and row.get("pr") == n
        and row.get("head") == head
        and row.get("reason") == reason
        for row in events
    ) or dry_run:
        return
    row = {"event": "promotion-blocked", "pr": n, "head": head, "branch": branch, "reason": reason}
    dispatch.record("promotion-blocked", pr=n, head=head, branch=branch, reason=reason)
    events.append(row)


def _open_promotion(events: list[dict], n: int, branch: str, head: str, dry_run: bool) -> None:
    if branch.startswith(REVERT_BRANCH) or any(
        row.get("event") == "promotion-open"
        and row.get("pr") == n
        and row.get("head") == head
        for row in events
    ):
        return
    baseline = promotion.window(promotion.scored(events)[-promotion.PROMOTION_N:])
    if dry_run:
        dispatch.log(f"PR #{n}: would open promotion measurement (baseline n={baseline['n']})")
        return
    dispatch.record("promotion-open", pr=n, head=head, branch=branch, baseline=baseline)
    events.append({
        "event": "promotion-open",
        "pr": n,
        "head": head,
        "branch": branch,
        "baseline": baseline,
    })
    dispatch.log(f"PR #{n}: promotion open (baseline n={baseline['n']})")


def _gate_pr_head(cfg: config.Config, branch: str, head: str) -> str:
    wt = cfg.factory / f"wt-{branch.removeprefix('agent/')}"
    dispatch.run(["git", "fetch", "origin", branch], cwd=cfg.root)
    dispatch.run(["git", "worktree", "add", "--force", "-B", branch, str(wt), head], cwd=cfg.root)
    try:
        return gate_head(wt, branch, not branch.startswith("agent/lessons-"))
    finally:
        dispatch.run(["git", "worktree", "remove", "--force", str(wt)], cwd=cfg.root, check=False)


def _error_text(exc: Exception) -> str:
    return "\n".join(
        text.strip()
        for text in (str(exc), getattr(exc, "stderr", ""), getattr(exc, "stdout", ""))
        if isinstance(text, str) and text.strip()
    )


def _reconcile_human_merges(
    cfg: config.Config,
    open_prs: set[int],
    events: list[dict],
    dry_run: bool,
) -> None:
    seen: set[int] = set()
    for review in events:
        n, head = review.get("pr"), review.get("head")
        if (
            review.get("event") not in {"review-intake", "review-result"}
            or type(n) is not int
            or not isinstance(head, str)
            or review.get("ticket") is not None
            or n in open_prs
            or n in seen
            or any(row.get("event") == "merged" and row.get("pr") == n for row in events)
            or any(
                row.get("event") == "promotion-reconciled"
                and row.get("pr") == n
                for row in events
            )
            or any(
                row.get("event") == "promotion-blocked"
                and row.get("pr") == n
                and row.get("reason") == "closed without merge"
                for row in events
            )
        ):
            continue
        seen.add(n)
        recorded_branch = review.get("branch")
        if isinstance(recorded_branch, str) and not LESSONS_BRANCH.match(recorded_branch):
            continue
        try:
            state = dispatch.gh_json([
                "pr", "view", str(n), "--repo", cfg.repo,
                "--json", "state,mergedBy,headRefName,headRefOid",
            ])
        except Exception as exc:  # noqa: BLE001 - reconciliation never blocks the merge stage
            dispatch.log(f"PR #{n}: could not reconcile promotion state ({type(exc).__name__}: {exc})")
            continue
        branch = state.get("headRefName")
        if not isinstance(branch, str) or not LESSONS_BRANCH.match(branch):
            if not dry_run:
                row = {
                    "event": "promotion-reconciled",
                    "pr": n,
                    "head": head,
                    "reason": "not a lessons branch",
                }
                dispatch.record("promotion-reconciled", pr=n, head=head, reason=row["reason"])
                events.append(row)
            continue
        current_head = state.get("headRefOid") or head
        if state.get("state") == "MERGED":
            actor = (state.get("mergedBy") or {}).get("login") or "unknown"
            if dry_run:
                dispatch.log(f"PR #{n}: would record human merge by {actor}")
                _open_promotion(events, n, branch, current_head, True)
                continue
            dispatch.record(
                "merged", pr=n, head=current_head, branch=branch,
                method="external", by="human", actor=actor,
            )
            events.append({
                "event": "merged",
                "pr": n,
                "head": current_head,
                "branch": branch,
                "method": "external",
                "by": "human",
                "actor": actor,
            })
            dispatch.log(f"PR #{n}: recorded human merge by {actor}")
            _open_promotion(events, n, branch, current_head, False)
        elif state.get("state") == "CLOSED":
            _promotion_blocked(events, n, branch, current_head, "closed without merge", dry_run)


def promote_pass(prs: list[dict], dry_run: bool = False) -> None:
    """Merge eligible lessons PRs and reconcile externally merged promotions."""
    cfg = dispatch.cfg
    events = lifecycle.read_events(dispatch.EVENTS)
    _reconcile_human_merges(cfg, {pr["number"] for pr in prs}, events, dry_run)
    for pr in sorted(prs, key=lambda p: p["number"]):
        n, branch = pr["number"], pr["headRefName"]
        if (
            not LESSONS_BRANCH.match(branch)
            or pr["isDraft"]
            or pr["baseRefName"] != cfg.main
            or pr["reviewDecision"] == "CHANGES_REQUESTED"
        ):
            continue
        try:
            promote(cfg, pr, events, dry_run)
        except Exception as exc:  # noqa: BLE001 - one stuck chore PR never blocks the others
            dispatch.log(f"PR #{n}: {branch} promotion failed ({type(exc).__name__}: {exc})")


def promote(cfg: config.Config, pr: dict, events: list[dict], dry_run: bool) -> None:
    n, branch, head = pr["number"], pr["headRefName"], pr["headRefOid"]
    if any(
        row.get("event") == "merged" and row.get("pr") == n and row.get("head") == head
        for row in events
    ):
        return
    reviewed = any(
        row.get("event") == "review-result"
        and row.get("pr") == n
        and row.get("head") == head
        and row.get("verdict") == "APPROVE"
        for row in events
    )
    if not reviewed:
        reason = "review approval missing"
        dispatch.log(f"PR #{n}: {branch} waiting for review at {head[:12]}")
        _promotion_blocked(events, n, branch, head, reason, dry_run)
        return
    gates = [
        row for row in events
        if row.get("event") == "gate"
        and row.get("branch") == branch
        and row.get("head") == head
    ]
    if not gates:
        if dry_run:
            dispatch.log(f"PR #{n}: would gate legacy head {head[:12]} on {branch}")
            return
        try:
            _gate_pr_head(cfg, branch, head)
        except Exception:
            _promotion_blocked(events, n, branch, head, "gate failed", False)
            raise
    elif not any(row.get("gate") == "PASS" for row in gates):
        dispatch.log(f"PR #{n}: {branch} gate failed at {head[:12]}")
        _promotion_blocked(events, n, branch, head, "gate failed", dry_run)
        return
    buckets = {check["bucket"] for check in dispatch.pr_checks(n)}
    if "pass" not in buckets or buckets - {"pass", "skipping"}:
        reason = f"CI {','.join(sorted(buckets)) or 'missing'}"
        dispatch.log(f"PR #{n}: {branch} {reason}; not merging")
        _promotion_blocked(events, n, branch, head, reason, dry_run)
        return
    compare = dispatch.gh_json(["api", f"repos/{cfg.repo}/compare/{cfg.main}...{head}"])
    if compare["behind_by"] and {
        item["filename"] for item in compare["files"]
    } - {LESSONS_NAME}:
        dispatch.log(f"PR #{n}: {'would refresh' if dry_run else 'refreshing'} {branch} (behind {cfg.main})")
        if not dry_run:
            refresh(cfg, n, branch, head)
        return
    from factory import eval as eval_suite

    paths = [
        item["filename"] for item in compare.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    ]
    if (
        cfg.eval_schedule
        and not branch.startswith(REVERT_BRANCH)
        and not eval_suite.merge_allowed(paths, head, events)
    ):
        command = (
            f"{shlex.quote(sys.executable)} -m factory eval "
            f"--profile {shlex.quote(cfg.eval_profile)}"
        )
        baseline = (
            f"First run `cd {shlex.quote(str(cfg.root))} && {command}` "
            "to establish the accepted baseline; "
            if not eval_suite.baseline_ready(cfg) else ""
        )
        dispatch.log(
            f"PR #{n}: {branch} waiting for worker eval keep evidence at {head[:12]}; "
            f"{baseline}run `{command}` from a checkout of {head}"
        )
        _promotion_blocked(events, n, branch, head, "worker eval keep evidence missing", dry_run)
        return
    blocker = next((
        row for row in events
        if row.get("event") == "promotion-blocked"
        and row.get("pr") == n
        and row.get("head") == head
        and isinstance(row.get("reason"), str)
        and (row["reason"] == "refresh failed" or row["reason"].startswith("merge failed:"))
    ), None)
    if blocker:
        dispatch.log(
            f"PR #{n}: {branch} blocked at {head[:12]} ({blocker['reason']}); "
            "resolve the blocker and push a new head to retry promotion"
        )
        return
    if dry_run:
        dispatch.log(f"PR #{n}: would merge {branch}")
        return
    with lifecycle.scope(dispatch.EVENTS, "merge"):
        try:
            dispatch.run([
                "gh", "pr", "edit", str(n), "--repo", cfg.repo,
                "--add-label", dispatch.FACTORY_APPROVED,
            ])
            dispatch.run([
                "gh", "pr", "merge", str(n), "--repo", cfg.repo,
                "--squash", "--match-head-commit", head,
            ])
        except Exception as exc:
            detail = _error_text(exc)
            if "not up to date" in detail.casefold():
                reason = "refresh failed"
                dispatch.log(f"PR #{n}: refreshing {branch} after strict-base merge refusal")
                try:
                    refresh(cfg, n, branch, head)
                except Exception:
                    _promotion_blocked(events, n, branch, head, reason, False)
                    raise
                return
            _promotion_blocked(events, n, branch, head, f"merge failed: {detail}", False)
            raise
        if not any(
            row.get("event") == "approved"
            and row.get("pr") == n
            and row.get("head") == head
            for row in events
        ):
            dispatch.record("approved", pr=n, head=head, branch=branch, gate_head=head, review_head=head)
        dispatch.record("merged", pr=n, head=head, branch=branch, method="squash", by="factory")
    events.append({"event": "merged", "pr": n, "head": head, "branch": branch, "method": "squash"})
    dispatch.log(f"PR #{n}: merged {branch} into {cfg.main}")
    _open_promotion(events, n, branch, head, False)


def decide_promotions(cfg: config.Config, dry_run: bool = False) -> list[str]:
    """One decision per `promotion-open` row, never replayed: once PROMOTION_N attempts are scored after
    the merge, `promotion-keep`, or `promotion-revert` with `agent/revert-lessons-<pr>` opened against the
    merge commit when efficiency or the first-pass rate fell beyond the baseline window's noise.
    Returns the revert branches opened; a failing promotion is logged and retried next pass while later
    promotions are still decided."""
    rows = lifecycle.read_events(dispatch.EVENTS)
    decided = {r.get("pr") for r in rows if r.get("event") in ("promotion-keep", "promotion-revert")}
    reverts = []
    for i, opened in enumerate(rows):
        if opened.get("event") != "promotion-open" or opened.get("pr") in decided:
            continue
        # Attempts journaled after the merge; a pre-merge attempt scored late ran on the old prompt.
        ran = {(r.get("ticket"), r.get("attempt"), r.get("transcript")) for r in rows[i + 1:]
               if r.get("event") == "attempt"}
        after = [s for s in promotion.scored(rows[i + 1:])
                 if (s.get("ticket"), s.get("attempt"), s.get("transcript")) in ran][:promotion.PROMOTION_N]
        if len(after) < promotion.PROMOTION_N:
            continue
        before = promotion.scored(rows[:i])[-promotion.PROMOTION_N:]
        verdict, reason = promotion.drift(before, after)
        candidate = promotion.window(after)
        if dry_run:
            dispatch.log(f"promotion #{opened['pr']}: would {verdict} ({reason})")
            continue
        fields = {}
        try:
            if verdict == "revert":
                fields["branch"] = revert(cfg, opened, reason, candidate)
                reverts.append(fields["branch"])
        except Exception as exc:  # noqa: BLE001 - e.g. a conflicting revert; never blocks later promotions
            dispatch.log(f"promotion #{opened['pr']}: failed ({type(exc).__name__}: {exc})")
            continue
        dispatch.record(f"promotion-{verdict}", pr=opened["pr"], head=opened.get("head"),
                        baseline=opened.get("baseline"), after=candidate, reason=reason, **fields)
        dispatch.log(f"promotion #{opened['pr']}: {verdict} ({reason})")
    return reverts


def revert(cfg: config.Config, opened: dict, reason: str, after: dict) -> str:
    """Open `agent/revert-lessons-<pr>` reverting the promotion's merge commit; the body carries the numbers."""
    pr, branch = opened["pr"], f"{REVERT_BRANCH}{opened['pr']}"
    merge = dispatch.gh_json(["pr", "view", str(pr), "--repo", cfg.repo, "--json", "mergeCommit"])["mergeCommit"]["oid"]

    def edit(wt: Path) -> list[str]:
        dispatch.run(["git", "revert", "--no-commit", merge], cwd=wt)
        # The revert staged everything already; `git add` rejects a pathspec for a file it deleted.
        return dispatch.run(["git", "diff", "--cached", "--name-only", "--diff-filter=d"], cwd=wt).stdout.split()

    base = opened.get("baseline") or {}
    table = "\n".join(f"| {k} | {base.get(k):.3f} | {after.get(k):.3f} |" if None not in (base.get(k), after.get(k))
                      else f"| {k} | {base.get(k)} | {after.get(k)} |"
                      for k in ("efficiency_mean", "compliance_mean", "gate_first_pass_rate"))
    pr_body = (f"{PIPELINE}\n\nReverts #{pr} ({merge[:12]}): {reason}.\n\n"
               f"| metric | before (n={base.get('n')}) | after (n={after['n']}) |\n|---|---|---|\n{table}\n")
    chore_pr(cfg, branch, edit, f"{branch}: revert #{pr}", pr_body, protected_override=True)
    return branch


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
