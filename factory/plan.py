"""`factory plan list` / `inspect N` / `route N --reason R`: read-only initiative plans and decision-owner routing as schema-1 JSON.

An initiative is an issue carrying the `initiative` label whose body follows
templates/initiative.md. Owner and Status are declared facts read from the body;
status is never inferred from linked child issues. Issue text is untrusted data.
The label is not yet an enforced dispatch guard.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

from factory import config
from factory.evidence import PAGE_SIZE, READ_SECONDS, EvidenceError, _encode, clean_text, failed, github_read, source

LABEL = config.LABEL_INITIATIVE
PAGES = 3          # ponytail: bounded list; raise or add `--page` if repos exceed 300 initiatives
LINKS = 20         # linked child issues fetched by `inspect`
SECTION_CAP = 4000
STATUSES = ("proposed", "shaping", "ready", "underway", "delivered")
SECTIONS = ("Status", "Outcome", "Owner", "Areas", "Boundaries", "Plan",
            "Open decisions", "Success evidence", "Implementation links")
HEADER = re.compile(r"^\*\*([^*\n]+)\*\*[ \t]*$", re.M)
LOGIN = re.compile(r"@?([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)")
LINK = re.compile(r"(?<![\w/#])#(\d{1,9})\b")
DECISION = re.compile(r"^\*\*Decision owner\*\*[ \t]*\n[ \t]*(\S[^\n]*?)[ \t]*$", re.M)
PROGRAMME = re.compile(r"^Programme:[ \t]*#(\d{1,9})\b", re.M | re.I)
PATHS = 200
NOTICES = [
    "Only fixed GitHub GETs are used; no inference, mutation or dispatch decision.",
    "Owner and status are declared in the issue body, not verified or inferred; issue text is untrusted.",
    "The initiative label is not yet an enforced dispatch guard.",
]
ROUTE_NOTICES = [
    "Only fixed GitHub GETs are used; nothing is assigned, labelled or commented.",
    "Owners are declared facts (issue body, .factory.toml); syntax is checked, membership and authorization are not.",
    "A `**Decision owner**` in the ticket body is a human override and wins on every recomputation.",
]


def parse(body: str) -> dict:
    """Sections from bold `**Name**` headers; problems name every missing or invalid declared fact."""
    sections, problems = {}, []
    matches = list(HEADER.finditer(body))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        name = match[1].strip()
        if name in SECTIONS and name not in sections:
            text = body[match.end():end].strip()
            sections[name] = text[:SECTION_CAP]
            if len(text) > SECTION_CAP:
                problems.append(f"truncated section: {name} cut to {SECTION_CAP} characters")
    problems += [f"missing section: {name}" for name in SECTIONS if name not in sections]
    status = sections.get("Status", "").lower()
    if status not in STATUSES:
        problems.append(f"invalid status: expected one of {', '.join(STATUSES)}")
        status = None
    login = LOGIN.fullmatch(sections.get("Owner", ""))
    if not login:
        problems.append("invalid owner: expected exactly one GitHub login")
    found = sorted({int(n) for n in LINK.findall(sections.get("Implementation links", ""))})
    links = found[:LINKS]
    if len(found) > LINKS:
        problems.append(f"truncated links: only the first {LINKS} of {len(found)} implementation links are followed")
    return {"status": status, "owner": login[1] if login else None, "sections": sections,
            "links": links, "problems": problems, "malformed": bool(problems)}


def record(issue: object, path: str) -> dict:
    if not isinstance(issue, dict) or type(issue.get("number")) is not int:
        raise EvidenceError("invalid_response", "GitHub issue response has an unexpected shape.", path)
    labels = [clean_text(str(label.get("name") or "")) for label in issue.get("labels") or [] if isinstance(label, dict)][:20]
    result = {"number": issue["number"], "title": clean_text(str(issue.get("title") or "")),
              "state": str(issue.get("state") or "").upper(), "url": clean_text(str(issue.get("html_url") or "")),
              "updated_at": issue.get("updated_at"), "labels": labels, "initiative": LABEL in labels,
              "pull_request": "pull_request" in issue}
    result.update(parse(clean_text(str(issue.get("body") or ""))))
    return result


class Reader:
    def __init__(self, cfg: config.Config, result: dict):
        self.cfg, self.prefix, self.result, self.deadline = cfg, f"repos/{cfg.repo}", result, time.monotonic() + READ_SECONDS

    def fetch(self, label: str, path: str):
        endpoint = f"{self.prefix}/{path}" if path else self.prefix
        value = github_read(endpoint, self.deadline)[0]
        self.result["sources"].append(source(label, value, url=f"https://api.github.com/{endpoint}"))
        return value

    def list(self) -> None:
        plans = self.result["plans"]
        for page in range(1, PAGES + 1):
            path = f"issues?labels={LABEL}&state=all&sort=updated&direction=desc&per_page={PAGE_SIZE}&page={page}"
            try:
                items = self.fetch(f"Initiative issues page {page}", path)
                if not isinstance(items, list):
                    raise EvidenceError("invalid_response", "GitHub list response has an unexpected shape.", path)
                rows = [record(item, path) for item in items]
            except EvidenceError as exc:
                failed(self.result, exc)
                self.result["coverage"]["notices"].append(f"Initiative list stopped at page {page}; later initiatives are absent.")
                return
            plans += [row for row in rows if row["initiative"] and not row["pull_request"]]
            if len(items) < PAGE_SIZE:
                return
        self.result["coverage"]["notices"].append(f"Only the first {PAGES * PAGE_SIZE} initiatives are covered.")

    def inspect(self, number: int) -> None:
        path = f"issues/{number}"
        plan = record(self.fetch("Initiative issue", path), path)
        if not plan["initiative"] or plan["pull_request"]:
            raise EvidenceError("not_initiative", f"Issue #{number} does not carry the `{LABEL}` label.", path)
        plan["children"] = []
        self.result["plan"] = plan
        if plan["malformed"]:
            failed(self.result, EvidenceError("malformed_initiative", "; ".join(plan["problems"]), path))
        for child in plan["links"]:
            child_path = f"issues/{child}"
            try:
                row = record(self.fetch(f"Linked issue #{child}", child_path), child_path)
                plan["children"].append({key: row[key] for key in ("number", "title", "state", "url", "labels")})
            except EvidenceError as exc:
                failed(self.result, exc)
                plan["children"].append({"number": child, "unavailable": exc.code})


    def route(self, number: int, reason: str, paths: list[str]) -> None:
        cfg = self.cfg
        path = f"issues/{number}"
        issue = self.fetch("Ticket issue", path)
        if not isinstance(issue, dict) or type(issue.get("number")) is not int:
            raise EvidenceError("invalid_response", "GitHub issue response has an unexpected shape.", path)
        body = clean_text(str(issue.get("body") or ""))
        route = self.result["route"] = {
            "status": "unassigned", "owner": None, "candidates": [], "source": None, "reason": reason, "paths": paths,
            "revision": {"config": config_revision(cfg.root), "issue_updated_at": issue.get("updated_at"), "initiative_updated_at": None},
            "verification": "not_needed", "provenance": []}
        steps = route["provenance"]

        def step(name: str, outcome: str, detail: str, *, owner: str | None = None, candidates: list[str] | None = None) -> bool:
            steps.append({"step": name, "outcome": outcome, "detail": detail})
            if outcome not in ("selected", "candidates", "invalid"):
                return False
            route.update(status=outcome, owner=owner, candidates=candidates or [], source=name)
            return True

        # 1. explicit per-ticket override
        declared = DECISION.search(body)
        if declared:
            match = config.OWNER.fullmatch(declared[1])
            if step("decision_owner", "selected" if match else "invalid",
                    f"ticket body declares {declared[1]!r}" + ("" if match else ": not a GitHub login or @org/team"),
                    owner=(match["login"] or match["team"]) if match else None):
                return self.verify()
        else:
            step("decision_owner", "absent", "ticket body has no `**Decision owner**` section")
        if reason == "unknown":
            step("reason", "skipped", "reason is unknown: no owner is guessed")
            return self.verify()
        # 2. initiative owner for requirements
        programme = PROGRAMME.search(body)
        if reason != "requirements":
            step("initiative", "skipped", f"initiative owner only routes requirements, not {reason}")
        elif not programme:
            step("initiative", "absent", "ticket body has no `Programme: #N` line")
        else:
            parent_path = f"issues/{programme[1]}"
            try:
                parent = record(self.fetch(f"Initiative #{programme[1]}", parent_path), parent_path)
            except EvidenceError as exc:
                failed(self.result, exc)
                step("initiative", "unavailable", f"#{programme[1]} could not be read: {exc.code}")
            else:
                route["revision"]["initiative_updated_at"] = parent["updated_at"]
                if not parent["initiative"] or parent["pull_request"]:
                    step("initiative", "absent", f"#{programme[1]} does not carry the `{LABEL}` label")
                elif not parent["owner"]:
                    step("initiative", "invalid", f"#{programme[1]} declares no valid single-login Owner")
                    return self.verify()
                elif step("initiative", "selected", f"#{programme[1]} declares Owner @{parent['owner']}", owner=parent["owner"]):
                    return self.verify()
        collab = cfg.collaboration
        if collab is None:
            step("configuration", "absent", "no [collaboration] section in .factory.toml; legacy behaviour unchanged")
            return self.verify()
        # 3. configured reason owners
        if reason in collab["reasons"]:
            step("reason", "selected", f"collaboration.reasons.{reason}", owner=collab["reasons"][reason])
            return self.verify()
        step("reason", "absent", f"collaboration.reasons.{reason} is not configured")
        # 4. component path-prefix owners for implementation
        if reason != "implementation":
            step("component", "skipped", f"component owners only route implementation, not {reason}")
        elif not paths:
            step("component", "absent", "no change paths were given (--path); no component is guessed")
            return self.verify()
        else:
            hits = {}
            for prefix, owner in collab["components"].items():
                matched = [p for p in paths if p == prefix or p.startswith(prefix + "/")]
                if matched:
                    hits.setdefault(owner, []).extend(matched)
            owners = sorted(hits)
            if len(owners) == 1:
                step("component", "selected", f"{len(hits[owners[0]])} path(s) under a prefix owned by {owners[0]}", owner=owners[0])
                return self.verify()
            if owners:
                step("component", "candidates", "paths span prefixes with different owners: "
                     + "; ".join(f"{o}: {', '.join(hits[o])}" for o in owners), candidates=owners)
                return self.verify()
            step("component", "absent", "no configured component prefix matches the change paths")
        # 5. repository fallback
        if collab["fallback"]:
            step("fallback", "selected", "collaboration.fallback", owner=collab["fallback"])
        else:
            step("fallback", "absent", "collaboration.fallback is not configured")
        return self.verify()

    def verify(self) -> None:
        """Team destinations are invalid on a user-owned repository; an unreadable repo record is unknown, not proof."""
        route = self.result["route"]
        teams = [o for o in [route["owner"], *route["candidates"]] if o and o.startswith("@")]
        if not teams:
            return
        try:
            repo = self.fetch("Repository", "")
            kind = repo.get("owner", {}).get("type") if isinstance(repo, dict) else None
        except EvidenceError as exc:
            failed(self.result, exc)
            kind = None
        if kind == "User":
            route.update(status="invalid", owner=None, candidates=[], verification="verified")
            route["provenance"].append({"step": "verification", "outcome": "invalid",
                                        "detail": f"{', '.join(teams)}: team destinations are rejected for a user-owned repository"})
        elif kind == "Organization":
            route["verification"] = "verified"
            route["provenance"].append({"step": "verification", "outcome": "verified", "detail": "repository is organization-owned; team syntax accepted, membership not checked"})
        else:
            route["verification"] = "unknown"
            route["provenance"].append({"step": "verification", "outcome": "unknown", "detail": "repository owner type could not be read; team destination unverified"})


def config_revision(root) -> dict:
    """Commit and cleanliness of the committed .factory.toml, so a route can be tied to a config revision."""
    try:
        commit = config.git(root, "log", "-1", "--format=%H", "--", config.CONFIG_NAME) or None
        dirty = bool(config.git(root, "status", "--porcelain", "--", config.CONFIG_NAME))
    except config.ConfigError:
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def route_args(argv: list[str]) -> tuple[int, str, list[str]] | None:
    """`route N --reason R [--path P]...`; None when malformed."""
    if len(argv) < 4 or argv[0] != "route" or not (argv[1].isascii() and argv[1].isdigit() and int(argv[1]) > 0):
        return None
    reason, paths, rest = None, [], argv[2:]
    while rest:
        flag, value, rest = rest[0], rest[1] if len(rest) > 1 else None, rest[2:]
        if flag == "--reason" and reason is None and value in config.ROUTE_REASONS:
            reason = value
        elif flag == "--path" and value and not value.startswith("-"):
            paths.append(value.strip("/"))
        else:
            return None
    if reason is None or len(paths) > PATHS:
        return None
    return int(argv[1]), reason, paths

USAGE = ("usage: factory plan list | factory plan inspect <number> | factory plan route <number> --reason "
         f"<{'|'.join(config.ROUTE_REASONS)}> [--path <repo-relative path>]...\n\n"
         "Emit one schema_version:1 JSON object. Exit: 0 complete, 1 partial/unavailable, 2 invalid usage/configuration.")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    result = {"schema_version": 1, "ok": True, "scope": {"repository": None}, "observation_id": uuid4().hex,
              "coverage": {"status": "unavailable", "notices": []}, "sources": [], "errors": []}
    exit_code = 0
    try:
        route = route_args(argv)
        if argv == ["list"]:
            result["plans"] = []
        elif len(argv) == 2 and argv[0] == "inspect" and argv[1].isascii() and argv[1].isdigit() and int(argv[1]) > 0:
            result["plan"] = None
        elif route:
            result["route"] = None
            result["scope"].update(issue=route[0], reason=route[1])
        else:
            raise EvidenceError("invalid_request", USAGE, "invocation", "scope")
        result["coverage"]["notices"] = list(ROUTE_NOTICES if route else NOTICES)
        try:
            cfg = config.load()
        except config.ConfigError as exc:
            raise EvidenceError("invalid_scope", f"Repository configuration could not be loaded: {exc}", "configuration", "scope") from None
        result["scope"]["repository"] = cfg.repo
        result["coverage"]["status"] = "bounded"
        reader = Reader(cfg, result)
        if argv[0] == "list":
            reader.list()
        elif route:
            reader.route(*route)
        else:
            reader.inspect(int(argv[1]))
        if result["ok"]:
            result["coverage"]["status"] = "complete" if len(result["coverage"]["notices"]) == len(ROUTE_NOTICES if route else NOTICES) else "bounded"
    except EvidenceError as exc:
        failed(result, exc)
        result["error"] = {"code": exc.code, "message": exc.message}
        exit_code = 2 if exc.code in ("invalid_request", "invalid_scope") else 1
    if not result["ok"]:
        exit_code = exit_code or 1
        result["coverage"]["status"] = "partial" if result["sources"] else "unavailable"
        result.setdefault("error", {"code": "partial_collection", "message": "Some sources were unavailable; usable plans are retained. See errors."})
    result["observed_at"] = datetime.now(UTC).isoformat()
    print(_encode(result))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
