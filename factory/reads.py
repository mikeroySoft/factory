"""Pass-local remote read retries, item skips, and durable quota backoff.

Only reads are replayed. Callers own item boundaries; a failed read never authorizes
an escalation, label edit, or claim transition.
"""
from __future__ import annotations

import contextvars
import json
import random
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from factory import lifecycle

_CURRENT = contextvars.ContextVar("factory_reads", default=None)
_NETWORK = re.compile(
    r"error connecting to api\.github\.com|could not resolve host(?:name)?|"
    r"connection reset|tls handshake timeout|i/o timeout|connection timed out|"
    r"temporary failure in name resolution|network is unreachable", re.IGNORECASE,
)
_RATE = re.compile(r"api rate limit exceeded|rate_limited|secondary rate limit|http(?:/\S+)?\s+429", re.IGNORECASE)
_FORBIDDEN = re.compile(r"http(?:/\S+)?\s+403", re.IGNORECASE)


class ReadFailure(RuntimeError):
    def __init__(self, code, result, *, quota=None, until=None):
        super().__init__(f"{code}: remote read exited {result.returncode}")
        self.code = code
        self.result = result
        self.quota = quota
        self.until = until


def is_read(command: list[str]) -> bool:
    if command[:2] == ["git", "fetch"]:
        return True
    if not command or command[0] != "gh" or len(command) < 3:
        return False
    if command[1] != "api":
        return command[2] in {"view", "list", "checks", "status"}
    method = None
    for i, arg in enumerate(command):
        if arg in {"--method", "-X"} and i + 1 < len(command):
            method = command[i + 1].upper()
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1].upper()
    if "graphql" in command:
        queries = [arg.split("=", 1)[1] for arg in command if arg.startswith("query=")]
        return method in {None, "GET", "POST"} and bool(queries) and all(
            re.match(r"\s*(?:query\b|\{)", query) for query in queries
        )
    return method == "GET" or (method is None and not any(
        arg in {"-f", "-F", "--field", "--raw-field", "--input"}
        or arg.startswith(("--field=", "--raw-field=", "--input=")) for arg in command
    ))


@dataclass
class Pass:
    journal: Path
    stage: str
    reads: int = 0
    failed: int = 0
    skipped: int = 0
    retries: int = 0
    stopped: bool = False
    code: str | None = None
    until: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, event, **fields):
        lifecycle.append(self.journal, {
            "event": event, "at": lifecycle._now(), "stage": self.stage, **fields,
        })


def current() -> Pass | None:
    return _CURRENT.get()


def copy_context() -> contextvars.Context:
    """Share read accounting with a helper thread, not its parent's mutable lifecycle."""
    context = contextvars.Context()
    context.run(_CURRENT.set, current())
    return context


@contextmanager
def pass_scope(journal: Path, stage: str, enabled=True):
    if not enabled or current() is not None:
        yield current()
        return
    state = Pass(Path(journal), stage)
    # Share the quota deadline across CLI stages and timer passes, not just threads.
    deadlines = [row["until"] for row in lifecycle.read_events(state.journal)
                 if row.get("event") == "read-backoff" and isinstance(row.get("until"), (int, float))]
    if deadlines and max(deadlines) > time.time():
        state.stopped, state.code, state.until = True, "rate_limited", max(deadlines)
    token = _CURRENT.set(state)
    try:
        try:
            yield state
        except ReadFailure as exc:
            skip(exc)
    finally:
        if state.skipped or state.retries or state.stopped:
            state.record("read-summary", reads=state.reads, failed=state.failed, skipped=state.skipped,
                         retries=state.retries, stopped=state.stopped, code=state.code, until=state.until)
            execution = lifecycle.current()
            if execution is not None and (state.skipped or state.stopped):
                execution.outcome, execution.reason = "mechanism_failure", state.code
            print(f"[{stage}] reads={state.reads} failed={state.failed} skipped={state.skipped} "
                  f"retries={state.retries} stopped={state.stopped} reason={state.code}", flush=True)
        _CURRENT.reset(token)


def skip(exc: ReadFailure, **identity):
    from factory.dispatch import stderr_tail

    state = current()
    if state is None:
        raise exc
    with state.lock:
        state.skipped += 1
        if not state.stopped:
            state.code = exc.code
        state.record("read-skip", code=exc.code, returncode=exc.result.returncode,
                     stderr_tail=stderr_tail(exc.result.stderr), quota=exc.quota, until=exc.until, **identity)
    execution = lifecycle.current()
    if execution is not None:
        execution.outcome, execution.reason = "mechanism_failure", exc.code


@contextmanager
def item(**identity):
    try:
        yield
    except ReadFailure as exc:
        skip(exc, **identity)


def _quota(env):
    try:
        result = subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True,
                                check=False, timeout=10, env=env)
        if result.returncode:
            return {"available": False, "returncode": result.returncode}
        data = json.loads(result.stdout)
        resources = data.get("resources")
        if not isinstance(resources, dict):
            return {"available": False}
        return {"available": True, "resources": {
            name: {key: value for key, value in bucket.items()
                   if key in {"remaining", "reset", "limit", "used"} and type(value) in {int, float}}
            for name, bucket in resources.items() if isinstance(bucket, dict)
        }}
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return {"available": False}


def _classify(command, result, env):
    text = result.stderr or ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    quota = _quota(env) if command[0] == "gh" else None
    resources = (quota or {}).get("resources", {})
    graphql = "graphql" in command or (
        command[1] in {"issue", "pr"} and command[2] != "checks"
    )
    resource = "graphql" if graphql else "core"
    bucket = resources.get(resource, {})
    exhausted = bucket.get("remaining") == 0
    limited = bool(_RATE.search(text) or (_FORBIDDEN.search(text) and exhausted))
    until = None
    if limited:
        reset = re.search(r"x-ratelimit-reset:\s*(\d+)", text, re.IGNORECASE)
        retry = re.search(r"retry-after:\s*(\d+)", text, re.IGNORECASE)
        deadline = (float(reset[1]) if reset else
                    time.time() + float(retry[1]) if retry else
                    bucket.get("reset") if exhausted else None)
        until = max(time.time(), float(deadline or time.time() + 60)) + 5
    return ReadFailure("rate_limited" if limited else "gh_read_failed", result, quota=quota, until=until)


def run(command, operation, check=True, env=None):
    """Run a remote read within a pass. Optional non-transient responses retain caller handling."""
    state = current()
    if state is None or not is_read(command):
        return operation()
    failure = None
    for attempt in range(4):
        with state.lock:
            if state.stopped:
                raise ReadFailure(state.code, subprocess.CompletedProcess(command, 0, "", "read circuit open"),
                                  until=state.until)
        try:
            result = operation()
        except subprocess.CalledProcessError as exc:
            result = subprocess.CompletedProcess(command, exc.returncode, exc.output, exc.stderr)
        if result.returncode == 0:
            with state.lock:
                state.reads += 1
            return result
        text = result.stderr or ""
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        transient = bool(_NETWORK.search(text))
        if not check and not transient and not (_RATE.search(text) or _FORBIDDEN.search(text)):
            return result  # e.g. a missing optional API or failing CI checks, not a transport failure
        if failure is None or failure.result.stderr != result.stderr:
            failure = _classify(command, result, env)
        else:
            failure.result = result
        if failure.code == "rate_limited" or not transient or attempt == 3:
            break
        delay = (5, 15, 30)[attempt] + random.uniform(0, 1)
        with state.lock:
            from factory.dispatch import stderr_tail

            state.retries += 1
            state.record("read-retry", code=failure.code, returncode=result.returncode, delay=delay,
                         stderr_tail=stderr_tail(result.stderr), quota=failure.quota)
        time.sleep(delay)
    with state.lock:
        state.reads += 1
        state.failed += 1
        if not state.stopped:
            state.code = failure.code
        if failure.code == "rate_limited":
            state.stopped, state.until = True, failure.until
            state.record("read-backoff", code=failure.code, until=failure.until, quota=failure.quota)
        elif state.failed >= 3 and state.failed * 2 > state.reads:
            state.stopped = True
            state.record("read-stop", code=failure.code, reason="most_reads_failed",
                         reads=state.reads, failed=state.failed)
    raise failure
