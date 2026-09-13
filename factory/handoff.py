"""Durable routed human handoffs: one public-safe request per terminal escalation generation.

A request is published only once automatic manager recovery is terminal for the latest
escalation (manager `HUMAN`, transport failure, rounds exhausted, or no manager). Its
identity is `<ticket>/<round>`; intent is journaled before the comment, the comment id
after. Uncertain publication is reconciled from GitHub before any retry, and a failed
reconciliation posts nothing. Owners come from the read-only `factory plan route`
producer; a `**Decision owner**` edit by a human wins and is never written back.
"""

from __future__ import annotations

import fcntl
import re
from contextlib import nullcontext
from pathlib import Path
from subprocess import CalledProcessError

from factory import dispatch, lifecycle
from factory.config import LABEL_AGENT, LABEL_HUMAN

MARK = "factory-handoff"
# Runner-local absolute paths; URLs are untouched (`://` and `host/` precede their slashes).
LOCAL_PATH = re.compile(r"(?<![\w:/])/[\w.@+-]+(?:/[\w.@+-]*)*")
TIMELINE_EVENTS = {"commented", "labeled", "unlabeled", "assigned", "unassigned", "edited", "renamed", "closed", "reopened"}


def public(text: str) -> str:
    return LOCAL_PATH.sub("(local path withheld)", text)


def timeline(n: int) -> list[dict]:
    pages = dispatch.gh_json(["api", f"repos/{dispatch.REPO}/issues/{n}/timeline", "--paginate", "--slurp"])
    return [item for page in pages for item in page] if pages and isinstance(pages[0], list) else pages


def terminal(cfg, events: list[dict], escalation: dict) -> str | None:
    """Why no automatic recovery remains for this escalation; None while the manager may still act.

    Mirrors every reason `manage.escalation_pass` will never run the manager again.
    """
    r = escalation.get("round", 0)
    if not cfg.manager:
        return "no manager is configured, so nothing recovers automatically"
    if not 1 <= r <= cfg.manager_rounds:
        return f"the manager's {cfg.manager_rounds} allowed round(s) are exhausted"
    decided = [e for e in events if e.get("event") == "manage" and e.get("round") == r]
    if not decided:
        if not Path(escalation["packet"]).is_file():
            return "the escalation packet is missing on the runner, so the manager cannot run"
        if not any(e.get("event") == "comment" and e.get("kind") == "escalation" and e.get("at", "") >= escalation["at"]
                   for e in events):
            # Without its own comment receipt the factory cannot tell its escalation comment from a human's.
            return "the escalation comment was not journaled, so takeover detection cannot clear the manager to run"
        return None
    if any(e.get("event") == "escalate" and e.get("reason") == "manager_failed" and e.get("round") == r for e in events):
        return "the manager could not run, so there is no automatic diagnosis; the cause is unknown until a human looks"
    if any(e.get("decision") == "HUMAN" for e in decided):
        return "the manager asked for a human decision"
    spent = next((e for e in decided if e.get("pr")), None)
    if spent:  # the escalation loop skips a round its PR frontier already decided (round numbers collide)
        return f"the manager's round for this escalation was already spent by its PR #{spent['pr']} {spent.get('decision')} decision"
    failed = next((e for e in events if e.get("event") == "lifecycle" and e.get("kind") == "exit"
                   and e.get("outcome") == "mechanism_failure"
                   and e.get("execution_id") in {d.get("execution_id") for d in decided}), None)
    if failed:
        return f"the manager's {decided[-1].get('decision')} decision could not be applied to GitHub and is never replayed"
    return None  # handed back to automation; a later failure is a new escalation generation


def observe(cfg, n: int, escalation: dict) -> dict | None:
    """Read-only routing plus the PR link; None when the ticket cannot be read (no owner is guessed)."""
    from factory import plan
    from factory.evidence import EvidenceError

    reason = "ci" if "CI failed" in (escalation.get("reason") or "") else "implementation"
    pr, paths = None, []
    try:
        pr = dispatch.gh_json(["pr", "view", f"agent/{n}", "--repo", cfg.repo, "--json", "url,files"])
        paths = [f["path"] for f in pr.get("files") or [] if isinstance(f, dict) and f.get("path")][:plan.PATHS]
    except (CalledProcessError, ValueError, AttributeError):
        pr = None
    result = {"ok": True, "sources": [], "errors": []}
    try:
        plan.Reader(cfg, result).route(n, reason, paths)
    except EvidenceError:
        return None
    return {"route": result["route"], "pr_url": (pr or {}).get("url")}


def mentions(route: dict) -> list[str]:
    """Logins always; `@org/team` only on a verified organization repository."""
    names = [route["owner"]] if route.get("owner") else list(route.get("candidates") or [])
    return [name if name.startswith("@") else f"@{name}" for name in names
            if not name.startswith("@") or route.get("verification") == "verified"]


def target(route: dict) -> str:
    return " ".join(mentions(route)) or route["status"]


def compose(n: int, request: str, token: str, escalation: dict, why: str, observed: dict, links: dict,
            previous: str | None) -> str:
    route = observed["route"]
    who = mentions(route)
    source = route.get("source") or "no source"
    if previous is not None:
        rationale = route["provenance"][-1]["detail"] if route["provenance"] else source
        return (f"Factory handoff `{request}`: the decision owner is now {' '.join(who)} ({source}: {public(rationale)}); "
                f"previously {previous}.\n\n<!-- {token} -->")
    if route["status"] == "selected" and who:
        owner = f"{who[0]} — you own this decision ({source})."
    elif route["status"] == "candidates" and who:
        owner = f"{', '.join(who)} — candidates ({source}); one of you should claim it."
    elif route["status"] in ("selected", "candidates"):
        teams = ", ".join(f"`{name}`" for name in ([route["owner"]] if route.get("owner") else route["candidates"]))
        owner = f"{teams}: team destinations could not be verified on this repository, so nobody is mentioned ({source})."
    elif route["status"] == "invalid":
        owner = f"The routed owner declaration is invalid ({public(route['provenance'][-1]['detail'])}); fix it before anyone is mentioned."
    else:
        owner = "No decision owner could be determined (unassigned); a maintainer should claim it."
    evidence = [f"- {label}: {url}" for label, url in links.items() if url] or ["- (none published)"]
    routing = [f"- {step['step']}: {step['outcome']} — {public(step['detail'])}" for step in route["provenance"]]
    return "\n".join([
        f"Factory handoff request `{request}`: a human decision is needed on #{n}.",
        "",
        owner,
        "",
        "**Question**",
        f"How should #{n} proceed? It escalated with: {public(escalation.get('reason') or 'unknown reason')}. "
        f"Automatic recovery stopped because {why}.",
        "",
        "**Evidence** (bounded links; worker logs and the escalation packet stay on the runner)",
        *evidence,
        "",
        "**Proposed next step**",
        f"Read the evidence, then amend the ticket and relabel `{LABEL_AGENT}` for another attempt, fix the branch by hand, "
        "request changes on the PR, or close it. Replying here is context for humans; nothing here retries, approves or merges.",
        "",
        "**Routing**",
        *routing,
        "To claim or reassign this decision, set a `**Decision owner**` section (a GitHub login) in the issue body; "
        "the next pass reflects it and never overwrites it.",
        "",
        f"<!-- {token} -->",
    ])


def handoff_pass(dry_run: bool = False) -> None:
    """Publish or reconcile the routed request for every terminal `ready-for-human` escalation."""
    cfg = dispatch.cfg
    issues = dispatch.gh_json(["issue", "list", "--repo", cfg.repo, "--state", "open", "--label", LABEL_HUMAN,
                               "--json", "number,title,labels", "--limit", "1000"])
    for issue in issues:
        n = issue["number"]
        lock_path = cfg.factory / "locks" / f"{n}.lock"
        if dry_run and dispatch.lock_held(lock_path):
            continue
        with nullcontext() if dry_run else lifecycle.scope(dispatch.EVENTS, "manage", ticket=n) as execution:
            with nullcontext() if dry_run else dispatch.ticket_lock(n).open("w") as lock:
                if not dry_run:
                    request = execution.resource("requested", lock_path, scope="repository")
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        execution.wait("ticket_lock_contended", mode="retry_next_pass", resource=request["resource"])
                        continue
                    execution.resource("acquired", lock_path, scope="repository")
                try:
                    events = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("ticket") == n]
                    escalation = next((e for e in reversed(events) if e.get("event") == "escalate"
                                       and e.get("reason") != "manager_failed"), None)
                    if not escalation or escalation.get("upstream") or escalation.get("pr") or not escalation.get("packet"):
                        continue
                    why = terminal(cfg, events, escalation)
                    if why is None:
                        if dry_run:
                            dispatch.log(f"#{n}: automatic recovery still eligible; routing stays advisory (`factory plan route {n}`)")
                        continue
                    if dispatch.initiative_kind(n):
                        dispatch.log(f"#{n}: refused (initiative records never receive handoff requests)")
                        continue
                    r = escalation.get("round", 0)
                    request = f"{n}/{r}"
                    receipts = [e for e in events if e.get("event") == "comment" and e.get("kind") == "handoff" and e.get("request") == request]
                    intents = [e for e in events if e.get("event") == "handoff" and e.get("request") == request]
                    pending = [i for i in intents if not any(p.get("token") == i["token"] for p in receipts)]
                    if pending:
                        # Uncertain publication: only GitHub can say whether the comment exists.
                        try:
                            items = timeline(n)
                        except (CalledProcessError, ValueError):
                            dispatch.log(f"#{n}: handoff publication uncertain and GitHub could not be read; nothing posted")
                            continue
                        for intent in pending:
                            found = next((item for item in items if item.get("event") == "commented"
                                          and type(item.get("id")) is int and intent["token"] in (item.get("body") or "")), None)
                            if found:
                                if not dry_run:
                                    dispatch.record("comment", ticket=n, kind="handoff", round=r, request=request,
                                                    token=intent["token"], target=intent["target"], comment=found["id"],
                                                    url=found.get("html_url"), reconciled=True)
                                receipts.append({"token": intent["token"], "target": intent["target"]})
                    # ponytail: ~2 GETs per parked terminal ticket per pass to notice owner changes;
                    # add an issue-updatedAt short-circuit if rate limits ever bite.
                    observed = observe(cfg, n, escalation)
                    if observed is None:
                        dispatch.log(f"#{n}: handoff request {request} deferred; the ticket could not be read for routing")
                        continue
                    route = observed["route"]
                    current = target(route)
                    previous = receipts[-1]["target"] if receipts else None
                    if previous is not None and (previous == current or not mentions(route)):
                        if dry_run:
                            dispatch.log(f"#{n}: handoff request {request} already published to {previous}; nothing to notify")
                        continue  # same owner, or a transition to nobody
                    if dry_run:
                        dispatch.log(f"#{n}: would publish handoff request {request} to {current} ({why})")
                        continue
                    intent = next((i for i in pending if i["target"] == current), None)
                    if intent is None:
                        token = f"{MARK} {request} #{len(intents) + 1}"
                        dispatch.record("handoff", ticket=n, round=r, request=request, token=token, target=current, why=why,
                                        route={k: route.get(k) for k in ("status", "owner", "candidates", "source", "reason")})
                    else:
                        token = intent["token"]
                    links = {"Pull request": observed["pr_url"]}
                    for kind, label in (("escalation", "Escalation"), ("manager", "Manager")):
                        row = next((e for e in reversed(events) if e.get("event") == "comment" and e.get("kind") == kind
                                    and e.get("at", "") >= escalation["at"]), None)
                        links[label] = row.get("url") if row else None
                    body = compose(n, request, token, escalation, why, observed, links, previous)
                    proc = dispatch.run(["gh", "issue", "comment", str(n), "--repo", cfg.repo, "--body", body], check=False)
                    if dispatch.comment_receipt(n, "handoff", proc.stdout if proc.returncode == 0 else "",
                                                round=r, request=request, token=token, target=current):
                        dispatch.log(f"#{n}: handoff request {request} published to {current}")
                    else:
                        dispatch.log(f"#{n}: handoff request {request} publication uncertain; reconciled on the next pass")
                except (CalledProcessError, OSError, ValueError) as exc:
                    if not dry_run:
                        execution.outcome, execution.reason = "mechanism_failure", "github_command_failed"
                    dispatch.log(f"#{n}: handoff failed: {exc}; leaving for human")
                finally:
                    if not dry_run:
                        lock.close()
                        execution.resource("released", lock_path, scope="repository")
