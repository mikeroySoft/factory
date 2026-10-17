#!/usr/bin/env python3
"""Deterministic first-pass classifier for escalation reasons (experiment, issue #163).

Tags every `escalate` row of a journal as requirement / eval / environment / model /
knowledge from its mechanical `code` (recovered from the prose `reason` on rows older
than #241), the escalation packet (gate report, latest review findings, handoff) and
the worker log, then reports agreement with the hand labels in `labels.json`.

    python experiments/escalation-reasons/classify.py .factory/events.jsonl [--table]

Packets and logs are looked up by basename next to the journal
(`<dir>/escalations/<n>.md`, `<dir>/logs/<name>.log`), so paths journaled under an old
checkout still resolve. Rotated `events.jsonl.N.gz` segments are read too.
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from factory import lifecycle

TAGS = ("requirement", "eval", "environment", "model", "knowledge")
LABELS = HERE / "labels.json"
# A dispatcher pass re-fires every ~10 min; identical rows closer than this are one escalation.
COLLAPSE_SECONDS = 3600

# Rows before #241 carry no `code`; the prose comes from the same call sites, so recover it.
CODE_FROM_REASON = (
    (r"edited protected paths|removed or changed existing tests", "protected_paths"),
    (r"wall-clock budget", "budget"),
    (r"^worker stuck", "worker_stuck"),
    (r"gate failed", "gate_failed"),
    (r"PR not published", "pr_unpublished"),
    (r"^pr checks failed", "pr_checks_failed"),
    (r"REVISE verdict|review requested changes", "review_revise"),
    (r"missing approval evidence", "approval_missing"),
    (r"approval evidence, head, or human review state changed", "approval_drift"),
    (r"CI runner unavailable", "ci_runner"),
    (r"CI failed", "ci_failed"),
    (r"differs from remote head|remote head changed", "refresh_head"),
    (r"onto moved main conflicts", "refresh_conflict"),
    (r"nothing ahead of", "refresh_empty"),
    (r"could not remove stale", "gh_failed"),
    (r"^manager_failed$", "manager_failed"),
    (r"manager rounds exhausted", "manager_exhausted"),
    (r"manager requires a human", "manager_human"),
    (r"unresolved feedback item", "pr_feedback"),
    (r"no activity for", "pr_stale"),
    (r"unresolved after .* automated review attempt", "review_intake"),
)

# Codes whose cause is fully determined by the mechanism that raised them.
BY_CODE = {
    "protected_paths": "requirement",
    "ci_failed": "eval", "pr_checks_failed": "eval",
    "refresh_conflict": "environment", "refresh_head": "environment", "refresh_empty": "environment",
    "approval_missing": "environment", "approval_drift": "environment", "manager_failed": "environment",
    "gh_failed": "environment", "ci_runner": "environment", "budget": "environment",
    "worker_stuck": "environment", "upstream_sync": "environment",
}
# Signatures in the worker log or gate report of a broken host, tool or credential.
ENV_SIGNATURE = re.compile(
    r"Authentication failed|not logged in|ModuleNotFoundError|Could not find platform"
    r"|FACTORY_API_KEY|command not found", re.IGNORECASE)
# Reviewer says the fix is an owner decision or authorization, not a code change.
REQUIREMENT_FINDING = re.compile(
    r"is an owner decision|owner should record a scope change|authorization is needed before", re.IGNORECASE)
# Reviewer findings that blame the pipeline or the worker's host.
ENVIRONMENT_FINDING = re.compile(
    r"leaks from the worker's environment|UV_EXCLUDE_NEWER|does not merge onto current", re.IGNORECASE)
# Worker stopped to ask a question instead of committing.
QUESTION = re.compile(r"\bMay I\b|Need approval|requires? (?:a )?(?:human|owner)", re.IGNORECASE)


def section(text: str, name: str, end: str | None = None) -> str:
    stop = r"^## " + re.escape(end) if end else r"^## |\Z"
    m = re.search(r"^## " + re.escape(name) + r"\n(.*?)(?=" + stop + ")", text, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else ""


def required_fixes(review: str) -> str:
    m = re.search(r"(?is)(\*{0,2}required fix.*?)(?=\*{0,2}optional|VERDICT|\Z)", review)
    return m.group(1) if m else review


def code_of(row: dict) -> str:
    if row.get("code"):
        return row["code"]
    reason = row.get("reason", "")
    for pattern, code in CODE_FROM_REASON:
        if re.search(pattern, reason):
            return code
    return "unknown"


def classify(code: str, packet: str, log: str) -> str:
    if code in BY_CODE:
        return BY_CODE[code]
    gate = section(packet, "Last gate report", "Latest review findings")
    if ENV_SIGNATURE.search(log) or ENV_SIGNATURE.search(gate):
        return "environment"
    if code == "gate_failed":
        return "requirement" if "protected-paths: FAIL" in gate or "protected" in log else "model"
    if code == "pr_unpublished":
        return "requirement" if QUESTION.search(log) or QUESTION.search(packet) else "environment"
    if code == "review_revise":
        findings = required_fixes(section(packet, "Latest review findings", "Handoff"))
        if REQUIREMENT_FINDING.search(findings):
            return "requirement"
        if ENVIRONMENT_FINDING.search(findings):
            return "environment"
        return "model"
    return "model"


def escalations(path: Path) -> list[dict]:
    """`escalate` rows oldest first, identical re-fires within COLLAPSE_SECONDS folded."""
    rows = sorted((r for r in lifecycle.read_events(path) if r.get("event") == "escalate"),
                  key=lambda r: r["at"])
    out: list[dict] = []
    for row in rows:
        prev = out[-1] if out else None
        if (prev and prev["ticket"] == row.get("ticket") and prev["reason"] == row.get("reason")
                and (stamp(row["at"]) - stamp(prev["last_at"])).total_seconds() < COLLAPSE_SECONDS):
            prev["repeats"] += 1
            prev["last_at"] = row["at"]
            continue
        out.append({"ticket": row.get("ticket"), "at": row["at"], "last_at": row["at"], "repeats": 1,
                    "reason": row.get("reason", ""), "code": code_of(row),
                    "packet": row.get("packet"), "log": row.get("log")})
    return out


def stamp(at: str) -> datetime:
    return datetime.fromisoformat(at)


def read_sibling(root: Path, kind: str, path: str | None, fallback: str | None = None) -> str:
    name = Path(path).name if path else fallback
    if not name:
        return ""
    try:
        return (root / kind / name).read_text(errors="replace")
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    journal = Path(args[0])
    root = journal.parent
    labels = {(l["ticket"], l["at"]): l for l in json.loads(LABELS.read_text())}
    rows = escalations(journal)
    folded = sum(r["repeats"] - 1 for r in rows)
    agree = agree_any = labelled = 0
    confusion: Counter = Counter()
    by_code: Counter = Counter()
    hand_tags: Counter = Counter()
    table = []
    for row in rows:
        packet = read_sibling(root, "escalations", row["packet"], f"{row['ticket']}.md")
        log = read_sibling(root, "logs", row["log"])
        row["auto"] = classify(row["code"], packet, log)
        label = labels.get((row["ticket"], row["at"]))
        if label is None:
            table.append((row, None))
            continue
        labelled += 1
        hand = label["tag"]
        hand_tags[hand] += 1
        also = label.get("also")
        if row["auto"] == hand:
            agree += 1
        else:
            confusion[(hand, row["auto"])] += 1
            by_code[row["code"]] += 1
        if row["auto"] in (hand, also):
            agree_any += 1
        table.append((row, label))

    if "--table" in argv:
        print("| # | Ticket | When (UTC) | `code` | Hand tag | Classifier | Evidence |")
        print("|---:|---:|---|---|---|---|---|")
        for i, (row, label) in enumerate(table, 1):
            hand = "—" if label is None else label["tag"] + (f" (+{label['also']})" if label.get("also") else "")
            evidence = "" if label is None else label["evidence"]
            reps = f" ×{row['repeats']}" if row["repeats"] > 1 else ""
            print(f"| {i} | #{row['ticket']} | {row['at'][:16]}{reps} | `{row['code']}` | {hand} "
                  f"| {row['auto']} | {evidence} |")
        print()

    print(f"escalations: {len(rows)} ({folded} repeated re-fires folded), labelled: {labelled}, "
          f"unlabelled: {len(rows) - labelled}")
    print("hand tags: " + ", ".join(f"{t}={hand_tags[t]}" for t in TAGS))
    print("classifier tags: " + ", ".join(f"{t}={sum(r['auto'] == t for r in rows)}" for t in TAGS))
    if labelled:
        print(f"agreement (primary tag): {agree}/{labelled} = {100 * agree / labelled:.1f}%")
        print(f"agreement (primary or secondary tag): {agree_any}/{labelled} = {100 * agree_any / labelled:.1f}%")
    for (hand, auto), n in sorted(confusion.items(), key=lambda kv: -kv[1]):
        print(f"  miss: hand={hand} classifier={auto} ×{n}")
    for code, n in sorted(by_code.items(), key=lambda kv: -kv[1]):
        print(f"  misses by code: {code} ×{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
