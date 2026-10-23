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
import tomllib
from collections.abc import Callable
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
            fields = {k: v for k, v in e.items() if k not in ("ticket", "title", "log")}
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



def record_learn_decision(
    cfg: config.Config, tickets: list[int], lessons: int, branch: str | None,
    *, curated: str | None = None,
) -> str:
    from factory import __version__, trace

    chosen = "propose" if lessons else "skip"
    payload = trace.decision(
        ticket=tickets[-1],
        stage="learn",
        inputs=[
            {"kind": "evidence", "ref": f"https://github.com/{cfg.repo}/issues/{ticket}",
             "summary": f"evidence ticket #{ticket}"}
            for ticket in tickets
        ] + ([
            {"kind": "resulting_pr", "ref": branch, "summary": f"lessons PR branch {branch}"}
        ] if branch else []),
        options=[
            {"option": "propose", "eligible": bool(lessons),
             "reason": f"proposed {lessons} lesson(s)" if lessons else "no recurring lesson was found"},
            {"option": "skip", "eligible": not lessons,
             "reason": "no lesson was proposed" if not lessons else "evidence supported a proposal"},
        ],
        chosen=chosen,
        rationale=(
            f"proposed {lessons} lesson(s)"
            + (f"; CURATE {curated}" if curated else "")
            if lessons else "evidence produced no lesson"
        ),
        causes=[{"ticket": ticket, "relation": "follows"} for ticket in tickets],
        cost=None,
        actor={"kind": "model", "name": "factory learn", "model": cfg.manager_model,
               "version": __version__},
    )
    dispatch.record("decision", **payload)
    return payload["decision_id"]

def chore_pr(cfg: config.Config, branch: str, edit: Callable[[Path], list[str]], title: str, pr_body: str,
             protected_override: bool = False) -> None:
    """Branch from origin/main in a throwaway worktree, let `edit` return the paths it changed, commit,
    run the attempt gate (journaled as `gate` bound to the head; a FAIL raises ValueError and pushes
    nothing), then open the PR. `protected_override` is for diffs whose add-only policy `curate`
    already enforced, and for reverting them."""
    wt = cfg.factory / f"wt-{branch.removeprefix('agent/')}"
    dispatch.run(["git", "fetch", "origin", cfg.main], cwd=cfg.root)
    dispatch.run(["git", "worktree", "add", "--force", "-B", branch, str(wt), f"origin/{cfg.main}"], cwd=cfg.root)
    try:
        paths = edit(wt)
        dispatch.run(["git", "add", "--", *paths], cwd=wt)
        flags = ["-s"] if cfg.signoff else []
        dispatch.run(["git", "commit", *flags, "-m", title], cwd=wt)
        gate_head(wt, branch, protected_override)
        dispatch.push_and_pr(wt, branch, title, pr_body, label=LABEL_CHORE)
    finally:
        dispatch.run(["git", "worktree", "remove", "--force", str(wt)], cwd=cfg.root, check=False)


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
    cfg: config.Config, tickets: list[int], paths: list[str],
    allowed: dict[str, bool], result: str,
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
    payload = trace.decision(
        ticket=tickets[-1],
        stage="curate",
        inputs=[
            {"kind": "evidence", "ref": f"https://github.com/{cfg.repo}/issues/{ticket}",
             "summary": f"evidence ticket #{ticket}"}
            for ticket in tickets
        ] + ([
            {"kind": "resulting_pr", "ref": result, "summary": f"CURATE PR branch {result}"}
        ] if accepted else []),
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


def curate(cfg: config.Config, diff: str, tickets: list[int], notes: str) -> str:
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
    pr_body = (f"{PIPELINE}\n\n`factory learn` CURATE proposed by the manager from tickets {cited}.\n\n"
               f"## Manager notes\n\n{notes or '(none)'}")
    try:
        chore_pr(cfg, branch, edit, f"{branch}: curate harness context", pr_body, protected_override=True)
        result = branch
    except ValueError as exc:
        result = f"rejected: {exc}"
    finally:
        patch.unlink(missing_ok=True)
    _record_curate_decision(cfg, tickets, observed_paths, allowed, result)
    return result


def run(cfg: config.Config, last: int = 10, dry_run: bool = False, **fields: object) -> dict | None:
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
    if not cfg.manager:
        path.write_text(content)
        event = {"tickets": tickets, "lessons": len(lessons), **fields}
        dispatch.record("learn", **event)
        record_learn_decision(cfg, tickets, len(lessons), None)
        print(f"wrote {path}; review and commit it")
        return event
    branch = branch_name("lessons", tickets)

    def edit(wt: Path) -> list[str]:
        (wt / LESSONS_NAME).write_text(content)
        return [LESSONS_NAME]

    pr_body = f"{PIPELINE}\n\n`factory learn` distilled tickets {', '.join(f'#{n}' for n in tickets)} via the manager.\n\n{body}"
    chore_pr(cfg, branch, edit, f"{branch}: update {LESSONS_NAME}", pr_body)
    print(f"opened a {LABEL_CHORE} PR from {branch}")
    curated = curate(cfg, diff, tickets, notes_md) if diff else None
    if curated:
        print(f"CURATE {curated}" if curated.startswith("rejected") else f"opened a {LABEL_CHORE} PR from {curated}")
    event = {"tickets": tickets, "lessons": len(lessons), "branch": branch, "curate": curated, **fields}
    dispatch.record("learn", **event)
    record_learn_decision(cfg, tickets, len(lessons), branch, curated=curated)
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


def promote_pass(prs: list[dict], dry_run: bool = False) -> None:
    """Merge stage for `factory learn` PRs (`LESSONS_BRANCH`) in the ticket merge stage's open-PR listing,
    under the landing lock: a journaled `gate` PASS and reviewer `review-result` APPROVE bound to the PR
    head and only passing (or skipped) CI checks earn `factory-approved` and a squash merge. A head behind
    main that changes more than LESSONS_NAME (which no check reads) is refreshed instead (`refresh`).
    A lessons or curate merge journals `promotion-open` with the baseline means of the last PROMOTION_N
    scored attempts; a revert merge closes the loop and opens nothing. A failing PR is logged and skipped."""
    cfg = dispatch.cfg
    events = None
    for pr in sorted(prs, key=lambda p: p["number"]):
        n, branch = pr["number"], pr["headRefName"]
        if (not LESSONS_BRANCH.match(branch) or pr["isDraft"] or pr["baseRefName"] != cfg.main
                or pr["reviewDecision"] == "CHANGES_REQUESTED"):
            continue
        if events is None:
            events = lifecycle.read_events(dispatch.EVENTS)
        try:
            promote(cfg, pr, events, dry_run)
        except Exception as exc:  # noqa: BLE001 - one stuck chore PR never blocks the others
            dispatch.log(f"PR #{n}: {branch} promotion failed ({type(exc).__name__}: {exc})")


def promote(cfg: config.Config, pr: dict, events: list[dict], dry_run: bool) -> None:
    n, branch, head = pr["number"], pr["headRefName"], pr["headRefOid"]
    gated = any(e.get("event") == "gate" and e.get("branch") == branch and e.get("head") == head
                and e.get("gate") == "PASS" for e in events)
    reviewed = any(e.get("event") == "review-result" and e.get("pr") == n and e.get("head") == head
                   and e.get("verdict") == "APPROVE" for e in events)
    if not (gated and reviewed):
        dispatch.log(f"PR #{n}: {branch} waiting for {'review' if gated else 'gate evidence'} at {head[:12]}")
        return
    buckets = {c["bucket"] for c in dispatch.pr_checks(n)}
    if "pass" not in buckets or buckets - {"pass", "skipping"}:
        dispatch.log(f"PR #{n}: {branch} CI {sorted(buckets) or 'missing'}; not merging")
        return
    compare = dispatch.gh_json(["api", f"repos/{cfg.repo}/compare/{cfg.main}...{head}"])
    if compare["behind_by"] and {f["filename"] for f in compare["files"]} - {LESSONS_NAME}:
        dispatch.log(f"PR #{n}: {'would refresh' if dry_run else 'refreshing'} {branch} (behind {cfg.main})")
        if not dry_run:
            refresh(cfg, n, branch, head)
        return
    if dry_run:
        dispatch.log(f"PR #{n}: would merge {branch}")
        return
    with lifecycle.scope(dispatch.EVENTS, "merge"):
        dispatch.run(["gh", "pr", "edit", str(n), "--repo", cfg.repo, "--add-label", dispatch.FACTORY_APPROVED])
        dispatch.record("approved", pr=n, head=head, branch=branch, gate_head=head, review_head=head)
        dispatch.run(["gh", "pr", "merge", str(n), "--repo", cfg.repo, "--squash", "--match-head-commit", head])
        dispatch.record("merged", pr=n, head=head, branch=branch, method="squash")
    dispatch.log(f"PR #{n}: merged {branch} into {cfg.main}")
    if branch.startswith(REVERT_BRANCH):
        return
    baseline = promotion.window(promotion.scored(events)[-promotion.PROMOTION_N:])
    dispatch.record("promotion-open", pr=n, head=head, branch=branch, baseline=baseline)
    dispatch.log(f"PR #{n}: promotion open (baseline n={baseline['n']})")


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
        run(cfg, args.last, args.dry_run)
    except ValueError as exc:
        raise SystemExit(f"factory learn: {exc}")
    return 0
