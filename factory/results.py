"""Bounded retention for accepted worker handoffs.

Only ``.factory/wt-N/.factory/handoff-N.md`` is eligible.  Acceptance remains
owned by dispatch; this module reuses its SHA-bound predicates before storing
one immutable result bundle under the main checkout's state directory.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from factory import __version__

ARTIFACT_CAP = 256 * 1024
BUNDLE_CAP = 1024 * 1024
REPOSITORY_CAP = 256 * 1024 * 1024
MANIFEST_CAP = 16 * 1024
PAGE_CAP = 20_000
PAYLOAD_DAYS = 90
METADATA_DAYS = 365
SCAN_ENTRY_CAP = 20_000
HEAD_SCAN_CAP = 4_096

_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_CREDENTIAL = re.compile(
    rb"(?:\b(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{20,}"
    rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    rb"|\bBearer\s+[A-Za-z0-9._-]{24,})"
)
_TEMP = re.compile(r"\.(?:handoff\.md|manifest\.json)\.[1-9][0-9]*\.[0-9a-f]{16}\.tmp\Z")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_MANIFEST_KEYS = {
    "schema_version", "repository", "ticket", "accepted_head", "accepted_at",
    "captured_at", "expires_at", "metadata_expires_at", "producer",
    "acceptance", "contract", "source", "artifact",
}
_SOURCE_STATUSES = {
    "complete", "missing", "oversized", "privacy_withheld", "unsafe",
    "unreadable", "unstable", "partial", "truncated", "redacted", "unsupported",
}
_ARTIFACT_STATUSES = {
    "complete", "missing", "oversized", "privacy_withheld", "partial",
    "truncated", "redacted", "unsupported", "expired",
}
_INCOMPLETE_PROVENANCE = {"partial", "truncated", "redacted", "unsupported"}
_RESULT_SCHEMA = "factory.accepted-result"
_RECEIPT_FIELDS = (
    "at", "event", "ticket", "pr", "attempt", "round", "head", "gate_head",
    "review_head", "actual_head", "gate", "verdict", "accepted", "parsed",
    "decision", "event_id", "execution_id",
)


class _Unsafe(Exception):
    pass


class _TooLarge(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_ticket(ticket: object) -> bool:
    return type(ticket) is int and ticket > 0


def _valid_head(head: object) -> bool:
    return isinstance(head, str) and _SHA.fullmatch(head) is not None


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _open_dir(parent: int, name: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _Unsafe from exc
        raise


def _mkdir_open(parent: int, name: str) -> tuple[int, bool]:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        created = True
    except FileExistsError:
        pass
    if created:
        os.fsync(parent)
    return _open_dir(parent, name), created


def _state_dir(cfg: Any, *, create: bool) -> int:
    root = os.open(Path(cfg.root), _DIR_FLAGS)
    try:
        if create:
            factory, _ = _mkdir_open(root, ".factory")
        else:
            factory = _open_dir(root, ".factory")
    finally:
        os.close(root)
    return factory


def _results_dir(cfg: Any, *, create: bool) -> int:
    factory = _state_dir(cfg, create=create)
    try:
        if create:
            results, _ = _mkdir_open(factory, "results")
        else:
            results = _open_dir(factory, "results")
    finally:
        os.close(factory)
    return results


def _read_regular(directory: int, name: str, cap: int) -> bytes:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
            raise _Unsafe from exc
        raise
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise _Unsafe
        if before.st_size > cap:
            raise _TooLarge
        data = bytearray()
        while len(data) <= cap:
            block = os.read(fd, min(65_536, cap + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        if _file_identity(before) != _file_identity(after) or len(data) != before.st_size:
            raise _Unsafe
        if len(data) > cap:
            raise _TooLarge
        return bytes(data)
    finally:
        os.close(fd)


def _source_snapshot(cfg: Any, ticket: int) -> tuple[dict, bytes | None]:
    empty = {"status": "missing", "bytes": None, "sha256": None}
    root = factory = worktree = local = None
    try:
        root = os.open(Path(cfg.root), _DIR_FLAGS)
        factory = _open_dir(root, ".factory")
        worktree = _open_dir(factory, f"wt-{ticket}")
        local = _open_dir(worktree, ".factory")
        try:
            fd = os.open(f"handoff-{ticket}.md", _FILE_FLAGS, dir_fd=local)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                raise _Unsafe from exc
            raise
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise _Unsafe
            size = before.st_size
            if size > ARTIFACT_CAP:
                after = os.fstat(fd)
                if _file_identity(before) != _file_identity(after):
                    return {"status": "unstable", "bytes": size, "sha256": None}, None
                return {"status": "oversized", "bytes": size, "sha256": None}, None
            data = bytearray()
            while len(data) <= ARTIFACT_CAP:
                block = os.read(fd, min(65_536, ARTIFACT_CAP + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(fd)
            if _file_identity(before) != _file_identity(after) or len(data) != size:
                return {"status": "unstable", "bytes": size, "sha256": None}, None
            raw = bytes(data)
            digest = hashlib.sha256(raw).hexdigest()
            status = "privacy_withheld" if _CREDENTIAL.search(raw) else "complete"
            return {"status": status, "bytes": size, "sha256": digest}, raw
        finally:
            os.close(fd)
    except FileNotFoundError:
        return empty, None
    except _Unsafe:
        return {"status": "unsafe", "bytes": None, "sha256": None}, None
    except OSError:
        return {"status": "unreadable", "bytes": None, "sha256": None}, None
    finally:
        for fd in (local, worktree, factory, root):
            if fd is not None:
                os.close(fd)


def source_metadata(cfg: Any, ticket: int) -> dict:
    """Return bounded, content-free metadata for the current worker handoff."""
    if not _valid_ticket(ticket):
        return {"status": "unsafe", "bytes": None, "sha256": None}
    return _source_snapshot(cfg, ticket)[0]


def _manifest_bytes(manifest: dict) -> bytes:
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if len(payload) > MANIFEST_CAP:
        raise _TooLarge
    return payload


def _producer() -> dict:
    from factory.evidence import reader_build

    try:
        build = reader_build()
    except (OSError, RuntimeError, TypeError, ValueError):
        build = {}
    if not isinstance(build, dict):
        build = {}
    verified = build.get("verified") is True and _valid_head(build.get("revision"))
    return {
        "name": "factory.results",
        "version": __version__,
        "revision": build.get("revision").lower() if verified else None,
        "verified": verified,
        "schema": _RESULT_SCHEMA,
        "schema_version": 1,
    }


def _receipt(event: dict | None) -> dict | None:
    if event is None:
        return None
    return {key: event[key] for key in _RECEIPT_FIELDS if key in event}


def _contract(cfg: Any, events: list[dict], ticket: int) -> dict:
    from factory import binding

    row = next((
        event for event in reversed(events)
        if event.get("event") == "plan-bound" and event.get("ticket") == ticket
    ), None)
    empty = {
        "status": "unbound", "initiative": None, "sha256": None,
        "source_url": None, "observed_at": None,
    }
    if row is None:
        return empty
    issue = row.get("issue")
    try:
        if (
            type(row.get("schema_version")) is not int or row["schema_version"] != 1
            or type(row.get("ticket")) is not int or row["ticket"] != ticket
            or not isinstance(issue, dict)
            or set(issue) != {"title", "body", "comments"}
            or not isinstance(issue["title"], str)
            or not isinstance(issue["body"], str)
            or not isinstance(issue["comments"], list)
        ):
            raise binding.BindingError("invalid retained binding")
        baseline = binding._canonical(row.get("baseline"), cfg.repo)
        pinned = binding.baseline(issue["body"], cfg.repo)
        if pinned is None or pinned != baseline or row["baseline"] != baseline:
            raise binding.BindingError("invalid retained binding")
    except (binding.BindingError, KeyError, TypeError, ValueError):
        return {**empty, "status": "unsupported"}
    return {
        "status": "bound",
        "initiative": baseline["initiative"],
        "sha256": baseline["sha256"],
        "source_url": baseline["source_url"],
        "observed_at": baseline["observed_at"],
    }


def _acceptance(cfg: Any, ticket: int, head: str, events: list[dict]) -> tuple[dict, int] | None:
    # Lazy import avoids the dispatch -> results import cycle and, more importantly,
    # keeps one acceptance policy rather than reproducing it here.
    from factory import dispatch

    if not dispatch._head_evidence_matches(events, ticket, head):
        return None
    required = bool(cfg.manager and cfg.manager_review == "all")
    for index, event in enumerate(events):
        if (
            event.get("event") != "approved"
            or event.get("ticket") != ticket
            or event.get("head") != head
            or event.get("gate_head") != head
            or event.get("review_head") != head
            or type(event.get("pr")) is not int or event["pr"] <= 0
        ):
            continue
        prefix = events[:index]
        if (
            not dispatch._head_evidence_matches(prefix, ticket, head)
            or required and not dispatch.manager_approval(prefix, ticket, head)
        ):
            return None
        accepted = _parse_time(event.get("at"))
        if accepted is None:
            return None
        gate = review = None
        for evidence in prefix:
            if evidence.get("ticket") != ticket:
                continue
            if evidence.get("event") == "attempt" and evidence.get("head") == head:
                gate = evidence
            elif evidence.get("event") == "refreshed" and evidence.get("gate_head") == head:
                gate = evidence
            elif evidence.get("event") == "review" and evidence.get("head") == head:
                review = evidence
        manager = next((
            evidence for evidence in reversed(prefix)
            if evidence.get("event") == "manage"
            and evidence.get("ticket") == ticket
            and evidence.get("head") == head
            and evidence.get("decision") == "APPROVE"
        ), None) if required else None
        return {
            "accepted_at": accepted,
            "approved": event,
            "gate": gate,
            "review": review,
            "manager": manager,
            "manager_required": required,
        }, index
    return None


def _origin(events: list[dict], ticket: int, digest: str | None, size: int | None) -> tuple[str | None, str | None]:
    """Return the first matching attempt observation, not a claim of authorship."""
    if digest is None or size is None:
        return None, None
    for event in events:
        handoff = event.get("handoff")
        if (
            event.get("event") != "attempt" or event.get("ticket") != ticket
            or not isinstance(handoff, dict) or handoff.get("sha256") != digest
            or handoff.get("bytes") != size
        ):
            continue
        status = handoff.get("status")
        head = event.get("head")
        if status not in {"complete", "privacy_withheld", *_INCOMPLETE_PROVENANCE}:
            continue
        return (head.lower() if _valid_head(head) else None), status
    return None, None


def _build_manifest(
    cfg: Any, ticket: int, head: str, events: list[dict], acceptance: dict,
    approval_index: int, source: dict, captured_at: datetime,
) -> dict:
    source_head, provenance = _origin(
        events[:approval_index + 1], ticket, source.get("sha256"), source.get("bytes"),
    )
    source_status = source["status"]
    if provenance in _INCOMPLETE_PROVENANCE:
        source_status = provenance
    accepted_at = acceptance["accepted_at"]
    expires = accepted_at + timedelta(days=PAYLOAD_DAYS)
    metadata_expires = accepted_at + timedelta(days=METADATA_DAYS)
    if captured_at >= expires:
        artifact_status = "expired"
        artifact_reason = "handoff payload retention window elapsed before capture"
    elif source_status == "complete":
        artifact_status, artifact_reason = "complete", None
    elif source_status == "missing":
        artifact_status, artifact_reason = "missing", "handoff source was missing at acceptance"
    elif source_status == "oversized":
        artifact_status = "oversized"
        artifact_reason = f"handoff exceeds the {ARTIFACT_CAP}-byte artifact limit"
    elif source_status == "privacy_withheld":
        artifact_status = "privacy_withheld"
        artifact_reason = "handoff withheld because it may contain credential material"
    elif source_status in _INCOMPLETE_PROVENANCE:
        artifact_status = source_status
        artifact_reason = f"handoff provenance is explicitly {source_status}"
    else:
        artifact_status = "unsupported"
        artifact_reason = f"handoff source is {source_status}"
    artifact = {
        "status": artifact_status,
        "reason": artifact_reason,
        "truncated": False,
        "bytes": source["bytes"] if artifact_status == "complete" else 0,
        "sha256": source["sha256"] if artifact_status == "complete" else None,
    }
    manifest = {
        "schema_version": 1,
        "repository": cfg.repo,
        "ticket": ticket,
        "accepted_head": head,
        "accepted_at": _format_time(accepted_at),
        "captured_at": _format_time(captured_at),
        "expires_at": _format_time(expires),
        "metadata_expires_at": _format_time(metadata_expires),
        "producer": _producer(),
        "acceptance": {
            "manager_required": acceptance["manager_required"],
            "approved": _receipt(acceptance["approved"]),
            "gate": _receipt(acceptance["gate"]),
            "review": _receipt(acceptance["review"]),
            "manager": _receipt(acceptance["manager"]),
        },
        "contract": _contract(cfg, events[:approval_index + 1], ticket),
        "source": {
            "path": f".factory/wt-{ticket}/.factory/handoff-{ticket}.md",
            "head": source_head,
            "head_basis": (
                "first_matching_attempt_observation" if source_head is not None else "unknown"
            ),
            "status": source_status,
            "bytes": source["bytes"],
            "sha256": source["sha256"],
        },
        "artifact": artifact,
    }
    return manifest


def _valid_receipt(value: object, event: str, ticket: int) -> bool:
    return (
        isinstance(value, dict)
        and set(value) <= set(_RECEIPT_FIELDS)
        and value.get("event") == event
        and type(value.get("ticket")) is int and value["ticket"] == ticket
        and _parse_time(value.get("at")) is not None
        and all(
            item is None or type(item) in (bool, int, str)
            for item in value.values()
        )
        and all(
            key not in value or isinstance(value[key], str) and 0 < len(value[key]) <= 200
            for key in ("event_id", "execution_id")
        )
    )


def _validate_manifest(value: object, cfg: Any, ticket: int, head: str) -> dict | None:
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        return None
    if (
        type(value.get("schema_version")) is not int or value["schema_version"] != 1
        or value.get("repository") != cfg.repo
        or type(value.get("ticket")) is not int or value["ticket"] != ticket
        or value.get("accepted_head") != head
    ):
        return None
    accepted = _parse_time(value.get("accepted_at"))
    captured = _parse_time(value.get("captured_at"))
    expires = _parse_time(value.get("expires_at"))
    metadata_expires = _parse_time(value.get("metadata_expires_at"))
    if (
        None in (accepted, captured, expires, metadata_expires)
        or captured < accepted
        or expires != accepted + timedelta(days=PAYLOAD_DAYS)
        or metadata_expires != accepted + timedelta(days=METADATA_DAYS)
    ):
        return None
    producer = value.get("producer")
    acceptance = value.get("acceptance")
    approved = acceptance.get("approved") if isinstance(acceptance, dict) else None
    gate = acceptance.get("gate") if isinstance(acceptance, dict) else None
    review = acceptance.get("review") if isinstance(acceptance, dict) else None
    manager = acceptance.get("manager") if isinstance(acceptance, dict) else None
    contract = value.get("contract")
    source = value.get("source")
    artifact = value.get("artifact")
    if (
        not isinstance(producer, dict)
        or set(producer) != {
            "name", "version", "revision", "verified", "schema", "schema_version",
        }
        or producer.get("name") != "factory.results"
        or not isinstance(producer.get("version"), str) or len(producer["version"]) > 100
        or type(producer.get("verified")) is not bool
        or producer.get("schema") != _RESULT_SCHEMA
        or type(producer.get("schema_version")) is not int
        or producer["schema_version"] != 1
        or producer.get("revision") is not None and (
            not _valid_head(producer["revision"])
            or producer["revision"] != producer["revision"].lower()
        )
        or producer.get("verified") is not (producer.get("revision") is not None)
        or not isinstance(acceptance, dict)
        or set(acceptance) != {
            "manager_required", "approved", "gate", "review", "manager",
        }
        or type(acceptance.get("manager_required")) is not bool
        or not isinstance(contract, dict)
        or set(contract) != {"status", "initiative", "sha256", "source_url", "observed_at"}
        or contract.get("status") not in {"bound", "unbound", "unsupported"}
        or not isinstance(source, dict)
        or set(source) != {"path", "head", "head_basis", "status", "bytes", "sha256"}
        or source.get("path") != f".factory/wt-{ticket}/.factory/handoff-{ticket}.md"
        or source.get("status") not in _SOURCE_STATUSES
        or source.get("head") is not None and (
            not _valid_head(source["head"]) or source["head"] != source["head"].lower()
        )
        or source.get("head_basis") != (
            "first_matching_attempt_observation" if source.get("head") is not None else "unknown"
        )
        or source.get("bytes") is not None and (type(source["bytes"]) is not int or source["bytes"] < 0)
        or source.get("sha256") is not None and (
            not isinstance(source["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
        )
        or not isinstance(artifact, dict)
        or set(artifact) != {"status", "reason", "truncated", "bytes", "sha256"}
        or artifact.get("status") not in _ARTIFACT_STATUSES
        or artifact.get("reason") is not None and (
            not isinstance(artifact["reason"], str) or len(artifact["reason"]) > 300
        )
        or artifact.get("truncated") is not False
        or type(artifact.get("bytes")) is not int or not 0 <= artifact["bytes"] <= ARTIFACT_CAP
        or artifact.get("sha256") is not None and (
            not isinstance(artifact["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
        )
    ):
        return None
    if (
        not _valid_receipt(approved, "approved", ticket)
        or approved.get("head") != head
        or approved.get("gate_head") != head
        or approved.get("review_head") != head
        or type(approved.get("pr")) is not int or approved["pr"] <= 0
        or _parse_time(approved.get("at")) != accepted
        or not _valid_receipt(gate, gate.get("event") if isinstance(gate, dict) else "", ticket)
        or gate.get("event") not in {"attempt", "refreshed"}
        or gate.get("gate") != "PASS"
        or gate.get("actual_head") != head
        or gate.get("head" if gate.get("event") == "attempt" else "gate_head") != head
        or not _valid_receipt(review, "review", ticket)
        or review.get("head") != head
        or review.get("actual_head") != head
        or review.get("verdict") != "APPROVE"
        or review.get("accepted") is not True
        or review.get("parsed") is not True
    ):
        return None
    if acceptance["manager_required"]:
        if (
            not _valid_receipt(manager, "manage", ticket)
            or manager.get("head") != head
            or manager.get("decision") != "APPROVE"
        ):
            return None
    elif manager is not None:
        return None
    source_status = source["status"]
    if source_status in {"complete", "privacy_withheld", *_INCOMPLETE_PROVENANCE}:
        if (
            type(source["bytes"]) is not int or not 0 <= source["bytes"] <= ARTIFACT_CAP
            or source["sha256"] is None
        ):
            return None
    elif source_status == "oversized":
        if type(source["bytes"]) is not int or source["bytes"] <= ARTIFACT_CAP or source["sha256"] is not None:
            return None
    elif source_status == "unstable":
        if type(source["bytes"]) is not int or source["bytes"] < 0 or source["sha256"] is not None:
            return None
    elif source["bytes"] is not None or source["sha256"] is not None:
        return None
    if artifact["status"] == "complete" and artifact["reason"] is not None:
        return None
    if artifact["status"] != "complete" and not artifact["reason"]:
        return None
    if artifact["status"] == "complete":
        if artifact["sha256"] is None or source["sha256"] != artifact["sha256"] or source["bytes"] != artifact["bytes"]:
            return None
    elif artifact["bytes"] != 0 or artifact["sha256"] is not None:
        return None
    if contract["status"] == "bound":
        initiative = contract.get("initiative")
        if (
            type(initiative) is not int or initiative <= 0
            or not isinstance(contract.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", contract["sha256"]) is None
            or contract.get("source_url") != f"https://github.com/{cfg.repo}/issues/{initiative}"
            or _parse_time(contract.get("observed_at")) is None
        ):
            return None
    elif any(contract.get(key) is not None for key in ("initiative", "sha256", "source_url", "observed_at")):
        return None
    return value


def _load_manifest(directory: int, cfg: Any, ticket: int, head: str) -> tuple[dict, bytes]:
    raw = _read_regular(directory, "manifest.json", MANIFEST_CAP)
    if _CREDENTIAL.search(raw):
        raise _Unsafe
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError):
        raise _Unsafe from None
    manifest = _validate_manifest(value, cfg, ticket, head)
    if manifest is None:
        raise _Unsafe
    return manifest, raw


def _entry_names(directory: int) -> list[str]:
    names = []
    with os.scandir(directory) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > SCAN_ENTRY_CAP:
                raise _TooLarge
    return names


def _usage(directory: int) -> int:
    count = 0

    def walk(parent: int, depth: int) -> int:
        nonlocal count
        total = 0
        with os.scandir(parent) as entries:
            for entry in entries:
                count += 1
                if count > SCAN_ENTRY_CAP:
                    raise _TooLarge
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                elif stat.S_ISDIR(info.st_mode) and depth < 2:
                    child = _open_dir(parent, entry.name)
                    try:
                        total += walk(child, depth + 1)
                    finally:
                        os.close(child)
                else:
                    raise _Unsafe
        return total

    return walk(directory, 0)


def _recover_temps(results: int) -> None:
    """Remove only staging names created by _atomic_write; callers hold the writer lock."""
    for ticket_name in _entry_names(results):
        if ticket_name == ".writer.lock":
            continue
        if not ticket_name.isdecimal() or int(ticket_name) <= 0:
            raise _Unsafe
        ticket_fd = _open_dir(results, ticket_name)
        try:
            heads = _entry_names(ticket_fd)
            if len(heads) > HEAD_SCAN_CAP:
                raise _TooLarge
            for head in heads:
                if not _valid_head(head) or head != head.lower():
                    raise _Unsafe
                result_fd = _open_dir(ticket_fd, head)
                remove_empty = False
                try:
                    names = _entry_names(result_fd)
                    removed = False
                    for name in names:
                        if _TEMP.fullmatch(name) is None:
                            continue
                        info = os.stat(name, dir_fd=result_fd, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            raise _Unsafe
                        os.unlink(name, dir_fd=result_fd)
                        removed = True
                    if removed:
                        os.fsync(result_fd)
                    remove_empty = bool(names) and all(
                        _TEMP.fullmatch(name) is not None for name in names
                    )
                finally:
                    os.close(result_fd)
                if remove_empty:
                    os.rmdir(head, dir_fd=ticket_fd)
                    os.fsync(ticket_fd)
        finally:
            os.close(ticket_fd)


def _atomic_write(directory: int, name: str, payload: bytes) -> None:
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    fd = None
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory,
        )
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
        raise


def _remove_expired(results: int, cfg: Any, now: datetime) -> bool:
    complete = True
    tickets = _entry_names(results)
    for ticket_name in tickets:
        if ticket_name == ".writer.lock":
            continue
        if not ticket_name.isdecimal() or int(ticket_name) <= 0:
            raise _Unsafe
        ticket = int(ticket_name)
        ticket_fd = _open_dir(results, ticket_name)
        try:
            heads = _entry_names(ticket_fd)
            if len(heads) > HEAD_SCAN_CAP:
                raise _TooLarge
            for head in heads:
                if not _valid_head(head) or head != head.lower():
                    raise _Unsafe
                result_fd = None
                remove_head = False
                try:
                    result_fd = _open_dir(ticket_fd, head)
                    names = set(_entry_names(result_fd))
                    if "manifest.json" not in names or not names <= {
                        "manifest.json", "handoff.md",
                    }:
                        complete = False
                        continue
                    manifest, _ = _load_manifest(result_fd, cfg, ticket, head)
                    expires = _parse_time(manifest["expires_at"])
                    metadata_expires = _parse_time(manifest["metadata_expires_at"])
                    artifact_complete = manifest["artifact"]["status"] == "complete"
                    if (
                        now < expires and artifact_complete != ("handoff.md" in names)
                        or not artifact_complete and "handoff.md" in names
                    ):
                        complete = False
                        continue
                    if now >= metadata_expires:
                        for name in names:
                            info = os.stat(name, dir_fd=result_fd, follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode):
                                raise _Unsafe
                        for name in names:
                            os.unlink(name, dir_fd=result_fd)
                        os.fsync(result_fd)
                        remove_head = True
                    elif now >= expires and "handoff.md" in names:
                        info = os.stat("handoff.md", dir_fd=result_fd, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            raise _Unsafe
                        os.unlink("handoff.md", dir_fd=result_fd)
                        os.fsync(result_fd)
                except (FileNotFoundError, PermissionError, OSError, _Unsafe, _TooLarge,
                        ValueError, TypeError):
                    complete = False
                    continue
                finally:
                    if result_fd is not None:
                        os.close(result_fd)
                if remove_head:
                    try:
                        os.rmdir(head, dir_fd=ticket_fd)
                        os.fsync(ticket_fd)
                    except OSError:
                        complete = False
            try:
                os.rmdir(ticket_name, dir_fd=results)
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    complete = False
        finally:
            os.close(ticket_fd)
    os.fsync(results)
    return complete


def _open_result(results: int, ticket: int, head: str) -> tuple[int, int]:
    ticket_fd = _open_dir(results, str(ticket))
    try:
        return ticket_fd, _open_dir(ticket_fd, head)
    except BaseException:
        os.close(ticket_fd)
        raise


def _existing_retention(
    result_fd: int, cfg: Any, ticket: int, head: str, source: dict, now: datetime,
) -> dict:
    try:
        manifest, _ = _load_manifest(result_fd, cfg, ticket, head)
    except (FileNotFoundError, OSError, _Unsafe, _TooLarge):
        return {"status": "unavailable", "cleanup_safe": False,
                "reason": "existing retained result is incomplete or unsafe", "manifest": None}
    current_missing = source["status"] == "missing"
    expires = _parse_time(manifest["expires_at"])
    artifact = manifest["artifact"]
    if now >= expires:
        safe = current_missing
        return {"status": "partial", "cleanup_safe": safe,
                "reason": "retained handoff payload has expired", "manifest": manifest}
    if artifact["status"] != "complete":
        safe = current_missing
        return {"status": "partial", "cleanup_safe": safe,
                "reason": artifact["reason"], "manifest": manifest}
    try:
        payload = _read_regular(result_fd, "handoff.md", ARTIFACT_CAP)
    except (FileNotFoundError, OSError, _Unsafe, _TooLarge):
        return {"status": "unavailable", "cleanup_safe": False,
                "reason": "retained handoff payload is missing or unsafe", "manifest": manifest}
    if len(payload) != artifact["bytes"] or hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        return {"status": "unavailable", "cleanup_safe": False,
                "reason": "retained handoff payload failed its integrity check", "manifest": manifest}
    safe = current_missing or (
        source["status"] == "complete"
        and source["bytes"] == artifact["bytes"]
        and source["sha256"] == artifact["sha256"]
    )
    return {
        "status": "complete" if safe else "partial",
        "cleanup_safe": safe,
        "reason": None if safe else "current handoff differs from the immutable retained artifact",
        "manifest": manifest,
    }


def retain(cfg: Any, ticket: int, head: str, events: list[dict]) -> dict:
    """Retain one accepted handoff and say whether destructive cleanup is safe."""
    def unavailable(reason: str) -> dict:
        return {
            "status": "unavailable", "cleanup_safe": False,
            "reason": reason, "manifest": None,
        }

    if not _valid_ticket(ticket) or not _valid_head(head):
        return unavailable("invalid ticket or accepted head")
    head = head.lower()
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        return unavailable("acceptance evidence is unavailable")
    try:
        accepted = _acceptance(cfg, ticket, head, events)
    except (OSError, ValueError, TypeError):
        return unavailable("acceptance evidence is unavailable")
    if accepted is None:
        return unavailable("exact SHA-bound acceptance evidence is unavailable")
    acceptance, approval_index = accepted
    now = _now()
    if acceptance["accepted_at"] > now:
        return unavailable("accepted result timestamp is in the future")
    if now >= acceptance["accepted_at"] + timedelta(days=METADATA_DAYS):
        return unavailable("accepted result metadata retention window has elapsed")
    source, payload = _source_snapshot(cfg, ticket)
    results = lock_fd = ticket_fd = result_fd = None
    created_ticket = created_result = False
    try:
        results = _results_dir(cfg, create=True)
        lock_fd = os.open(
            ".writer.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=results,
        )
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise _Unsafe
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _usage(results)
        _recover_temps(results)
        _remove_expired(results, cfg, now)
        try:
            ticket_fd, result_fd = _open_result(results, ticket, head)
        except FileNotFoundError:
            ticket_fd, created_ticket = _mkdir_open(results, str(ticket))
            try:
                result_fd = _open_dir(ticket_fd, head)
            except FileNotFoundError:
                result_fd, created_result = _mkdir_open(ticket_fd, head)
        if not created_result:
            return _existing_retention(result_fd, cfg, ticket, head, source, now)
        manifest = _build_manifest(
            cfg, ticket, head, events, acceptance, approval_index, source, now,
        )
        manifest_raw = _manifest_bytes(manifest)
        if _validate_manifest(manifest, cfg, ticket, head) is None or _CREDENTIAL.search(manifest_raw):
            raise _Unsafe
        artifact = manifest["artifact"]
        stored = payload if artifact["status"] == "complete" else None
        if stored is not None and len(stored) > ARTIFACT_CAP:
            raise _TooLarge
        bundle_size = len(manifest_raw) + (len(stored) if stored is not None else 0)
        if bundle_size > BUNDLE_CAP:
            raise _TooLarge
        if _usage(results) + bundle_size > REPOSITORY_CAP:
            raise _TooLarge
        if stored is not None:
            _atomic_write(result_fd, "handoff.md", stored)
        try:
            _atomic_write(result_fd, "manifest.json", manifest_raw)
        except BaseException:
            if stored is not None:
                try:
                    os.unlink("handoff.md", dir_fd=result_fd)
                    os.fsync(result_fd)
                except OSError:
                    pass
            raise
        complete = artifact["status"] == "complete"
        cleanup_safe = complete or source["status"] == "missing"
        return {
            "status": "complete" if complete else "partial",
            "cleanup_safe": cleanup_safe,
            "reason": artifact["reason"],
            "manifest": manifest,
        }
    except _TooLarge:
        reason = "retained results quota or size limit is exhausted"
    except (FileNotFoundError, PermissionError, OSError, _Unsafe, ValueError, TypeError):
        reason = "retained result storage is unavailable or unsafe"
    finally:
        if result_fd is not None:
            os.close(result_fd)
        if ticket_fd is not None:
            if created_result:
                try:
                    os.rmdir(head, dir_fd=ticket_fd)
                except OSError:
                    pass
            os.close(ticket_fd)
        if results is not None and created_ticket:
            try:
                os.rmdir(str(ticket), dir_fd=results)
            except OSError:
                pass
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        if results is not None:
            os.close(results)
    return unavailable(reason)


def prune(cfg: Any) -> dict:
    """Apply physical expiry on a dispatcher pass; never create an absent archive."""
    results = lock_fd = None
    try:
        results = _results_dir(cfg, create=False)
    except FileNotFoundError:
        return {"status": "complete", "reason": None}
    except (PermissionError, OSError, _Unsafe):
        return {"status": "unavailable", "reason": "retained result expiry is unavailable or unsafe"}
    try:
        names = _entry_names(results)
        has_archive = any(name != ".writer.lock" for name in names)
        if not has_archive and ".writer.lock" not in names:
            return {"status": "complete", "reason": None}
        flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        if has_archive:
            flags |= os.O_CREAT
        lock_fd = os.open(".writer.lock", flags, 0o600, dir_fd=results)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise _Unsafe
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if has_archive:
            _usage(results)
            _recover_temps(results)
            if not _remove_expired(results, cfg, _now()):
                return {
                    "status": "unavailable",
                    "reason": "one or more retained result bundles could not be safely expired",
                }
        return {"status": "complete", "reason": None}
    except _TooLarge:
        return {"status": "unavailable", "reason": "retained result scan exceeded its bound"}
    except (FileNotFoundError, PermissionError, OSError, _Unsafe, ValueError, TypeError):
        return {"status": "unavailable", "reason": "retained result expiry is unavailable or unsafe"}
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(results)


def _read_response(
    status: str, reason: str | None, manifest: dict | None, *, text: str = "",
    offset: int = 0, next_offset: int | None = None,
) -> dict:
    return {
        "status": status,
        "reason": reason,
        "manifest": manifest,
        "text": text,
        "offset": offset,
        "next_offset": next_offset,
        "truncated": next_offset is not None,
    }


def read_result(cfg: Any, ticket: int, head: str, offset: int = 0) -> dict:
    """Read one integrity-checked UTF-8 page without mutating retention state."""
    if (
        not _valid_ticket(ticket) or not _valid_head(head)
        or type(offset) is not int or offset < 0
    ):
        return _read_response(
            "unavailable", "invalid ticket, head, or byte offset", None, offset=0,
        )
    head = head.lower()
    results = ticket_fd = result_fd = None
    try:
        results = _results_dir(cfg, create=False)
        ticket_fd, result_fd = _open_result(results, ticket, head)
    except FileNotFoundError:
        if results is not None:
            os.close(results)
        return _read_response(
            "missing", "no retained result exists for this accepted head", None, offset=offset,
        )
    except (PermissionError, OSError, _Unsafe):
        if results is not None:
            os.close(results)
        return _read_response(
            "unavailable", "retained result is unavailable or unsafe", None, offset=offset,
        )
    try:
        manifest, _ = _load_manifest(result_fd, cfg, ticket, head)
        now = _now()
        if now >= _parse_time(manifest["metadata_expires_at"]):
            return _read_response(
                "expired", "retained result metadata has expired", None, offset=offset,
            )
        if now >= _parse_time(manifest["expires_at"]):
            return _read_response(
                "expired", "retained handoff payload has expired", manifest, offset=offset,
            )
        artifact = manifest["artifact"]
        if artifact["status"] != "complete":
            status = artifact["status"]
            if status in {"oversized", "partial", "truncated", "redacted", "unsupported"}:
                status = "partial"
            return _read_response(status, artifact["reason"], manifest, offset=offset)
        payload = _read_regular(result_fd, "handoff.md", ARTIFACT_CAP)
        if (
            len(payload) != artifact["bytes"]
            or hashlib.sha256(payload).hexdigest() != artifact["sha256"]
        ):
            return _read_response(
                "tampered", "retained handoff failed its integrity check", manifest,
                offset=offset,
            )
        if _CREDENTIAL.search(payload):
            return _read_response(
                "privacy_withheld", "retained handoff may contain credential material",
                manifest, offset=offset,
            )
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            return _read_response(
                "unavailable", "retained handoff is not valid UTF-8", manifest, offset=offset,
            )
        if offset > len(payload):
            return _read_response(
                "unavailable", "byte offset is beyond the retained handoff",
                manifest, offset=offset,
            )
        if offset < len(payload) and payload[offset] & 0xC0 == 0x80:
            return _read_response(
                "unavailable", "byte offset is not a UTF-8 boundary",
                manifest, offset=offset,
            )
        end = min(len(payload), offset + PAGE_CAP)
        while end > offset and end < len(payload) and payload[end] & 0xC0 == 0x80:
            end -= 1
        text = payload[offset:end].decode("utf-8")
        next_offset = end if end < len(payload) else None
        return _read_response(
            "complete", None, manifest, text=text, offset=offset, next_offset=next_offset,
        )
    except FileNotFoundError:
        return _read_response(
            "unavailable", "retained result is incomplete", None, offset=offset,
        )
    except _TooLarge:
        return _read_response(
            "tampered", "retained result exceeds its bounded schema", None, offset=offset,
        )
    except (PermissionError, OSError, _Unsafe, ValueError, TypeError):
        return _read_response(
            "unavailable", "retained result is unavailable or unsafe", None, offset=offset,
        )
    finally:
        os.close(result_fd)
        os.close(ticket_fd)
        os.close(results)


def latest_result(cfg: Any, ticket: int) -> dict | None:
    """Read the newest historical accepted result after a bounded manifest-only scan."""
    if not _valid_ticket(ticket):
        return _read_response("unavailable", "invalid ticket", None)
    results = ticket_fd = None
    try:
        results = _results_dir(cfg, create=False)
    except FileNotFoundError:
        return None
    except (PermissionError, OSError, _Unsafe):
        return _read_response("unavailable", "retained result history is unavailable or unsafe", None)
    try:
        try:
            ticket_fd = _open_dir(results, str(ticket))
        except FileNotFoundError:
            return None
        names = _entry_names(ticket_fd)
        if len(names) > HEAD_SCAN_CAP:
            raise _TooLarge
        newest: tuple[datetime, str] | None = None
        for head in names:
            if not _valid_head(head) or head != head.lower():
                raise _Unsafe
            result_fd = _open_dir(ticket_fd, head)
            try:
                manifest, _ = _load_manifest(result_fd, cfg, ticket, head)
            finally:
                os.close(result_fd)
            accepted = _parse_time(manifest["accepted_at"])
            if newest is None or (accepted, head) > newest:
                newest = accepted, head
        if newest is None:
            return None
        selected = newest[1]
    except FileNotFoundError:
        return _read_response("unavailable", "retained result history is incomplete", None)
    except _TooLarge:
        return _read_response("unavailable", "retained result scan exceeded its bound", None)
    except (PermissionError, OSError, _Unsafe, ValueError, TypeError):
        return _read_response("unavailable", "retained result history is unavailable or unsafe", None)
    finally:
        if ticket_fd is not None:
            os.close(ticket_fd)
        os.close(results)
    return read_result(cfg, ticket, selected)
