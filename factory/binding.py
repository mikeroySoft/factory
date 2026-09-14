"""Immutable initiative-plan bindings for linked execution tickets."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

from factory import config, lifecycle
from factory.evidence import EvidenceError, READ_SECONDS, github_read

SECTIONS = ("Outcome", "Boundaries", "Plan", "Success evidence")
_PLAN_HEADINGS = {
    "Status", "Outcome", "Owner", "Areas", "Boundaries", "Plan",
    "Open decisions", "Success evidence", "Implementation links",
}
_KEYS = {"schema_version", "initiative", "sections", "sha256", "source_url", "observed_at"}
_DECLARATION = re.compile(r"^[ \t]{0,3}Initiative[ \t]*:[ \t]*#([1-9]\d{0,8})[ \t]*$")
_DECLARATION_CANDIDATE = re.compile(
    r"^[ \t]{0,3}Initiative(?:[ \t]*:|[ \t]+#|[ \t]*$)", re.I
)
_BOLD_HEADING = re.compile(r"^[ \t]{0,3}\*\*([^*\r\n]+)\*\*[ \t]*$")
_BASELINE_HEADING = re.compile(
    r"^[ \t]{0,3}(?:\*\*[ \t]*Plan baseline[ \t]*\*\*|##[ \t]+Plan baseline)[ \t]*$"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCE_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+)/issues/([1-9]\d{0,8})"
)


class BindingError(ValueError):
    """A binding is absent, ambiguous, invalid, or cannot be observed safely."""

    def __init__(self, message: str, code: str = "invalid_binding"):
        super().__init__(message)
        self.code = code


def normalize(text: str) -> str:
    """Canonical section text: LF line endings and no edge whitespace."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _lines(body: str) -> tuple[list[str], list[bool]]:
    """Return source lines and whether each starts outside a fenced code block."""
    lines = body.splitlines(keepends=True)
    outside, fence = [], None
    for line in lines:
        bare = line.rstrip("\r\n")
        if fence is not None:
            outside.append(False)
            char, width = fence
            if re.fullmatch(rf"[ ]{{0,3}}{re.escape(char)}{{{width},}}[ \t]*", bare):
                fence = None
            continue
        outside.append(True)
        match = re.match(r"[ ]{0,3}(`{3,}|~{3,})(.*)$", bare)
        if match and (match[1][0] == "~" or "`" not in match[2]):
            fence = (match[1][0], len(match[1]))
    return lines, outside


def _line_text(line: str) -> str:
    return line.rstrip("\r\n")


def linked(body: str) -> int | None:
    """Return the single top-level ``Initiative: #N`` declaration, if present."""
    if not isinstance(body, str):
        raise BindingError("ticket body is unavailable")
    lines, outside = _lines(body)
    candidates = [
        _line_text(line)
        for line, visible in zip(lines, outside)
        if visible and _DECLARATION_CANDIDATE.match(_line_text(line))
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise BindingError("ambiguous Initiative declaration: expected exactly one 'Initiative: #N' line")
    match = _DECLARATION.fullmatch(candidates[0])
    if not match:
        raise BindingError("malformed Initiative declaration: expected exactly 'Initiative: #N'")
    return int(match[1])


def _heading(line: str) -> str | None:
    match = _BOLD_HEADING.fullmatch(line)
    return match[1].strip() if match else None


def _sections(body: str, initiative: int) -> dict[str, str]:
    lines, outside = _lines(body)
    headings = [
        (index, name)
        for index, (line, visible) in enumerate(zip(lines, outside))
        if visible
        and (name := _heading(_line_text(line))) is not None
        and name in _PLAN_HEADINGS
    ]
    found: dict[str, str] = {}
    for position, (index, name) in enumerate(headings):
        if name not in SECTIONS:
            continue
        if name in found:
            raise BindingError(f"initiative #{initiative} source is ambiguous: duplicate {name} section")
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        found[name] = normalize("".join(lines[index + 1:end]))
    missing = [name for name in SECTIONS if not found.get(name)]
    if missing:
        raise BindingError(
            f"initiative #{initiative} source is incomplete: missing or empty section(s): {', '.join(missing)}",
            "incomplete_source",
        )
    return {name: found[name] for name in SECTIONS}


def _digest(sections: dict[str, str]) -> str:
    payload = json.dumps(sections, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _canonical(value: object, repo: str | None = None, initiative: int | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != _KEYS:
        raise BindingError("invalid Plan baseline: expected exactly the schema-1 binding fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise BindingError("invalid Plan baseline: schema_version must be exactly 1")
    number = value["initiative"]
    if type(number) is not int or number <= 0:
        raise BindingError("invalid Plan baseline: initiative must be a positive integer")
    if initiative is not None and number != initiative:
        raise BindingError(
            f"invalid Plan baseline: Initiative declaration #{initiative} does not match baseline #{number}"
        )
    raw_sections = value["sections"]
    if not isinstance(raw_sections, dict) or set(raw_sections) != set(SECTIONS):
        raise BindingError(f"invalid Plan baseline: sections must be exactly {', '.join(SECTIONS)}")
    if any(not isinstance(raw_sections[name], str) or not raw_sections[name].strip() for name in SECTIONS):
        raise BindingError("invalid Plan baseline: every relevant section must be non-empty text")
    sections = {name: normalize(raw_sections[name]) for name in SECTIONS}
    digest = value["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or digest != _digest(sections):
        raise BindingError("invalid Plan baseline: sha256 does not match the normalized sections")
    source_url = value["source_url"]
    source = _SOURCE_URL.fullmatch(source_url) if isinstance(source_url, str) else None
    source_repo = repo if repo is not None else source[1] if source else None
    if source is None or source_repo is None or source_url != f"https://github.com/{source_repo}/issues/{number}":
        raise BindingError("invalid Plan baseline: source_url does not match the repository and initiative")
    if not _timestamp(value["observed_at"]):
        raise BindingError("invalid Plan baseline: observed_at must be a timezone-aware timestamp")
    return {
        "schema_version": 1,
        "initiative": number,
        "sections": sections,
        "sha256": digest,
        "source_url": source_url,
        "observed_at": value["observed_at"],
    }


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BindingError(f"invalid Plan baseline: duplicate JSON field {key!r}")
        result[key] = value
    return result


def baseline(body: str, repo: str) -> dict | None:
    """Validate and return the ticket's fenced Plan baseline, or ``None`` when absent."""
    if not isinstance(body, str):
        raise BindingError("ticket body is unavailable")
    lines, outside = _lines(body)
    headings = [index for index, (line, visible) in enumerate(zip(lines, outside))
                if visible and _BASELINE_HEADING.fullmatch(_line_text(line))]
    if not headings:
        return None
    if len(headings) != 1:
        raise BindingError("ambiguous Plan baseline: expected exactly one Plan baseline heading")
    number = linked(body)
    if number is None:
        raise BindingError("invalid Plan baseline: an Initiative: #N declaration is required")
    index = headings[0] + 1
    while index < len(lines) and not _line_text(lines[index]).strip():
        index += 1
    if index == len(lines):
        raise BindingError("invalid Plan baseline: fenced JSON object is missing")
    opening = re.fullmatch(r"[ ]{0,3}(`{3,}|~{3,})[ \t]*(?:json)?[ \t]*", _line_text(lines[index]), re.I)
    if not opening:
        raise BindingError("invalid Plan baseline: expected a JSON code fence immediately under the heading")
    char, width = opening[1][0], len(opening[1])
    close = next((position for position in range(index + 1, len(lines))
                  if re.fullmatch(rf"[ ]{{0,3}}{re.escape(char)}{{{width},}}[ \t]*", _line_text(lines[position]))), None)
    if close is None:
        raise BindingError("invalid Plan baseline: JSON code fence is incomplete")
    try:
        value = json.loads("".join(lines[index + 1:close]), object_pairs_hook=_json_object)
    except json.JSONDecodeError as exc:
        raise BindingError(f"invalid Plan baseline: fenced content is not JSON ({exc.msg})") from None
    return _canonical(value, repo, number)


def accepted(cfg: config.Config, ticket: int) -> dict | None:
    """Return the latest validated ``plan-bound`` event for a ticket, if any."""
    if type(ticket) is not int or ticket <= 0:
        raise BindingError("ticket number must be a positive integer")
    try:
        event = next((
            row
            for row in reversed(lifecycle.read_events(cfg.factory / "events.jsonl"))
            if row.get("event") == "plan-bound" and row.get("ticket") == ticket
        ), None)
    except OSError as exc:
        raise BindingError(f"ticket #{ticket} accepted plan binding evidence is unavailable", "source_unavailable") from exc
    if event is None:
        return None
    if type(event.get("schema_version")) is not int or event["schema_version"] != 1:
        raise BindingError(f"ticket #{ticket} has an unsupported accepted plan binding schema")
    if type(event.get("ticket")) is not int or event["ticket"] != ticket:
        raise BindingError(f"ticket #{ticket} has invalid accepted plan binding evidence")
    issue = event.get("issue")
    if (
        not isinstance(issue, dict)
        or set(issue) != {"title", "body", "comments"}
        or not isinstance(issue["title"], str)
        or not isinstance(issue["body"], str)
        or not isinstance(issue["comments"], list)
    ):
        raise BindingError(f"ticket #{ticket} has incomplete accepted plan binding evidence")
    stored = _canonical(event.get("baseline"), cfg.repo)
    pinned = baseline(issue["body"], cfg.repo)
    if pinned is None or pinned != stored or event["baseline"] != stored:
        raise BindingError(f"ticket #{ticket} has invalid accepted plan binding evidence")
    return event


def observe(cfg: config.Config, number: int) -> dict:
    """Read one complete REST issue body and produce its canonical relevant-section revision."""
    if type(number) is not int or number <= 0:
        raise BindingError("initiative number must be a positive integer")
    path = f"repos/{cfg.repo}/issues/{number}"
    try:
        issue, truncated = github_read(path, time.monotonic() + READ_SECONDS)
    except EvidenceError as exc:
        if exc.code == "response_too_large":
            raise BindingError(
                f"initiative #{number} source is incomplete: REST response exceeded the complete read limit",
                "incomplete_source",
            ) from exc
        raise BindingError(f"initiative #{number} source is unavailable: {exc.code}", "source_unavailable") from exc
    if truncated:
        raise BindingError(f"initiative #{number} source is incomplete: REST response was truncated", "incomplete_source")
    if not isinstance(issue, dict) or type(issue.get("number")) is not int or issue["number"] != number:
        raise BindingError(f"initiative #{number} source is incomplete: GitHub response has an unexpected shape", "incomplete_source")
    labels = issue.get("labels")
    if "pull_request" in issue or not isinstance(labels, list) or not any(
        isinstance(label, dict) and label.get("name") == config.LABEL_INITIATIVE for label in labels
    ):
        raise BindingError(f"issue #{number} is not an initiative")
    body = issue.get("body")
    if not isinstance(body, str):
        raise BindingError(f"initiative #{number} source is incomplete: complete issue body is missing", "incomplete_source")
    sections = _sections(body, number)
    return {
        "schema_version": 1,
        "initiative": number,
        "sections": sections,
        "sha256": _digest(sections),
        "source_url": f"https://github.com/{cfg.repo}/issues/{number}",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def admit(cfg: config.Config, body: str) -> dict | None:
    """Accept a linked ticket only when its pinned baseline still matches the initiative."""
    number = linked(body)
    if number is None:
        return None
    accepted = baseline(body, cfg.repo)
    if accepted is None:
        raise BindingError(f"linked ticket for initiative #{number} is missing a Plan baseline")
    observed = observe(cfg, number)
    if accepted["sha256"] != observed["sha256"]:
        raise BindingError(f"initiative #{number} relevant plan sections changed after this ticket was baselined")
    return accepted


def drift(cfg: config.Config, accepted: dict) -> dict:
    """Compare live relevant sections with accepted evidence without changing either source."""
    accepted = _canonical(accepted, cfg.repo)
    try:
        observed = observe(cfg, accepted["initiative"])
    except BindingError as exc:
        return {
            "status": "unavailable",
            "baseline": accepted,
            "observed": None,
            "changed_sections": [],
            "proposed_question": (
                f"Initiative #{accepted['initiative']} is unavailable. Can a human verify the source "
                "while the accepted ticket baseline remains unchanged?"
            ),
            "attribution": "unknown",
            "reason": str(exc),
            "error_code": exc.code,
        }
    changed = [name for name in SECTIONS if accepted["sections"][name] != observed["sections"][name]]
    status = "changed" if changed else "unchanged"
    question = None
    if changed:
        question = (
            f"Initiative #{accepted['initiative']} changed in {', '.join(changed)}. "
            "Should a new ticket be reviewed and baselined through intake?"
        )
    return {
        "status": status,
        "baseline": accepted,
        "observed": observed,
        "changed_sections": changed,
        "proposed_question": question,
        "attribution": "unknown",
    }


def render(value: dict) -> str:
    """Render a canonical binding for insertion into a human-reviewed ticket body."""
    value = _canonical(value)
    return (
        f"Initiative: #{value['initiative']}\n\n"
        "**Plan baseline**\n\n"
        "```json\n"
        f"{json.dumps(value, ensure_ascii=False, indent=2)}\n"
        "```"
    )
