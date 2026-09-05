"""Stateless AI-factory dispatcher.

One pass per invocation: first the upstream sync merges any new upstream main
commits into the fork's main (host gate, no CI), then the merge stage lands
at most one approved, green, up-to-date factory PR on main; then pick
claimable issues (or --ticket N), run a worker agent in a git worktree, gate,
open a PR, review with the reviewer model, bounce once. A reviewer APPROVE marks the PR
`factory-approved`; the merge stage requires that label, green GitHub CI, and
a head containing the current main tip before squash-merging.
All state lives in GitHub and .factory/ on disk.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from factory import config
from factory.config import (
    LABEL_AGENT,
    LABEL_APPROVED,
    LABEL_CHORE,
    LABEL_HUMAN,
    LESSONS_NAME,
    Config,
)

cfg: Config
ROOT: Path
REPO: str
UPSTREAM: str | None  # git remote name; None disables upstream sync
UPSTREAM_REPO: str | None  # GitHub "owner/name" of UPSTREAM, or None
FACTORY: Path
LOGS: Path
SYNC_LOG: Path
EVENTS: Path
MAX_ACTIVE: int
MAX_ATTEMPTS: int


def configure(c: Config) -> None:
    global cfg, ROOT, REPO, UPSTREAM, UPSTREAM_REPO, FACTORY, LOGS, SYNC_LOG, EVENTS
    global MAX_ACTIVE, MAX_ATTEMPTS
    cfg = c
    ROOT = cfg.root
    REPO = cfg.repo
    UPSTREAM = cfg.upstream
    UPSTREAM_REPO = config.remote_slug(ROOT, UPSTREAM) if UPSTREAM else None
    FACTORY = cfg.factory
    LOGS = FACTORY / "logs"
    SYNC_LOG = FACTORY / "upstream-sync.jsonl"
    EVENTS = FACTORY / "events.jsonl"
    MAX_ACTIVE = cfg.max_active
    MAX_ATTEMPTS = cfg.max_attempts


STANDING_INSTRUCTIONS = """
## Instructions

- Implement exactly what the ticket above asks for; nothing more.
- Use TDD where practical: failing test first, then the fix.
- Commit incrementally with `git commit{commit_flag}`. Stage only files you created or
  edited for the ticket; never `git add -A`, and never commit
  `.factory-prompt.md` or gate reports.
- NEVER use `git stash` — the stash is shared with the user's other worktrees.
- Only push `agent/{n}`. NEVER push, merge into, or fast-forward `{main}`, and
  never close the ticket yourself: the dispatcher opens the PR and the merge
  stage lands it after review and CI.
- Finish by running `{python} -m factory gate --report .factory/gate-report-{n}.md`
  and fixing any failures it reports.
- Last, write `.factory/handoff-{n}.md` (gitignored): what you changed, what is
  still unverified, and what you would do next. The next attempt and the human
  who inherits this ticket read it.
"""


def log(msg: str) -> None:
    print(f"[dispatch] {msg}", flush=True)


def run(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def gh_json(args: list[str]) -> object:
    out = run(["gh", *args]).stdout
    return json.loads(out)


def lock_held(lockfile: Path) -> bool:
    """True if another process holds an flock on lockfile."""
    if not lockfile.exists():
        return False
    with lockfile.open("r") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except OSError:
            return True


def ticket_lock(n: int) -> Path:
    # Outside the worktree: deleting a worktree must not erase the evidence
    # that its pipeline is alive (learned from ticket #5's mid-flight wipe).
    locks = FACTORY / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    return locks / f"{n}.lock"


def active_ticket_count() -> int:
    if not FACTORY.is_dir():
        return 0
    return sum(1 for f in (FACTORY / "locks").glob("*.lock") if lock_held(f))


def issue_is_open(number: int) -> bool:
    data = gh_json(["issue", "view", str(number), "--repo", REPO, "--json", "state"])
    return data["state"].upper() == "OPEN"


def open_blockers(number: int, body: str) -> list[int]:
    blockers: set[int] = set()
    # GitHub issue-dependency API; 404 means the feature/edges are absent.
    proc = run(
        ["gh", "api", f"repos/{REPO}/issues/{number}/dependencies/blocked_by"],
        check=False,
    )
    if proc.returncode == 0:
        for dep in json.loads(proc.stdout):
            if dep.get("state", "").lower() == "open":
                blockers.add(dep["number"])
    # "Blocked by: #n" lines in the body.
    for line in re.findall(r"(?im)^blocked by:(.*)$", body or ""):
        for ref in re.findall(r"#(\d+)", line):
            n = int(ref)
            if n not in blockers and issue_is_open(n):
                blockers.add(n)
    return sorted(blockers)


def frontier() -> list[dict]:
    issues = gh_json(
        [
            "issue",
            "list",
            "--repo",
            REPO,
            "--state",
            "open",
            "--label",
            LABEL_AGENT,
            "--json",
            "number,title,body,labels,assignees",
        ]
    )
    ready = []
    for issue in issues:
        n = issue["number"]
        if issue["assignees"]:
            log(f"#{n}: skipped (assigned)")
            continue
        blockers = open_blockers(n, issue.get("body", ""))
        if blockers:
            log(f"#{n}: skipped (blocked by {', '.join(f'#{b}' for b in blockers)})")
            continue
        ready.append(issue)
    return ready


def build_prompt(n: int, wt: Path, extra: str = "") -> str:
    issue = gh_json(
        ["issue", "view", str(n), "--repo", REPO, "--json", "title,body,comments"]
    )
    parts = [f"# Issue #{n}: {issue['title']}", "", issue.get("body") or "(no body)"]
    for c in issue.get("comments") or []:
        author = (c.get("author") or {}).get("login", "unknown")
        parts += ["", f"## Comment by {author}", "", c.get("body", "")]
    commit_flag = " -s" if cfg.signoff else ""
    parts.append(
        STANDING_INSTRUCTIONS.format(
            n=n, commit_flag=commit_flag, main=cfg.main, python=sys.executable
        )
    )
    lessons = ROOT / LESSONS_NAME
    if lessons.exists():
        parts += ["", "## Lessons from previous tickets in this repository", "", lessons.read_text()]
    handoff = wt / ".factory" / f"handoff-{n}.md"
    if handoff.exists():
        parts += ["", "## Handoff from the previous attempt", "", handoff.read_text()]
    if extra:
        parts += ["", extra]
    return "\n".join(parts) + "\n"


def record(event: str, **fields: object) -> None:
    """Append one row to .factory/events.jsonl: the factory's audit trail.

    Every stage transition lands here with its evidence pointers, so a ticket's
    history is readable without GitHub round trips, and `stats`/the dashboard
    can be computed from traces rather than reconstructed.
    """
    FACTORY.mkdir(exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
    with EVENTS.open("a") as f:
        f.write(json.dumps(row) + "\n")


def run_worker(cmd: list[str], wt: Path, logfile: Path) -> int:
    log(f"worker: {' '.join(cmd)} -> {logfile}")
    started = time.monotonic()
    with logfile.open("a") as out:
        code = subprocess.run(cmd, cwd=wt, stdout=out, stderr=subprocess.STDOUT).returncode
    log(f"worker exited {code} after {int(time.monotonic() - started)}s")
    return code


def ensure_worktree(n: int) -> Path:
    wt = FACTORY / f"wt-{n}"
    if wt.is_dir():
        return wt
    run(["git", "fetch", "origin"], cwd=ROOT)
    branch = f"agent/{n}"
    exists = (
        run(["git", "rev-parse", "--verify", branch], cwd=ROOT, check=False).returncode
        == 0
    )
    if exists:
        run(["git", "worktree", "add", str(wt), branch], cwd=ROOT)
    else:
        run(
            ["git", "worktree", "add", str(wt), "-b", branch, f"origin/{cfg.main}"],
            cwd=ROOT,
        )
    return wt


def commit_leftovers(wt: Path, n: int, title: str) -> None:
    run(
        [
            "git",
            "add",
            "-A",
            "--",
            ".",
            ":(exclude).factory-prompt.md",
            ":(exclude).factory",
        ],
        cwd=wt,
        check=False,
    )
    staged = run(["git", "diff", "--cached", "--quiet"], cwd=wt, check=False)
    if staged.returncode != 0:
        flags = ["-s"] if cfg.signoff else []
        run(["git", "commit", *flags, "-m", f"agent/{n}: {title}"], cwd=wt)


def run_gate(wt: Path, n: int | str, skip: str = "") -> tuple[bool, str]:
    report_rel = f".factory/gate-report-{n}.md"
    (wt / ".factory").mkdir(exist_ok=True)
    # GPU serialization is the gate's job: the gate flocks the exclusive lock
    # itself. Locking here too deadlocks the gate subprocess (seen in run #7).
    cmd = [
        sys.executable,
        "-m",
        "factory",
        "gate",
        "--base",
        f"origin/{cfg.main}",
        "--report",
        report_rel,
    ]
    if skip:
        cmd += ["--skip", skip]
    proc = subprocess.run(cmd, cwd=wt, capture_output=True, text=True)
    report = wt / report_rel
    text = report.read_text() if report.exists() else proc.stdout + proc.stderr
    return proc.returncode == 0, text


def escalation_packet(
    n: int,
    reason: str,
    log_path: Path | None,
    wt: Path,
    gate_detail: str = "",
    artifact: str | None = None,
) -> tuple[Path, int]:
    events = [
        json.loads(line)
        for line in EVENTS.read_text().splitlines()
        if line.strip()
    ] if EVENTS.exists() else []
    ticket_events = [event for event in events if event.get("ticket") == n]
    attempts = [event for event in ticket_events if event.get("event") == "attempt"]
    rows = [
        f"| {event.get('attempt', '')} | {event.get('gate') or 'not run'} | "
        f"{event.get('worker_exit', '')} | {event.get('seconds', '')} | "
        f"`{event.get('log') or ''}` |"
        for event in attempts
    ] or ["| — | — | — | — | none recorded |"]
    artifact = artifact or str(n)
    gate = wt / ".factory" / f"gate-report-{artifact}.md"
    review = FACTORY / f"review-{artifact}.md"
    handoff = wt / ".factory" / f"handoff-{artifact}.md"
    logs = list(dict.fromkeys(
        str(event["log"]) for event in attempts if event.get("log")
    ))
    if log_path and str(log_path) not in logs:
        logs.append(str(log_path))
    packet = FACTORY / "escalations" / f"{n}.md"
    packet.parent.mkdir(parents=True, exist_ok=True)
    packet.write_text(
        f"# Escalation #{n}\n\n"
        f"## Reason\n\n{reason}\n\n"
        "## Attempts\n\n"
        "| Attempt | Gate | Worker exit | Seconds | Log |\n"
        "|---:|---|---:|---:|---|\n"
        + "\n".join(rows)
        + "\n\n## Last gate report\n\n"
        + ((gate.read_text() if gate.exists() else gate_detail).strip()[-6000:] or "(none recorded)")
        + "\n\n## Latest review findings\n\n"
        + (review.read_text().strip()[-6000:] if review.exists() else "(none recorded)")
        + "\n\n## Handoff\n\n"
        + (handoff.read_text().strip()[-4000:] if handoff.exists() else "(none recorded)")
        + "\n\n## Log paths\n\n"
        + ("\n".join(f"- `{path}`" for path in logs) or "- none recorded")
        + f"\n\n## Worktree path\n\n`{wt}`\n"
    )
    round_number = 1 + sum(
        event.get("event") == "escalate" for event in ticket_events
    )
    return packet, round_number


def escalate(n: int, reason: str, log_path: Path | None) -> None:
    log(f"#{n}: escalating to human ({reason})")
    wt = FACTORY / f"wt-{n}"
    packet, round_number = escalation_packet(n, reason, log_path, wt)
    record(
        "escalate", ticket=n, reason=reason, log=str(log_path) if log_path else None,
        packet=str(packet), round=round_number,
    )
    run(
        [
            "gh",
            "issue",
            "edit",
            str(n),
            "--repo",
            REPO,
            "--remove-assignee",
            "@me",
            "--remove-label",
            LABEL_AGENT,
            "--add-label",
            LABEL_HUMAN,
        ],
        check=False,
    )
    body = f"Factory dispatcher escalating: {reason}.\n\nEscalation packet: `{packet}`"
    if log_path:
        body += f"\n\nWorker logs: `{log_path}`"
    handoff = wt / ".factory" / f"handoff-{n}.md"
    if handoff.exists():
        body += f"\n\nWorker handoff notes:\n\n{handoff.read_text().strip()[-4000:]}"
    run(["gh", "issue", "comment", str(n), "--repo", REPO, "--body", body], check=False)


def review(wt: Path, n: int, gate_report: str) -> tuple[str, str]:
    """Run the two-axis diff review. Returns (verdict, findings markdown)."""
    prompt = (
        f"Review `git diff origin/{cfg.main}..HEAD` in this repository on two axes:\n"
        f"1. Standards: does the code follow this repo's documented conventions "
        f"(AGENTS.md, CONTRIBUTING.md, docs/)?\n"
        f"2. Spec: does the diff satisfy the text and acceptance criteria of "
        f"GitHub issue #{n} in {REPO}?\n"
        f"Review the DIFF only. Do NOT execute builds or tests: your sandbox "
        f"differs from the target host, so your results are not evidence. The "
        f"deterministic gate already ran on the target host; its report is "
        f"authoritative for build/test/scan status:\n\n"
        f"```\n{gate_report}\n```\n\n"
        f"Every finding MUST cite evidence as `path:line` from the diff; a "
        f"finding without a citation does not count. Do not report style "
        f"preferences, hypothetical extensibility, or anything the gate already "
        f"covers. REVISE only for findings that would fail the issue's acceptance "
        f"criteria or this repo's documented conventions.\n"
        f"Output findings as markdown. End with exactly one line: "
        f"`VERDICT: APPROVE` or `VERDICT: REVISE`."
    )
    proc = subprocess.run(
        cfg.review_cmd(prompt), cwd=wt, capture_output=True, text=True
    )
    findings = proc.stdout.strip() or proc.stderr.strip()
    m = re.search(r"VERDICT:\s*(APPROVE|REVISE)", findings)
    verdict = m.group(1) if m else "REVISE"
    record("review", ticket=n, verdict=verdict, parsed=bool(m))
    return verdict, findings


def push_and_pr(wt: Path, n: int, title: str, gate_report: str) -> bool:
    """Push agent/n and open its PR. False if the branch adds nothing over
    main (nothing to review; a worker that landed its work elsewhere)."""
    run(["git", "fetch", "origin", cfg.main], cwd=wt)
    ahead = run(["git", "rev-list", "--count", f"origin/{cfg.main}..HEAD"], cwd=wt).stdout
    if int(ahead) == 0:
        return False
    run(["git", "push", "-u", "origin", f"agent/{n}"], cwd=wt)
    existing = gh_json(
        ["pr", "list", "--repo", REPO, "--head", f"agent/{n}", "--json", "number"]
    )
    if existing:
        log(f"#{n}: PR already exists (#{existing[0]['number']})")
        return True
    body_file = FACTORY / f"pr-body-{n}.md"
    body_file.write_text(f"Closes #{n}\n\n## Gate report\n\n{gate_report}\n")
    run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            REPO,
            "--head",
            f"agent/{n}",
            "--title",
            f"agent/{n}: {title}",
            "--body-file",
            str(body_file),
        ]
    )
    record("pr-opened", ticket=n)
    return True


def pr_comment(n: int, text: str) -> None:
    body_file = FACTORY / f"review-{n}.md"
    body_file.write_text(text + "\n")
    run(
        [
            "gh",
            "pr",
            "comment",
            f"agent/{n}",
            "--repo",
            REPO,
            "--body-file",
            str(body_file),
        ],
        check=False,
    )


# ---------------------------------------------------------------------------
# Upstream sync: merge new upstream main commits into the fork's main.
# ---------------------------------------------------------------------------

SYNC_TITLE = "upstream sync: "


def sync_record(**rec: object) -> None:
    record("upstream-sync", **rec)
    FACTORY.mkdir(exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec}
    with SYNC_LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")


def open_sync_issue() -> int | None:
    issues = gh_json(
        [
            "issue",
            "list",
            "--repo",
            REPO,
            "--state",
            "open",
            "--search",
            '"upstream sync" in:title',
            "--json",
            "number,title",
        ]
    )
    for issue in issues:
        if issue["title"].startswith(SYNC_TITLE):
            return issue["number"]
    return None


def sync_escalate(tip: str, reason: str, detail: str) -> str:
    """Open one ready-for-human issue for a failed sync; returns its URL."""
    body = (
        f"Automatic sync of `{UPSTREAM_REPO}` {cfg.main} ({tip}) into `{cfg.main}` failed: "
        f"{reason}.\n\n```\n{detail.strip()[-6000:]}\n```\n\n"
        f"Resolve through the normal flow: a PR onto `{cfg.main}` that contains the "
        "upstream tip (merge, do not squash or rebase it away), gated and reviewed "
        f"like any factory PR. Never push `{cfg.main}` directly. Close this issue once "
        f"`{cfg.main}` contains the tip; the dispatcher skips upstream sync while it is "
        "open."
    )
    body_file = FACTORY / "sync-issue.md"
    body_file.write_text(body)
    out = run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            REPO,
            "--title",
            f"{SYNC_TITLE}{reason} at {tip[:12]}",
            "--label",
            LABEL_HUMAN,
            "--body-file",
            str(body_file),
        ]
    ).stdout
    url = out.strip().splitlines()[-1]
    n = int(url.rstrip("/").rsplit("/", 1)[-1])
    packet, round_number = escalation_packet(
        n, reason, None, FACTORY / "wt-upstream", detail, "upstream"
    )
    record(
        "escalate", ticket=n, upstream=tip, reason=reason, packet=str(packet),
        round=round_number,
    )
    return url


def sync_pass(dry_run: bool) -> None:
    """Merge upstream main into fork main when upstream moved; one merge per pass.

    Evidence is the host gate (minus the leak scan: upstream is already
    public). Conflicts or a failed gate open one ready-for-human issue and the
    stage stays parked until that issue closes. Runs under the merge lock:
    it moves main, so it must not race the merge stage.
    """
    if UPSTREAM is None:
        return
    run(["git", "fetch", "origin", cfg.main], cwd=ROOT)
    run(["git", "fetch", UPSTREAM, cfg.main], cwd=ROOT)
    tip = run(["git", "rev-parse", f"{UPSTREAM}/{cfg.main}"], cwd=ROOT).stdout.strip()
    contained = run(
        ["git", "merge-base", "--is-ancestor", tip, f"origin/{cfg.main}"],
        cwd=ROOT,
        check=False,
    )
    if contained.returncode == 0:
        log(f"upstream sync: {cfg.main} contains upstream tip {tip[:12]}")
        return
    count = run(
        ["git", "rev-list", "--count", f"origin/{cfg.main}..{UPSTREAM}/{cfg.main}"],
        cwd=ROOT,
    ).stdout.strip()
    issue = open_sync_issue()
    if issue:
        log(f"upstream sync: {count} commit(s) behind; waiting on human (#{issue})")
        return
    if dry_run:
        log(f"upstream sync: would merge {count} upstream commit(s) at {tip[:12]}")
        return
    wt = FACTORY / "wt-upstream"
    if wt.is_dir():
        run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, check=False)
    run(
        ["git", "worktree", "add", "--detach", str(wt), f"origin/{cfg.main}"], cwd=ROOT
    )
    try:
        merge = run(
            [
                "git",
                "merge",
                "--no-ff",
                *(["--signoff"] if cfg.signoff else []),
                "-m",
                f"Merge upstream {cfg.main} at {tip[:12]} ({count} commits)",
                f"{UPSTREAM}/{cfg.main}",
            ],
            cwd=wt,
            check=False,
        )
        if merge.returncode != 0:
            conflicts = run(
                ["git", "diff", "--name-only", "--diff-filter=U"], cwd=wt, check=False
            ).stdout
            run(["git", "merge", "--abort"], cwd=wt, check=False)
            url = sync_escalate(tip, "merge conflict", conflicts or merge.stderr)
            sync_record(upstream=tip, commits=count, result="conflict", issue=url)
            log(f"upstream sync: merge conflict at {tip[:12]}; escalated {url}")
            return
        ok, report = run_gate(wt, "upstream", skip="leak-scan")
        if not ok:
            url = sync_escalate(tip, "gate failed", report)
            sync_record(upstream=tip, commits=count, result="gate-failed", issue=url)
            log(f"upstream sync: gate failed at {tip[:12]}; escalated {url}")
            return
        merged = run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
        push = run(["git", "push", "origin", f"HEAD:{cfg.main}"], cwd=wt, check=False)
        if push.returncode != 0:
            # main moved under us; the next pass retries from the new tip.
            sync_record(
                upstream=tip,
                commits=count,
                result="push-rejected",
                detail=push.stderr[-500:],
            )
            log(f"upstream sync: push rejected; retry next pass\n{push.stderr}")
            return
        sync_record(upstream=tip, commits=count, result="synced", merge=merged)
        log(f"upstream sync: merged {count} commit(s) at {tip[:12]} -> {merged[:12]}")
    finally:
        run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, check=False)


# ---------------------------------------------------------------------------
# Merge stage: consume the review verdict + CI and land approved PRs on main.
# ---------------------------------------------------------------------------

FACTORY_APPROVED = LABEL_APPROVED


def approve_pr(n: int) -> None:
    """Record the reviewer APPROVE durably on the PR (merge-stage precondition)."""
    record("approved", ticket=n)
    run(
        [
            "gh",
            "pr",
            "edit",
            f"agent/{n}",
            "--repo",
            REPO,
            "--add-label",
            FACTORY_APPROVED,
        ],
        check=False,
    )


def signoff() -> str:
    name = run(["git", "config", "user.name"], cwd=ROOT).stdout.strip()
    email = run(["git", "config", "user.email"], cwd=ROOT).stdout.strip()
    return f"Signed-off-by: {name} <{email}>"


def pr_checks(pr: int) -> list[dict]:
    """gh check rows [{name, bucket}]; bucket: pass/fail/pending/skipping/cancel.

    `gh pr checks` exits nonzero for failing or pending checks; that is data
    here, not an error. Unparseable output returns [] which the caller treats
    as "no passing CI" and refuses to merge — fail closed.
    """
    proc = run(
        ["gh", "pr", "checks", str(pr), "--repo", REPO, "--json", "name,bucket"],
        check=False,
    )
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return []


def refresh_pr_branch(n: int, pr: int) -> None:
    """Rebase agent/n onto current main, re-gate on this host, force-push.

    Re-earns the evidence against what the PR will actually merge into; CI
    re-runs on the push and the merge happens on a later pass.
    """
    wt = ensure_worktree(n)
    run(["git", "fetch", "origin"], cwd=wt)
    if run(["git", "rebase", f"origin/{cfg.main}"], cwd=wt, check=False).returncode != 0:
        run(["git", "rebase", "--abort"], cwd=wt, check=False)
        escalate(n, f"PR #{pr}: rebase onto moved main conflicts; worktree {wt}", None)
        return
    ok, report = run_gate(wt, n)
    if not ok:
        pr_comment(n, f"Gate failed after rebase onto current main:\n\n{report}")
        escalate(n, f"PR #{pr}: gate failed after rebase onto moved main", None)
        return
    run(["git", "push", "--force-with-lease", "origin", f"agent/{n}"], cwd=wt)
    record("refreshed", ticket=n, pr=pr)
    log(f"#{n}: PR #{pr} rebased onto current main and re-gated; merge next pass")


def cleanup_after_merge(n: int) -> None:
    wt = FACTORY / f"wt-{n}"
    if wt.is_dir():
        run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, check=False)
    run(["git", "branch", "-D", f"agent/{n}"], cwd=ROOT, check=False)
    ticket_lock(n).unlink(missing_ok=True)


def land_pass(dry_run: bool) -> None:
    """Everything that moves main, under one lock: upstream sync, then the
    merge stage (at most ONE approved, green, up-to-date factory PR per pass).

    A merge requires all four independently produced pieces of evidence:
    host gate PASS (in the PR body), reviewer APPROVE (`factory-approved`
    label), green GitHub CI, and a head that already contains the current
    main tip — so the evidence was produced against what it merges into.
    One merge per pass is the merge queue: landing one PR makes the others
    stale, and the refresh path re-earns their evidence before they land.
    A human blocks any merge by requesting changes on the PR.
    """
    # Serialize against concurrent dispatcher runs (timer + manual): two merge
    # stages rebasing the same worktree would corrupt it. Skip, don't wait —
    # the next timer pass retries.
    (FACTORY / "locks").mkdir(parents=True, exist_ok=True)
    lock_fd = (FACTORY / "locks" / "merge.lock").open("w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("sync + merge stage: skipped (another dispatcher holds the merge lock)")
        lock_fd.close()
        return
    try:
        sync_pass(dry_run)
        merge_pass_locked(dry_run)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def merge_pass_locked(dry_run: bool) -> None:
    prs = gh_json(
        [
            "pr",
            "list",
            "--repo",
            REPO,
            "--state",
            "open",
            "--json",
            "number,headRefName,isDraft,labels,reviewDecision",
        ]
    )
    candidates = []
    for pr in prs:
        m = re.fullmatch(r"agent/(\d+)", pr["headRefName"])
        if not m or pr["isDraft"]:
            continue
        if FACTORY_APPROVED not in {label["name"] for label in pr["labels"]}:
            continue
        if pr["reviewDecision"] == "CHANGES_REQUESTED":
            log(f"PR #{pr['number']}: human requested changes; not merging")
            continue
        candidates.append((pr["number"], int(m.group(1))))
    for pr_num, n in sorted(candidates):
        checks = pr_checks(pr_num)
        buckets: dict[str, int] = {}
        for c in checks:
            buckets[c["bucket"]] = buckets.get(c["bucket"], 0) + 1
        failed = [c["name"] for c in checks if c["bucket"] in ("fail", "cancel")]
        if failed:
            # A finished red run is deterministic evidence, not a flake guess.
            # Pull the PR from candidacy so this escalates once, not every pass;
            # a human (or a re-run pipeline) re-adds the label after the fix.
            if dry_run:
                log(f"PR #{pr_num}: would escalate (CI failed: {', '.join(failed)})")
                continue
            run(
                [
                    "gh",
                    "pr",
                    "edit",
                    str(pr_num),
                    "--repo",
                    REPO,
                    "--remove-label",
                    FACTORY_APPROVED,
                ],
                check=False,
            )
            escalate(
                n,
                f"PR #{pr_num}: CI failed ({', '.join(failed)}); "
                f"`{FACTORY_APPROVED}` label removed",
                None,
            )
            continue
        if buckets.get("pending"):
            log(f"PR #{pr_num}: CI pending {buckets}; waiting")
            continue
        if not buckets.get("pass"):
            log(f"PR #{pr_num}: no passing CI checks reported; refusing to merge")
            continue
        behind = gh_json(["api", f"repos/{REPO}/compare/{cfg.main}...agent/{n}"])[
            "behind_by"
        ]
        if dry_run:
            log(f"PR #{pr_num}: would {'refresh (behind main)' if behind else 'merge'}")
            return
        if behind:
            refresh_pr_branch(n, pr_num)
            return
        title = gh_json(["pr", "view", str(pr_num), "--repo", REPO, "--json", "title"])[
            "title"
        ]
        # A PR that carries new upstream commits (a human/agent-resolved sync)
        # must keep them as ancestors of main, or the sync stage never sees
        # main contain the upstream tip. Squash everything else.
        run(["git", "fetch", "origin", cfg.main, f"agent/{n}"], cwd=ROOT)
        if UPSTREAM is None:
            carries_upstream = False
        else:
            run(["git", "fetch", UPSTREAM, cfg.main], cwd=ROOT)
            contains = lambda ref: (  # noqa: E731
                run(
                    ["git", "merge-base", "--is-ancestor", f"{UPSTREAM}/{cfg.main}", ref],
                    cwd=ROOT,
                    check=False,
                ).returncode
                == 0
            )
            carries_upstream = contains(f"origin/agent/{n}") and not contains(
                f"origin/{cfg.main}"
            )
        method = "--merge" if carries_upstream else "--squash"
        body = f"Closes #{n}\n\n{signoff()}" if cfg.signoff else f"Closes #{n}"
        run(
            [
                "gh",
                "pr",
                "merge",
                str(pr_num),
                "--repo",
                REPO,
                method,
                "--subject",
                title,
                "--body",
                body,
            ]
        )
        record("merged", ticket=n, pr=pr_num, method=method[2:])
        log(f"PR #{pr_num}: merged into {cfg.main} ({method[2:]}, ticket #{n})")
        cleanup_after_merge(n)
        return


def worker_round(
    n: int,
    wt: Path,
    labels: set[str],
    title: str,
    extra: str,
    attempt: int,
    deadline: float,
) -> tuple[bool, str, Path]:
    """One worker + gate cycle. Returns (gate_ok, report, logfile)."""
    promptfile = wt / ".factory-prompt.md"
    promptfile.write_text(build_prompt(n, wt, extra))
    logfile = LOGS / f"{n}-attempt-{attempt}.log"
    started = time.monotonic()
    code = run_worker(cfg.worker(labels, promptfile, wt), wt, logfile)
    commit_leftovers(wt, n, title)
    if time.monotonic() > deadline:
        record("attempt", ticket=n, attempt=attempt, worker_exit=code, gate=None, log=str(logfile))
        return False, "budget exceeded before gate", logfile
    ok, report = run_gate(wt, n)
    record(
        "attempt", ticket=n, attempt=attempt, worker_exit=code, gate="PASS" if ok else "FAIL",
        seconds=int(time.monotonic() - started), cost=log_cost(logfile), log=str(logfile),
    )
    return ok, report, logfile


def log_cost(logfile: Path) -> float | None:
    """Sum of `cost_pattern` captures in the worker log; None when unset/absent."""
    if not cfg.cost_pattern:
        return None
    hits = re.findall(cfg.cost_pattern, logfile.read_text(errors="replace"))
    return round(sum(float(h) for h in hits), 4) if hits else None


def process_ticket(
    issue: dict, budget_min: int, dry_run: bool, forced: bool = False
) -> None:
    n, title = issue["number"], issue["title"]
    labels = {label["name"] for label in issue.get("labels", [])}
    wt = FACTORY / f"wt-{n}"
    worker = cfg.worker(labels, wt / ".factory-prompt.md", wt)[0]

    if lock_held(ticket_lock(n)):
        log(f"#{n}: skipped (in flight, lock held on {ticket_lock(n)})")
        return
    if dry_run:
        log(
            f"#{n}: would claim (assign @me), create worktree {wt} on branch agent/{n}, "
            f"run {worker} worker, gate, push, open PR, review"
        )
        return

    deadline = time.monotonic() + budget_min * 60
    lock_fd = ticket_lock(n).open("w")  # held for the life of this pipeline
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"#{n}: skipped (lost lock race)")
        lock_fd.close()
        return

    # Strong re-read before claiming: `issue list` is search-backed and lags
    # label/assignee edits, which re-claimed #16/#17 seconds after escalation.
    if not forced:
        fresh = gh_json(
            [
                "issue",
                "view",
                str(n),
                "--repo",
                REPO,
                "--json",
                "state,labels,assignees",
            ]
        )
        if (
            fresh["state"].upper() != "OPEN"
            or fresh["assignees"]
            or LABEL_AGENT not in {label["name"] for label in fresh["labels"]}
        ):
            log(f"#{n}: skipped (state changed since frontier query)")
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            return

    run(["gh", "issue", "edit", str(n), "--repo", REPO, "--add-assignee", "@me"])
    record("claimed", ticket=n, title=title, labels=sorted(labels))
    wt = ensure_worktree(n)
    LOGS.mkdir(parents=True, exist_ok=True)

    try:
        # Attempts 1..MAX_ATTEMPTS: worker + gate, feeding the failed report back.
        extra, logfile, report = "", None, ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            ok, report, logfile = worker_round(
                n, wt, labels, title, extra, attempt, deadline
            )
            if ok:
                break
            if time.monotonic() > deadline:
                escalate(n, f"wall-clock budget ({budget_min} min) exceeded", logfile)
                return
            extra = f"## Previous gate report (attempt {attempt} failed)\n\n{report}"
        else:
            escalate(
                n, f"gate failed {MAX_ATTEMPTS} times; worktree kept at {wt}", logfile
            )
            return

        if not push_and_pr(wt, n, title, report):
            escalate(n, f"agent/{n} has no commits over main; nothing to PR", logfile)
            return
        verdict, findings = review(wt, n, report)
        pr_comment(n, findings)
        # Review rounds: each REVISE goes back to the worker with the findings,
        # then re-gate, push, re-review. `review_rounds` bounces max.
        for bounce in range(1, cfg.review_rounds + 1):
            if verdict == "APPROVE":
                break
            if time.monotonic() > deadline:
                escalate(
                    n,
                    f"wall-clock budget ({budget_min} min) exceeded before bounce {bounce}",
                    logfile,
                )
                return
            extra = f"## Reviewer findings, round {bounce} (address these)\n\n{findings}"
            ok, report, logfile = worker_round(
                n, wt, labels, title, extra, MAX_ATTEMPTS + bounce, deadline
            )
            if not ok:
                escalate(
                    n, f"gate failed after review bounce {bounce}; worktree kept at {wt}", logfile
                )
                return
            run(["git", "push", "origin", f"agent/{n}"], cwd=wt)
            verdict, findings = review(wt, n, report)
            pr_comment(n, findings)
        if verdict != "APPROVE":
            escalate(n, f"REVISE verdict after {cfg.review_rounds} review round(s)", logfile)
        else:
            approve_pr(n)
            log(f"#{n}: done (approved)")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="AI-factory dispatcher (one pass, stateless)"
    )
    parser.add_argument("--ticket", type=int, help="process exactly this open issue")
    parser.add_argument(
        "--budget-min",
        type=int,
        default=None,
        help="per-ticket wall-clock budget in minutes (default: .factory.toml budget_min)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print planned actions; no side effects"
    )
    args = parser.parse_args(argv)
    configure(config.load())
    if args.budget_min is None:
        args.budget_min = cfg.budget_min

    if args.ticket:
        if not issue_is_open(args.ticket):
            log(f"#{args.ticket}: not open, nothing to do")
            return 1
        issue = gh_json(
            [
                "issue",
                "view",
                str(args.ticket),
                "--repo",
                REPO,
                "--json",
                "number,title,body,labels,assignees",
            ]
        )
        process_ticket(issue, args.budget_min, args.dry_run, forced=True)
        return 0

    land_pass(args.dry_run)
    active = active_ticket_count()
    capacity = MAX_ACTIVE - active
    log(f"active tickets: {active}, capacity: {max(capacity, 0)}")
    if capacity <= 0:
        log("at capacity, nothing to do")
        return 0
    ready = frontier()
    if not ready:
        log("frontier empty, nothing to do")
        return 0
    for issue in ready[:capacity]:
        log(f"claimable: #{issue['number']} {issue['title']}")
    for issue in ready[:capacity]:
        process_ticket(issue, args.budget_min, args.dry_run)
    return 0
