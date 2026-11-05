"""Ticket generations: the span of journal rows a ticket's budgets count over.

An owner requeue (a human releasing an escalated ticket back to `ready-for-agent`) opens a
generation with a `generation` row. Attempts, escalations, manager rounds and the
dispatcher's round counters recorded after that row belong to it; earlier rows stay
history. A ticket with no `generation` row is in the legacy generation 0, which spans its
whole journal, so budgets count exactly as they did before generations existed.

A factory requeue never opens a generation. The factory journals each of its own releases
before it relabels the ticket, so an escalation followed by none of those rows was
released by a human. A worktree belongs to the generation's attempts; whether work starts
fresh is the generation's `intent`, never whether a worktree or branch survived.
"""
from __future__ import annotations

from dataclasses import dataclass

INTENTS = ("resume", "fresh")
# `manage.apply` relabels the ticket ready-for-agent after these decisions; their `manage`
# row is journaled before the edit.
RELEASING_DECISIONS = frozenset({"RETRY", "REWRITE", "AUTHORIZE", "ROUTE"})
# The dispatcher journals these immediately before it relabels the ticket ready-for-agent.
REQUEUE_EVENTS = frozenset({"conflict-requeue", "gate-requeue", "risk-requeue"})
# Statuses of a guarded `requeue_agent` decision whose label edit may have happened.
_APPLIED = frozenset({"success", "partial", "uncertain"})


@dataclass(frozen=True)
class Generation:
    number: int  # 0: the legacy generation, before any owner requeue
    intent: str
    rows: list[dict]  # this ticket's journal rows from the generation's start

    @property
    def claimed(self) -> bool:
        return any(row.get("event") == "claimed" for row in self.rows)

    @property
    def awaiting_attempt(self) -> bool:
        """An owner requeue with no worker attempt yet: everything observed so far predates it."""
        return self.number > 0 and not any(row.get("event") == "attempt" for row in self.rows)


def current(events: list[dict], n: int) -> Generation:
    rows = [row for row in events if row.get("ticket") == n]
    start = next(
        (index for index in range(len(rows) - 1, -1, -1) if rows[index].get("event") == "generation"),
        None,
    )
    if start is None:
        return Generation(0, "resume", rows)
    row = rows[start]
    return Generation(row["generation"], row["intent"], rows[start:])


def _factory_release(row: dict) -> bool:
    kind = row.get("event")
    return (
        kind in REQUEUE_EVENTS
        or (kind == "review-rework" and row.get("deferred") is True)
        or (kind == "manage" and not row.get("pr") and row.get("decision") in RELEASING_DECISIONS)
    )


def _owner_decision(row: dict) -> bool:
    action = row.get("action")
    return (
        row.get("event") == "human-decision"
        and isinstance(action, dict)
        and action.get("name") == "requeue_agent"
        and row.get("status") in _APPLIED
    )


def owner_requeue(events: list[dict], n: int) -> dict | None:
    """Fields of the `generation` row a human release of the latest escalation opens, else None.

    The caller has observed the ticket back in `ready-for-agent`. The last release after the
    escalation decides: a factory release or an already-open generation means none is due. A
    guarded `requeue_agent` decision supplies its actor and intent; a plain relabel resumes.
    """
    now = current(events, n)
    rows = [row for row in events if row.get("ticket") == n]
    escalation = next(
        (index for index in range(len(rows) - 1, -1, -1)
         if rows[index].get("event") == "escalate" and rows[index].get("reason") != "manager_failed"),
        None,
    )
    if escalation is None:
        return None
    release = None
    for row in rows[escalation + 1:]:
        if row.get("event") == "generation" or _factory_release(row) or _owner_decision(row):
            release = row
    if release is not None and not _owner_decision(release):
        return None
    args = (release or {}).get("action", {}).get("args") or {}
    intent = args.get("intent", "resume")
    return {
        "generation": now.number + 1,
        "intent": intent if intent in INTENTS else "resume",
        "source": "requeue_agent" if release else "label",
        "actor": release.get("actor") if release else None,
        "decision": release.get("proposal_id") if release else None,
        "escalation_at": rows[escalation].get("at"),
    }
