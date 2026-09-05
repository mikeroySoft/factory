"""Versioned execution evidence in the existing, flock-serialized events journal.

Only newline-terminated JSON objects are records. Legacy objects remain unchanged;
an interrupted tail is invalidated before the next append. Reconciliation uses
process identities and existing lock evidence, never timestamps or artifact age.
"""
from __future__ import annotations

import contextvars
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CONTEXT_ENV = "FACTORY_LIFECYCLE_CONTEXT"
_CURRENT = contextvars.ContextVar("factory_execution", default=None)
_IDENTITY_FIELDS = (
    "dispatcher_run_id", "root_execution_id", "execution_id", "parent_execution_id",
    "ticket", "attempt", "review_round", "stage",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _rows(handle) -> list[dict]:
    handle.seek(0)
    rows = []
    for line in handle:
        if not line.endswith(b"\n"):
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write(handle, row: dict) -> None:
    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    handle.seek(0, os.SEEK_END)
    if handle.tell():
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) != b"\n":
            # Never promote a syntactically complete but uncommitted tail to a record.
            handle.write(b"\x00\n")
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())


def append(path: Path, row: dict) -> None:
    """Append a legacy or versioned row without interleaving concurrent writers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        _write(handle, row)


def read_events(path: Path) -> list[dict]:
    """Read committed JSON objects, ignoring legacy malformed/interrupted lines."""
    try:
        with Path(path).open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            return _rows(handle)
    except FileNotFoundError:
        return []


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or None
    except OSError:
        return None


def _process(pid: int) -> dict:
    """Read Linux identity; /proc stat starttime is field 22, not process age."""
    directory = Path("/proc") / str(pid)
    text = (directory / "stat").read_text()
    fields = text[text.rfind(")") + 2:].split()
    return {
        "pid": pid, "boot_id": _boot_id(), "start_ticks": int(fields[19]),
        "pid_namespace": os.readlink(directory / "ns/pid"),
        "uid": directory.stat().st_uid, "state": fields[0], "ppid": int(fields[1]),
    }


def _identity(pid: int) -> dict:
    try:
        return _process(pid)
    except (OSError, ValueError, IndexError):
        return {"pid": pid, "boot_id": _boot_id(), "start_ticks": None,
                "pid_namespace": None, "uid": None, "state": None, "ppid": None}


def _process_state(identity: dict | None) -> str:
    if not isinstance(identity, dict):
        return "unknown"
    if (type(identity.get("pid")) is not int or identity["pid"] <= 0
            or type(identity.get("start_ticks")) is not int or identity["start_ticks"] < 0
            or not isinstance(identity.get("boot_id"), str) or not identity["boot_id"]
            or not isinstance(identity.get("pid_namespace"), str) or not identity["pid_namespace"]):
        return "unknown"
    boot = _boot_id()
    if boot is None:
        return "unknown"
    if boot != identity["boot_id"]:
        return "dead"
    try:
        if os.readlink("/proc/self/ns/pid") != identity["pid_namespace"]:
            return "unknown"
        process = _process(identity["pid"])
    except FileNotFoundError:
        # A readable proc mount in our recorded PID namespace makes absence meaningful.
        try:
            mounts = Path("/proc/mounts").read_text()
            if any("hidepid=" in line and "hidepid=0" not in line for line in mounts.splitlines() if " /proc " in line):
                return "unknown"
            _process(os.getpid())
        except (OSError, ValueError, IndexError):
            return "unknown"
        return "dead"
    except (OSError, ValueError, IndexError, TypeError):
        return "unknown"
    if process["boot_id"] != identity["boot_id"]:
        return "unknown"  # The boot changed during observation.
    if process["start_ticks"] != identity["start_ticks"] or process["state"] in {"Z", "X"}:
        return "dead"
    return "alive"


def _lock(path: str | Path) -> dict:
    path = Path(path).absolute()
    try:
        stat = path.stat()
        return {"path": str(path), "device": stat.st_dev, "inode": stat.st_ino}
    except OSError:
        return {"path": str(path), "device": None, "inode": None}


def _lock_state(lock: dict) -> str:
    if lock.get("device") is None or lock.get("inode") is None:
        return "unknown"
    try:
        with Path(lock["path"]).open("rb") as handle:
            stat = os.fstat(handle.fileno())
            if (stat.st_dev, stat.st_ino) != (lock["device"], lock["inode"]):
                return "unknown"  # The old inode could still be locked after unlink.
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "held"
            fcntl.flock(handle, fcntl.LOCK_UN)
            return "free"
    except (OSError, KeyError, TypeError):
        return "unknown"


def _inherited() -> dict:
    try:
        value = json.loads(os.environ.get(CONTEXT_ENV, "null"))
        if isinstance(value, dict) and value.get("schema_version") == 1 and isinstance(value.get("path"), str):
            return value
    except (ValueError, TypeError):
        pass
    return {}


def current():
    return _CURRENT.get()


class Execution:
    def __init__(self, path: Path, stage: str, *, ticket=None, attempt=None,
                 review_round=None, dispatcher=False, lock=None):
        parent = current()
        inherited = parent._context() if parent is not None else _inherited()
        self.path = Path(inherited.get("path", path)).absolute()
        self.execution_id = str(uuid.uuid4())
        self.dispatcher_run_id = str(uuid.uuid4()) if dispatcher else inherited.get("dispatcher_run_id")
        self.parent_execution_id = inherited.get("parent_execution_id")
        self.root_execution_id = inherited.get("root_execution_id") or self.execution_id
        self.ticket = inherited.get("ticket") if ticket is None else ticket
        self.attempt = inherited.get("attempt") if attempt is None else attempt
        self.review_round = inherited.get("review_round") if review_round is None else review_round
        self.stage = stage
        self.outcome = "completed"
        self.reason = None
        self.process = _identity(os.getpid())
        self.locks = list(inherited.get("locks", []))
        if lock is not None:
            self.locks.append(_lock(lock))
        self.execution_ids = list(inherited.get("execution_ids", [])) + [self.execution_id]
        self._sequence = 0
        self._terminal = False
        self._handoffs = []
        self._children = {}

    def _context(self) -> dict:
        return {
            "schema_version": 1, "path": str(self.path),
            "dispatcher_run_id": self.dispatcher_run_id,
            "root_execution_id": self.root_execution_id,
            "parent_execution_id": self.execution_id,
            "ticket": self.ticket, "attempt": self.attempt, "review_round": self.review_round,
            "execution_ids": self.execution_ids, "locks": self.locks,
        }

    def emit(self, kind: str, **fields) -> dict:
        if kind == "lock_acquired":
            self.locks.append(_lock(fields["lock"]))
        elif kind == "lock_released":
            path = str(Path(fields["lock"]).absolute())
            self.locks = [lock for lock in self.locks if lock["path"] != path]
        if kind == "exit" and self._children:
            fields["evidence"] = {
                "children": [{"process": child, "state": _process_state(child)}
                             for child in self._children.values()],
            }
            if self.outcome == "completed":
                self.outcome = "unknown"
                self.reason = "scope exited before its registered children were reaped"
        row = {
            **fields, **{key: getattr(self, key) for key in _IDENTITY_FIELDS},
            "event": "lifecycle", "schema_version": 1, "event_id": str(uuid.uuid4()),
            "kind": kind, "at": _now(), "outcome": self.outcome if kind == "exit" else None,
            "reason": self.reason if kind == "exit" else None,
            "process": self.process, "locks": list(self.locks),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            # One owning Execution object; the journal flock also serializes its threads.
            if self._terminal:
                raise RuntimeError("execution already has a terminal lifecycle event")
            row["sequence"] = self._sequence + 1
            _write(handle, row)
            self._sequence = row["sequence"]
            self._terminal = kind == "exit"
        return row

    def env(self) -> dict[str, str]:
        """Persist launch intent before handing context to Popen; return a full env."""
        handoff_id = str(uuid.uuid4())
        self.emit("handoff", handoff_id=handoff_id)
        self._handoffs.append(handoff_id)
        context = {**self._context(), "handoff_id": handoff_id}
        return {**os.environ, CONTEXT_ENV: json.dumps(context, separators=(",", ":"))}

    def child(self, pid: int) -> None:
        identity = _identity(pid)
        handoff_id = self._handoffs.pop() if self._handoffs else None
        self._children[pid] = identity
        self.emit("child_start", child_process=identity, handoff_id=handoff_id)

    def child_done(self, pid: int) -> None:
        identity = self._children.pop(pid, {
            "pid": pid, "boot_id": None, "start_ticks": None,
            "pid_namespace": None, "uid": None, "state": None, "ppid": None,
        })
        self.emit("child_exit", child_process=identity)


@contextmanager
def scope(path: Path, stage: str, *, ticket=None, attempt=None, review_round=None,
          dispatcher=False, lock=None):
    execution = Execution(path, stage, ticket=ticket, attempt=attempt, review_round=review_round,
                          dispatcher=dispatcher, lock=lock)
    execution.emit("enter")
    token = _CURRENT.set(execution)
    try:
        yield execution
    except BaseException as error:
        if isinstance(error, (FileNotFoundError, PermissionError)):
            execution.outcome = "mechanism_failure"
            execution.reason = f"{type(error).__name__}: {error}"
        elif execution.outcome == "completed":
            execution.outcome = "unknown"
            execution.reason = f"{type(error).__name__}: {error}"
        try:
            execution.emit("exit")
        except Exception:
            # Recording failure must not replace the caller's original exception.
            pass
        raise
    else:
        execution.emit("exit")
    finally:
        _CURRENT.reset(token)


def _valid_process(value) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or type(value.get("pid")) is not int or value["pid"] <= 0:
        return False
    return (all(key in value and (value[key] is None or isinstance(value[key], str))
                for key in ("boot_id", "pid_namespace", "state"))
            and all(key in value and (value[key] is None or type(value[key]) is int)
                    for key in ("start_ticks", "uid", "ppid")))


def _lifecycle(row: dict) -> bool:
    required = {*_IDENTITY_FIELDS, "event", "schema_version", "event_id", "sequence",
                "kind", "at", "outcome", "reason", "process", "locks"}
    return (required <= row.keys()
            and row["event"] == "lifecycle" and type(row["schema_version"]) is int
            and row["schema_version"] == 1
            and all(isinstance(row[key], str) and bool(row[key])
                    for key in ("execution_id", "root_execution_id", "event_id", "stage", "kind", "at"))
            and type(row["sequence"]) is int and row["sequence"] > 0
            and all(row[key] is None or isinstance(row[key], str)
                    for key in ("dispatcher_run_id", "parent_execution_id", "outcome", "reason"))
            and all(row[key] is None or type(row[key]) is int
                    for key in ("ticket", "attempt", "review_round"))
            and _valid_process(row["process"])
            and isinstance(row["locks"], list)
            and all(isinstance(lock, dict) and isinstance(lock.get("path"), str)
                    and all(key in lock and (lock[key] is None or type(lock[key]) is int)
                            for key in ("device", "inode")) for lock in row["locks"])
            and ("child_process" not in row or
                 (isinstance(row["child_process"], dict) and _valid_process(row["child_process"])))
            and (row.get("handoff_id") is None or isinstance(row.get("handoff_id"), str)))


def _descendants(executions: list[dict]) -> dict[str, dict]:
    """Find tagged/reparented children and ordinary live ancestry in one proc scan."""
    found = {entry["execution_id"]: {"processes": [], "complete": True} for entry in executions}
    if not executions:
        return found
    uids = {(entry.get("process") or {}).get("uid") for entry in executions}
    for entry in executions:
        if type((entry.get("process") or {}).get("uid")) is not int:
            found[entry["execution_id"]]["complete"] = False
    processes = {}
    contexts = {}
    try:
        directories = list(Path("/proc").iterdir())
    except OSError:
        for evidence in found.values():
            evidence["complete"] = False
        return found
    for directory in directories:
        if not directory.name.isdecimal():
            continue
        try:
            if directory.stat().st_uid not in uids:
                continue
            process = _process(int(directory.name))
            if process["state"] in {"Z", "X"}:
                continue
            processes[process["pid"]] = process
            data = (directory / "environ").read_bytes()
            prefix = CONTEXT_ENV.encode() + b"="
            for item in data.split(b"\x00"):
                if item.startswith(prefix):
                    try:
                        context = json.loads(item[len(prefix):])
                        if isinstance(context, dict):
                            contexts[process["pid"]] = context
                    except (ValueError, UnicodeError):
                        pass
                    break
        except FileNotFoundError:
            continue  # A process disappearing during enumeration cannot still be live.
        except (OSError, ValueError, IndexError):
            for entry in executions:
                found[entry["execution_id"]]["complete"] = False
    for entry in executions:
        execution_id = entry["execution_id"]
        owner = entry.get("process") or {}
        for pid, process in processes.items():
            context = contexts.get(pid, {})
            tagged = (context.get("schema_version") == 1
                      and isinstance(context.get("execution_ids"), list)
                      and execution_id in context.get("execution_ids", []))
            cursor = process
            seen = set()
            descendant = False
            while cursor["pid"] not in seen:
                seen.add(cursor["pid"])
                if (cursor["pid"] == owner.get("pid")
                        and cursor["start_ticks"] == owner.get("start_ticks")
                        and cursor["boot_id"] == owner.get("boot_id")):
                    descendant = pid != owner["pid"]
                    break
                cursor = processes.get(cursor["ppid"])
                if cursor is None:
                    break
            if tagged or descendant:
                found[execution_id]["processes"].append(process)
    return found


def _evidence(events: list[dict], descendants: dict) -> tuple[str, dict]:
    entry = events[0]
    process_state = _process_state(entry.get("process"))
    boot = _boot_id()
    origin_boot = (entry.get("process") or {}).get("boot_id")
    rebooted = bool(boot and origin_boot and boot != origin_boot and process_state == "dead")
    children = {}
    handoffs = set()
    for event in events:
        if event["kind"] == "handoff":
            handoffs.add(event.get("handoff_id"))
        elif event["kind"] == "child_start":
            child = event.get("child_process", {})
            children[child.get("pid")] = child
            handoffs.discard(event.get("handoff_id"))
        elif event["kind"] == "child_exit":
            children.pop(event.get("child_process", {}).get("pid"), None)
    child_states = [{"process": child, "state": _process_state(child)} for child in children.values()]
    locks = [{**lock, "state": _lock_state(lock)} for lock in events[-1].get("locks", [])]
    evidence = {"process": process_state, "children": child_states, "locks": locks,
                "descendants": descendants["processes"], "scan_complete": descendants["complete"],
                "descendant_absence_proven": rebooted or not descendants.get("launched", False),
                "pending_handoffs": sorted(value for value in handoffs if isinstance(value, str))}
    if rebooted:
        return "interrupted", evidence
    if process_state == "alive" or any(child["state"] == "alive" for child in child_states) or descendants["processes"]:
        return "active", evidence
    if (process_state == "unknown" or any(child["state"] == "unknown" for child in child_states)
            or any(lock["state"] != "free" for lock in locks)
            or not descendants["complete"] or handoffs or descendants.get("launched", False)):
        return "unknown", evidence
    return "interrupted", evidence


def observe(path: Path) -> list[dict]:
    """Project independent executions and persist each proven interruption once.

    Ordinary product outcomes (check failure, revision, escalation) are completed
    mechanisms, not runtime failures. Only mechanism_failure yields failed.
    """
    try:
        handle = Path(path).open("r+b")
    except FileNotFoundError:
        return []
    with handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        groups = {}
        for row in _rows(handle):
            if _lifecycle(row):
                groups.setdefault(row["execution_id"], []).append(row)
        groups = {key: sorted(events, key=lambda row: row["sequence"])
                  for key, events in groups.items() if any(event["kind"] == "enter" for event in events)}
        open_groups = [events for events in groups.values() if not any(event["kind"] == "exit" for event in events)]
        # A nested stage's launch also makes its open ancestors responsible for descendants.
        launched_ids = set()
        for execution_id, events in groups.items():
            if not any(event["kind"] in {"handoff", "child_start"} for event in events):
                continue
            cursor = execution_id
            while cursor in groups and cursor not in launched_ids:
                launched_ids.add(cursor)
                cursor = groups[cursor][0].get("parent_execution_id")
        launched = [events[0] for events in open_groups if events[0]["execution_id"] in launched_ids]
        descendants = {events[0]["execution_id"]: {"processes": [], "complete": True}
                       for events in open_groups}
        descendants.update(_descendants(launched))
        for execution_id in launched_ids:
            if execution_id in descendants:
                # Reparented children may exec with a scrubbed environment; a proc scan
                # proves visible survivors, not the absence of every possible descendant.
                descendants[execution_id]["launched"] = True
        result = []
        for execution_id, events in groups.items():
            entry = next(event for event in events if event["kind"] == "enter")
            terminal = next((event for event in events if event["kind"] == "exit"), None)
            evidence = None
            if terminal is None:
                state, evidence = _evidence(events, descendants[execution_id])
                if state == "interrupted":
                    terminal = {
                        **{key: events[-1].get(key) for key in _IDENTITY_FIELDS},
                        "event": "lifecycle", "schema_version": 1,
                        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"factory:interrupted:{execution_id}")),
                        "sequence": events[-1]["sequence"] + 1, "kind": "exit", "at": _now(),
                        "outcome": "interrupted", "reason": "recorded execution ended according to process and lock evidence",
                        "process": entry.get("process"), "locks": events[-1].get("locks", []),
                        "reconciled": True, "observer_process": _identity(os.getpid()), "evidence": evidence,
                    }
                    _write(handle, terminal)
                    events.append(terminal)
            if terminal is not None:
                outcome = terminal.get("outcome")
                state = {"interrupted": "interrupted", "mechanism_failure": "failed", "unknown": "unknown", None: "unknown"}.get(outcome, "completed")
                evidence = terminal.get("evidence", evidence)
            result.append({
                **{key: events[-1].get(key) for key in _IDENTITY_FIELDS},
                "state": state, "entered_at": entry.get("at"),
                "ended_at": terminal.get("at") if terminal else None,
                "outcome": terminal.get("outcome") if terminal else None,
                "reason": terminal.get("reason") if terminal else None,
                "events": events, "evidence": evidence,
            })
        return result
