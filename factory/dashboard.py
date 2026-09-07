"""Local dashboard for the AI factory: activity, progress, history.

    factory dashboard [--host 0.0.0.0] [--port 8765] [--no-open]

Serves dashboard.html (loopback by default) plus a JSON snapshot
assembled from one GitHub GraphQL call (issues, timelines, PRs, CI), the
.factory/ state dir, git worktrees, systemd, and the journal. Human answers
go through POST /api/act, which runs the same gh calls the dispatcher does.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from factory import __version__, briefing, config, dispatch, lifecycle, stats
from factory.config import (
    LABEL_AGENT,
    LABEL_APPROVED,
    LABEL_HUMAN,
    LABEL_INFO,
    LABEL_TRIAGE,
    Config,
)

cfg: Config
FACTORY: Path
FACTORY_APPROVED = LABEL_APPROVED
LOGS: Path
MAX_ACTIVE: int
MAX_ATTEMPTS: int
REPO: str
ROOT: Path
SYNC_LOG: Path
SYNC_TITLE: str
UPSTREAM: str | None  # GitHub slug of the upstream repo; None = sync disabled
GPU_LOCK: Path
LLM_URL: str
LLM_MODEL: str
GATE_CHECKS: list[str]
lock_held = dispatch.lock_held
ticket_lock = dispatch.ticket_lock

TRIAGE = [sys.executable, "-m", "factory", "triage"]
ACT_LABELS = {
    LABEL_TRIAGE,
    LABEL_INFO,
    LABEL_AGENT,
    LABEL_HUMAN,
    "wontfix",
    FACTORY_APPROVED,
}
CLOSE_REASONS = {"completed", "not planned"}

HTML = Path(__file__).with_name("dashboard.html")
ATLAS = Path(__file__).with_name("architecture.html")
BRIEFING_CSS = Path(__file__).with_name("briefing.css")
NEWSREADER = Path(__file__).with_name("fonts") / "Newsreader.ttf"
NEWSREADER_LICENSE = Path(__file__).with_name("fonts") / "Newsreader-OFL.txt"
FACTORY_LABELS = {
    LABEL_TRIAGE,
    LABEL_INFO,
    LABEL_AGENT,
    LABEL_HUMAN,
    "wontfix",
}
SNAPSHOT_TTL = 15  # seconds; /api/snapshot?fresh=1 bypasses
FILE_CAP = 2_000_000  # bytes served per /api/file request
AGENT_BRANCH = re.compile(r"agent/(\d+)$")
ATTEMPT_LOG = re.compile(r"(\d+)-attempt-(\d+)\.log$")
GATE_LINE = re.compile(r"^- ([\w-]+): (PASS|FAIL|SKIP)$", re.M)
VERDICT = re.compile(r"VERDICT:\s*(APPROVE|REVISE)")


def configure(c: Config) -> None:
    global cfg, FACTORY, LOGS, MAX_ACTIVE, MAX_ATTEMPTS, REPO, ROOT, SYNC_LOG
    global SYNC_TITLE, UPSTREAM, GPU_LOCK, LLM_URL, LLM_MODEL, GATE_CHECKS
    cfg = c
    dispatch.configure(c)
    FACTORY = dispatch.FACTORY
    LOGS = dispatch.LOGS
    ROOT = dispatch.ROOT
    REPO = dispatch.REPO
    SYNC_LOG = dispatch.SYNC_LOG
    SYNC_TITLE = dispatch.SYNC_TITLE
    MAX_ACTIVE = c.max_active
    MAX_ATTEMPTS = c.max_attempts
    UPSTREAM = dispatch.UPSTREAM_REPO
    GPU_LOCK = c.lock
    LLM_URL = c.llm_url
    LLM_MODEL = c.llm_model
    GATE_CHECKS = ["conflict-markers", *(k.name for k in c.checks), "leak-scan"]


# ponytail: first 100 issues / 100 PRs, no pagination; add cursors when the
# tracker outgrows that.
GRAPHQL_UPSTREAM = """
  upstream: repository(owner:$uowner,name:$uname){
    defaultBranchRef{name target{... on Commit{
      history(first:20){nodes{oid committedDate messageHeadline url author{name}}}}}}
  }
"""
GRAPHQL = """
query($owner:String!,$name:String!{UVARS}){
{UPSTREAM}
  repository(owner:$owner,name:$name){
    issues(first:100,orderBy:{field:CREATED_AT,direction:DESC}){
      nodes{
        number title state url createdAt updatedAt closedAt body
        labels(first:20){nodes{name color}}
        assignees(first:5){nodes{login}}
        timelineItems(last:100,itemTypes:[LABELED_EVENT,UNLABELED_EVENT,
          ASSIGNED_EVENT,UNASSIGNED_EVENT,ISSUE_COMMENT,CROSS_REFERENCED_EVENT,
          CLOSED_EVENT,REOPENED_EVENT]){
          pageInfo{hasPreviousPage}
          nodes{
            __typename
            ... on LabeledEvent{createdAt label{name} actor{login __typename}}
            ... on UnlabeledEvent{createdAt label{name} actor{login __typename}}
            ... on AssignedEvent{createdAt assignee{... on User{login}}}
            ... on UnassignedEvent{createdAt assignee{... on User{login}}}
            ... on IssueComment{createdAt author{login} body url}
            ... on CrossReferencedEvent{createdAt source{... on PullRequest{number}}}
            ... on ClosedEvent{createdAt actor{login}}
            ... on ReopenedEvent{createdAt actor{login}}
          }
        }
      }
    }
    pullRequests(first:100,orderBy:{field:CREATED_AT,direction:DESC}){
      nodes{
        number title state url headRefName createdAt mergedAt closedAt isDraft
        additions deletions changedFiles body reviewDecision
        labels(first:10){nodes{name}}
        comments(last:30){pageInfo{hasPreviousPage} nodes{createdAt author{login} body url}}
        commits(last:1){nodes{commit{statusCheckRollup{state
          contexts(first:60){nodes{__typename
            ... on CheckRun{name conclusion status}
            ... on StatusContext{context state}}}}}}}
      }
    }
  }
}
"""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else ""


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def file_meta(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {
        "path": str(path.relative_to(FACTORY)),
        "size": st.st_size,
        "mtime": iso(st.st_mtime),
    }


# ---------------------------------------------------------------- GitHub


def github() -> dict:
    owner, name = REPO.split("/", 1)
    query = GRAPHQL.replace(
        "{UVARS}", ",$uowner:String!,$uname:String!" if UPSTREAM else ""
    ).replace("{UPSTREAM}", GRAPHQL_UPSTREAM if UPSTREAM else "")
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
    ]
    if UPSTREAM:
        uowner, uname = UPSTREAM.split("/", 1)
        cmd += ["-F", f"uowner={uowner}", "-F", f"uname={uname}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "gh api graphql failed")
    return json.loads(proc.stdout)["data"]


def pr_checks(pr: dict) -> dict:
    commits = pr.get("commits", {}).get("nodes") or []
    rollup = (
        (commits[0].get("commit") or {}).get("statusCheckRollup") if commits else None
    )
    checks = []
    for ctx in (rollup or {}).get("contexts", {}).get("nodes") or []:
        if ctx["__typename"] == "CheckRun":
            result = ctx["conclusion"] or ctx["status"]
            checks.append({"name": ctx["name"], "result": result})
        else:
            checks.append({"name": ctx["context"], "result": ctx["state"]})
    counts: dict[str, int] = {}
    for check in checks:
        counts[check["result"]] = counts.get(check["result"], 0) + 1
    return {"state": (rollup or {}).get("state"), "counts": counts, "list": checks}


def pr_record(pr: dict) -> dict:
    verdicts = [
        {
            "at": c["createdAt"],
            "verdict": VERDICT.search(c["body"] or "").group(1),
            "body": c["body"],
            "url": c.get("url") or pr["url"],
        }
        for c in pr.get("comments", {}).get("nodes") or []
        if VERDICT.search(c["body"] or "")
    ]
    body = pr.get("body") or ""
    gate_text = (
        body.split("## Gate report", 1)[1].strip() if "## Gate report" in body else ""
    )
    return {
        "number": pr["number"],
        "state": pr["state"],
        "url": pr["url"],
        "draft": pr["isDraft"],
        "created_at": pr["createdAt"],
        "merged_at": pr["mergedAt"],
        "closed_at": pr["closedAt"],
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": pr["changedFiles"],
        "checks": pr_checks(pr),
        "verdicts": verdicts,
        "comments": [
            {"at": c["createdAt"], "body": c.get("body") or "", "url": c.get("url") or pr["url"]}
            for c in pr.get("comments", {}).get("nodes") or []
        ],
        "comments_truncated": pr.get("comments", {}).get("pageInfo", {}).get("hasPreviousPage", False),
        "gate_text": gate_text,
        "labels": [lab["name"] for lab in pr.get("labels", {}).get("nodes") or []],
        "approved": FACTORY_APPROVED
        in {lab["name"] for lab in pr.get("labels", {}).get("nodes") or []},
        "review_decision": pr.get("reviewDecision"),
    }


def issue_events(issue: dict) -> list[dict]:
    events = []
    for item in issue.get("timelineItems", {}).get("nodes") or []:
        kind = item["__typename"]
        at = item.get("createdAt")
        if not at:
            continue
        if kind == "LabeledEvent":
            events.append(
                {"at": at, "kind": "labeled", "detail": item["label"]["name"]}
            )
        elif kind == "UnlabeledEvent":
            events.append(
                {"at": at, "kind": "unlabeled", "detail": item["label"]["name"]}
            )
        elif kind == "AssignedEvent":
            events.append(
                {
                    "at": at,
                    "kind": "assigned",
                    "detail": (item.get("assignee") or {}).get("login", "?"),
                }
            )
        elif kind == "UnassignedEvent":
            events.append(
                {
                    "at": at,
                    "kind": "unassigned",
                    "detail": (item.get("assignee") or {}).get("login", "?"),
                }
            )
        elif kind == "IssueComment":
            body = item.get("body") or ""
            author = (item.get("author") or {}).get("login", "?")
            if body.startswith("Factory dispatcher escalating"):
                reason = body.split("escalating:", 1)[-1].split("\n", 1)[0].strip(" .")
                events.append(
                    {"at": at, "kind": "escalated", "detail": reason, "body": body}
                )
            elif body.startswith("Triage"):
                events.append(
                    {
                        "at": at,
                        "kind": "triaged",
                        "detail": body.split("\n", 1)[0],
                        "body": body,
                    }
                )
            else:
                events.append(
                    {
                        "at": at,
                        "kind": "comment",
                        "detail": f"{author}: {body[:120]}",
                        "body": body,
                    }
                )
        elif kind == "CrossReferencedEvent":
            number = (item.get("source") or {}).get("number")
            if number:
                events.append({"at": at, "kind": "referenced", "detail": f"#{number}"})
        elif kind == "ClosedEvent":
            events.append(
                {
                    "at": at,
                    "kind": "closed",
                    "detail": (item.get("actor") or {}).get("login", "?"),
                }
            )
        elif kind == "ReopenedEvent":
            events.append(
                {
                    "at": at,
                    "kind": "reopened",
                    "detail": (item.get("actor") or {}).get("login", "?"),
                }
            )
        if kind == "IssueComment" and events:
            events[-1]["url"] = item.get("url") or issue.get("url")
    return events


# ---------------------------------------------------------------- disk


def worktree_state(wt: Path) -> dict | None:
    if not wt.is_dir():
        return None
    head = sh(["git", "rev-parse", "--short", "HEAD"], cwd=wt).strip()
    commits = []
    for line in sh(
        ["git", "log", "--format=%h%x1f%s%x1f%cI", f"origin/{cfg.main}..HEAD"], cwd=wt
    ).splitlines():
        sha, subject, at = line.split("\x1f")
        commits.append({"sha": sha, "subject": subject, "at": at})
    return {
        "path": str(wt),
        "head": head,
        "commits": commits,
        "diffstat": sh(
            ["git", "diff", "--shortstat", f"origin/{cfg.main}..HEAD"], cwd=wt
        ).strip(),
        # Same exclusions as commit_leftovers: the prompt and gate dir are expected.
        "dirty": bool(
            sh(
                [
                    "git",
                    "status",
                    "--porcelain",
                    "--",
                    ".",
                    ":(exclude).factory-prompt.md",
                    ":(exclude).factory",
                ],
                cwd=wt,
            ).strip()
        ),
    }


def disk_state(n: int) -> dict:
    wt = FACTORY / f"wt-{n}"
    attempts = []
    for log in sorted(LOGS.glob(f"{n}-attempt-*.log")):
        meta = file_meta(log)
        if meta:
            meta["attempt"] = int(ATTEMPT_LOG.search(log.name).group(2))
            attempts.append(meta)
    attempts.sort(key=lambda a: a["attempt"])

    gate = None
    gate_path = wt / ".factory" / f"gate-report-{n}.md"
    meta = file_meta(gate_path)
    if meta:
        text = read_text(gate_path)
        gate = {**meta, "checks": dict(GATE_LINE.findall(text)), "text": text}

    review = None
    review_path = FACTORY / f"review-{n}.md"
    meta = file_meta(review_path)
    if meta:
        text = read_text(review_path)
        m = VERDICT.search(text)
        review = {**meta, "verdict": m.group(1) if m else None, "text": text}

    return {
        "lock_held": lock_held(ticket_lock(n)),
        "attempts": attempts,
        "gate": gate,
        "review": review,
        "prompt": file_meta(wt / ".factory-prompt.md"),
        "pr_body": file_meta(FACTORY / f"pr-body-{n}.md"),
        "worktree": worktree_state(wt),
    }


def disk_ticket_numbers() -> set[int]:
    numbers = set()
    for wt in FACTORY.glob("wt-*"):
        if wt.name[3:].isdigit():
            numbers.add(int(wt.name[3:]))
    for log in LOGS.glob("*-attempt-*.log"):
        numbers.add(int(ATTEMPT_LOG.search(log.name).group(1)))
    return numbers


def spend_by_ticket() -> dict[int, dict]:
    """Per-ticket spend from events.jsonl `attempt` rows: seconds, dollars, rounds."""
    spend: dict[int, dict] = {}
    for row in lifecycle.read_events(dispatch.EVENTS):
        if (
            row.get("event") != "attempt"
            or type(row.get("ticket")) is not int
            or any(row.get(key) is not None and type(row[key]) not in (int, float) for key in ("seconds", "cost"))
        ):
            continue
        s = spend.setdefault(row["ticket"], {"seconds": 0, "cost": None, "rounds": 0})
        s["seconds"] += row.get("seconds") or 0
        s["rounds"] += 1
        if row.get("cost") is not None:
            s["cost"] = round((s["cost"] or 0) + row["cost"], 4)
    return spend


# ---------------------------------------------------------------- upstream


def upstream_state(gh: dict | None, issues: list[dict]) -> dict:
    """Upstream main vs fork main: GitHub's view of upstream, local refs for
    containment (the dispatcher fetches both every pass)."""
    ref = (gh or {}).get("defaultBranchRef") or {}
    nodes = ((ref.get("target") or {}).get("history") or {}).get("nodes") or []
    commits = []
    for c in nodes:
        synced = (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", c["oid"], f"origin/{cfg.main}"],
                cwd=ROOT,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        commits.append(
            {
                "sha": c["oid"],
                "at": c["committedDate"],
                "subject": c["messageHeadline"],
                "author": (c.get("author") or {}).get("name"),
                "url": c["url"],
                "synced": synced,
            }
        )
    counts = (
        sh(
            [
                "git",
                "rev-list",
                "--left-right",
                "--count",
                f"origin/{cfg.main}...{cfg.upstream}/{cfg.main}",
            ],
            cwd=ROOT,
        ).split()
        if cfg.upstream
        else []
    )
    syncs = []
    for line in read_text(SYNC_LOG).splitlines():
        with contextlib.suppress(ValueError):
            syncs.append(json.loads(line))
    blocker = next(
        (
            {"number": i["number"], "url": i["url"], "title": i["title"]}
            for i in issues
            if i["state"] == "OPEN" and i["title"].startswith(SYNC_TITLE)
        ),
        None,
    )
    return {
        "repo": UPSTREAM,
        "branch": ref.get("name"),
        "fork_main": sh(["git", "rev-parse", f"origin/{cfg.main}"], cwd=ROOT).strip(),
        "ahead": int(counts[0]) if len(counts) == 2 else None,
        "behind": int(counts[1]) if len(counts) == 2 else None,
        "commits": commits,
        "syncs": syncs[-50:],
        "blocker": blocker,
    }


# ---------------------------------------------------------------- systemd


def journal_runs() -> list[dict]:
    out = sh(["journalctl", "--user", "-u", f"{cfg.unit}.service", "-o", "json", "-n", "3000", "--no-pager"])
    return parse_journal(out)[-100:]


def parse_journal(out: str) -> list[dict]:
    """One run per systemd Starting…Finished/Failed bracket; app lines in between."""
    runs: list[dict] = []
    cur = None
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        at = int(entry["__REALTIME_TIMESTAMP"]) / 1e6
        if entry.get("SYSLOG_IDENTIFIER") == "systemd":
            if msg.startswith("Starting"):
                cur = {
                    "started": iso(at),
                    "finished": None,
                    "result": "running",
                    "lines": [],
                }
                runs.append(cur)
            elif cur and msg.startswith("Finished"):
                cur["finished"], cur["result"], cur = iso(at), "done", None
            elif cur and (msg.startswith("Failed") or "Failed with result" in msg):
                cur["finished"], cur["result"], cur = iso(at), "failed", None
        elif cur is not None:
            cur["lines"].append(msg)
    return runs


def consecutive_failures(runs: list[dict]) -> int:
    """Trailing unit runs that failed (a pass still running is skipped)."""
    n = 0
    for run in reversed(runs):
        if run["result"] == "running":
            continue
        if run["result"] != "failed":
            break
        n += 1
    return n


def dispatcher() -> dict:
    def active(unit: str) -> bool | None:
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", unit],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            return None
        status = proc.stdout.strip()
        if status == "active" and proc.returncode == 0:
            return True
        if status in {"inactive", "failed"} and proc.returncode == 3:
            return False
        return None

    timer = {"next": None, "last": None}
    try:
        raw = sh(
            ["systemctl", "--user", "list-timers", f"{cfg.unit}.timer", "--output=json"]
        )
        rows = json.loads(raw) if raw else []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            timer.update(
                next=iso(rows[0]["next"] / 1e6) if rows[0].get("next") else None,
                last=iso(rows[0]["last"] / 1e6) if rows[0].get("last") else None,
            )
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError):
        pass
    timer["active"] = active(f"{cfg.unit}.timer")
    service_active = active(f"{cfg.unit}.service")
    schedule = lifecycle.observe_schedule(
        dispatch.EVENTS, next_at=timer["next"], timer_active=timer["active"],
        service_active=service_active, observed_at=lifecycle._now(),
    )
    runs = journal_runs()
    return {
        "timer": timer,
        "service_active": service_active,
        "schedule": schedule,
        "consecutive_failures": consecutive_failures(runs),
        "runs": runs,
    }


def metrics(tickets: list[dict]) -> dict:
    """Fleet KPIs: fractions except human_resolved_pct (0–100); None when undefined."""
    bounce = lambda a: a["attempt"] > MAX_ATTEMPTS  # noqa: E731
    rounds = lambda t: sum(1 for a in t["attempts"] if not bounce(a))  # noqa: E731
    reached = [t for t in tickets if t["pr"]]
    ran = sorted(rounds(t) for t in tickets if t["attempts"])
    frac = lambda n: round(n / len(reached), 3) if reached else None  # noqa: E731
    return {
        "first_pass": frac(sum(1 for t in reached if rounds(t) == 1)),
        "bounce_rate": frac(sum(1 for t in reached if any(bounce(a) for a in t["attempts"]))),
        "escalations": sum(t["human_touch"]["escalation_count"] for t in tickets),
        "med_attempts": ran[(len(ran) - 1) // 2] if ran else None,
        **stats.human_touch_metrics([t["human_touch"] for t in tickets]),
    }


def triage_llm_online() -> bool:
    base = LLM_URL.rsplit("/chat/completions", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=1.5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---------------------------------------------------------------- tickets


def stage_of(labels: set[str], state: str, pr: dict | None, lock: bool) -> str:
    if pr and pr["merged_at"]:
        return "merged"
    if lock:
        return "in-flight"
    if LABEL_HUMAN in labels:
        return "escalated"
    if pr and pr["state"] == "OPEN":
        return "pr-open"
    if state != "OPEN":
        return "closed"
    if "wontfix" in labels:
        return "wontfix"
    if LABEL_INFO in labels:
        return "needs-info"
    if LABEL_AGENT in labels:
        return "queued"
    if LABEL_TRIAGE in labels:
        return "triage"
    return "other"


def phase_of(executions: list[dict]) -> dict | None:
    """Project one authoritative stage, never artifacts or ambiguous activity."""
    by_id = {e["execution_id"]: e for e in executions}
    unresolved = [e for e in executions if e["state"] in ("active", "unknown") and e.get("ended_at") is None]
    ancestors = set()
    for execution in unresolved:
        parent = execution["parent_execution_id"]
        while parent and parent not in ancestors:
            ancestors.add(parent)
            parent = by_id.get(parent, {}).get("parent_execution_id")
    leaves = [e for e in unresolved if e["execution_id"] not in ancestors]
    if len(leaves) != 1 or leaves[0]["state"] != "active" or leaves[0].get("wait"):
        return None
    execution = leaves[0]
    return {
        "at": execution["entered_at"],
        "artifact": execution["stage"],
        "attempt": execution["attempt"],
        "execution_id": execution["execution_id"],
    }


def worker_name(labels: set[str]) -> str:
    """Program name of the worker that owns these labels (same first-match
    rule as Config.worker)."""
    argv = next((cfg.workers[k] for k in cfg.workers if k in labels), cfg.workers["default"])
    return Path(argv[0]).name


def build_ticket(
    issue: dict, pr: dict | None, disk: dict, spend: dict | None = None,
    executions: list[dict] | None = None, *, audit: list[dict] | None = None,
) -> dict:
    labels = {lab["name"] for lab in issue.get("labels", {}).get("nodes") or []}
    events = issue_events(issue)
    if pr:
        events.append(
            {"at": pr["created_at"], "kind": "pr-opened", "detail": f"#{pr['number']}"}
        )
        for v in pr["verdicts"]:
            events.append(
                {
                    "at": v["at"],
                    "kind": "verdict",
                    "detail": v["verdict"],
                    "body": v["body"],
                    "url": v.get("url") or pr["url"],
                }
            )
        if pr["merged_at"]:
            events.append(
                {"at": pr["merged_at"], "kind": "merged", "detail": f"#{pr['number']}"}
            )
        elif pr["closed_at"]:
            events.append(
                {
                    "at": pr["closed_at"],
                    "kind": "pr-closed",
                    "detail": f"#{pr['number']}",
                }
            )
    for a in disk["attempts"]:
        events.append(
            {
                "at": a["mtime"],
                "kind": "attempt",
                "detail": f"attempt {a['attempt']} log",
                "path": a["path"],
            }
        )
    if disk["gate"]:
        failed = [k for k, v in disk["gate"]["checks"].items() if v == "FAIL"]
        events.append(
            {
                "at": disk["gate"]["mtime"],
                "kind": "gate",
                "detail": "FAIL: " + ", ".join(failed) if failed else "PASS",
                "path": disk["gate"]["path"],
            }
        )
    events.sort(key=lambda e: e["at"])
    lock = disk["lock_held"]
    return {
        "number": issue["number"],
        "title": issue["title"],
        "state": issue["state"],
        "url": issue["url"],
        "body": issue.get("body") or "",
        "labels": sorted(labels),
        "assignees": [
            a["login"] for a in issue.get("assignees", {}).get("nodes") or []
        ],
        "created_at": issue["createdAt"],
        "updated_at": issue["updatedAt"],
        "closed_at": issue["closedAt"],
        "worker": worker_name(labels),
        "stage": stage_of(labels, issue["state"], pr, lock),
        "phase": phase_of(executions or []),
        "pr": pr,
        "spend": spend or {"seconds": 0, "cost": None, "rounds": 0},
        "human_touch": stats.human_touch(
            issue.get("timelineItems", {}).get("nodes") or [], audit or [],
            (pr or {}).get("merged_at") or issue.get("closedAt"),
        ),
        "events": events,
        "timeline_truncated": issue.get("timelineItems", {}).get("pageInfo", {}).get("hasPreviousPage", False),
        **disk,
    }


def snapshot() -> dict:
    errors = []
    issues: list[dict] = []
    prs: dict[int, dict] = {}
    gh_upstream = None
    try:
        data = github()
        repo, gh_upstream = data["repository"], data.get("upstream")
        issues = repo["issues"]["nodes"]
        for pr in repo["pullRequests"]["nodes"]:
            m = AGENT_BRANCH.fullmatch(pr["headRefName"])
            if not m:
                continue
            n = int(m.group(1))
            rec = pr_record(pr)
            # Prefer the merged PR, else the newest, when a branch had several.
            if n not in prs or (rec["merged_at"] and not prs[n]["merged_at"]):
                prs[n] = rec
    except (RuntimeError, ValueError, KeyError) as exc:
        errors.append(f"github: {exc}")

    on_disk = disk_ticket_numbers()
    spend = spend_by_ticket()
    audit = stats.audit_by_ticket(FACTORY / "events.jsonl")
    tickets = []
    dispatcher_state = dispatcher()
    executions = lifecycle.observe(dispatch.EVENTS)
    by_ticket: dict[int, list[dict]] = {}
    for execution in executions:
        if execution["ticket"] is not None:
            by_ticket.setdefault(execution["ticket"], []).append(execution)
    for issue in issues:
        n = issue["number"]
        labels = {lab["name"] for lab in issue.get("labels", {}).get("nodes") or []}
        if not (labels & FACTORY_LABELS or n in prs or n in on_disk or n in audit or n in by_ticket):
            continue
        tickets.append(build_ticket(
            issue, prs.get(n), disk_state(n), spend.get(n), by_ticket.get(n), audit=audit.get(n),
        ))
    tickets.sort(key=lambda t: t["number"], reverse=True)

    return {
        "generated_at": iso(time.time()),
        "version": __version__,
        "repo": REPO,
        "root": str(ROOT),
        "errors": errors,
        "config": {
            "name": cfg.name,
            "unit": cfg.unit,
            "upstream": cfg.upstream,
            "main": cfg.main,
            "max_active": MAX_ACTIVE,
            "max_attempts": MAX_ATTEMPTS,
            "budget_min": cfg.budget_min,
            "review_rounds": cfg.review_rounds,
            "cost_pattern": cfg.cost_pattern,
            "timer_interval": cfg.install["every"],
            "gate_checks": GATE_CHECKS,
            "exclusive_checks": [c.name for c in cfg.checks if c.exclusive],
            "check_timeout": cfg.check_timeout,
            "leak_pattern": cfg.leak_pattern,
            "gpu_lock": str(cfg.lock),
            "workers": {label: " ".join(argv) for label, argv in cfg.workers.items()},
            "reviewer": " ".join(cfg.reviewer),
            "manager": {
                "command": " ".join(cfg.manager) if cfg.manager else None,
                "rounds": cfg.manager_rounds,
                "review": cfg.manager_review,
            },
            "approved_label": FACTORY_APPROVED,
            "triage": {
                "url": LLM_URL,
                "model": LLM_MODEL,
                "online": triage_llm_online(),
            },
            "state_dir": str(FACTORY),
        },
        "gpu_lock_held": lock_held(GPU_LOCK),
        "active": sum(1 for t in tickets if t["lock_held"]),
        "spend": {
            "seconds": sum(s["seconds"] for s in spend.values()),
            "cost": round(sum(s["cost"] for s in spend.values() if s["cost"] is not None), 2)
            if any(s["cost"] is not None for s in spend.values()) else None,
            "tickets": len(spend),
        },
        "dispatcher": dispatcher_state,
        "upstream": upstream_state(gh_upstream, issues),
        "metrics": metrics(tickets),
        "workers": stats.worker_metrics(audit, cfg.workers),
        "executions": executions,
        "resources": lifecycle.resources(
            dispatch.EVENTS,
            paths=[(GPU_LOCK, "host"), (FACTORY / "locks" / "merge.lock", "repository"),
                   *((path, "repository") for path in (FACTORY / "locks").glob("*.lock")
                     if path.stem.isdecimal())],
        ),
        "tickets": tickets,
    }


# ---------------------------------------------------------------- server

_cache: dict = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()


def cached_snapshot(fresh: bool) -> dict:
    with _cache_lock:
        if fresh or _cache["data"] is None or time.time() - _cache["at"] > SNAPSHOT_TTL:
            _cache["data"] = snapshot()
            _cache["at"] = time.time()
        return _cache["data"]


# ---------------------------------------------------------------- actions


def step(steps: list[dict], cmd: list[str], cwd: Path | None = None) -> bool:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=120)
        ok, output = proc.returncode == 0, (proc.stdout + proc.stderr).strip()[-4000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, output = False, str(exc)
    steps.append({"cmd": " ".join(cmd[:4]) + (" …" if len(cmd) > 4 else ""), "ok": ok, "output": output})
    return ok


def labels_arg(req: dict, key: str) -> list[str]:
    labels = req.get(key, [])
    if not isinstance(labels, list) or not all(isinstance(label, str) and label in ACT_LABELS for label in labels):
        raise ValueError(f"{key}: labels must be a subset of {sorted(ACT_LABELS)}")
    return labels


def act(req: dict) -> dict:
    """Apply exactly one human action, stopping at the first failed step."""
    if not isinstance(req, dict):
        raise ValueError("request must be a JSON object")
    op, number = req.get("op"), req.get("number")
    if type(number) is not int or not 0 < number < 2**31:
        raise ValueError("number: positive int required")
    comment = req.get("comment", "")
    if not isinstance(comment, str) or len(comment) > 20000 or "\x00" in comment:
        raise ValueError("comment: string ≤ 20000 chars, without NUL")
    commands: list[list[str]] = []
    if op in ("issue", "pr"):
        add, remove = labels_arg(req, "add"), labels_arg(req, "remove")
        close = req.get("close")
        if close is not None and (op != "issue" or not isinstance(close, str) or close not in CLOSE_REASONS):
            raise ValueError(f"close: issue only, one of {sorted(CLOSE_REASONS)}")
        for flag in ("assign", "unassign"):
            if flag in req and type(req[flag]) is not bool:
                raise ValueError(f"{flag}: boolean required")
        if comment.strip():
            commands.append(["gh", op, "comment", str(number), "--repo", REPO, "--body", comment])
        edit = []
        for label in add:
            edit += ["--add-label", label]
        for label in remove:
            edit += ["--remove-label", label]
        if op == "issue" and req.get("unassign"):
            edit += ["--remove-assignee", "@me"]
        if op == "issue" and req.get("assign"):
            edit += ["--add-assignee", "@me"]
        if edit:
            commands.append(["gh", op, "edit", str(number), "--repo", REPO, *edit])
        if close:
            commands.append(["gh", "issue", "close", str(number), "--repo", REPO, "--reason", close])
        if not commands:
            raise ValueError("action has no comment, label, assignment or close change")
    elif op == "triage":
        commands.append([*TRIAGE, "--issue", str(number)])
    elif op == "cleanup":
        if lock_held(ticket_lock(number)):
            raise ValueError(f"#{number} is in flight; not removing its worktree")
    else:
        raise ValueError(f"op: unknown {op!r}")

    decision_id = uuid4().hex
    fields = {"decision_id": decision_id, "op": op, "request": req}
    fields["pr" if op == "pr" else "ticket"] = number
    # Record intent before touching GitHub. An unwritable audit trail fails closed.
    dispatch.record("human-decision", status="started", **fields)
    steps: list[dict] = []
    try:
        if op == "cleanup":
            wt = FACTORY / f"wt-{number}"
            if not wt.is_dir() or step(steps, ["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT):
                branch = subprocess.run(["git", "branch", "--list", f"agent/{number}"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
                if not branch.stdout.strip() or step(steps, ["git", "branch", "-D", f"agent/{number}"], cwd=ROOT):
                    ticket_lock(number).unlink(missing_ok=True)
                    steps.append({"cmd": f"remove ticket lock #{number}", "ok": True, "output": ""})
        else:
            for command in commands:
                if not step(steps, command, cwd=ROOT if op == "triage" else None):
                    break
    except Exception as exc:
        steps.append({"cmd": str(op), "ok": False, "output": str(exc)})
    status = "success" if steps and all(s["ok"] for s in steps) else "partial" if any(s["ok"] for s in steps) or op == "cleanup" else "failure"
    result = {"ok": status == "success", "status": status, "decision_id": decision_id, "steps": steps}
    if not result["ok"]:
        result["error"] = f"Decision {status}: " + (steps[-1]["output"] or "command failed; inspect the recorded steps before retrying")
    try:
        dispatch.record("human-decision", status=status, steps=steps, **fields)
    except OSError as exc:
        result.update(ok=False, status="partial" if any(s["ok"] for s in steps) else "failure", error=f"Could not record final decision outcome: {exc}. Inspect the completed steps before retrying.")
    with _cache_lock:
        _cache["at"] = 0.0  # next snapshot re-reads GitHub
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            self._send(200, "text/html; charset=utf-8", HTML.read_bytes())
        elif url.path == "/theme.css":
            self._send(200, "text/css", cfg.dashboard_theme.read_bytes() if cfg.dashboard_theme else b"")
        elif url.path == "/briefing.css":
            self._send(200, "text/css; charset=utf-8", BRIEFING_CSS.read_bytes())
        elif url.path == "/fonts/Newsreader.ttf":
            self._send(200, "font/ttf", NEWSREADER.read_bytes())
        elif url.path == "/fonts/Newsreader-OFL.txt":
            self._send(200, "text/plain; charset=utf-8", NEWSREADER_LICENSE.read_bytes())
        elif url.path == "/atlas":
            self._send(200, "text/html; charset=utf-8", ATLAS.read_bytes())
        elif url.path == "/api/snapshot":
            data = cached_snapshot("fresh" in query)
            self._send(200, "application/json", json.dumps(data).encode())
        elif url.path == "/api/file":
            self._file(query.get("path", [""])[0])
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route not in ("/api/act", "/api/briefing", "/api/ask"):
            self._send(404, "application/json", b'{"ok":false,"error":"Unknown API route"}')
            return
        # A custom header forces a CORS preflight we never answer. Check Origin
        # as well so a cross-origin request cannot drive authenticated actions.
        origin = self.headers.get("Origin")
        if self.headers.get("X-Factory-Act") != "1" or (origin and urlparse(origin).netloc != self.headers.get("Host")):
            self._send(403, "application/json", b'{"ok":false,"error":"Same-origin X-Factory-Act: 1 required"}')
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Transfer-Encoding is not supported; send Content-Length")
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= briefing.REQUEST_CAP:
                raise ValueError(f"request body must be 1–{briefing.REQUEST_CAP} bytes")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type: application/json required")
            self.connection.settimeout(15)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete request body")
            req = json.loads(body)
            if route == "/api/act":
                result = act(req)
            else:
                asking = route == "/api/ask"
                briefing.validate_request(req, asking)
                result = briefing.respond(cfg, cached_snapshot(False), req, asking)
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            result = {"ok": False, "error": str(exc)}
            if route == "/api/act":
                result["steps"] = []
        except Exception as exc:
            result = {"ok": False, "error": f"Dashboard request failed ({type(exc).__name__}): {exc}"}
        self._send(200, "application/json", json.dumps(result).encode())

    def _file(self, rel: str) -> None:
        root = FACTORY.resolve()
        path = (root / rel).resolve()
        if not rel or not path.is_relative_to(root) or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        if len(data) > FILE_CAP:
            data = b"[... truncated ...]\n" + data[-FILE_CAP:]
        self._send(200, "text/plain; charset=utf-8", data)

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory dashboard", description="Local AI-factory dashboard"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="default: [dashboard].port or 8765"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; 0.0.0.0 exposes the dashboard AND /api/act "
        "(which mutates GitHub with your gh credentials) to the whole network",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print one full snapshot and exit")
    output.add_argument("--runtime-json", action="store_true",
                        help="print one bounded read-only local runtime observation and exit")
    args = parser.parse_args(argv)
    if args.runtime_json:
        if any(arg == "--no-open" or arg.split("=")[0] in {"--host", "--port"} for arg in argv):
            parser.error("--runtime-json cannot be combined with server options")
        from factory import runtime_events, runtime_local

        runtime_cfg = runtime_local.load()
        data = runtime_events.project(
            runtime_cfg.factory / "events.jsonl",
            [(runtime_cfg.lock, "host"), (runtime_cfg.factory / "locks" / "merge.lock", "repository")],
        )
        dispatcher_data, errors = runtime_local.dispatcher(
            runtime_cfg, data["executions"], data["history"],
        )
        transition = next((row for row in reversed(data["events"])
                           if row["kind"] in {"enter", "exit"}), None)
        dispatcher_data["latest_transition"] = (
            {key: transition[key] for key in ("event_id", "at", "execution_id", "kind")}
            if transition else None
        )
        data["errors"] = (data["errors"] + errors)[:32]
        print(json.dumps({
            "schema_version": 1, "generated_at": lifecycle._now(), "repo": runtime_cfg.repo,
            "dispatcher": dispatcher_data, **data,
        }, separators=(",", ":"), allow_nan=False))
        return 0
    configure(config.load())
    port = cfg.dashboard_port if args.port is None else args.port

    if args.json:
        print(json.dumps(snapshot(), indent=2))
        return 0

    server = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(
        f"factory dashboard: listening on {args.host}:{port}  "
        f"(repo {REPO}, state {FACTORY})",
        flush=True,
    )
    if not args.no_open:
        webbrowser.open(url)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0
