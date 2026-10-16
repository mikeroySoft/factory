"""Revision-bound owner outcome attestations from canonical initiative comments."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from factory import binding, config, plan

KINDS = ("released", "installed", "healthy", "accepted")
MARKER = "<!-- factory-outcome:v1 -->"
DETAIL_KEYS = {"op", "number", "kind", "source_revision", "evidence_url", "summary"}
DATA_KEYS = {
    "schema_version", "initiative", "canonical_revision", "kind",
    "source_revision", "evidence_url", "summary",
}
SOURCE_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
CANONICAL_REVISION = re.compile(r"[0-9a-f]{64}")
MARKER_CANDIDATE = re.compile(r"<!--\s*factory-outcome\s*:", re.IGNORECASE)
SUMMARY_CAP = 2000
URL_CAP = 2048
COMMENT_CAP = 100


class OutcomeError(ValueError):
    """An outcome attestation request cannot be safely attributed."""

    def __init__(self, message: str, code: str = "invalid_details") -> None:
        super().__init__(message)
        self.code = code


def _issue_url(value: object, cfg: config.Config, initiative: int) -> bool:
    if not isinstance(value, str) or len(value) > URL_CAP:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "github.com"
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.casefold() == f"/{cfg.repo}/issues/{initiative}".casefold()
    )


def _initiative(cfg: config.Config, raw_issue: object) -> tuple[int, dict]:
    if not isinstance(raw_issue, dict):
        raise OutcomeError("outcome target must be a complete initiative issue", "invalid_issue")
    number = raw_issue.get("number")
    if type(number) is not int or number <= 0:
        raise OutcomeError("outcome target must identify a positive issue number", "invalid_issue")
    if not _issue_url(raw_issue.get("html_url"), cfg, number):
        raise OutcomeError("outcome target has an invalid source URL", "invalid_issue")
    try:
        return number, binding.from_issue(cfg, number, raw_issue)
    except binding.BindingError as exc:
        raise OutcomeError(str(exc), "invalid_issue") from exc


def _owner(raw_issue: dict) -> str:
    body = raw_issue.get("body")
    if not isinstance(body, str):
        raise OutcomeError("initiative owner is unavailable", "invalid_owner")
    declared = [text for name, text in plan._sections(body) if name == "Owner"]
    if len(declared) != 1:
        raise OutcomeError("initiative must declare exactly one Owner section", "invalid_owner")
    match = plan.LOGIN.fullmatch(declared[0])
    if match is None:
        raise OutcomeError("initiative Owner must be exactly one GitHub login", "invalid_owner")
    return match[1]


def _login(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = plan.LOGIN.fullmatch(value)
    return match[1] if match else None


def _plain_text(value: object, *, cap: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= cap
        and value == value.strip()
        and "\r" not in value
        and all(character in "\n\t" or not unicodedata.category(character).startswith("C")
                for character in value)
    )


def _source_revision(value: object) -> bool:
    return isinstance(value, str) and SOURCE_REVISION.fullmatch(value) is not None


def _evidence_url(value: object) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= URL_CAP or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not any(character.isspace() or unicodedata.category(character).startswith("C")
                    for character in value)
    )


def _validate_details(details: object, number: int) -> dict:
    if not isinstance(details, dict) or set(details) != DETAIL_KEYS:
        raise OutcomeError(
            "outcome details must contain exactly op, number, kind, source_revision, evidence_url, and summary",
            "invalid_details",
        )
    if details["op"] != "outcome" or type(details["number"]) is not int or details["number"] != number:
        raise OutcomeError("outcome details do not match the initiative target", "invalid_details")
    if details["kind"] not in KINDS:
        raise OutcomeError(f"outcome kind must be one of {', '.join(KINDS)}", "invalid_kind")
    if not _source_revision(details["source_revision"]):
        raise OutcomeError("source_revision must be a bounded explicit revision identity", "invalid_source_revision")
    if not _evidence_url(details["evidence_url"]):
        raise OutcomeError("evidence_url must be a bounded HTTPS URL", "invalid_evidence_url")
    if not _plain_text(details["summary"], cap=SUMMARY_CAP):
        raise OutcomeError(f"summary must be 1 to {SUMMARY_CAP} safe characters", "invalid_summary")
    return details


def _render(data: dict) -> str:
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{MARKER}\n\n"
        f"```json\n{encoded}\n```\n\n"
        f"**Factory outcome owner attestation — {data['kind']}**\n\n"
        f"{data['summary']}\n\n"
        f"Evidence: {data['evidence_url']}"
    )


def format_comment(cfg: config.Config, raw_issue: object, actor: object, details: object) -> str:
    """Validate an authenticated declared owner and render one canonical v1 comment."""
    number, canonical = _initiative(cfg, raw_issue)
    owner = _owner(raw_issue)
    authenticated = _login(actor)
    if authenticated is None or authenticated.casefold() != owner.casefold():
        raise OutcomeError(
            f"authenticated actor is not the declared owner of initiative #{number}",
            "unauthorized_actor",
        )
    request = _validate_details(details, number)
    return _render({
        "schema_version": 1,
        "initiative": number,
        "canonical_revision": canonical["sha256"],
        "kind": request["kind"],
        "source_revision": request["source_revision"],
        "evidence_url": request["evidence_url"],
        "summary": request["summary"],
    })


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _parse(body: str) -> dict | None:
    if MARKER_CANDIDATE.search(body) is None:
        return None
    prefix = f"{MARKER}\n\n```json\n"
    separator = "\n```\n\n"
    if not body.startswith(prefix) or separator not in body[len(prefix):]:
        raise ValueError("invalid marker envelope")
    encoded, _ = body[len(prefix):].split(separator, 1)
    try:
        data = json.loads(encoded, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError) as exc:
        raise ValueError("invalid marker data") from exc
    if (
        not isinstance(data, dict)
        or set(data) != DATA_KEYS
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or type(data["initiative"]) is not int
        or data["initiative"] <= 0
        or not isinstance(data["canonical_revision"], str)
        or CANONICAL_REVISION.fullmatch(data["canonical_revision"]) is None
        or data["kind"] not in KINDS
        or not _source_revision(data["source_revision"])
        or not _evidence_url(data["evidence_url"])
        or not _plain_text(data["summary"], cap=SUMMARY_CAP)
        or _render(data) != body
    ):
        raise ValueError("invalid marker data")
    return data


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _comment_url(value: object, cfg: config.Config, initiative: int, comment_id: int) -> bool:
    if not isinstance(value, str) or len(value) > URL_CAP:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "github.com"
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and parsed.path.casefold() == f"/{cfg.repo}/issues/{initiative}".casefold()
        and parsed.fragment == f"issuecomment-{comment_id}"
    )


def _evidence(row: dict, data: dict | None) -> dict:
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    return {
        "id": row.get("id") if type(row.get("id")) is int else None,
        "url": row.get("html_url") if isinstance(row.get("html_url"), str) else None,
        "created_at": row.get("created_at") if isinstance(row.get("created_at"), str) else None,
        "updated_at": row.get("updated_at") if isinstance(row.get("updated_at"), str) else None,
        "author": user.get("login") if isinstance(user.get("login"), str) else None,
        "initiative": data.get("initiative") if data else None,
        "kind": data.get("kind") if data else None,
        "canonical_revision": data.get("canonical_revision") if data else None,
        "source_revision": data.get("source_revision") if data else None,
        "evidence_url": data.get("evidence_url") if data else None,
        "summary": data.get("summary") if data else None,
        "status": "invalid",
        "reason": "invalid_marker_data",
    }


def project(cfg: config.Config, raw_issue: object, comments: object, *, complete: bool = True) -> dict:
    """Project bounded raw comments without converting owner reports into machine facts.

    ``comments=None`` means the comment source was unavailable. ``complete=False``
    preserves observed marker evidence but cannot establish a current attestation.
    """
    if type(complete) is not bool:
        raise OutcomeError("complete must be a boolean", "invalid_comments")
    number, canonical = _initiative(cfg, raw_issue)
    owner = _owner(raw_issue)
    missing = comments is None
    if comments is None:
        rows = []
    elif isinstance(comments, list):
        if len(comments) > COMMENT_CAP:
            raise OutcomeError(f"comments must contain at most {COMMENT_CAP} rows", "invalid_comments")
        rows = comments
    else:
        raise OutcomeError("comments must be a list or None", "invalid_comments")

    source_invalid = False
    marker_comments = 0
    evidence = []
    candidates: dict[str, list[tuple[datetime, int, dict]]] = {kind: [] for kind in KINDS}
    identities = set()
    identity_conflicts = set()
    for row in rows:
        row_id = row.get("id") if isinstance(row, dict) else None
        if type(row_id) is int and row_id > 0:
            if row_id in identities:
                identity_conflicts.add(row_id)
            identities.add(row_id)
        if not isinstance(row, dict) or not isinstance(row.get("body"), str):
            source_invalid = True
            continue
        body = row["body"]
        try:
            data = _parse(body)
        except ValueError:
            marker_comments += 1
            evidence.append(_evidence(row, None))
            continue
        if data is None:
            continue
        marker_comments += 1
        item = _evidence(row, data)
        comment_id = item["id"]
        created = _timestamp(item["created_at"])
        updated = _timestamp(item["updated_at"])
        author = _login(item["author"])
        if type(comment_id) is not int or comment_id <= 0:
            item["reason"] = "comment_identity_invalid"
        elif not _comment_url(item["url"], cfg, number, comment_id):
            item["reason"] = "comment_url_invalid"
        elif created is None or updated is None:
            item["reason"] = "comment_time_invalid"
        elif item["updated_at"] != item["created_at"]:
            item.update(status="edited", reason="comment_was_edited")
        elif author is None:
            item["reason"] = "comment_author_invalid"
        elif author.casefold() != owner.casefold():
            item.update(status="foreign_owner", reason="comment_author_is_not_declared_owner")
        elif data["initiative"] != number:
            item["reason"] = "initiative_mismatch"
        elif data["canonical_revision"] != canonical["sha256"]:
            item.update(status="stale", reason="canonical_revision_changed")
        else:
            item.update(status="current", reason=None)
            candidates[data["kind"]].append((created, comment_id, item))
        evidence.append(item)

    if identity_conflicts:
        source_invalid = True
        for item in evidence:
            if item["id"] in identity_conflicts:
                item.update(status="invalid", reason="comment_identity_conflict")
        for kind, found in candidates.items():
            candidates[kind] = [
                candidate for candidate in found if candidate[1] not in identity_conflicts
            ]

    winners = {}
    for kind, found in candidates.items():
        if not found:
            continue
        found.sort(key=lambda value: (value[0], value[1]))
        for _, _, item in found[:-1]:
            item.update(status="superseded", reason="newer_owner_attestation")
        winners[kind] = found[-1][2]

    # A changed later owner record must not resurrect an older success claim.
    # Malformed owner markers cannot safely identify a kind, so cover all kinds.
    for item in evidence:
        if (item["status"] not in {"edited", "invalid"}
                or (_login(item["author"]) or "").casefold() != owner.casefold()
                or type(item["id"]) is not int
                or not _comment_url(item["url"], cfg, number, item["id"])):
            continue
        changed_at = _timestamp(item["updated_at"])
        if changed_at is None:
            continue
        affected = (item["kind"],) if item["kind"] in KINDS else KINDS
        for kind in affected:
            winner = winners.get(kind)
            if winner and changed_at >= _timestamp(winner["created_at"]):
                winner.update(status="superseded", reason="later_owner_record_unusable")
                del winners[kind]

    if missing:
        coverage_status, coverage_reason = "unavailable", "comment_source_unavailable"
    elif not complete:
        coverage_status, coverage_reason = "partial", "comment_coverage_partial"
    elif source_invalid:
        coverage_status, coverage_reason = "partial", "comment_source_invalid"
    else:
        coverage_status, coverage_reason = "complete", None

    attributed = {}
    for kind in KINDS:
        current = winners.get(kind)
        if current is not None and coverage_status == "complete":
            attributed[kind] = {"status": "attested", "evidence": current, "reason": None}
        elif current is not None:
            attributed[kind] = {"status": "unknown", "evidence": current, "reason": coverage_reason}
        else:
            attributed[kind] = {
                "status": "unknown",
                "evidence": None,
                "reason": coverage_reason or "no_current_owner_attestation",
            }

    accepted = attributed["accepted"]["status"] == "attested"
    if accepted:
        detail = (
            f"Declared owner @{owner} attested acceptance for the current canonical revision; "
            "this is attributed owner evidence, not independent deployment or health verification."
        )
    elif coverage_status != "complete":
        detail = (
            "Outcome comment evidence is incomplete or unavailable; observed owner reports do not "
            "establish current acceptance."
        )
    else:
        detail = (
            "No current declared-owner acceptance attestation was established; release, installation, "
            "health, and acceptance remain separate states."
        )
    return {
        "schema_version": 1,
        "status": "owner_attested" if accepted else "unknown",
        "detail": detail,
        "initiative": number,
        "canonical_revision": canonical["sha256"],
        "owner": owner,
        "attributed": attributed,
        "verified": {
            "deployment": {"status": "unknown", "evidence": []},
            "health": {"status": "unknown", "evidence": []},
        },
        "evidence": evidence,
        "sources": [],
        "coverage": {
            "status": coverage_status,
            "complete": coverage_status == "complete",
            "comments": len(rows),
            "marker_comments": marker_comments,
            "reason": coverage_reason,
        },
    }
