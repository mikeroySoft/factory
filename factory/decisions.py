"""Restart-safe, confirmed human decisions for one configured repository."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factory import config, dispatch, lifecycle
from factory.config import (
    LABEL_AGENT,
    LABEL_APPROVED,
    LABEL_HUMAN,
    LABEL_INFO,
    LABEL_TRIAGE,
    Config,
)

PREPARE_SECONDS = 30
APPLY_SECONDS = 150
COMMAND_SECONDS = 120
PROPOSAL_SECONDS = 300
OUTPUT_CAP = 8_000
READ_CAP = 256 * 1024
REQUEST_CAP = 64 * 1024
MAX_REQUESTS = 16
COMMENT_CAP = 20_000
SUMMARY_CAP = 4_000

ACT_LABELS = frozenset({
    LABEL_TRIAGE,
    LABEL_INFO,
    LABEL_AGENT,
    LABEL_HUMAN,
    "wontfix",
    LABEL_APPROVED,
})
CLOSE_REASONS = frozenset({"completed", "not planned"})
_OUTCOME_KEYS = frozenset({
    "op", "number", "kind", "source_revision", "evidence_url", "summary",
})
_AGENT_BRANCH = re.compile(r"agent/([1-9][0-9]*)\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")
_SECRET = re.compile(
    r"(?:\b(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{12,}"
    r"|\bBearer\s+[A-Za-z0-9._-]{12,})",
    re.IGNORECASE,
)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class DecisionError(ValueError):
    """A closed, safe-to-return decision error."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


class _AmbiguousCommand(Exception):
    pass


class _FailedStep(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise DecisionError("request is not bounded JSON", "invalid_request") from exc


def _open_dir(parent: int, name: str) -> int:
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise DecisionError("decision storage contains an unsafe path", "unsafe_storage") from exc
        raise
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise DecisionError("decision storage is not owned by the service user", "unsafe_storage")
    return fd


def _mkdir_open(parent: int, name: str, mode: int = 0o700) -> int:
    try:
        os.mkdir(name, mode, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        pass
    return _open_dir(parent, name)


def _state_dir(cfg: Config, child: str, *, create: bool) -> int:
    root = factory = directory = None
    try:
        root = os.open(Path(cfg.root), _DIR_FLAGS)
        if os.fstat(root).st_uid != os.geteuid():
            raise DecisionError("repository root is not owned by the service user", "unsafe_storage")
        factory = _mkdir_open(root, ".factory") if create else _open_dir(root, ".factory")
        directory = _mkdir_open(factory, child) if create else _open_dir(factory, child)
        if os.fstat(directory).st_mode & 0o022:
            raise DecisionError("decision coordination directory is writable by another user", "unsafe_storage")
        result, directory = directory, None
        return result
    except FileNotFoundError as exc:
        raise DecisionError("decision storage is unavailable", "storage_unavailable") from exc
    except OSError as exc:
        raise DecisionError("decision storage is unavailable", "storage_unavailable") from exc
    finally:
        if directory is not None:
            os.close(directory)
        if factory is not None:
            os.close(factory)
        if root is not None:
            os.close(root)


def _read_artifact(directory: int, name: str, *, missing: bool = False) -> dict | None:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        if missing:
            return None
        raise DecisionError("decision artifact was not found", "not_found") from None
    except OSError as exc:
        raise DecisionError("decision artifact is unsafe", "unsafe_storage") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o222
            or info.st_size > READ_CAP
        ):
            raise DecisionError("decision artifact is unsafe", "unsafe_storage")
        data = bytearray()
        while len(data) <= READ_CAP:
            chunk = os.read(fd, min(65_536, READ_CAP + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > READ_CAP:
            raise DecisionError("decision artifact is too large", "unsafe_storage")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise DecisionError("decision artifact is invalid", "unsafe_storage") from exc
    finally:
        os.close(fd)


def _store_artifact(directory: int, name: str, value: dict) -> None:
    payload = _json(value)
    if len(payload) > READ_CAP:
        raise DecisionError("decision artifact is too large", "storage_unavailable")
    temporary = f".tmp-{name}-{secrets.token_hex(16)}"
    try:
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o400,
                dir_fd=directory,
            )
        except OSError as exc:
            raise DecisionError("decision artifact could not be persisted", "storage_unavailable") from exc
        try:
            view = memoryview(payload)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError("short write")
                view = view[count:]
            os.fsync(fd)
        except OSError as exc:
            raise DecisionError("decision artifact could not be persisted", "storage_unavailable") from exc
        finally:
            os.close(fd)
        try:
            os.link(
                temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise DecisionError("immutable decision artifact already exists", "conflict") from exc
        except OSError as exc:
            raise DecisionError("decision artifact could not be published", "storage_unavailable") from exc
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError as exc:
            raise DecisionError("decision artifact could not be published", "storage_unavailable") from exc
        temporary = ""
        try:
            os.fsync(directory)
        except OSError as exc:
            raise DecisionError("decision artifact directory could not be persisted", "storage_unavailable") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass


def _artifact_name(kind: str, proposal_id: str) -> str:
    if not isinstance(proposal_id, str) or _ID.fullmatch(proposal_id) is None:
        raise DecisionError("proposal_id is invalid", "invalid_proposal")
    return f"{kind}-{proposal_id}.json"


def _environment(cfg: Config) -> dict[str, str]:
    return {
        **config.dashboard_env(cfg),
        "GH_PROMPT_DISABLED": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GH_PAGER": "cat",
    }


def _run(
    argv: list[str], *, env: dict[str, str], cwd: Path | None, timeout: float, cap: int,
) -> subprocess.CompletedProcess[str]:
    """Run fixed argv with bounded time and pipes; clipping is an ambiguous mutation result."""
    if timeout <= 0:
        raise _AmbiguousCommand("command deadline expired")
    process = None
    output, diagnostic = bytearray(), bytearray()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, diagnostic)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not (ready := selector.select(remaining)):
                    raise _AmbiguousCommand("command timed out")
                for key, _ in ready:
                    target = key.data
                    chunk = os.read(key.fd, min(65_536, cap + 1 - len(target)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        target.extend(chunk)
                        if len(target) > cap:
                            raise _AmbiguousCommand("command output exceeded its bound")
        status = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(
            argv,
            status,
            output.decode(errors="replace"),
            diagnostic.decode(errors="replace"),
        )
    except subprocess.TimeoutExpired:
        raise _AmbiguousCommand("command timed out") from None
    except OSError as exc:
        if process is None:
            raise DecisionError("required command is unavailable", "command_unavailable") from exc
        raise _AmbiguousCommand("command transport failed") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            process.stdout.close()
            process.stderr.close()


def _timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DecisionError("decision deadline exceeded", "deadline_exceeded")
    return min(COMMAND_SECONDS, remaining)


def _checked(
    cfg: Config, argv: list[str], deadline: float, *, cwd: Path | None = None, cap: int = READ_CAP,
) -> subprocess.CompletedProcess[str]:
    try:
        result = _run(
            argv, env=_environment(cfg), cwd=cwd, timeout=_timeout(deadline), cap=cap,
        )
    except _AmbiguousCommand as exc:
        raise DecisionError("provider read did not complete", "provider_unavailable") from exc
    if result.returncode:
        raise DecisionError("provider read failed", "provider_unavailable")
    return result


def _read_json(
    cfg: Config, argv: list[str], deadline: float, *, cwd: Path | None = None,
) -> object:
    result = _checked(cfg, argv, deadline, cwd=cwd)
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError, RecursionError) as exc:
        raise DecisionError("provider returned invalid JSON", "provider_unavailable") from exc


def _actor(cfg: Config, deadline: float) -> str:
    result = _checked(
        cfg,
        ["gh", "api", "--hostname", "github.com", "--method", "GET", "user", "--jq", ".login"],
        deadline,
        cap=1_024,
    )
    actor = result.stdout.strip()
    if not actor or len(actor) > 39 or _LOGIN.fullmatch(actor) is None:
        raise DecisionError("authenticated GitHub actor is unavailable", "actor_unavailable")
    return actor


def _number(value: object) -> int:
    if type(value) is not int or not 0 < value < 2**31:
        raise DecisionError("number must be a positive integer")
    return value


def _labels(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(label, str) or label not in ACT_LABELS for label in value
    ):
        raise DecisionError(f"{key} must be a list of supported labels")
    if len(value) != len(set(value)):
        raise DecisionError(f"{key} contains duplicate labels")
    return list(value)


def _comment(value: object) -> str:
    if not isinstance(value, str) or len(value) > COMMENT_CAP or "\x00" in value:
        raise DecisionError(f"comment must be a string of at most {COMMENT_CAP} characters without NUL")
    return value


def _normalize_request(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise DecisionError("each request must be a JSON object")
    op = raw.get("op")
    number = _number(raw.get("number"))
    if op == "outcome":
        if set(raw) != _OUTCOME_KEYS:
            raise DecisionError("outcome request fields are closed")
        details = dict(raw)
        if not isinstance(details["kind"], str):
            raise DecisionError("outcome kind must be a string")
        if not isinstance(details["source_revision"], str):
            raise DecisionError("outcome source_revision must be a string")
        if not isinstance(details["evidence_url"], str):
            raise DecisionError("outcome evidence_url must be a string")
        if not isinstance(details["summary"], str) or len(details["summary"]) > SUMMARY_CAP:
            raise DecisionError(f"outcome summary must be a string of at most {SUMMARY_CAP} characters")
        return details
    allowed = {
        "issue": {"op", "number", "comment", "add", "remove", "assign", "unassign", "close"},
        "pr": {"op", "number", "comment", "add", "remove"},
        "triage": {"op", "number"},
        "cleanup": {"op", "number"},
    }.get(op)
    if allowed is None:
        raise DecisionError(f"unsupported decision operation {op!r}")
    if not set(raw) <= allowed:
        raise DecisionError(f"{op} request fields are closed")
    if op in {"triage", "cleanup"}:
        if set(raw) != allowed:
            raise DecisionError(f"{op} requires exactly op and number")
        return {"op": op, "number": number}
    request = {"op": op, "number": number}
    if "comment" in raw:
        request["comment"] = _comment(raw["comment"])
    add = _labels(raw.get("add", []), "add")
    remove = _labels(raw.get("remove", []), "remove")
    if set(add) & set(remove):
        raise DecisionError("the same label cannot be added and removed")
    if add:
        request["add"] = add
    if remove:
        request["remove"] = remove
    if op == "issue":
        for key in ("assign", "unassign"):
            if key in raw:
                if type(raw[key]) is not bool:
                    raise DecisionError(f"{key} must be boolean")
                request[key] = raw[key]
        if request.get("assign") and request.get("unassign"):
            raise DecisionError("assign and unassign cannot both be true")
        if "close" in raw:
            if not isinstance(raw["close"], str) or raw["close"] not in CLOSE_REASONS:
                raise DecisionError(f"close must be one of {sorted(CLOSE_REASONS)}")
            request["close"] = raw["close"]
    if not (
        request.get("comment", "").strip()
        or request.get("add")
        or request.get("remove")
        or request.get("assign")
        or request.get("unassign")
        or request.get("close")
    ):
        raise DecisionError("request has no supported effect")
    return request


def _normalize_requests(requests: object) -> list[dict]:
    if not isinstance(requests, list) or not 0 < len(requests) <= MAX_REQUESTS:
        raise DecisionError(f"requests must contain between 1 and {MAX_REQUESTS} actions")
    normalized = [_normalize_request(request) for request in requests]
    if len(_json(normalized)) > REQUEST_CAP:
        raise DecisionError("requests exceed the decision input bound")
    return normalized


def _target_labels(value: object) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(label, dict) or not isinstance(label.get("name"), str) for label in value
    ):
        raise DecisionError("provider returned invalid labels", "provider_unavailable")
    return sorted({label["name"] for label in value})


def _issue_target(cfg: Config, number: int, deadline: float) -> tuple[dict, dict]:
    fields = "number,title,body,state,labels,assignees,updatedAt,url"
    raw = _read_json(
        cfg,
        ["gh", "issue", "view", str(number), "--repo", cfg.repo, "--json", fields],
        deadline,
    )
    if (
        not isinstance(raw, dict)
        or raw.get("number") != number
        or not isinstance(raw.get("title"), str)
        or not isinstance(raw.get("body"), str)
        or raw.get("state") not in {"OPEN", "CLOSED"}
        or not isinstance(raw.get("updatedAt"), str)
        or not isinstance(raw.get("assignees"), list)
        or any(not isinstance(a, dict) or not isinstance(a.get("login"), str) for a in raw["assignees"])
        or not isinstance(raw.get("url"), str)
        or raw["url"].casefold() != f"https://github.com/{cfg.repo}/issues/{number}".casefold()
    ):
        raise DecisionError("provider returned an incomplete issue", "provider_unavailable")
    target = {
        "kind": "issue",
        "number": number,
        "title": raw["title"],
        "state": raw["state"],
        "labels": _target_labels(raw.get("labels")),
        "assignees": sorted({a["login"] for a in raw["assignees"]}),
        "updated_at": raw["updatedAt"],
        "body_sha256": hashlib.sha256(raw["body"].encode()).hexdigest(),
    }
    raw["html_url"] = raw["url"]
    return target, raw


def _pr_target(cfg: Config, number: int, deadline: float) -> tuple[dict, dict]:
    fields = "number,title,state,isDraft,headRefName,headRefOid,baseRefName,labels,reviewDecision,updatedAt"
    raw = _read_json(
        cfg,
        ["gh", "pr", "view", str(number), "--repo", cfg.repo, "--json", fields],
        deadline,
    )
    branch = raw.get("headRefName") if isinstance(raw, dict) else None
    match = _AGENT_BRANCH.fullmatch(branch or "")
    if (
        not isinstance(raw, dict)
        or raw.get("number") != number
        or not isinstance(raw.get("title"), str)
        or raw.get("state") not in {"OPEN", "CLOSED", "MERGED"}
        or type(raw.get("isDraft")) is not bool
        or not isinstance(branch, str)
        or not isinstance(raw.get("headRefOid"), str)
        or not isinstance(raw.get("baseRefName"), str)
        or not isinstance(raw.get("updatedAt"), str)
        or raw.get("reviewDecision") not in {None, "", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}
    ):
        raise DecisionError("provider returned an incomplete pull request", "provider_unavailable")
    target = {
        "kind": "pr",
        "number": number,
        "title": raw["title"],
        "state": raw["state"],
        "draft": raw["isDraft"],
        "head_branch": branch,
        "head": raw["headRefOid"],
        "base": raw["baseRefName"],
        "labels": _target_labels(raw.get("labels")),
        "review_decision": raw.get("reviewDecision") or "",
        "updated_at": raw["updatedAt"],
        "ticket": int(match[1]) if match else None,
    }
    return target, raw


def _local_cleanup_target(cfg: Config, number: int, deadline: float) -> dict:
    worktree = cfg.factory / f"wt-{number}"
    branch_name = f"agent/{number}"
    try:
        info = worktree.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISDIR(info.st_mode) or worktree.is_symlink()):
        raise DecisionError("cleanup worktree is unsafe", "unsafe_target")
    branch = _checked(
        cfg,
        [
            "git", "-C", str(cfg.root), "branch", "--format=%(objectname)",
            "--list", branch_name,
        ],
        deadline,
        cap=4_000,
    ).stdout.strip()
    if branch and _SHA.fullmatch(branch) is None:
        raise DecisionError("cleanup branch revision is unsafe", "unsafe_target")
    local = None
    if info is not None:
        lines = _checked(
            cfg,
            [
                "git", "-C", str(worktree), "status", "--porcelain=v2",
                "--branch", "--untracked-files=all",
            ],
            deadline,
            cap=READ_CAP,
        ).stdout.splitlines()
        oid = next((line.removeprefix("# branch.oid ") for line in lines if line.startswith("# branch.oid ")), "")
        head = next((line.removeprefix("# branch.head ") for line in lines if line.startswith("# branch.head ")), "")
        if _SHA.fullmatch(oid) is None or head != branch_name or oid != branch:
            raise DecisionError("cleanup worktree revision is unsafe", "unsafe_target")
        if any(line and not line.startswith("# ") for line in lines):
            raise DecisionError("cleanup worktree has unreviewed changes", "target_busy")
        local = {
            "path": str(worktree), "device": info.st_dev, "inode": info.st_ino, "head": oid,
        }
    target = {
        "kind": "cleanup",
        "number": number,
        "worktree": local,
        "branch": branch or None,
    }
    if target["worktree"] is None and target["branch"] is None:
        raise DecisionError("cleanup target no longer exists", "stale_target")
    return target


def _approval_policy(cfg: Config, target: dict) -> None:
    ticket, head = target.get("ticket"), target.get("head")
    if (
        type(ticket) is not int
        or not isinstance(head, str)
        or _SHA.fullmatch(head) is None
        or target.get("state") != "OPEN"
        or target.get("draft")
        or target.get("base") != cfg.main
        or target.get("review_decision") == "CHANGES_REQUESTED"
    ):
        raise DecisionError("PR is outside Factory's exact-head approval scope", "approval_scope")
    events = lifecycle.read_events(cfg.factory / "events.jsonl")
    if not dispatch._head_evidence_matches(events, ticket, head):
        raise DecisionError("exact-head gate and independent review evidence is missing", "approval_evidence")
    if cfg.manager and cfg.manager_review == "all" and not dispatch.manager_approval(events, ticket, head):
        raise DecisionError("exact-head manager approval evidence is missing", "approval_evidence")


def _sync_routing(request: dict, issue: dict) -> None:
    if LABEL_AGENT not in request.get("add", []):
        return
    labels = {label["name"] for label in issue.get("labels", []) if isinstance(label, dict)}
    if issue.get("title", "").startswith(dispatch.SYNC_TITLE):
        raise DecisionError("upstream-sync issues cannot enter ordinary agent routing", "routing_refused")
    if config.LABEL_INITIATIVE in labels:
        raise DecisionError("initiative records cannot enter ordinary agent routing", "routing_refused")


def _effect(request: dict, actor: str, target: dict) -> dict:
    changes = []
    if request.get("comment", "").strip():
        changes.append("post the exact stored comment")
    changes.extend(f"add label {label}" for label in request.get("add", []))
    changes.extend(f"remove label {label}" for label in request.get("remove", []))
    if request.get("assign"):
        changes.append(f"assign authenticated actor @{actor}")
    if request.get("unassign"):
        changes.append(f"unassign authenticated actor @{actor}")
    if request.get("close"):
        changes.append(f"close issue as {request['close']}")
    if request["op"] == "triage":
        changes.append("run the configured bounded triage command")
    if request["op"] == "cleanup":
        changes.append("remove the fixed local worktree and agent branch; retain lock evidence")
    if request["op"] == "outcome":
        changes.append("post one canonical owner attestation; no release or install is run")
    if LABEL_APPROVED in request.get("add", []):
        changes.append("require exact-head gate and independent review evidence before approval")
    return {
        "target": {"kind": target["kind"], "number": target["number"]},
        "changes": changes,
    }


def _command_step(action: str, argv: list[str], *, cwd: Path | None = None) -> dict:
    return {"kind": "command", "action": action, "argv": argv, "cwd": str(cwd) if cwd else None}


def _plan_request(
    cfg: Config, request: dict, actor: str, target: dict, raw: dict | None,
) -> list[dict]:
    number, op = request["number"], request["op"]
    steps = []
    if op in {"issue", "pr"}:
        if request.get("comment", "").strip():
            steps.append(_command_step(
                f"{op}_comment",
                ["gh", op, "comment", str(number), "--repo", cfg.repo, "--body", request["comment"]],
            ))
        add = [label for label in request.get("add", []) if label != LABEL_APPROVED]
        remove = request.get("remove", [])
        edit = []
        for label in add:
            edit += ["--add-label", label]
        for label in remove:
            edit += ["--remove-label", label]
        if request.get("unassign"):
            edit += ["--remove-assignee", actor]
        if request.get("assign"):
            edit += ["--add-assignee", actor]
        if edit:
            steps.append(_command_step(
                f"{op}_edit", ["gh", op, "edit", str(number), "--repo", cfg.repo, *edit],
            ))
        if LABEL_APPROVED in request.get("add", []):
            _approval_policy(cfg, target)
            steps.append({"kind": "approve", "action": "pr_approve", "target": target})
        if request.get("close"):
            steps.append(_command_step(
                "issue_close",
                ["gh", "issue", "close", str(number), "--repo", cfg.repo, "--reason", request["close"]],
            ))
    elif op == "triage":
        if raw is not None and raw.get("title", "").startswith(dispatch.SYNC_TITLE):
            raise DecisionError("upstream-sync issues cannot enter ordinary triage routing", "routing_refused")
        steps.append(_command_step(
            "triage",
            [sys.executable, "-P", "-B", "-m", "factory", "triage", "--issue", str(number)],
            cwd=cfg.root,
        ))
    elif op == "cleanup":
        worktree = target.get("worktree")
        if worktree:
            steps.append(_command_step(
                "cleanup_worktree",
                ["git", "-C", str(cfg.root), "worktree", "remove", worktree["path"]],
                cwd=cfg.root,
            ))
        if target.get("branch"):
            steps.append(_command_step(
                "cleanup_branch",
                ["git", "-C", str(cfg.root), "branch", "-d", f"agent/{number}"],
                cwd=cfg.root,
            ))
    elif op == "outcome":
        from factory import outcomes

        try:
            body = outcomes.format_comment(cfg, raw, actor, request)
        except outcomes.OutcomeError as exc:
            raise DecisionError(str(exc), exc.code) from exc
        steps.append(_command_step(
            "outcome_comment",
            ["gh", "issue", "comment", str(number), "--repo", cfg.repo, "--body", body],
        ))
    return steps


def prepare(cfg: Config, requests: object) -> dict:
    """Resolve and durably store one exact, expiring human-decision preview."""
    normalized = _normalize_requests(requests)
    deadline = time.monotonic() + PREPARE_SECONDS
    actor = _actor(cfg, deadline)
    targets: list[dict] = []
    target_by_key: dict[tuple[str, int], tuple[dict, dict | None]] = {}
    locks: set[tuple[str, int | None]] = set()
    merge_lock = False
    steps: list[dict] = []
    effects: list[dict] = []

    for index, request in enumerate(normalized):
        op, number = request["op"], request["number"]
        kind = "pr" if op == "pr" else "cleanup" if op == "cleanup" else "issue"
        key = (kind, number)
        if key not in target_by_key:
            if kind == "pr":
                target, raw = _pr_target(cfg, number, deadline)
            elif kind == "cleanup":
                issue, _ = _issue_target(cfg, number, deadline)
                if issue["assignees"] or (issue["state"] == "OPEN" and LABEL_AGENT in issue["labels"]):
                    raise DecisionError("cleanup target has live ownership or routing", "target_busy")
                target = {**_local_cleanup_target(cfg, number, deadline), "issue": issue}
                raw = None
            else:
                target, raw = _issue_target(cfg, number, deadline)
            target_by_key[key] = target, raw
            targets.append(target)
        target, raw = target_by_key[key]
        if kind == "pr":
            ticket = target.get("ticket") or number
            locks.add(("ticket", ticket))
            if LABEL_APPROVED in request.get("add", []) or LABEL_APPROVED in request.get("remove", []):
                merge_lock = True
        else:
            locks.add(("ticket", number))
            if op == "cleanup":
                merge_lock = True
        if op == "issue":
            _sync_routing(request, raw)
        planned = _plan_request(cfg, request, actor, target, raw)
        steps.extend({**step, "request": index} for step in planned)
        effects.append({"request": index, **_effect(request, actor, target)})

    if merge_lock:
        locks.add(("merge", None))
    proposal_id = secrets.token_hex(16)
    confirmation = secrets.token_urlsafe(24)
    now = _now()
    preview = {
        "proposal_id": proposal_id,
        "confirmation": confirmation,
        "actor": actor,
        "targets": targets,
        "requests": normalized,
        "effects": effects,
        "expires_at": _format_time(now + timedelta(seconds=PROPOSAL_SECONDS)),
    }
    document = {
        "schema_version": 1,
        "repository": cfg.repo,
        "prepared_at": _format_time(now),
        "preview": preview,
        "steps": steps,
        "locks": [
            {"kind": kind, **({"number": number} if number is not None else {})}
            for kind, number in sorted(locks, key=lambda item: (item[0] != "merge", item[1] or 0))
        ],
    }
    directory = _state_dir(cfg, "decisions", create=True)
    try:
        # Refuse a target already known to be live; apply takes the same locks again.
        with _coordination_locks(cfg, document["locks"]):
            _store_artifact(directory, _artifact_name("proposal", proposal_id), document)
    finally:
        os.close(directory)
    return preview


def _load_proposal(directory: int, proposal_id: str) -> dict:
    document = _read_artifact(directory, _artifact_name("proposal", proposal_id))
    preview = document.get("preview") if isinstance(document, dict) else None
    if (
        document.get("schema_version") != 1
        or not isinstance(document.get("repository"), str)
        or not isinstance(preview, dict)
        or preview.get("proposal_id") != proposal_id
        or not isinstance(preview.get("confirmation"), str)
        or not isinstance(preview.get("actor"), str)
        or not isinstance(preview.get("targets"), list)
        or not isinstance(preview.get("requests"), list)
        or not isinstance(preview.get("effects"), list)
        or _parse_time(preview.get("expires_at")) is None
        or not isinstance(document.get("steps"), list)
        or not isinstance(document.get("locks"), list)
    ):
        raise DecisionError("stored proposal is invalid", "unsafe_storage")
    return document


def _lock_file(directory: int, name: str) -> int:
    try:
        fd = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory,
        )
    except OSError as exc:
        raise DecisionError("decision lock is unavailable", "storage_unavailable") from exc
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_mode & 0o022
    ):
        os.close(fd)
        raise DecisionError("decision lock is unsafe", "unsafe_storage")
    return fd


@contextmanager
def _proposal_lock(directory: int, proposal_id: str, deadline: float):
    fd = _lock_file(directory, f"apply-{proposal_id}.lock")
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DecisionError("another apply is still running", "conflict") from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _coordination_locks(cfg: Config, locks: list[dict]):
    directory = _state_dir(cfg, "locks", create=True)
    held = []
    try:
        for lock in locks:
            kind = lock.get("kind") if isinstance(lock, dict) else None
            number = lock.get("number") if isinstance(lock, dict) else None
            if kind == "merge" and set(lock) == {"kind"}:
                name = "merge.lock"
            elif kind == "ticket" and set(lock) == {"kind", "number"} and type(number) is int and number > 0:
                name = f"{number}.lock"
            else:
                raise DecisionError("stored coordination scope is invalid", "unsafe_storage")
            fd = _lock_file(directory, name)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                raise DecisionError("target has a live claim or merge operation", "target_busy") from exc
            held.append(fd)
        yield
    finally:
        for fd in reversed(held):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        os.close(directory)


def _live_target(cfg: Config, target: dict, deadline: float) -> dict:
    kind, number = target.get("kind"), target.get("number")
    if kind == "issue":
        return _issue_target(cfg, number, deadline)[0]
    if kind == "pr":
        return _pr_target(cfg, number, deadline)[0]
    if kind == "cleanup":
        return {
            **_local_cleanup_target(cfg, number, deadline),
            "issue": _issue_target(cfg, number, deadline)[0],
        }
    raise DecisionError("stored target scope is invalid", "unsafe_storage")


def _journal(cfg: Config, document: dict, status: str, *, result: dict | None = None) -> None:
    preview = document["preview"]
    row = {
        "at": _format_time(_now()),
        "event": "human-decision",
        "schema_version": 1,
        "decision_id": preview["proposal_id"],
        "proposal_id": preview["proposal_id"],
        "repository": cfg.repo,
        "actor": preview["actor"],
        "targets": preview["targets"],
        "request": preview["requests"][0] if len(preview["requests"]) == 1 else {"batch": preview["requests"]},
        "requests": preview["requests"],
        "status": status,
    }
    if result is not None:
        row["receipt"] = {
            "status": result["status"],
            "steps": result["steps"],
            **({"error": result["error"]} if result.get("error") else {}),
        }
    for target in preview["targets"]:
        key = "pr" if target["kind"] == "pr" else "ticket"
        lifecycle.append(cfg.factory / "events.jsonl", {**row, key: target["number"]})


def _sanitize_output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout + result.stderr).strip()[-OUTPUT_CAP:]
    return _SECRET.sub("[credential withheld]", text)


def _mutation(cfg: Config, step: dict, deadline: float) -> dict:
    env = _environment(cfg)
    if step.get("action") == "triage":
        env = {key: value for key, value in env.items() if not key.startswith("PYTHON")}
        env.update({
            "PYTHONPATH": str(_PACKAGE_ROOT),
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        })
    try:
        result = _run(
            step["argv"],
            env=env,
            cwd=Path(step["cwd"]) if step.get("cwd") else None,
            timeout=_timeout(deadline),
            cap=OUTPUT_CAP,
        )
    except _AmbiguousCommand as exc:
        raise _AmbiguousCommand(str(exc)) from exc
    output = _sanitize_output(result)
    if result.returncode:
        raise _AmbiguousCommand(
            output or f"command exited {result.returncode} without proving whether its effect occurred",
        )
    return {
        "request": step["request"],
        "action": step["action"],
        "status": "applied",
        "ok": True,
        "output": output,
    }


def _withdraw_approval(cfg: Config, target: dict, deadline: float) -> None:
    step = {
        "argv": [
            "gh", "pr", "edit", str(target["number"]), "--repo", cfg.repo,
            "--remove-label", LABEL_APPROVED,
        ],
        "cwd": None,
    }
    result = _run(
        step["argv"], env=_environment(cfg), cwd=None, timeout=_timeout(deadline), cap=OUTPUT_CAP,
    )
    if result.returncode:
        raise _AmbiguousCommand("stale approval could not be withdrawn")


def _approve(cfg: Config, step: dict, deadline: float) -> dict:
    target = step["target"]
    _approval_policy(cfg, target)
    add = {
        **step,
        "argv": [
            "gh", "pr", "edit", str(target["number"]), "--repo", cfg.repo,
            "--add-label", LABEL_APPROVED,
        ],
        "cwd": None,
    }
    receipt = _mutation(cfg, add, deadline)
    try:
        fresh = _pr_target(cfg, target["number"], deadline)[0]
    except DecisionError as exc:
        raise _AmbiguousCommand("approval result could not be revalidated") from exc
    if (
        fresh.get("head") != target.get("head")
        or fresh.get("base") != target.get("base")
        or fresh.get("state") != "OPEN"
        or fresh.get("draft")
        or fresh.get("review_decision") == "CHANGES_REQUESTED"
        or LABEL_APPROVED not in fresh.get("labels", [])
    ):
        _withdraw_approval(cfg, target, deadline)
        raise _FailedStep("PR changed while approval was applied; approval label withdrawn")
    try:
        lifecycle.append(cfg.factory / "events.jsonl", {
            "at": _format_time(_now()),
            "event": "approved",
            "ticket": target["ticket"],
            "pr": target["number"],
            "head": target["head"],
            "gate_head": target["head"],
            "review_head": target["head"],
            "actor": step.get("actor"),
            "source": "human-decision",
        })
    except OSError as exc:
        try:
            _withdraw_approval(cfg, target, deadline)
        except (DecisionError, _AmbiguousCommand):
            raise _AmbiguousCommand("approval audit failed and label withdrawal is uncertain") from exc
        raise _FailedStep("approval audit failed; approval label withdrawn") from exc
    try:
        from factory import results

        results.retain(
            cfg, target["ticket"], target["head"], lifecycle.read_events(cfg.factory / "events.jsonl"),
        )
    except (OSError, ValueError, TypeError):
        pass
    return receipt


def _receipt_value(document: dict, status: str, steps: list[dict], started_at: str, error: str | None = None) -> dict:
    preview = document["preview"]
    value = {
        "schema_version": 1,
        "proposal_id": preview["proposal_id"],
        "repository": document["repository"],
        "actor": preview["actor"],
        "status": status,
        "ok": status == "success",
        "targets": preview["targets"],
        "requests": preview["requests"],
        "effects": preview["effects"],
        "started_at": started_at,
        "completed_at": _format_time(_now()),
        "steps": steps,
    }
    if error:
        value["error"] = error
    return value


def _uncertain_after_intent(document: dict, intent: dict) -> dict:
    return _receipt_value(
        document,
        "uncertain",
        [],
        intent.get("started_at") if isinstance(intent.get("started_at"), str) else _format_time(_now()),
        "A durable intent exists without a receipt. An external effect may have occurred; it was not retried.",
    )


def apply(cfg: Config, proposal_id: str, confirmation: object) -> dict:
    """Apply one stored proposal once; replay returns its immutable receipt."""
    directory = _state_dir(cfg, "decisions", create=False)
    deadline = time.monotonic() + APPLY_SECONDS
    try:
        # Validate existence and identifier before deriving a lock filename.
        _load_proposal(directory, proposal_id)
        with _proposal_lock(directory, proposal_id, deadline):
            document = _load_proposal(directory, proposal_id)
            preview = document["preview"]
            if document["repository"] != cfg.repo:
                raise DecisionError("proposal repository scope changed", "scope_changed")
            if not isinstance(confirmation, str) or not secrets.compare_digest(
                confirmation, preview["confirmation"],
            ):
                raise DecisionError("confirmation does not match the stored proposal", "confirmation_refused")
            stored = _read_artifact(
                directory, _artifact_name("receipt", proposal_id), missing=True,
            )
            if stored is not None:
                return stored
            intent = _read_artifact(
                directory, _artifact_name("intent", proposal_id), missing=True,
            )
            if intent is not None:
                result = _uncertain_after_intent(document, intent)
                _store_artifact(directory, _artifact_name("receipt", proposal_id), result)
                try:
                    _journal(cfg, document, "uncertain", result=result)
                except OSError:
                    pass
                return result
            expires = _parse_time(preview["expires_at"])
            if expires is None or _now() >= expires:
                raise DecisionError("proposal expired; prepare a fresh decision", "expired")

            with _coordination_locks(cfg, document["locks"]):
                actor = _actor(cfg, deadline)
                if actor.casefold() != preview["actor"].casefold():
                    raise DecisionError("authenticated actor changed after prepare", "stale_actor")
                for target in preview["targets"]:
                    if _live_target(cfg, target, deadline) != target:
                        raise DecisionError("target changed after prepare", "stale_target")
                for step in document["steps"]:
                    if step.get("kind") == "approve":
                        _approval_policy(cfg, step["target"])
                started_at = _format_time(_now())
                intent = {
                    "schema_version": 1,
                    "proposal_id": proposal_id,
                    "repository": cfg.repo,
                    "actor": actor,
                    "targets": preview["targets"],
                    "requests": preview["requests"],
                    "effects": preview["effects"],
                    "started_at": started_at,
                }
                _store_artifact(directory, _artifact_name("intent", proposal_id), intent)
                try:
                    _journal(cfg, document, "started")
                except OSError as exc:
                    result = _receipt_value(
                        document, "failure", [], started_at,
                        "Durable intent journal failed; no external effect was attempted.",
                    )
                    _store_artifact(directory, _artifact_name("receipt", proposal_id), result)
                    raise DecisionError(result["error"], "intent_unavailable") from exc

                step_results: list[dict] = []
                status, error = "success", None
                for step in document["steps"]:
                    try:
                        if step.get("kind") == "approve":
                            step = {**step, "actor": actor}
                            step_results.append(_approve(cfg, step, deadline))
                        elif step.get("kind") == "command":
                            step_results.append(_mutation(cfg, step, deadline))
                        else:
                            raise _FailedStep("stored action kind is invalid")
                    except _FailedStep as exc:
                        step_results.append({
                            "request": step.get("request"),
                            "action": step.get("action", "unknown"),
                            "status": "failed",
                            "ok": False,
                            "output": str(exc),
                        })
                        status = "partial" if any(item["ok"] for item in step_results) else "failure"
                        error = "Decision stopped at the first failed effect."
                        break
                    except (DecisionError, _AmbiguousCommand) as exc:
                        step_results.append({
                            "request": step.get("request"),
                            "action": step.get("action", "unknown"),
                            "status": "uncertain",
                            "ok": False,
                            "output": str(exc),
                        })
                        status = "uncertain"
                        error = "The last external effect is uncertain and was not retried."
                        break
                result = _receipt_value(document, status, step_results, started_at, error)
                _store_artifact(directory, _artifact_name("receipt", proposal_id), result)
                try:
                    _journal(cfg, document, status, result=result)
                except OSError:
                    pass
                return result
    finally:
        os.close(directory)


def _pending_receipt(document: dict, status: str, error: str | None = None) -> dict:
    preview = document["preview"]
    result = {
        "schema_version": 1,
        "proposal_id": preview["proposal_id"],
        "repository": document["repository"],
        "actor": preview["actor"],
        "status": status,
        "ok": False,
        "targets": preview["targets"],
        "requests": preview["requests"],
        "effects": preview["effects"],
        "steps": [],
    }
    if error:
        result["error"] = error
    return result


def _observed_target(cfg: Config, target: dict, deadline: float) -> dict:
    kind, number = target["kind"], target["number"]
    if kind == "cleanup":
        issue = _issue_target(cfg, number, deadline)[0]
        try:
            actual = _local_cleanup_target(cfg, number, deadline)
        except DecisionError as exc:
            if exc.code == "stale_target":
                return {
                    "kind": "cleanup",
                    "number": number,
                    "worktree": None,
                    "branch": False,
                    "issue": issue,
                }
            raise
        return {**actual, "issue": issue}
    return _live_target(cfg, target, deadline)


def _postcondition(request: dict, actor: str, actual: dict | None) -> dict:
    evidence: list[str] = []
    checks: list[bool] = []
    op = request["op"]
    if actual is None:
        return {"status": "unknown", "evidence": ["target observation unavailable"]}
    if op in {"issue", "pr"}:
        labels = set(actual.get("labels", []))
        for label in request.get("add", []):
            checks.append(label in labels)
            evidence.append(f"label {label} is {'present' if label in labels else 'absent'}")
        for label in request.get("remove", []):
            checks.append(label not in labels)
            evidence.append(f"label {label} is {'absent' if label not in labels else 'present'}")
        assignees = {login.casefold() for login in actual.get("assignees", [])}
        if request.get("assign"):
            checks.append(actor.casefold() in assignees)
            evidence.append(f"authenticated actor assignment is {'present' if actor.casefold() in assignees else 'absent'}")
        if request.get("unassign"):
            checks.append(actor.casefold() not in assignees)
            evidence.append(f"authenticated actor assignment is {'absent' if actor.casefold() not in assignees else 'present'}")
        if request.get("close"):
            checks.append(actual.get("state") == "CLOSED")
            evidence.append(f"issue state is {actual.get('state', 'unknown')}")
        if request.get("comment", "").strip():
            evidence.append("comment identity was not collected; comment postcondition is unknown")
    elif op == "cleanup":
        checks.append(actual.get("worktree") is None and not actual.get("branch"))
        evidence.append("fixed worktree and branch are absent" if checks[-1] else "fixed worktree or branch remains")
    elif op == "outcome":
        evidence.append("canonical comment identity was not collected; attestation postcondition is unknown")
    else:
        evidence.append("triage target state is observed, but its model-selected postcondition is not inferred")
    status = "unknown" if not checks else "satisfied" if all(checks) else "not_satisfied"
    if any("unknown" in item for item in evidence) and status == "satisfied":
        status = "partial"
    return {"status": status, "evidence": evidence}


def _observe(cfg: Config, document: dict) -> dict:
    deadline = time.monotonic() + PREPARE_SECONDS
    observed_at = _format_time(_now())
    actual: dict[tuple[str, int], dict | None] = {}
    targets, errors = [], []
    for target in document["preview"]["targets"]:
        key = (target["kind"], target["number"])
        try:
            value = _observed_target(cfg, target, deadline)
            actual[key] = value
            targets.append(value)
        except DecisionError as exc:
            actual[key] = None
            errors.append({"target": {"kind": key[0], "number": key[1]}, "code": exc.code})
    postconditions = []
    for index, request in enumerate(document["preview"]["requests"]):
        kind = "pr" if request["op"] == "pr" else "cleanup" if request["op"] == "cleanup" else "issue"
        postconditions.append({
            "request": index,
            **_postcondition(request, document["preview"]["actor"], actual.get((kind, request["number"]))),
        })
    return {
        "observed_at": observed_at,
        "targets": targets,
        "postconditions": postconditions,
        "errors": errors,
        "interpretation": (
            "Observed labels and assignments are target state only. They do not establish that a worker ran, "
            "a pull request merged, or an outcome was delivered."
        ),
    }


def receipt(cfg: Config, proposal_id: str, observe: bool = False) -> dict:
    """Retrieve a durable receipt, optionally with a fresh separate target observation."""
    if type(observe) is not bool:
        raise DecisionError("observe must be boolean")
    directory = _state_dir(cfg, "decisions", create=False)
    try:
        document = _load_proposal(directory, proposal_id)
        if document["repository"] != cfg.repo:
            raise DecisionError("proposal repository scope changed", "scope_changed")
        result = _read_artifact(
            directory, _artifact_name("receipt", proposal_id), missing=True,
        )
        if result is None:
            intent = _read_artifact(
                directory, _artifact_name("intent", proposal_id), missing=True,
            )
            result = (
                _uncertain_after_intent(document, intent)
                if intent is not None
                else _pending_receipt(document, "prepared")
            )
        if observe:
            result = {**result, "observation": _observe(cfg, document)}
        return result
    finally:
        os.close(directory)
