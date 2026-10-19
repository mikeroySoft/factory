"""Print read-only factory ticket metrics from GitHub and events.jsonl."""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from factory import config, feedback, lifecycle, onboard
from factory.config import LABEL_AGENT, LABEL_HUMAN, USAGE_FIELDS, Config

cfg: Config
REPOSITORY = ""
AGENT_BRANCH = re.compile(r"agent/(\d+)$")
AGENT_SUBJECT = re.compile(r"agent/(\d+):")
REGRESSED_BY = re.compile(r"^[ \t]*Regressed-by:[ \t]*#(\d+)\b", re.MULTILINE)
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? ")
WORKERS = 8  # concurrent per-ticket gh reads
NOT_REPORTED = "n/a (not reported)"


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


def graphql(*, query: str, variables: dict) -> dict:
    """feedback's read(query=..., variables=...) transport over `gh api graphql`."""
    return gh("api", "graphql", "-f", f"query={query}",
              *(arg for key, value in variables.items() for arg in ("-f", f"{key}={value}")))["data"]


@functools.cache
def _login(env: tuple[tuple[str, str], ...]) -> str:
    return onboard.gh_login({**os.environ, **dict(env)})


def dispatcher_login(c: Config) -> str:
    """The dispatcher's authenticated login under `[install].dispatch_env`; "" when unset,
    because the factory then shares the human's account and nothing is attributable."""
    if not c.install.get("dispatch_env"):
        return ""
    return _login(tuple({**c.install["env"], **c.install["dispatch_env"]}.items()))


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


def audit_by_ticket(path: Path, rows: list[dict] | None = None) -> dict[int, list[dict]]:
    """Group committed traces, reusing a caller's coherent journal snapshot."""
    result: dict[int, list[dict]] = {}
    for row in lifecycle.read_events(path) if rows is None else rows:
        if isinstance(row.get("ticket"), int):
            result.setdefault(row["ticket"], []).append(row)
    return result

def priced(event: dict, prices: dict[str, dict]) -> float | None:
    """Dollars for one usage event: its reported cost, else its model's price table entry, else None.

    Cached tokens (or the cache hit rate's share of prompt tokens) bill at `cached` when the entry has it.
    """
    def num(key: str) -> float | None:
        return event[key] if type(event.get(key)) in (int, float) else None

    model = event.get("model")
    price = prices.get(model) if isinstance(model, str) else None
    prompt, completion, cached, rate = map(num, ("prompt_tokens", "completion_tokens", "cached_tokens", "prefix_cache_hit_rate"))
    if num("cost") is not None or price is None or prompt is None and completion is None:
        return num("cost")
    prompt, completion = prompt or 0, completion or 0
    cached = 0 if "cached" not in price else cached if cached is not None else prompt * (rate or 0)
    return ((prompt - cached) * price["prompt"] + cached * price.get("cached", 0)
            + completion * price["completion"]) / 1_000_000


def llm_usage(events: list[dict], prices: dict[str, dict] | None = None) -> dict[str, dict]:
    """Sum tokens and dollars by stage plus a ticket `total`; None = no event reported it.

    Dollars come from a reported cost or, for token-only events, the `[prices]` table.
    """
    usage: dict[str, dict] = {}
    for event in events:
        stage = event.get("stage")
        if event.get("event") != "llm-usage" or stage not in {"triage", "review", "worker"}:
            continue
        values = {**{key: event.get(key) for key in USAGE_FIELDS}, "cost": priced(event, prices or {})}
        for totals in (usage.setdefault(stage, dict.fromkeys(USAGE_FIELDS)),
                       usage.setdefault("total", dict.fromkeys(USAGE_FIELDS))):
            for key, value in values.items():
                if type(value) in (int, float):
                    totals[key] = round((totals[key] or 0) + value, 4)
        rate = event.get("prefix_cache_hit_rate")
        if type(rate) in (int, float):
            usage[stage].setdefault("prefix_cache_hit_rates", []).append(rate)
    return usage


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
    """The cost column only when some worker reported a cost, never a column of n/a."""
    width = max(len("worker"), *(len(row["worker"]) for row in rows))
    costs = any(row["cost"] is not None for row in rows)
    print(f"{'worker':<{width}}  first-gate pass  attempts" + "  cost" * costs)
    for row in rows:
        rate = "n/a" if row["first_pass"] is None else f"{row['first_pass']:.1%}"
        cost = "n/a" if row["cost"] is None else f"${row['cost']:.2f}"
        print(f"{row['worker']:<{width}}  {rate:>15}  {row['attempts']:>8}" + f"  {cost}" * costs)


def brief_metrics(audit: dict[int, list[dict]]) -> list[dict]:
    """First-gate pass rate split by whether attempt 1 carried a brief."""
    outcomes: dict[str, list[bool]] = {"with brief": [], "without brief": []}
    for events in audit.values():
        for event in events:
            if event.get("event") == "attempt" and event.get("attempt") == 1 and event.get("gate") in ("PASS", "FAIL"):
                outcomes["with brief" if event.get("brief") else "without brief"].append(event["gate"] == "PASS")
    return [{"tickets": key, "first_pass": sum(gates) / len(gates) if gates else None, "attempts": len(gates)}
            for key, gates in outcomes.items()]


def print_briefs(rows: list[dict]) -> None:
    print("tickets        first-gate pass  attempts")
    for row in rows:
        rate = "n/a" if row["first_pass"] is None else f"{row['first_pass']:.1%}"
        print(f"{row['tickets']:<13}  {rate:>15}  {row['attempts']:>8}")


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


def human_touch(items: list[dict], audit: list[dict], end: str | None = None,
                login: str = "", cutover: str | None = None) -> dict:
    """Label intervals; removal actor, never comment author, resolves an escalation.

    Actors are attributed by feedback.attribute: Bot or the dispatcher `login` is factory,
    any other account human, anything before `cutover` unattributable.
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
                "resolved_by": feedback.attribute(actor, at, login, cutover),
            })
            opened = None
    if opened:
        minutes += merge_hours(opened, end or datetime.now(UTC).isoformat()) * 60
    escalated = [(e["at"], e.get("code") or "unknown") for e in audit
                 if e.get("event") == "escalate" and e.get("reason") != "manager_failed"]
    claims = sum(e.get("event") == "claimed" for e in audit)
    return {
        "escalation_count": max(len(starts), len(escalated)),
        "escalation_times": [at for at, _ in escalated] if len(escalated) >= len(starts) else starts,
        "escalation_codes": escalated,
        "manager_failures": sum(e.get("event") == "escalate" and e.get("reason") == "manager_failed"
                                for e in audit),
        "resolutions": resolutions,
        "ready_for_human_minutes": round(minutes, 2),
        "requeue_count": max(0, queued - 1, claims - 1),
    }


def human_touch_metrics(rows: list[dict], now: datetime | None = None) -> dict:
    """Trailing seven-day escalation count, by escalation code; human share of attributed resolutions."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=7)
    resolved = [r["resolved_by"] for row in rows for r in row.get("resolutions", [])
                if r["resolved_by"] in {"human", "factory"}]
    by_code = Counter(code for row in rows for at, code in row.get("escalation_codes", [])
                      if since <= datetime.fromisoformat(at) <= now)
    return {
        "escalations_per_week": sum(
            since <= datetime.fromisoformat(at) <= now
            for row in rows for at in row.get("escalation_times", [])
        ),
        "escalations_by_code": dict(by_code.most_common()),
        "human_resolved_pct": round(100 * resolved.count("human") / len(resolved), 1) if resolved else None,
    }


def human_touched_pct(merged: list[dict]) -> float | None:
    """Share of merged agent PRs with any human correction; unattributable PRs left out."""
    keys = feedback.CORRECTION_KEYS.values()
    known = [any(pr[k] for k in keys) for pr in merged if all(pr.get(k) is not None for k in keys)]
    return round(100 * sum(known) / len(known), 1) if known else None


def merge_hours(created_at: str, merged_at: str | None) -> float | None:
    if not merged_at:
        return None
    created = datetime.fromisoformat(created_at)
    merged = datetime.fromisoformat(merged_at)
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


def collect_rows(progress: bool = False) -> list[dict[str, Any]]:
    """One row per agent PR, open escalated/queued issue, and journaled ticket. Per-ticket
    reads (issue, PR comments, timeline) run on WORKERS threads; `progress` counts them on stderr."""
    audit = audit_by_ticket(cfg.factory / "events.jsonl")
    pulls: dict[int, dict] = {}
    for pull_request in gh("pr", "list", "--state", "all", "--limit", "1000",
                           "--json", "number,state,headRefName,mergedAt"):
        if match := AGENT_BRANCH.fullmatch(pull_request["headRefName"]):
            pulls[int(match.group(1))] = pull_request
    listed: dict[int, str | None] = dict.fromkeys(pulls)
    for label, state in ((LABEL_HUMAN, "escalated"), (LABEL_AGENT, "queued")):
        for listed_issue in gh("issue", "list", "--state", "open", "--label", label,
                               "--limit", "1000", "--json", "number"):
            listed.setdefault(listed_issue["number"], state)
    for number in audit:
        listed.setdefault(number, None)

    def read(number: int) -> tuple[dict, dict | None, list[dict]]:
        review = gh("pr", "view", str(pulls[number]["number"]), "--json", "comments") if number in pulls else None
        return issue(number), review, timeline(number)

    fetched = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(read, number): number for number in listed}
        try:
            for done, future in enumerate(as_completed(futures), 1):
                fetched[futures[future]] = future.result()
                if progress:
                    sys.stderr.write(f"\rstats: {done}/{len(futures)} tickets")
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise
    if progress and futures:
        sys.stderr.write("\n")

    rows: dict[int, dict[str, Any]] = {}
    for number, state in listed.items():
        details, review, _ = fetched[number]
        if number in pulls:
            pull_request = pulls[number]
            state = "open PR" if pull_request["state"] == "OPEN" else "merged" if pull_request["mergedAt"] else "escalated"
            rows[number] = make_row(details, state, review_rounds=count_comments(review["comments"], "VERDICT:"),
                                    merged_at=pull_request["mergedAt"])
        elif state:
            rows[number] = make_row(details, state)
        else:
            merged = next((e["at"] for e in reversed(audit[number]) if e.get("event") == "merged"), None)
            rows[number] = make_row(details, "merged" if merged else details["state"].lower(), merged_at=merged)
    pr_by_ticket = {number: pull_request["number"] for number, pull_request in pulls.items()}
    login = dispatcher_login(cfg)
    actions = feedback.correction_reads(graphql, REPOSITORY, pr_by_ticket.values()) if pr_by_ticket else {}
    cutover = feedback.first_action(login, [
        *((item.get("actor"), item.get("createdAt")) for _, _, items in fetched.values() for item in items),
        *((actor, action["at"]) for acts in actions.values() for action in acts for actor in action["actors"]),
    ])
    for number, row in rows.items():
        details, _, items = fetched[number]
        row.update(human_touch(items, audit.get(number, []), details.get("closedAt"), login, cutover))
        if number in pr_by_ticket:
            row.update(feedback.corrections(actions[pr_by_ticket[number]], login, cutover))
        row["llm_usage"] = llm_usage(audit.get(number, []), cfg.prices)

    return [rows[number] for number in sorted(rows)]


def truncate(title: str) -> str:
    return title if len(title) <= 40 else f"{title[:37]}..."


def format_hours(hours: float | None) -> str:
    return "" if hours is None else f"{hours:.1f}"


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cfg.root,
                          text=True, capture_output=True, check=True).stdout


def agent_commits() -> list[tuple[str, int, str]]:
    """(sha, ticket, title) of first-parent `agent/<n>:` commits on origin/<main>, oldest first."""
    commits = []
    for line in git("log", "--first-parent", "--reverse", "--format=%H %s", f"origin/{cfg.main}").splitlines():
        sha, _, subject = line.partition(" ")
        if match := AGENT_SUBJECT.match(subject):
            commits.append((sha, int(match.group(1)), subject[match.end():].strip()))
    return commits


def blamed_lines(fix: str) -> list[tuple[str, str]]:
    """(path, commit subject) per line `fix` modifies or deletes, blamed at its first parent."""
    lines, path, header = [], None, False
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", f"{fix}^", fix)
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("--- "):
            path = None if line == "--- /dev/null" else line[6:].rstrip("\t")  # git appends a TAB to paths with spaces
        elif match := HUNK.match(line):
            header = False
            count = 1 if match.group(2) is None else int(match.group(2))
            if path and count:
                blame = git("blame", "--line-porcelain", "-L", f"{match.group(1)},+{count}", f"{fix}^", "--", path)
                lines += [(path, row[8:]) for row in blame.splitlines() if row.startswith("summary ")]
    return lines


def present(fix: str) -> bool:
    """True when `fix` and its first parent are in this clone, after one `git fetch origin <fix>` if not."""
    def have() -> bool:
        return subprocess.run(["git", "cat-file", "-e", f"{fix}^"], cwd=cfg.root,
                              capture_output=True, check=False).returncode == 0
    if not have():
        subprocess.run(["git", "fetch", "-q", "origin", fix], cwd=cfg.root, capture_output=True, check=False)
    return have()


def escapes() -> list[dict[str, Any]]:
    """Candidate escaped defects: closed `bug` issues whose fix changed lines an earlier agent ticket wrote.
    SZZ-style and noisy: refactors yield false candidates, add-only fixes yield none."""
    bugs = gh("issue", "list", "--state", "closed", "--label", "bug", "--limit", "1000",
              "--json", "number,title,body,closedAt")
    if not bugs:
        return []
    commits = agent_commits()
    titles = {n: title for _, n, title in commits}
    pulls = None
    rows = []
    for bug in sorted(bugs, key=lambda b: b["number"]):
        number = bug["number"]
        fixes = [sha for sha, n, _ in commits if n == number]
        if not fixes:
            if pulls is None:
                pulls = gh("pr", "list", "--state", "merged", "--limit", "1000",
                           "--json", "mergeCommit,closingIssuesReferences")
            fixes = [pr["mergeCommit"]["oid"] for pr in pulls if pr.get("mergeCommit")
                     and any(ref["number"] == number for ref in pr["closingIssuesReferences"])]
        declared = sorted({int(n) for n in REGRESSED_BY.findall(bug.get("body") or "")})
        candidates: dict[int, dict] = {}
        skipped: list[str] = []
        if declared:
            status = "candidates"
            candidates = {n: {"ticket": n, "title": titles.get(n, ""), "lines": None, "paths": []} for n in declared}
        elif not fixes:
            status = "unlinked"
        else:
            skipped = [fix for fix in fixes if not present(fix)]
            for fix in skipped:
                print(f"factory stats: skipped fix {fix[:7]} of bug #{number}: not in this clone or on origin",
                      file=sys.stderr)
            blamed = [line for fix in fixes if fix not in skipped for line in blamed_lines(fix)]
            for path, subject in blamed:
                match = AGENT_SUBJECT.match(subject)
                if match and int(match.group(1)) != number:
                    n = int(match.group(1))
                    candidate = candidates.setdefault(n, {"ticket": n, "title": titles.get(n, ""), "lines": 0, "paths": []})
                    candidate["lines"] += 1
                    if path not in candidate["paths"]:
                        candidate["paths"].append(path)
            status = ("candidates" if candidates else "skipped" if len(skipped) == len(fixes)
                      else "no_candidates" if blamed else "no_blamed_lines")
        rows.append({"bug": number, "title": bug["title"], "closed_at": bug["closedAt"], "fixes": fixes,
                     "source": "declared" if declared else "blame", "status": status, "skipped": skipped,
                     "candidates": [candidates[n] for n in sorted(candidates)]})
    return rows


def print_escapes(rows: list[dict[str, Any]], merged: set[int]) -> None:
    print("bug   fix       source   candidates")
    for row in rows:
        fixes = ",".join(sha[:7] for sha in row["fixes"]) or "-"
        result = ", ".join(
            f"#{c['ticket']}" + ("" if c["lines"] is None else f" ({c['lines']} line{'s' * (c['lines'] != 1)})")
            for c in row["candidates"]) or row["status"]
        print(f"#{row['bug']:<4} {fixes:<8} {row['source']:<8} {result}")
    escaped = len({c["ticket"] for row in rows for c in row["candidates"]} & merged)
    share = f"{100 * escaped / len(merged):.1f}%" if merged else "n/a"
    print(f"\nCandidate escapes: {escaped} of {len(merged)} merged agent tickets ({share})")
    if skipped := [sha[:7] for row in rows for sha in row["skipped"]]:
        print(f"Skipped fixes: {len(skipped)} not in this clone or on origin ({', '.join(skipped)})")


def print_table(rows: list[dict[str, Any]]) -> None:
    """Dollar columns only when some row has a cost; main notes missing prices once."""
    def usage(row: dict, stage: str, key: str) -> str:
        value = row["llm_usage"].get(stage, {}).get(key)
        if isinstance(value, list):
            return ", ".join(map(str, value))
        if key == "cost" and value is not None:
            return f"${value:.4f}"
        if value is None and stage == "worker" and key != "cost":
            return NOT_REPORTED
        return "" if value is None else str(value)

    def tokens(row: dict) -> str:
        total = row["llm_usage"].get("total", {})
        known = [v for v in (total.get("prompt_tokens"), total.get("completion_tokens")) if v is not None]
        return str(sum(known)) if known else ""

    def correction(row: dict, key: str) -> str:
        return "" if key not in row else "n/a" if row[key] is None else str(row[key])

    columns = (
        ("ticket#", lambda row: f"#{row['ticket']}"),
        ("title", lambda row: truncate(row["title"])),
        ("state", lambda row: row["state"]),
        ("attempts", lambda row: str(row["attempts"])),
        ("review rounds", lambda row: str(row["review_rounds"])),
        ("hours", lambda row: format_hours(row["merge_hours"])),
        ("triage prompt", lambda row: usage(row, "triage", "prompt_tokens")),
        ("triage completion", lambda row: usage(row, "triage", "completion_tokens")),
        ("triage cache rates", lambda row: usage(row, "triage", "prefix_cache_hit_rates")),
        ("triage $", lambda row: usage(row, "triage", "cost")),
        ("review prompt", lambda row: usage(row, "review", "prompt_tokens")),
        ("review completion", lambda row: usage(row, "review", "completion_tokens")),
        ("review cache rates", lambda row: usage(row, "review", "prefix_cache_hit_rates")),
        ("review $", lambda row: usage(row, "review", "cost")),
        ("worker prompt", lambda row: usage(row, "worker", "prompt_tokens")),
        ("worker completion", lambda row: usage(row, "worker", "completion_tokens")),
        ("worker cached", lambda row: usage(row, "worker", "cached_tokens")),
        ("worker $", lambda row: usage(row, "worker", "cost")),
        ("total tokens", tokens),
        ("total $", lambda row: usage(row, "total", "cost")),
        ("escalations", lambda row: str(row["escalation_count"])),
        ("manager failures", lambda row: str(row["manager_failures"])),
        ("resolved by (actor)", lambda row: ", ".join(
            f"{r['resolved_by']} ({r['actor'] or '?'})" for r in row["resolutions"])),
        ("human minutes", lambda row: f"{row['ready_for_human_minutes']:.1f}"),
        ("re-queues", lambda row: str(row["requeue_count"])),
        ("human CRs", lambda row: correction(row, "human_change_requests")),
        ("human comments", lambda row: correction(row, "human_comments")),
        ("human commits", lambda row: correction(row, "human_commits")),
    )
    if not any(row["llm_usage"].get("total", {}).get("cost") is not None for row in rows):
        columns = tuple(column for column in columns if not column[0].endswith("$"))
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


def worker_tokens(rows: list[dict[str, Any]]) -> str:
    """Summed worker tokens over the tickets whose worker reported any."""
    reported = [usage for row in rows if (usage := row["llm_usage"].get("worker", {}))
                and (usage["prompt_tokens"] is not None or usage["completion_tokens"] is not None)]
    if not reported:
        return NOT_REPORTED
    prompt, completion = (sum(usage[key] or 0 for usage in reported) for key in ("prompt_tokens", "completion_tokens"))
    return f"{prompt} prompt, {completion} completion ({len(reported)} of {len(rows)} tickets reported)"


def resolve(repo: str | None) -> Config:
    """The factory root the dispatcher runs in: host `[repo."<slug>"].path` for `repo` (else this
    checkout's slug), falling back to this checkout when it is that repository."""
    try:
        local = config.load()
    except config.ConfigError as exc:
        if not repo:
            raise SystemExit(f"factory stats: {exc}; run inside a checkout or pass --repo owner/name") from exc
        local = None
    slug = repo or local.repo
    table = config.host_config().get("repo", {}).get(slug)
    if isinstance(table, dict) and table.get("path"):
        try:
            root = config.load(Path(table["path"]))
        except config.ConfigError as exc:
            raise SystemExit(f'factory stats: [repo."{slug}"].path in {config.host_config_path()}: {exc}') from exc
        if root.repo != slug:
            raise SystemExit(f'factory stats: [repo."{slug}"].path {table["path"]} is a checkout of {root.repo}')
        return root
    if local is None or local.repo != slug:
        raise SystemExit(f'factory stats: no [repo."{slug}"].path in {config.host_config_path()}')
    return local


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory stats", description=__doc__)
    parser.add_argument("--json", action="store_true", help="print metric rows as JSON")
    parser.add_argument("--by-worker", action="store_true", help="split gate pass rate, attempts and known cost by claim label")
    parser.add_argument("--by-brief", action="store_true", help="split first-gate pass rate by tickets with/without a brief")
    parser.add_argument("--escapes", action="store_true",
                        help="candidate escaped defects: closed bugs whose fix changed lines an earlier agent ticket wrote")
    parser.add_argument("--repo", metavar="OWNER/NAME",
                        help='read the root registered as [repo."OWNER/NAME"].path in the host config (default: this checkout\'s repository)')
    parser.add_argument("--quiet", action="store_true", help="no progress on stderr")
    args = parser.parse_args(argv)
    configure(resolve(args.repo))
    journal = cfg.factory / "events.jsonl"
    if not args.escapes and not journal.is_file():
        raise SystemExit(f"factory stats: no journal at {journal}; this is not the dispatcher's root. "
                         f'Set [repo."{cfg.repo}"].path in {config.host_config_path()} or pass --repo owner/name')
    if args.escapes:
        rows = escapes()
    elif args.by_worker or args.by_brief:
        audit = audit_by_ticket(journal)
        rows = worker_metrics(audit, cfg.workers) if args.by_worker else brief_metrics(audit)
    else:
        rows = collect_rows(progress=not args.quiet)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif args.escapes:
        print_escapes(rows, {n for _, n, _ in agent_commits()})
    elif args.by_worker:
        print_workers(rows)
    elif args.by_brief:
        print_briefs(rows)
    else:
        print_table(rows)
        totals = human_touch_metrics(rows)
        human = totals["human_resolved_pct"]
        print(f"\nWorker tokens: {worker_tokens(rows)}")
        print(f"Escalations/week (last 7 days): {totals['escalations_per_week']}")
        if totals["escalations_by_code"]:
            print("  by code: " + ", ".join(f"{c} {k}" for c, k in totals["escalations_by_code"].items()))
        print(f"Human-resolved: {str(human) + '%' if human is not None else 'n/a'} (attributed resolutions)")
        touched = human_touched_pct([row for row in rows if row["state"] == "merged"])
        print(f"Human-touched PRs: {str(touched) + '%' if touched is not None else 'n/a'} (attributed merged agent PRs)")
        if not cfg.prices:
            print("Prices: none configured, so tokens without a reported cost have no dollars "
                  '(host config [prices."<model>"], see README)')
    return 0
