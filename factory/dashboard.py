"""Passive Factory observations and the packaged outcome-control workspace."""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from factory import (
    __version__,
    briefing,
    codebase,
    config,
    dispatch,
    feedback,
    lifecycle,
    runtime_events,
    runtime_local,
    stats,
)
from factory.config import (
    LABEL_AGENT,
    LABEL_APPROVED,
    LABEL_HUMAN,
    LABEL_INFO,
    LABEL_REVIEW,
    LABEL_TRIAGE,
    LABEL_VIABILITY,
    Config,
)
from factory.onboard import dashboard_port_error

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

FACTORY_LABELS = {
    LABEL_VIABILITY,
    LABEL_TRIAGE,
    LABEL_INFO,
    LABEL_AGENT,
    LABEL_HUMAN,
    "wontfix",
}
AGENT_BRANCH = re.compile(r"agent/(\d+)$")
ATTEMPT_LOG = re.compile(r"(\d+)-attempt-(\d+)\.log$")
GATE_LINE = re.compile(r"^- ([\w-]+): (PASS|FAIL|SKIP)$", re.MULTILINE)
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
  viewer{login}
  repository(owner:$owner,name:$name){
    id
    issues(first:100,orderBy:{field:CREATED_AT,direction:DESC}){
      nodes{
        id number title state url createdAt updatedAt closedAt body
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
        id number title state url headRefName headRefOid createdAt mergedAt closedAt isDraft
        additions deletions changedFiles body reviewDecision
        author{login}
        reviewRequests(first:100){nodes{requestedReviewer{... on User{login}}}}
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
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def github(*, endpoint: str | None = None, query: str | None = None,
           variables: dict | None = None, timeout: float | None = None) -> dict | list:
    """Shared full-snapshot transport; feedback supplies only fixed read queries."""
    if endpoint is not None:
        cmd = ["gh", "api", "--method", "GET", endpoint]
    else:
        if query is None:
            owner, name = REPO.split("/", 1)
            query = GRAPHQL.replace(
                "{UVARS}", ",$uowner:String!,$uname:String!" if UPSTREAM else ""
            ).replace("{UPSTREAM}", GRAPHQL_UPSTREAM if UPSTREAM else "")
            variables = {"owner": owner, "name": name}
            if UPSTREAM:
                variables["uowner"], variables["uname"] = UPSTREAM.split("/", 1)
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in (variables or {}).items():
            if value is not None:
                cmd += ["-F" if type(value) is int else "-f", f"{key}={value}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "gh api graphql failed")
    value = json.loads(proc.stdout)
    if endpoint is not None:
        return value
    if value.get("errors"):
        raise RuntimeError("GitHub query was incomplete")
    return value["data"]


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


def canonical_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except (OSError, RuntimeError, ValueError):
        return str(path.absolute())


def disk_state(n: int, lock_held: bool | None) -> dict:
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
        "lock_held": lock_held,
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


def spend_by_ticket(rows: list[dict]) -> dict[int, dict]:
    """Per-ticket spend from bounded `attempt` rows: seconds, dollars, rounds."""
    spend: dict[int, dict] = {}
    for row in rows:
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


def _timer_last() -> str | None:
    """Return systemd's bounded LastTriggerUSec observation when available."""
    try:
        rows = json.loads(runtime_local._command("schedule", unit=cfg.unit))
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            return None
        matches = [row for row in rows if row.get("unit") == f"{cfg.unit}.timer"]
        if len(matches) != 1:
            return None
        value = matches[0].get("last")
        if type(value) is not int or value in (0, 2**64 - 1) or value < 0:
            return None
        return iso(value / 1e6)
    except (runtime_local._Unavailable, ValueError, TypeError, OverflowError, OSError):
        return None


def dispatcher(runtime: dict) -> dict:
    state, errors = runtime_local.dispatcher(
        cfg, runtime.get("executions", []), runtime.get("history", {}),
    )
    wait = (
        {
            "reason": "scheduled_next_pass",
            "mode": "retry_next_pass",
            "resource": None,
            "details": {"next_at": state["next_at"]},
        }
        if state["timer_active"] is True
        and state["service_active"] is False
        and state["next_at"] is not None
        else None
    )
    previous = next(
        (row for row in reversed(runtime.get("events", []))
         if row.get("kind") == "scheduling_observation"),
        None,
    )
    matching = (
        previous is not None
        and previous.get("timer_active") is state["timer_active"]
        and previous.get("service_active") is state["service_active"]
        and (previous.get("wait") is None) == (wait is None)
    )
    if matching and wait is not None:
        try:
            matching = (
                previous["wait"].get("reason") == wait["reason"]
                and previous["wait"].get("mode") == wait["mode"]
                and datetime.fromisoformat(previous["wait"]["details"]["next_at"])
                == datetime.fromisoformat(wait["details"]["next_at"])
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            matching = False
    schedule = {
        "wait": wait,
        "timer_active": state["timer_active"],
        "service_active": state["service_active"],
        "event_id": previous.get("event_id") if matching else None,
        "at": previous.get("at") if matching else None,
        "observed_at": state["observed_at"],
        "observation": state["observation"],
    }
    runs = journal_runs()
    return {
        "timer": {
            "next": state["next_at"],
            "last": _timer_last(),
            "active": state["timer_active"],
        },
        "service_active": state["service_active"],
        "schedule": schedule,
        "consecutive_failures": consecutive_failures(runs),
        "runs": runs,
        "observation": state["observation"],
        "observed_at": state["observed_at"],
        "capacity": state["capacity"],
        "run_ids": state["run_ids"],
        "errors": errors,
    }


def metrics(tickets: list[dict]) -> dict:
    """Fleet KPIs: fractions except the *_pct metrics (0–100); None when undefined."""
    bounce = lambda a: a["attempt"] > MAX_ATTEMPTS
    rounds = lambda t: sum(1 for a in t["attempts"] if not bounce(a))
    reached = [t for t in tickets if t["pr"]]
    ran = sorted(rounds(t) for t in tickets if t["attempts"])
    frac = lambda n: round(n / len(reached), 3) if reached else None
    return {
        "first_pass": frac(sum(1 for t in reached if rounds(t) == 1)),
        "bounce_rate": frac(sum(1 for t in reached if any(bounce(a) for a in t["attempts"]))),
        "escalations": sum(t["human_touch"]["escalation_count"] for t in tickets),
        "med_attempts": ran[(len(ran) - 1) // 2] if ran else None,
        **stats.human_touch_metrics([t["human_touch"] for t in tickets]),
        "human_touched_pct": stats.human_touched_pct(
            [t["pr"].get("corrections") or {} for t in tickets if t["pr"] and t["pr"].get("merged_at")]),
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


def selected_issue(issue: dict, prs: dict, on_disk: set, by_ticket: dict, audit_tickets=None) -> bool:
    """The shared Factory case-selection policy, independent of transport.

    audit_tickets is membership only (a recorded ticket number, any actor): the
    dashboard feeds it from stats.audit_by_ticket; the evidence CLI feeds the
    bounded evidence.audit_membership read.
    """
    number = issue["number"]
    labels = {lab["name"] for lab in issue.get("labels", {}).get("nodes") or []}
    audit_tickets = audit_tickets or set()
    return bool(labels & FACTORY_LABELS or number in prs or number in on_disk or number in by_ticket or number in audit_tickets)


def build_ticket(
    issue: dict, pr: dict | None, disk: dict, spend: dict | None = None,
    executions: list[dict] | None = None, *, audit: list[dict] | None = None,
    include_worker: bool = True, login: str = "", cutover: str | None = None,
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
        "worker": worker_name(labels) if include_worker else None,
        "stage": stage_of(labels, issue["state"], pr, lock),
        "phase": phase_of(executions or []),
        "pr": pr,
        "spend": spend or {"seconds": 0, "cost": None, "rounds": 0},
        "human_touch": stats.human_touch(
            issue.get("timelineItems", {}).get("nodes") or [], audit or [],
            (pr or {}).get("merged_at") or issue.get("closedAt"), login, cutover,
        ),
        "llm_usage": stats.llm_usage(audit, cfg.prices) if audit else {},
        "events": events,
        "timeline_truncated": issue.get("timelineItems", {}).get("pageInfo", {}).get("hasPreviousPage", False),
        **disk,
    }


def review_queue(prs: list[dict], rows: list[dict], login: str) -> list[dict]:
    """Read-only projection of intake and SHA-bound review/required-CI evidence."""
    history: dict[int, list[dict]] = {}
    for row in rows:
        if row.get("pr") is not None:
            history.setdefault(row["pr"], []).append(row)
    queue = []
    for pr in prs:
        if pr["state"] != "OPEN" or pr["isDraft"]:
            continue
        events = history.get(pr["number"], [])
        review = next((e for e in reversed(events) if e.get("event") == "review-result"), {})
        labels = {label["name"] for label in pr.get("labels", {}).get("nodes") or []}
        requested = any(
            login and (r.get("requestedReviewer") or {}).get("login", "").casefold() == login.casefold()
            for r in pr.get("reviewRequests", {}).get("nodes") or []
        )
        if LABEL_REVIEW not in labels and not requested and not any(
            e.get("event") == "review-result" and e.get("verdict") in {"APPROVE", "REQUEST_CHANGES"}
            for e in events
        ):
            continue
        head = pr["headRefOid"]
        current = next((e for e in reversed(events)
                        if e.get("event") == "review-result" and e.get("head") == head), {})
        readiness = next((e for e in reversed(events)
                          if e.get("event") == "review-readiness" and e.get("head") == head), {})
        escalation = next((e for e in reversed(events) if e.get("event") == "escalate"), {})
        checks = readiness.get("checks") or []
        buckets = {check["bucket"] for check in checks}
        ci = ("fail" if buckets & {"fail", "cancel"} else
              "pass" if buckets == {"pass"} else "pending" if buckets else "unknown")
        state = "review_pending"
        if current.get("verdict") == "REQUEST_CHANGES":
            state = "changes_requested"
        elif current.get("verdict") == "APPROVE":
            state = "ci_failed" if ci == "fail" else "ci_pending"
            if ci == "pass" and readiness.get("state") == "ready":
                state = "ready"
        if escalation:
            state = "escalated"
        queue.append({
            "number": pr["number"], "title": pr["title"], "url": pr["url"],
            "author": (pr.get("author") or {}).get("login"), "head": head,
            "review_head": review.get("head"), "verdict": review.get("verdict"),
            "ci_state": ci, "ci_at": readiness.get("at"), "state": state,
            "reason": escalation.get("reason") or current.get("reason"),
        })
    return sorted(queue, key=lambda pr: pr["number"], reverse=True)


RECENT_EXECUTIONS = 200


def recent_executions(executions: list[dict]) -> list[dict]:
    """Every observed open execution plus the newest observed closed ones."""
    open_ = [e for e in executions if e.get("ended_at") is None]
    closed = sorted((e for e in executions if e.get("ended_at") is not None),
                    key=lambda e: e["ended_at"])[-RECENT_EXECUTIONS:]
    return [{k: v for k, v in e.items() if k != "events"} for e in open_ + closed]


DASHBOARD_EVENT_READ_CAP = runtime_events.BYTE_LIMIT
DASHBOARD_EVENT_ROW_CAP = runtime_events.ROW_LIMIT


def bounded_journal_rows() -> tuple[list[dict], dict]:
    """Read only committed rows from a bounded live-journal window, without flock."""
    path = FACTORY / "events.jsonl"
    unavailable = {
        "source": "events.jsonl",
        "scope": "retained_live_journal",
        "status": "unavailable",
        "complete": False,
        "window_complete": False,
        "truncated": False,
        "rows_returned": 0,
        "byte_limit": DASHBOARD_EVENT_READ_CAP,
        "row_limit": DASHBOARD_EVENT_ROW_CAP,
        "gaps": ["unavailable"],
    }
    try:
        before = path.stat()
        archived = path.with_name(f"{path.name}.1.gz").exists()
        entry = briefing.bounded_file(
            FACTORY, "events.jsonl", DASHBOARD_EVENT_READ_CAP, tail=True,
        )
        after = path.stat()
    except OSError:
        return [], unavailable
    if entry is None:
        return [], unavailable

    text, clipped = entry
    gaps = []
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        gaps.append("changed_during_read")
    if archived:
        gaps.append("rotated_history")
    lines = text.split("\n")
    if lines.pop():
        gaps.append("unterminated_tail")
    if clipped:
        gaps.append("byte_limit")
        if lines:
            lines.pop(0)  # The byte before this bounded tail was not observed.
    rows = []
    for line in lines:
        if not line:
            continue
        if "\ufffd" in line:
            gaps.append("invalid_utf8")
            continue
        if len(line.encode()) > DASHBOARD_EVENT_ROW_CAP:
            gaps.append("row_limit")
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            gaps.append("invalid_json")
            continue
        if not isinstance(row, dict):
            gaps.append("invalid_record")
            continue
        rows.append(row)
    gaps = list(dict.fromkeys(gaps))
    return rows, {
        "source": "events.jsonl",
        "scope": "retained_live_journal",
        "status": "partial" if gaps else "bounded",
        # This may cover the entire current live file, never rotated/lifetime history.
        "complete": False,
        "window_complete": not gaps,
        "truncated": clipped or archived,
        "rows_returned": len(rows),
        "byte_limit": DASHBOARD_EVENT_READ_CAP,
        "row_limit": DASHBOARD_EVENT_ROW_CAP,
        "gaps": gaps,
    }


def snapshot() -> dict:
    errors = []
    issues: list[dict] = []
    prs: dict[int, dict] = {}
    raw_prs: dict[int, dict] = {}
    repo: dict = {}
    gh_upstream = None
    review_prs: list[dict] = []
    viewer = ""
    try:
        data = github()
        repo, gh_upstream = data["repository"], data.get("upstream")
        issues = repo["issues"]["nodes"]
        review_prs = repo["pullRequests"]["nodes"]
        viewer = (data.get("viewer") or {}).get("login", "")
        for pr in repo["pullRequests"]["nodes"]:
            m = AGENT_BRANCH.fullmatch(pr["headRefName"])
            if not m:
                continue
            n = int(m.group(1))
            rec = pr_record(pr)
            # Prefer the merged PR, else the newest, when a branch had several.
            if n not in prs or (rec["merged_at"] and not prs[n]["merged_at"]):
                prs[n] = rec
                raw_prs[n] = pr
    except (RuntimeError, ValueError, KeyError) as exc:
        errors.append(f"github: {exc}")

    on_disk = disk_ticket_numbers()
    locks = FACTORY / "locks"
    ticket_lock_paths = {
        locks / f"{issue['number']}.lock"
        for issue in issues
        if type(issue.get("number")) is int
    }
    ticket_lock_paths.update(
        path for path in locks.glob("*.lock") if path.stem.isdecimal()
    )
    resource_paths = [
        (GPU_LOCK, "host"), (locks / "merge.lock", "repository"),
        *((path, "repository") for path in sorted(ticket_lock_paths, key=str)),
    ]
    runtime = runtime_events.project(dispatch.EVENTS, resource_paths)
    rows, legacy_coverage = bounded_journal_rows()
    audit = stats.audit_by_ticket(FACTORY / "events.jsonl", rows)
    spend = spend_by_ticket(rows)
    dispatcher_state = dispatcher(runtime)
    execution_events: dict[str, list[dict]] = {}
    for event in runtime["events"]:
        if event.get("execution_id"):
            execution_events.setdefault(event["execution_id"], []).append(event)
    executions = [
        {**execution, "events": execution_events.get(execution["execution_id"], [])}
        for execution in runtime["executions"]
    ]
    resources = runtime["resources"]
    lock_states = {
        row["resource"]["lock"]["path"]: {"held": True, "free": False}.get(row["state"])
        for row in resources
    }
    by_ticket: dict[int, list[dict]] = {}
    for execution in executions:
        if execution["ticket"] is not None:
            by_ticket.setdefault(execution["ticket"], []).append(execution)
    provenance, provenance_complete = [], True
    if raw_prs:
        try:
            ledger = briefing.bounded_file(FACTORY, "events.jsonl", briefing.EVENT_READ_CAP, tail=True)
            if ledger is not None:
                text, cut = ledger
                lines = text.split("\n")
                unfinished = lines.pop()
                provenance_complete = not cut and not unfinished
                if cut and lines:
                    lines = lines[1:]
                for line in lines:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            provenance.append(row)
                        else:
                            provenance_complete = False
                    except ValueError:
                        provenance_complete = False
            else:
                provenance_complete = False
        except OSError:
            provenance_complete = False
        # Revision belongs to the imported source checkout, not the observed repository.
        source_root = Path(__file__).resolve().parent
        revision = sh(["git", "rev-parse", "HEAD"], cwd=source_root).strip()
        if (not sh(["git", "ls-files", "--error-unmatch", "--", "feedback.py"], cwd=source_root).strip()
                or sh(["git", "status", "--porcelain", "--", "."], cwd=source_root).strip()):
            revision = None
    # Merged PRs' corrections are read once per dashboard process, not on every refresh.
    global _corrections
    merged = {raw["number"] for raw in raw_prs.values() if raw.get("mergedAt")}
    if missing := sorted(merged - _corrections.keys()):
        try:
            _corrections = {**_corrections, **feedback.correction_reads(github, REPO, missing)}
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            errors.append(f"corrections: {exc}")
    login = stats.dispatcher_login(cfg)
    cutover = feedback.first_action(login, [
        *((item.get("actor"), item.get("createdAt")) for issue in issues
          for item in issue.get("timelineItems", {}).get("nodes") or []),
        *((actor, action["at"]) for number in merged & _corrections.keys()
          for action in _corrections[number] for actor in action["actors"]),
    ])
    for n, raw in raw_prs.items():
        if raw["number"] in merged & _corrections.keys():
            prs[n]["corrections"] = feedback.corrections(_corrections[raw["number"]], login, cutover)
    tickets = []
    for issue in issues:
        n = issue["number"]
        if not selected_issue(issue, prs, on_disk, by_ticket, audit):
            continue
        if n in raw_prs:
            raw = raw_prs[n]
            # Detail reads stay bounded to open work: the snapshot lists up to 100
            # PRs, and closed history must not multiply provider calls per refresh.
            prs[n]["feedback"] = feedback.collect(
                github, repository={"id": repo.get("id"), "slug": REPO,
                                    "host": urlparse(raw["url"]).hostname or "github.com"},
                pr={"id": raw.get("id"), "number": raw["number"], "url": raw["url"],
                    "state": raw.get("state"), "draft": raw.get("isDraft")},
                issue={"id": issue.get("id"), "number": n, "url": issue["url"]},
                events=provenance, provenance_complete=provenance_complete,
                producer_revision=revision, collect_details=raw.get("state") == "OPEN",
            )
        tickets.append(build_ticket(
            issue, prs.get(n),
            disk_state(n, lock_states.get(canonical_path(locks / f"{n}.lock"))),
            spend.get(n), by_ticket.get(n), audit=audit.get(n), login=login, cutover=cutover,
        ))
    tickets.sort(key=lambda t: t["number"], reverse=True)

    bounded_executions = recent_executions(executions)
    retained_count_coverage = {
        "status": legacy_coverage["status"],
        "scope": legacy_coverage["scope"],
        "complete": False,
    }
    runtime_history = {
        **runtime["history"],
        "scope": "retained_live_journal",
        "window_complete": runtime["history"].get("complete") is True,
        "complete": False,
        "errors": runtime["errors"],
    }
    runtime_status = (
        "unavailable"
        if runtime["history"].get("status") in {"missing", "unreadable"}
        else "partial"
        if not runtime_history["window_complete"] or len(bounded_executions) < len(executions)
        else "bounded"
    )

    return {
        "generated_at": iso(time.time()),
        "version": __version__,
        "engine": {"commit": config.engine_commit(), "warning": config.engine_drift(cfg)},
        "repo": REPO,
        "root": str(ROOT),
        "errors": errors,
        "coverage": {
            "legacy": legacy_coverage,
            "runtime": runtime_history,
            "counts": {
                name: dict(retained_count_coverage)
                for name in ("spend", "worker_metrics", "ticket_audit", "review_queue_history")
            } | {
                "executions": {
                    "status": runtime_status,
                    "scope": "retained_live_journal",
                    "complete": False,
                    "observed": len(executions),
                    "returned": len(bounded_executions),
                    "closed_limit": RECENT_EXECUTIONS,
                },
            },
        },
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
            "worker_when": cfg.worker_when,
            "reviewer": " ".join(cfg.reviewer),
            "manager": {
                "command": " ".join(cfg.manager) if cfg.manager else None,
                "rounds": cfg.manager_rounds,
                "review": cfg.manager_review,
                "max_active_cap": cfg.manager_max_active_cap,
                "budget_min_cap": cfg.manager_budget_min_cap,
            },
            "approved_label": FACTORY_APPROVED,
            "triage": {
                "url": LLM_URL,
                "model": LLM_MODEL,
                "online": triage_llm_online(),
            },
            "state_dir": str(FACTORY),
        },
        "gpu_lock_held": lock_states.get(canonical_path(GPU_LOCK)),
        "active": (None if any(t["lock_held"] is None for t in tickets)
                   else sum(t["lock_held"] is True for t in tickets)),
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
        "executions": bounded_executions,
        "resources": resources,
        "tickets": tickets,
        "review_queue": review_queue(review_prs, rows, viewer),
    }


# ---------------------------------------------------------------- server

_corrections: dict[int, list[dict]] = {}  # merged PR number -> feedback.correction_actions




class CodebaseMonitor:
    """Refresh local Git history off the request thread; keep the last good map."""

    def __init__(self, c: Config, ref: str | None = None, limit: int = 80):
        self.config = c
        self.ref = ref
        self.limit = limit
        self.state: dict = {"status": "building", "error": None, "data": None}
        self.stop = threading.Event()

    def refresh(self) -> None:
        previous = self.state["data"]
        try:
            c = self.config
            ref = self.ref or codebase.default_ref(c.root, c.main)
            tip = config.git(c.root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
            if previous and previous["tip"] == tip and previous["ref"] == ref:
                self.state = {"status": "ready", "error": None, "data": previous}
                return
            self.state = {"status": "building", "error": None, "data": previous}
            data = codebase.build_history(c.root, ref, c.repo, c.factory / "codebase", self.limit)
            self.state = {"status": "ready", "error": None, "data": data}
        except (Exception, config.ConfigError) as exc:  # noqa: BLE001 — retain last good snapshot
            self.state = {"status": "error", "error": str(exc), "data": previous}

    def run(self) -> None:
        while not self.stop.is_set():
            self.refresh()
            self.stop.wait(30)


codebase_monitor: CodebaseMonitor | None = None




def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory dashboard", description="Factory outcome-control workspace"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="default: [dashboard].port or 8765"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="bind address (default: 0.0.0.0); APIs require the workspace access key",
    )
    parser.add_argument(
        "--allowed-host", action="append", default=[],
        help="additional trusted HTTP Host name or IP (repeatable)",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print one full snapshot and exit")
    output.add_argument("--runtime-json", action="store_true",
                        help="print one bounded read-only local runtime observation and exit")
    parser.add_argument(
        "--codebase-ref",
        help="local Git ref to visualize (default: origin/<main>, then <main>)",
    )
    parser.add_argument(
        "--codebase-limit",
        type=int,
        default=80,
        help="recent first-parent commits to visualize (default: 80)",
    )
    args = parser.parse_args(argv)
    if args.codebase_limit < 1:
        parser.error("--codebase-limit must be positive")
    if args.runtime_json:
        if any(arg == "--no-open" or arg.split("=")[0] in {
            "--host", "--port", "--codebase-ref", "--codebase-limit", "--allowed-host"
        } for arg in argv):
            parser.error("--runtime-json cannot be combined with server options")

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

    from factory.workspace import Handler, Server, Workspace

    global codebase_monitor
    codebase_monitor = CodebaseMonitor(cfg, args.codebase_ref, args.codebase_limit)
    try:
        workspace = Workspace(cfg, codebase_monitor=codebase_monitor)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"factory dashboard: could not initialize workspace ({type(exc).__name__})",
              file=sys.stderr)
        return 1
    try:
        server = Server((args.host, port), Handler, workspace,
                        allowed_hosts=tuple(args.allowed_host))
    except (OSError, ValueError) as exc:
        workspace.close()
        reason = "is in use" if getattr(exc, "errno", None) == errno.EADDRINUSE else str(exc)
        print(dashboard_port_error(cfg, args.host, port, reason), file=sys.stderr)
        return 1
    port = server.server_address[1]
    if workspace.repository_config(cfg.repo).root.resolve() == cfg.root.resolve():
        threading.Thread(target=codebase_monitor.run, name="factory-codebase", daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(
        f"factory dashboard: listening on {args.host}:{port}  "
        f"(repo {REPO}, state {FACTORY})",
        flush=True,
    )
    if not args.no_open:
        webbrowser.open(url)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        codebase_monitor.stop.set()
        server.server_close()
    return 0
