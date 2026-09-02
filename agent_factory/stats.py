"""Print read-only factory ticket metrics from GitHub."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

from agent_factory import config
from agent_factory.config import LABEL_AGENT, LABEL_HUMAN, Config

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
        ["gh", *args, "--repo", REPOSITORY],
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
        "number,title,createdAt,comments",
    )


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
            rows.setdefault(number, make_row(issue(number), state))

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
    args = parser.parse_args(argv)
    configure(config.load())
    rows = collect_rows()
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows)
    return 0
