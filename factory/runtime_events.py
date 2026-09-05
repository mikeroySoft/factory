"""Bounded, read-only projections of the lifecycle journal and local evidence."""
from __future__ import annotations

import json
import os
import math
from pathlib import Path
import re
import stat
import uuid
from datetime import datetime, timezone

from factory import lifecycle

BYTE_LIMIT = 1024 * 1024
EVENT_LIMIT = 512
ROW_LIMIT = 16 * 1024
PROC_LIMIT = 128
PROC_BYTES = 4096
LOCK_LIMIT = 512
LOCK_BYTES = 1024 * 1024
ERROR_LIMIT = 32
IDENTITY = lifecycle._IDENTITY_FIELDS
KINDS = {
    "enter", "exit", "handoff", "child_start", "child_exit", "check", "result", "timeout",
    "lock_acquired", "lock_released", "resource_requested", "wait", "wait_end",
    "resource_observation", "scheduling_observation",
}
OBSERVATIONS = {"resource_observation", "scheduling_observation"}
OUTCOMES = {
    "completed", "mechanism_failure", "interrupted", "unknown", "product_feedback",
    "project_escalation", "approved", "merged", "refreshed", "not_admitted", "not_eligible",
}
REASONS = {
    "APPROVE", "REVISE", "unparsed_verdict", "merge_conflict", "upstream_gate_failed",
    "upstream_sync", "check_terminated_by_signal", "check_timeout", "configured_check_failed",
    "conflict_markers", "git_grep_failed", "git_diff_failed", "leak_scan_matches",
    "github_command_failed", "triage_endpoint_timeout", "triage_endpoint_unavailable",
    "no_tickets", "unparseable_decision", "triage_feedback", "ci_pending", "no_passing_ci",
    "exclusive_resource", "merge_lock_contended", "ticket_lock_contended", "capacity_reached",
    "state_changed", "check_failed",
    "scheduled_next_pass", "scope exited before its registered children were reaped",
    "recorded execution ended according to process and lock evidence",
}
PROCESS_FIELDS = ("pid", "boot_id", "start_ticks", "pid_namespace", "uid", "state", "ppid")


def _error(errors: list, source: str, scope: str, code: str) -> None:
    value = {"source": source, "scope": scope, "code": code}
    if value not in errors and len(errors) < ERROR_LIMIT:
        errors.append(value)


def _text(value, limit=256) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and not any(
        ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value
    )


def _utc(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value
    ):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _reason(value):
    return value if isinstance(value, str) and (
        value in REASONS or re.fullmatch(r"(?:worker|review|merge|push)_exit:-?\d{1,10}", value)
    ) else None


def _lock(value: dict) -> dict:
    return {key: value[key] for key in ("path", "device", "inode")}


def _resource(value: dict) -> dict:
    return {**{key: value[key] for key in ("id", "scope", "host_id", "repository")},
            "lock": _lock(value["lock"])}


def _process(value):
    return {key: value[key] for key in PROCESS_FIELDS} if value is not None else None


def _valid_process(value) -> bool:
    return value is None or (
        lifecycle._valid_process(value) and value["pid"] <= 2**31 - 1
        and all(value[key] is None or _text(value[key]) for key in ("boot_id", "pid_namespace", "state"))
        and all(value[key] is None or value[key] >= 0 for key in ("start_ticks", "uid", "ppid"))
    )


def _valid_lock(value) -> bool:
    return (lifecycle._valid_lock(value) and _text(value["path"], 4096)
            and all(value[key] is None or value[key] <= 2**64 - 1 for key in ("device", "inode")))


def _valid_resource(value) -> bool:
    return lifecycle._valid_resource(value) and _valid_lock(value["lock"]) and (
        _text(value["id"]) and _text(value["host_id"])
        and (value["repository"] is None or _text(value["repository"], 4096))
    )


def _wait(value) -> dict | None:
    if value is None:
        return None
    details = {}
    for key in ("active", "max_active", "pr"):
        if type(value["details"].get(key)) is int and 0 <= value["details"][key] <= 2**63 - 1:
            details[key] = value["details"][key]
    if _utc(value["details"].get("next_at")):
        details["next_at"] = value["details"]["next_at"]
    return {"reason": _reason(value["reason"]), "mode": value["mode"],
            "resource": _resource(value["resource"]) if value["resource"] is not None else None,
            "details": details}


def _identity_valid(value) -> bool:
    return (isinstance(value, dict) and all(key in value for key in IDENTITY)
            and all(_text(value[key]) for key in ("root_execution_id", "execution_id", "stage"))
            and all(value[key] is None or _text(value[key])
                    for key in ("dispatcher_run_id", "parent_execution_id"))
            and all(value[key] is None or type(value[key]) is int and 0 <= value[key] <= 2**63 - 1
                    for key in ("ticket", "attempt", "review_round")))


def _recorded_resource_state(row) -> dict | None:
    """Only the F02 state whitelist may participate in observation matching."""
    if (row.get("state") not in ("held", "free", "unknown")
            or row.get("ownership") not in ("confirmed", "none", "unknown")
            or not isinstance(row.get("requests"), list) or len(row["requests"]) > EVENT_LIMIT):
        return None
    owner = row.get("owner")
    if owner is not None:
        if (not _identity_valid(owner) or not _valid_process(owner.get("process"))
                or not _text(owner.get("acquisition_id")) or not _utc(owner.get("acquired_at"))):
            return None
        owner = {**{key: owner[key] for key in IDENTITY}, "process": _process(owner.get("process")),
                 "acquisition_id": owner["acquisition_id"], "acquired_at": owner["acquired_at"]}
    requests = []
    for request in row["requests"]:
        if (not _identity_valid(request) or not _valid_process(request.get("process"))
                or not _text(request.get("event_id")) or not _utc(request.get("requested_at"))
                or type(request.get("blocking")) is not bool):
            return None
        requests.append({**{key: request[key] for key in IDENTITY},
                         "process": _process(request.get("process")), "event_id": request["event_id"],
                         "requested_at": request["requested_at"], "blocking": request["blocking"]})
    evidence = row.get("evidence")
    if (not isinstance(evidence, dict) or evidence.get("lock_state") not in ("held", "free", "unknown")
            or evidence.get("attribution") not in ("proc_locks", "unavailable")):
        return None
    holders = evidence.get("holder_pids")
    if holders is not None and (not isinstance(holders, list) or len(holders) > EVENT_LIMIT
                               or any(type(pid) is not int or pid <= 0 for pid in holders)):
        return None
    return {"resource": _resource(row["resource"]), "state": row["state"],
            "ownership": row["ownership"], "owner": owner, "requests": requests,
            "evidence": {"lock_state": evidence["lock_state"], "attribution": evidence["attribution"],
                         "holder_pids": holders}}


def _sanitize(row: dict) -> dict | None:
    if (not lifecycle._lifecycle(row) or not _identity_valid(row) or not _utc(row["at"])
            or not _text(row["event_id"]) or row["sequence"] > 2**63 - 1
            or not _valid_process(row["process"]) or len(row["locks"]) > 64
            or not all(_valid_lock(lock) for lock in row["locks"])
            or "resource" in row and not _valid_resource(row["resource"])
            or "child_process" in row and not _valid_process(row["child_process"])):
        return None
    kind = row["kind"]
    if kind in {"child_start", "child_exit"} and not isinstance(row.get("child_process"), dict):
        return None
    if kind == "handoff" and not _text(row.get("handoff_id")):
        return None
    if kind == "wait_end" and not _text(row.get("wait_event_id")):
        return None
    if kind == "wait" or kind == "scheduling_observation" and row.get("wait") is not None:
        value = row.get("wait")
        if (not lifecycle._valid_wait(value) or not _text(value["reason"])
                or value["resource"] is not None and not _valid_resource(value["resource"])):
            return None
    if kind == "scheduling_observation" and any(
        row.get(key) is not None and type(row[key]) is not bool for key in ("timer_active", "service_active")
    ):
        return None
    state = None
    if kind == "resource_observation":
        if "resource" not in row:
            return None
        state = _recorded_resource_state(row)
        if state is None:
            return None
    event = {**{key: row[key] for key in IDENTITY},
             **{key: row[key] for key in ("event", "schema_version", "event_id", "sequence", "kind", "at")},
             "outcome": row["outcome"] if row["outcome"] in OUTCOMES else None,
             "reason": _reason(row["reason"]), "process": _process(row["process"]),
             "locks": [_lock(lock) for lock in row["locks"]]}
    for key in ("handoff_id", "wait_event_id", "acquisition_id"):
        if key in row:
            if row[key] is not None and not _text(row[key]):
                return None
            event[key] = row[key]
    for key in ("blocking", "parsed", "timed_out", "reconciled", "passed"):
        if key in row:
            if type(row[key]) is not bool:
                return None
            event[key] = row[key]
    for key in ("returncode", "timeout_seconds"):
        if key in row:
            if type(row[key]) is not int or not -(2**63) <= row[key] <= 2**63 - 1:
                return None
            event[key] = row[key]
    if row.get("verdict") in ("APPROVE", "REVISE"):
        event["verdict"] = row["verdict"]
    if "check" in row:
        if not _text(row["check"]):
            return None
        event["check"] = row["check"]
    if "child_process" in row:
        event["child_process"] = _process(row["child_process"])
    if "resource" in row:
        event["resource"] = _resource(row["resource"])
    if "lock" in row:
        if not _text(row["lock"], 4096):
            return None
        event["lock"] = row["lock"]
    if kind in {"wait", "scheduling_observation"}:
        event["wait"] = _wait(row.get("wait"))
    if kind == "scheduling_observation":
        event.update({key: row.get(key) for key in ("timer_active", "service_active")})
    if state is not None:
        event.update(state)
    return event


def _valid_json(value) -> bool:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32 or type(item) is float and not math.isfinite(item):
            return False
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return True


def _journal(path: Path, errors: list) -> tuple[list, dict, list]:
    history = {"source": "events.jsonl", "status": "empty", "start_at": None, "end_at": None,
               "complete": True, "truncated": False, "gaps": [], "bytes_read": 0,
               "byte_limit": BYTE_LIMIT, "event_limit": EVENT_LIMIT, "retained_events": 0}
    gaps = []

    def gap(code, position):
        if code not in history["gaps"]:
            history["gaps"].append(code)
        if gaps:
            gaps[0] = max(gaps[0], position)
        else:
            gaps.append(position)
        history["complete"] = False
        _error(errors, "events.jsonl", "history", code)

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                history["status"] = "unreadable"
                gap("not_regular", 0)
                return [], history, gaps
            offset = max(0, before.st_size - BYTE_LIMIT)
            data = os.pread(fd, min(before.st_size, BYTE_LIMIT), offset)
            after = os.fstat(fd)
        finally:
            os.close(fd)
    except FileNotFoundError:
        history["status"] = "missing"
        gap("missing", 0)
        return [], history, gaps
    except OSError:
        history["status"] = "unreadable"
        gap("unreadable", 0)
        return [], history, gaps
    history["bytes_read"] = len(data)
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or len(data) != min(before.st_size, BYTE_LIMIT):
        gap("changed_during_read", len(data) + 1)
    if offset:
        history["truncated"] = True
        gap("byte_limit", -1)
        # The preceding byte was not read; never promote a clipped beginning to a record.
        first = data.find(b"\n")
        data = data[first + 1:] if first >= 0 else b""
    rows = []
    lines = data.split(b"\n")
    if lines[-1]:
        gap("unterminated_tail", len(lines))
    for position, line in enumerate(lines[:-1]):
        if len(line) + 1 > ROW_LIMIT:
            gap("row_limit", position)
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            gap("invalid_json", position)
            continue
        if not isinstance(row, dict) or not _valid_json(row):
            gap("invalid_record", position)
            continue
        if row.get("event") != "lifecycle":
            gap("unsupported_record", -1)
            continue  # Legacy facts cannot hide a lifecycle exit, but are not lifecycle history.
        if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
            gap("unsupported_version", position)
            continue
        if isinstance(row.get("kind"), str) and row["kind"] not in KINDS:
            gap("unsupported_kind", position)
            continue
        try:
            event = _sanitize(row)
        except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
            event = None
        if event is None:
            gap("invalid_lifecycle", position)
            continue
        rows.append((position, event, row))
    newest = set()
    beginning = 0
    for index in range(len(rows) - 1, -1, -1):
        identity = rows[index][1]["event_id"]
        if identity not in newest and len(newest) == EVENT_LIMIT:
            beginning = index + 1
            history["truncated"] = True
            gap("event_limit", rows[index][0])
            break
        newest.add(identity)
    retained, identities, sequences = [], {}, {}
    for position, event, raw in rows[beginning:]:
        sequence = (event["execution_id"], event["sequence"])
        previous = identities.get(event["event_id"], sequences.get(sequence))
        if previous is not None:
            if previous != raw:
                gap("duplicate_conflict", position)
            continue
        identities[event["event_id"]] = raw
        sequences[sequence] = raw
        retained.append((position, event))
    if retained:
        history["status"] = "available"
        times = [event["at"] for _, event in retained]
        history["start_at"] = min(times, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
        history["end_at"] = max(times, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
    elif before.st_size:
        history["status"] = "available"
    history["retained_events"] = len(retained)
    return retained, history, gaps


def _small(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not_regular")
        data = bytearray()
        while len(data) < limit:
            chunk = os.read(fd, limit - len(data))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        raise OSError("byte_limit")
    finally:
        os.close(fd)


class _Observation:
    def __init__(self, errors: list):
        self.errors = errors
        self.at = lifecycle._now()
        self.processes = {}
        self.lock_files = {}
        self.kernel = None
        self.kernel_read = False
        try:
            self.boot = _small(Path("/proc/sys/kernel/random/boot_id"), 128).decode().strip() or None
        except (OSError, UnicodeError):
            self.boot = None
            _error(errors, "proc", "identity", "boot_unavailable")
        try:
            self.namespace = os.readlink("/proc/self/ns/pid")
        except OSError:
            self.namespace = None
            _error(errors, "proc", "identity", "namespace_unavailable")
        try:
            machine = _small(Path("/etc/machine-id"), 128).decode().strip()
        except (OSError, UnicodeError):
            machine = ""
        value = machine or self.boot
        self.host = str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:" + value)) if value else None
        self.absence = None

    def process_state(self, identity) -> str:
        if (not isinstance(identity, dict) or identity.get("start_ticks") is None
                or not identity.get("boot_id") or not identity.get("pid_namespace") or self.boot is None):
            return "unknown"
        if identity["boot_id"] != self.boot:
            return "dead"
        if self.namespace != identity["pid_namespace"]:
            return "unknown"
        pid = identity["pid"]
        if pid not in self.processes:
            if len(self.processes) >= PROC_LIMIT:
                _error(self.errors, "proc", "identity", "process_limit")
                return "unknown"
            directory = Path("/proc") / str(pid)
            try:
                text = _small(directory / "stat", PROC_BYTES).decode()
                fields = text[text.rfind(")") + 2:].split()
                self.processes[pid] = {"start_ticks": int(fields[19]), "state": fields[0],
                                       "pid_namespace": os.readlink(directory / "ns/pid"),
                                       "uid": directory.stat().st_uid}
            except FileNotFoundError:
                self.processes[pid] = "missing"
            except (OSError, UnicodeError, ValueError, IndexError):
                self.processes[pid] = None
                _error(self.errors, "proc", "identity", "process_unavailable")
        current = self.processes[pid]
        if current == "missing":
            if self.absence is None:
                try:
                    mounts = _small(Path("/proc/mounts"), 64 * 1024).decode()
                    self_stat = _small(Path("/proc/self/stat"), PROC_BYTES).decode()
                    proc_mounts = [line.split() for line in mounts.splitlines()
                                   if len(line.split()) >= 4 and line.split()[1:3] == ["/proc", "proc"]]
                    self.absence = bool(proc_mounts and self_stat) and not any(
                        option.startswith("hidepid=") and option != "hidepid=0"
                        for fields in proc_mounts for option in fields[3].split(",")
                    )
                except (OSError, UnicodeError):
                    self.absence = False
            if not self.absence:
                _error(self.errors, "proc", "identity", "absence_unavailable")
            return "dead" if self.absence else "unknown"
        if current is None or current["pid_namespace"] != identity["pid_namespace"]:
            return "unknown"
        if current["start_ticks"] != identity["start_ticks"] or current["state"] in {"Z", "X"}:
            return "dead"
        if identity.get("uid") is not None and current["uid"] != identity["uid"]:
            return "unknown"
        return "alive"

    def lock_file(self, path: str) -> dict:
        if path not in self.lock_files:
            if len(self.lock_files) >= LOCK_LIMIT:
                _error(self.errors, "filesystem", "resources", "lock_limit")
                return {"path": path, "device": None, "inode": None}
            value = {"path": path, "device": None, "inode": None}
            try:
                info = Path(path).stat()
                if stat.S_ISREG(info.st_mode):
                    value.update(device=info.st_dev, inode=info.st_ino)
                else:
                    _error(self.errors, "filesystem", "resources", "not_regular")
            except OSError:
                _error(self.errors, "filesystem", "resources", "lock_unavailable")
            self.lock_files[path] = value
        return self.lock_files[path]

    def holders(self, lock: dict):
        if lock["device"] is None or lock["inode"] is None:
            return None
        if not self.kernel_read:
            self.kernel_read = True
            try:
                data = _small(Path("/proc/locks"), LOCK_BYTES).decode()
                kernel = {}
                for line in data.splitlines():
                    fields = line.split()
                    if len(fields) < 2:
                        raise ValueError
                    if fields[1] != "FLOCK":
                        continue  # Ignore blocked waiters and non-flock lock classes.
                    if len(fields) < 8:
                        raise ValueError
                    major, minor, inode = fields[5].split(":")
                    key = (int(major, 16), int(minor, 16), int(inode))
                    kernel.setdefault(key, set()).add(int(fields[4]))
                self.kernel = kernel
            except (OSError, UnicodeError, ValueError, IndexError):
                _error(self.errors, "proc/locks", "resources", "locks_unavailable")
        if self.kernel is None:
            return None
        holders = self.kernel.get((os.major(lock["device"]), os.minor(lock["device"]), lock["inode"]), set())
        if len(holders) > PROC_LIMIT:
            _error(self.errors, "proc/locks", "resources", "holder_limit")
            return None
        return sorted(holders)

    def lock_state(self, lock: dict) -> tuple[str, list | None]:
        current = self.lock_file(lock["path"])
        if (lock["device"], lock["inode"]) != (current["device"], current["inode"]):
            return "unknown", None
        holders = self.holders(current)
        return ("unknown", None) if holders is None else ("held" if holders else "free", holders)


def _executions(retained: list, history: dict, gaps: list, observation: _Observation) -> tuple[list, dict]:
    groups = {}
    for position, row in retained:
        if row["kind"] not in OBSERVATIONS:
            groups.setdefault(row["execution_id"], []).append((position, row))
    launched = set()
    for key, rows in groups.items():
        if not any(row["kind"] in {"handoff", "child_start"} for _, row in rows):
            continue
        cursor = key
        while cursor in groups and cursor not in launched:
            launched.add(cursor)
            cursor = groups[cursor][0][1]["parent_execution_id"]
    result = []
    for key, rows in groups.items():
        rows = sorted(rows, key=lambda pair: pair[1]["sequence"])
        events = [row for _, row in rows]
        latest = events[-1]
        entry = next((row for row in events if row["kind"] == "enter"), None)
        terminal = next((row for row in events if row["kind"] == "exit"), None)
        sequences = [row["sequence"] for row in events]
        inconsistent = any(
            any(row[field] != events[0][field] for field in IDENTITY) for row in events
        )
        causal_gap = sequences != list(range(sequences[0], sequences[0] + len(sequences)))
        if inconsistent or causal_gap or entry is None or entry["sequence"] != 1:
            code = "identity_conflict" if inconsistent else "missing_enter" if entry is None else "sequence_gap"
            if code not in history["gaps"]:
                history["gaps"].append(code)
            history["complete"] = False
            _error(observation.errors, "events.jsonl", "executions", code)
        if terminal is not None and terminal != latest or sum(row["kind"] == "enter" for row in events) > 1:
            inconsistent = True
            if "sequence_conflict" not in history["gaps"]:
                history["gaps"].append("sequence_conflict")
            history["complete"] = False
            _error(observation.errors, "events.jsonl", "executions", "sequence_conflict")
        uncertain = inconsistent or causal_gap or entry is None or entry["sequence"] != 1
        # A corrupt/unsupported suffix could contain an exit even while its process lives.
        uncertain = uncertain or any(position >= min(position for position, _ in rows) for position in gaps)
        quality = "partial" if uncertain or not history["complete"] else "fresh"
        evidence = None
        pending = None
        state = "unknown"
        if terminal is not None:
            outcome = terminal["outcome"]
            state = {"interrupted": "interrupted", "mechanism_failure": "failed",
                     "unknown": "unknown", None: "unknown"}.get(outcome, "completed")
            if inconsistent:
                state = "unknown"
        elif entry is not None:
            process = observation.process_state(entry["process"])
            children = {}
            handoffs = set()
            for event in events:
                if event["kind"] == "handoff":
                    handoffs.add(event["handoff_id"])
                elif event["kind"] == "child_start":
                    child = event["child_process"]
                    children[child["pid"]] = child
                    handoffs.discard(event.get("handoff_id"))
                elif event["kind"] == "child_exit":
                    child = event["child_process"]
                    if children.get(child["pid"]) == child:
                        children.pop(child["pid"], None)
            child_states = [{"process": child, "state": observation.process_state(child)}
                            for child in children.values()]
            locks = [{**lock, "state": observation.lock_state(lock)[0]} for lock in latest["locks"]]
            rebooted = bool(observation.boot and (entry["process"] or {}).get("boot_id")
                            and observation.boot != entry["process"]["boot_id"])
            # ponytail: no proc enumeration; retained direct identities prove life, never descendant absence.
            omitted = key in launched and not rebooted
            evidence = {"process": process, "children": child_states, "locks": locks,
                        "descendants": [], "scan_complete": not omitted and not uncertain,
                        "descendant_absence_proven": rebooted or (not uncertain and key not in launched),
                        "pending_handoffs": sorted(handoffs)}
            if omitted:
                quality = "partial"
                _error(observation.errors, "proc", "executions", "descendants_not_scanned")
            if rebooted:
                state = "interrupted"
            elif not uncertain:
                if process == "alive" or any(child["state"] == "alive" for child in child_states):
                    state = "active"
                elif (process == "dead" and all(child["state"] == "dead" for child in child_states)
                      and all(lock["state"] == "free" for lock in locks) and not omitted and not handoffs):
                    state = "interrupted"
            if state == "unknown":
                quality = "partial" if process != "unknown" or child_states else "unavailable"
            if state == "active" and process == "alive":
                for event in events:
                    if event["kind"] == "wait":
                        pending = {**event["wait"], "event_id": event["event_id"], "at": event["at"]}
                    elif event["kind"] == "wait_end" and pending is not None and event["wait_event_id"] == pending["event_id"]:
                        pending = None
        result.append({**{field: latest[field] for field in IDENTITY}, "state": state,
                       "entered_at": entry["at"] if entry else None,
                       "ended_at": terminal["at"] if terminal else None,
                       "outcome": terminal["outcome"] if terminal else None,
                       "reason": terminal["reason"] if terminal else None, "wait": pending,
                       "latest_event_id": latest["event_id"], "latest_at": latest["at"],
                       "observation": quality, "observed_at": observation.at, "evidence": evidence})
    return result, groups


def _resources(path: Path, paths, retained: list, executions: list, groups: dict, observation: _Observation) -> list:
    descriptors, acquisitions, requests, recorded = {}, {}, {}, {}
    for _, event in retained:
        resource = event.get("resource")
        if resource is not None:
            descriptors[resource["id"]] = resource
            if event["kind"] == "resource_observation":
                recorded[resource["id"]] = event
        if event["kind"] == "wait" and event["wait"]["resource"] is not None:
            resource = event["wait"]["resource"]
            descriptors[resource["id"]] = resource
    for key, rows in groups.items():
        for _, event in sorted(rows, key=lambda pair: pair[1]["sequence"]):
            resource = event.get("resource")
            if resource is None:
                continue
            resource_id = resource["id"]
            if event["kind"] == "resource_requested":
                requests[(resource_id, key)] = event
            elif event["kind"] == "lock_acquired":
                acquisitions[(resource_id, key, event["acquisition_id"])] = event
                requests.pop((resource_id, key), None)
            elif event["kind"] == "lock_released":
                acquisitions.pop((resource_id, key, event["acquisition_id"]), None)
                requests.pop((resource_id, key), None)
    for index, (lock_path, scope) in enumerate(paths):
        if index == LOCK_LIMIT:
            _error(observation.errors, "configuration", "resources", "lock_limit")
            break
        if scope not in {"repository", "host"}:
            _error(observation.errors, "configuration", "resources", "invalid_scope")
            continue
        if observation.host is None:
            _error(observation.errors, "configuration", "resources", "host_identity_unavailable")
            continue
        try:
            canonical = str(Path(lock_path).resolve())
            repository = str(path.resolve().parent) if scope == "repository" else None
        except (OSError, RuntimeError, ValueError):
            canonical = str(Path(lock_path).absolute())
            repository = str(path.absolute().parent) if scope == "repository" else None
            _error(observation.errors, "filesystem", "resources", "canonical_path_unavailable")
        host = observation.host
        resource = {"id": lifecycle._resource_id(host, repository, canonical), "scope": scope,
                    "host_id": host, "repository": repository, "lock": observation.lock_file(canonical)}
        descriptors[resource["id"]] = resource
    active = {row["execution_id"] for row in executions if row["state"] == "active"}
    result = []
    for resource_id, descriptor in descriptors.items():
        local = observation.host is not None and descriptor["host_id"] == observation.host
        resource = {**descriptor, "lock": observation.lock_file(descriptor["lock"]["path"])} if local else descriptor
        state, holders = observation.lock_state(resource["lock"]) if local else ("unknown", None)
        candidates = []
        for (candidate_id, key, _), event in acquisitions.items():
            if candidate_id != resource_id or key not in active or state != "held":
                continue
            process = event["process"]
            if (process is not None and holders == [process["pid"]]
                    and event["resource"]["lock"]["device"] == resource["lock"]["device"]
                    and event["resource"]["lock"]["inode"] == resource["lock"]["inode"]
                    and observation.process_state(process) == "alive"):
                candidates.append(event)
        owner = None
        if len(candidates) == 1:
            event = candidates[0]
            owner = {**{key: event[key] for key in IDENTITY}, "process": event["process"],
                     "acquisition_id": event["acquisition_id"], "acquired_at": event["at"]}
        pending = [{**{field: event[field] for field in IDENTITY}, "process": event["process"],
                    "event_id": event["event_id"], "requested_at": event["at"], "blocking": event["blocking"]}
                   for (candidate_id, key), event in requests.items()
                   if candidate_id == resource_id and key in active and observation.process_state(event["process"]) == "alive"]
        value = {"resource": resource, "state": state,
                 "ownership": "confirmed" if owner else "none" if state == "free" else "unknown",
                 "owner": owner, "requests": pending,
                 "evidence": {"lock_state": state, "attribution": "proc_locks" if holders is not None else "unavailable",
                              "holder_pids": holders}}
        previous = recorded.get(resource_id)
        matching = previous is not None and all(previous.get(key) == field for key, field in value.items())
        quality = "unavailable" if state == "unknown" else "partial" if state == "held" and owner is None else "fresh"
        result.append({**value, "event_id": previous["event_id"] if matching else None,
                       "at": previous["at"] if matching else None,
                       "observed_at": observation.at, "observation": quality})
    return result


def project(path: Path, paths=()) -> dict:
    """Read at most 1 MiB/512 events; never append, take a flock, or scan processes."""
    path = Path(path)
    errors = []
    retained, history, gaps = _journal(path, errors)
    observation = _Observation(errors)
    executions, groups = _executions(retained, history, gaps, observation)
    resources = _resources(path, paths, retained, executions, groups, observation)
    return {"executions": executions, "resources": resources,
            "events": [event for _, event in retained], "history": history, "errors": errors}
