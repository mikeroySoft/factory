"""Print read-only factory ticket metrics from GitHub and events.jsonl."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from factory import config
from factory.config import LABEL_AGENT, LABEL_HUMAN, Config

cfg: Config
REPOSITORY = ""
AGENT_BRANCH = re.compile(r"agent/(\d+)$")


def configure(c: Config) -> None:
    global cfg, REPOSITORY
    cfg = c
    REPOSITORY = cfg.repo


def gh(*args: str) -> Any:
    """Run gh and decode its JSON output."""
    completed = subprocess.run(
        ["gh", *args, *([] if args[0] == "api" else ["--repo", REPOSITORY])],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        print(completed.stderr.strip() or "gh command failed", file=sys.stderr)
        raise SystemExit(completed.returncode)
    return json.loads(completed.stdout)


def count_comments(comments: list[dict[str, Any]], text: str) -> int:
    return sum(text in comment.get("body", "") for comment in comments)


def issue(number: int) -> dict[str, Any]:
    return gh(
        "issue",
        "view",
        str(number),
        "--json",
        "number,title,state,createdAt,closedAt,comments",
    )


def audit_by_ticket(path: Path) -> dict[int, list[dict]]:
    """Read existing traces, tolerating a partially written final record."""
    result: dict[int, list[dict]] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("ticket"), int):
            result.setdefault(row["ticket"], []).append(row)
    return result


def worker_metrics(audit: dict[int, list[dict]], workers: dict[str, list[str]]) -> list[dict]:
    """Claim-label attribution, using Config.worker's first-match precedence.

    First-pass denominators are attempt-1 gate outcomes per claim. All attempts
    (including review bounces) and reported costs count; unclaimed history does not.
    """
    rows = {key: {"worker": key, "first_pass": None, "attempts": 0, "cost": None} for key in workers}
    outcomes: dict[str, list[bool]] = {key: [] for key in workers}
    for events in audit.values():
        worker = None
        for event in events:
            if event.get("event") == "claimed":
                labels = event.get("labels", [])
                worker = next((key for key in workers if key in labels), "default")
            elif event.get("event") == "attempt" and worker is not None:
                row = rows[worker]
                row["attempts"] += 1
                cost = event.get("cost")
                if type(cost) in (int, float):
                    row["cost"] = round((row["cost"] or 0) + cost, 4)
                if event.get("attempt") == 1 and event.get("gate") in ("PASS", "FAIL"):
                    outcomes[worker].append(event["gate"] == "PASS")
    for worker, gates in outcomes.items():
        if gates:
            rows[worker]["first_pass"] = sum(gates) / len(gates)
    return list(rows.values())


def print_workers(rows: list[dict]) -> None:
    width = max(len("worker"), *(len(row["worker"]) for row in rows))
    print(f"{'worker':<{width}}  first-gate pass  attempts  cost")
    for row in rows:
        rate = "n/a" if row["first_pass"] is None else f"{row['first_pass']:.1%}"
        cost = "n/a" if row["cost"] is None else f"${row['cost']:.2f}"
        print(f"{row['worker']:<{width}}  {rate:>15}  {row['attempts']:>8}  {cost}")


def timeline(number: int) -> list[dict]:
    pages = gh("api", f"repos/{REPOSITORY}/issues/{number}/timeline",
               "--paginate", "--slurp")
    return [
        {"__typename": "LabeledEvent" if item["event"] == "labeled" else "UnlabeledEvent",
         "createdAt": item["created_at"], "label": item["label"],
         "actor": {"login": actor.get("login"), "__typename": actor.get("type")}}
        for page in pages for item in page
        if item.get("event") in {"labeled", "unlabeled"}
        for actor in [item.get("actor") or {}]
    ]


def human_touch(items: list[dict], audit: list[dict], end: str | None = None) -> dict:
    """Label intervals; removal actor, never comment author, resolves an escalation.

    Bot actors are factory, User actors human; missing actors remain unknown.
    Shared human credentials cannot distinguish automation from manual activity.
    Trace counts supplement missing timeline history without double-counting it.
    """
    starts = []
    resolutions = []
    opened = None
    minutes = 0.0
    queued = 0
    for item in sorted(items, key=lambda e: e.get("createdAt") or ""):
        kind, at = item.get("__typename"), item.get("createdAt")
        label = (item.get("label") or {}).get("name")
        if not at or (end and at > end):
            continue
        if kind == "LabeledEvent" and label == LABEL_AGENT:
            queued += 1
        if label != LABEL_HUMAN:
            continue
        if kind == "LabeledEvent" and opened is None:
            opened = at
            starts.append(at)
        elif kind == "UnlabeledEvent" and opened is not None:
            minutes += merge_hours(opened, at) * 60
            actor = item.get("actor") or {}
            resolutions.append({
                "actor": actor.get("login"),
                "resolved_by": {"Bot": "factory", "User": "human"}.get(actor.get("__typename"), "unknown"),
            })
            opened = None
    if opened:
        minutes += merge_hours(opened, end or datetime.now(timezone.utc).isoformat()) * 60
    escalated = [e["at"] for e in audit if e.get("event") == "escalate"]
    claims = sum(e.get("event") == "claimed" for e in audit)
    return {
        "escalation_count": max(len(starts), len(escalated)),
        "escalation_times": escalated if len(escalated) >= len(starts) else starts,
        "resolutions": resolutions,
        "ready_for_human_minutes": round(minutes, 2),
        "requeue_count": max(0, queued - 1, claims - 1),
    }


def human_touch_metrics(rows: list[dict], now: datetime | None = None) -> dict:
    """Trailing seven-day escalation count; human share of attributed resolutions."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    resolved = [r["resolved_by"] for row in rows for r in row.get("resolutions", [])
                if r["resolved_by"] in {"human", "factory"}]
    return {
        "escalations_per_week": sum(
            since <= datetime.fromisoformat(at.replace("Z", "+00:00")) <= now
            for row in rows for at in row.get("escalation_times", [])
        ),
        "human_resolved_pct": round(100 * resolved.count("human") / len(resolved), 1) if resolved else None,
    }


def merge_hours(created_at: str, merged_at: str | None) -> float | None:
    if not merged_at:
        return None
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    merged = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    return (merged - created).total_seconds() / 3600


def make_row(
    details: dict[str, Any],
    state: str,
    *,
    review_rounds: int = 0,
    merged_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ticket": details["number"],
        "title": details["title"],
        "state": state,
        "attempts": count_comments(details["comments"], "gate"),
        "review_rounds": review_rounds,
        "merge_hours": merge_hours(details["createdAt"], merged_at),
    }


def collect_rows() -> list[dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    audit = audit_by_ticket(cfg.factory / "events.jsonl")
    details_by_ticket = {}
    pull_requests = gh(
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,state,headRefName,mergedAt",
    )
    for pull_request in pull_requests:
        match = AGENT_BRANCH.fullmatch(pull_request["headRefName"])
        if not match:
            continue
        details = issue(int(match.group(1)))
        details_by_ticket[details["number"]] = details
        review = gh("pr", "view", str(pull_request["number"]), "--json", "comments")
        state = "open PR" if pull_request["state"] == "OPEN" else "merged"
        if not pull_request["mergedAt"] and state != "open PR":
            state = "escalated"
        rows[details["number"]] = make_row(
            details,
            state,
            review_rounds=count_comments(review["comments"], "VERDICT:"),
            merged_at=pull_request["mergedAt"],
        )

    for label, state in (
        (LABEL_HUMAN, "escalated"),
        (LABEL_AGENT, "queued"),
    ):
        issues = gh(
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            label,
            "--limit",
            "1000",
            "--json",
            "number",
        )
        for listed_issue in issues:
            number = listed_issue["number"]
            if number not in rows:
                details_by_ticket[number] = details = issue(number)
                rows[number] = make_row(details, state)

    for number, events in audit.items():
        if number not in rows:
            details_by_ticket[number] = details = issue(number)
            merged = next((e["at"] for e in reversed(events) if e.get("event") == "merged"), None)
            rows[number] = make_row(details, "merged" if merged else details["state"].lower(), merged_at=merged)
    for number, row in rows.items():
        details = details_by_ticket[number]
        row.update(human_touch(timeline(number), audit.get(number, []), details.get("closedAt")))

    return [rows[number] for number in sorted(rows)]


def truncate(title: str) -> str:
    return title if len(title) <= 40 else f"{title[:37]}..."


def format_hours(hours: float | None) -> str:
    return "" if hours is None else f"{hours:.1f}"


def print_table(rows: list[dict[str, Any]]) -> None:
    columns = (
        ("ticket#", lambda row: f"#{row['ticket']}"),
        ("title", lambda row: truncate(row["title"])),
        ("state", lambda row: row["state"]),
        ("attempts", lambda row: str(row["attempts"])),
        ("review rounds", lambda row: str(row["review_rounds"])),
        ("hours", lambda row: format_hours(row["merge_hours"])),
        ("escalations", lambda row: str(row["escalation_count"])),
        ("resolved by (actor)", lambda row: ", ".join(
            f"{r['resolved_by']} ({r['actor'] or '?'})" for r in row["resolutions"])),
        ("human minutes", lambda row: f"{row['ready_for_human_minutes']:.1f}"),
        ("re-queues", lambda row: str(row["requeue_count"])),
    )
    values = [[render(row) for _, render in columns] for row in rows]
    widths = [
        max([len(name), *(len(row[index]) for row in values)])
        for index, (name, _) in enumerate(columns)
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.ljust(width) for cell, width in zip(cells, widths, strict=True)
        ).rstrip()

    print(line([name for name, _ in columns]))
    print(line(["-" * width for width in widths]))
    for row in values:
        print(line(row))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory stats", description=__doc__)
    parser.add_argument("--json", action="store_true", help="print metric rows as JSON")
    parser.add_argument("--by-worker", action="store_true", help="split gate pass rate, attempts and known cost by claim label")
    args = parser.parse_args(argv)
    configure(config.load())
    rows = worker_metrics(audit_by_ticket(cfg.factory / "events.jsonl"), cfg.workers) if args.by_worker else collect_rows()
    if args.json:
        print(json.dumps(rows, indent=2))
    elif args.by_worker:
        print_workers(rows)
    else:
        print_table(rows)
        totals = human_touch_metrics(rows)
        human = totals["human_resolved_pct"]
        print(f"\nEscalations/week (last 7 days): {totals['escalations_per_week']}")
        print(f"Human-resolved: {str(human) + '%' if human is not None else 'n/a'} (attributed resolutions)")
    return 0
