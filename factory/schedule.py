"""Accepted-priority admission order for the dispatcher's claimable frontier (#102).

A ticket's priority is read from one place: the pinned Plan baseline in its own body,
the accepted scope binding that admission re-checks (`binding.baseline`). The tier is
the first `NOW`/`NEXT`/`THEN`/`LATER` Plan line naming the ticket. Triage briefs,
manager recommendations and retained experiment results never rank anything; an
experiment's outcome reaches production policy only through an ordinary reviewed PR.
"""
from __future__ import annotations

import re

from factory import binding, config

PRIORITY_TIERS = ("NOW", "NEXT", "THEN", "LATER")
# Consecutive explicit-priority admissions before the oldest unknown-priority ticket is admitted.
FAIRNESS_STREAK = 3
# `experiment` tickets active at once; production delivery has the remaining capacity.
MAX_EXPERIMENTS = 1


def priorities(plan_text: str | None) -> dict[int, str]:
    """Accepted priority tier per ticket: first `NOW`/`NEXT`/`THEN`/`LATER` Plan line naming `#N`."""
    tiers: dict[int, str] = {}
    for line in (plan_text or "").splitlines():
        match = re.match(r"\s*(NOW|NEXT|THEN|LATER)\b", line)
        for number in re.findall(r"#(\d+)", line) if match else ():
            tiers.setdefault(int(number), match.group(1))
    return tiers


def priority(number: int, body: object, repo: str) -> tuple[str | None, str]:
    """(tier, why) for one ticket from its pinned Plan baseline; a None tier is unknown priority.

    Raises BindingError exactly when `binding.admit` would refuse the ticket (ambiguous or
    malformed binding, linked without a baseline), so a scheduler never ranks such work.
    """
    body = body or ""  # admit_plan reads a null body the same way
    initiative = binding.linked(body)
    if initiative is None:
        return None, "unknown priority: no Plan baseline (ticket is not bound to an initiative)"
    pinned = binding.baseline(body, repo)
    if pinned is None:
        raise binding.BindingError(f"linked ticket for initiative #{initiative} is missing a Plan baseline")
    tier = priorities(pinned["sections"]["Plan"]).get(number)
    if tier is None:
        return None, f"unknown priority: initiative #{initiative} Plan baseline names no tier for #{number}"
    return tier, f"{tier} in initiative #{initiative} Plan baseline"


def is_experiment(issue: dict) -> bool:
    return any(label.get("name") == config.LABEL_EXPERIMENT for label in issue.get("labels") or [])


def explicit_streak(events: list[dict]) -> int:
    """Consecutive explicit-priority `claimed` rows at the journal tail."""
    streak = 0
    for row in reversed(events):
        if row.get("event") != "claimed":
            continue
        if row.get("priority") not in PRIORITY_TIERS:
            break
        streak += 1
    return streak


def active_experiments(events: list[dict], active: set[int]) -> int:
    """Lock-holding tickets whose latest claim carried the `experiment` label."""
    seen: set[int] = set()
    count = 0
    for row in reversed(events):
        n = row.get("ticket")
        if row.get("event") == "claimed" and n in active and n not in seen:
            seen.add(n)
            count += bool(row.get("experiment"))
    return count


def plan(rows: list[dict], capacity: int, *, repo: str, streak: int, experiments: int) -> dict:
    """Admission order for one pass under the accepted policy; mutates nothing.

    Eligibility came first: a row with `held` (assigned, blocked, initiative) is explained,
    never ranked. Then the experiment slot and capacity, then priority: explicit tiers in
    PRIORITY_TIERS order before unknown priority, oldest ticket first within a class. After
    FAIRNESS_STREAK consecutive explicit admissions (journal tail plus this pass) the oldest
    eligible unknown-priority ticket is admitted instead. Returns the `selected` issues in
    admission order and one `explained` row per frontier ticket.
    """
    explained, held, pool = [], [], []
    for issue in rows:
        n = issue["number"]
        if issue.get("held"):
            held.append({"ticket": n, "decision": "held", "reason": issue["held"]})
            continue
        try:
            tier, why = priority(n, issue.get("body"), repo)
        except binding.BindingError as exc:
            held.append({"ticket": n, "decision": "held",
                         "reason": f"Plan binding is invalid; admission would refuse it: {exc}"})
            continue
        pool.append({"issue": issue, "ticket": n, "priority": tier, "why": why,
                     "experiment": is_experiment(issue), "age": (issue.get("createdAt") or "", n)})

    def ranked(candidates: list[dict]) -> list[dict]:
        return sorted(candidates, key=lambda c: (
            PRIORITY_TIERS.index(c["priority"]) if c["priority"] else len(PRIORITY_TIERS), c["age"]))

    selected = []
    while pool and len(selected) < capacity:
        eligible = [c for c in pool if not (c["experiment"] and experiments >= MAX_EXPERIMENTS)]
        if not eligible:
            break
        explicit = ranked([c for c in eligible if c["priority"]])
        unknown = ranked([c for c in eligible if not c["priority"]])
        if unknown and (not explicit or streak >= FAIRNESS_STREAK):
            pick = unknown[0]
            rule = (f"fairness after {streak} consecutive explicit-priority admissions; oldest unknown-priority"
                    if explicit else "oldest unknown-priority; no explicit priority is eligible")
            streak = 0
        else:
            pick = explicit[0]
            rule = f"highest accepted priority {pick['priority']}, oldest first"
            streak += 1
        experiments += pick["experiment"]
        pool.remove(pick)
        selected.append(pick["issue"])
        explained.append(_explain(pick, "selected", rule))
    for c in ranked(pool):
        if c["experiment"] and experiments >= MAX_EXPERIMENTS:
            rule = f"experiment slot full ({experiments} of {MAX_EXPERIMENTS} active)"
        elif c["priority"]:
            rule = f"no capacity left; accepted priority {c['priority']} ranks after the selected tickets"
        else:
            rule = "no capacity left; unknown priority ranks after explicit priorities"
        explained.append(_explain(c, "deferred", rule))
    return {"selected": selected, "explained": explained + held}


def _explain(candidate: dict, decision: str, rule: str) -> dict:
    return {"ticket": candidate["ticket"], "decision": decision, "priority": candidate["priority"],
            "experiment": candidate["experiment"], "reason": f"{rule} ({candidate['why']})"}
