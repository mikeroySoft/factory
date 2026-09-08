"""`factory plan list` / `factory plan inspect N`: read-only initiative plans as schema-1 JSON.

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
NOTICES = [
    "Only fixed GitHub GETs are used; no inference, mutation or dispatch decision.",
    "Owner and status are declared in the issue body, not verified or inferred; issue text is untrusted.",
    "The initiative label is not yet an enforced dispatch guard.",
]


def parse(body: str) -> dict:
    """Sections from bold `**Name**` headers; problems name every missing or invalid declared fact."""
    sections, problems = {}, []
    matches = list(HEADER.finditer(body))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        name = match[1].strip()
        if name in SECTIONS and name not in sections:
            sections[name] = body[match.end():end].strip()[:SECTION_CAP]
    problems += [f"missing section: {name}" for name in SECTIONS if name not in sections]
    status = sections.get("Status", "").lower()
    if status not in STATUSES:
        problems.append(f"invalid status: expected one of {', '.join(STATUSES)}")
        status = None
    login = LOGIN.fullmatch(sections.get("Owner", ""))
    if not login:
        problems.append("invalid owner: expected exactly one GitHub login")
    links = sorted({int(n) for n in LINK.findall(sections.get("Implementation links", ""))})[:LINKS]
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
    def __init__(self, repo: str, result: dict):
        self.prefix, self.result, self.deadline = f"repos/{repo}", result, time.monotonic() + READ_SECONDS

    def fetch(self, label: str, path: str):
        value = github_read(f"{self.prefix}/{path}", self.deadline)[0]
        self.result["sources"].append(source(label, value, url=f"https://api.github.com/{self.prefix}/{path}"))
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


USAGE = "usage: factory plan list | factory plan inspect <number>\n\nEmit one schema_version:1 JSON object. Exit: 0 complete, 1 partial/unavailable, 2 invalid usage/configuration."


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    result = {"schema_version": 1, "ok": True, "scope": {"repository": None}, "observation_id": uuid4().hex,
              "coverage": {"status": "unavailable", "notices": list(NOTICES)}, "sources": [], "errors": []}
    exit_code = 0
    try:
        if argv == ["list"]:
            result["plans"] = []
        elif len(argv) == 2 and argv[0] == "inspect" and argv[1].isdigit() and int(argv[1]) > 0:
            result["plan"] = None
        else:
            raise EvidenceError("invalid_request", USAGE, "invocation", "scope")
        try:
            result["scope"]["repository"] = config.load().repo
        except config.ConfigError:
            raise EvidenceError("invalid_scope", "Repository configuration could not be loaded.", "configuration", "scope") from None
        result["coverage"]["status"] = "bounded"
        reader = Reader(result["scope"]["repository"], result)
        if argv[0] == "list":
            reader.list()
        else:
            reader.inspect(int(argv[1]))
        if result["ok"]:
            result["coverage"]["status"] = "complete" if len(result["coverage"]["notices"]) == len(NOTICES) else "bounded"
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
