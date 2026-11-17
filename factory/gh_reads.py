"""Pass-scoped GitHub read isolation; mutations and programming errors never retry.

ReadFailed is a control-flow signal, like cancellation: it must bypass existing
Exception handlers that escalate, release claims, or substitute incomplete data.
Only explicit item/pass boundaries consume it. No failed command is retried.
"""
from __future__ import annotations

import contextvars
import json
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from functools import wraps

from factory import lifecycle

_CURRENT = contextvars.ContextVar("github_read_pass", default=None)
_ITEM = contextvars.ContextVar("github_read_item", default=None)


class ReadFailed(BaseException):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def is_read(cmd):
    if not cmd or cmd[0] != "gh" or len(cmd) < 3:
        return False
    if cmd[1] != "api":
        return cmd[2] in {"view", "list", "checks", "diff", "status"}
    method = next((arg.split("=", 1)[1] for arg in cmd if arg.startswith("--method=")), None)
    for flag in ("--method", "-X"):
        if flag in cmd:
            method = cmd[cmd.index(flag) + 1]
    fields = [arg for arg in cmd if arg.startswith(("query=", "-fquery=", "-Fquery="))]
    if fields and any("graphql" == arg or arg.endswith("/graphql") for arg in cmd):
        return not any(re.search(r"\bmutation\b", field.split("=", 1)[1]) for field in fields)
    return method.upper() in {"GET", "HEAD"} if method else not any(
        arg in {"-f", "-F", "--field", "--raw-field", "--input"}
        or arg.startswith(("--field=", "--raw-field=", "--input=", "-f", "-F")) for arg in cmd[2:]
    )


def current():
    return _CURRENT.get()


class Pass:
    def __init__(self, journal, stage, *, persistent=True):
        self.journal, self.stage, self.persistent = journal, stage, persistent
        self.lock = threading.Lock()
        self.reads = self.failed_reads = self.skipped = 0
        self.codes = {"rate_limited": 0, "gh_read_failed": 0}
        self.reason = None
        self.failed_tickets = set()
        self.reset = None
        for row in lifecycle.read_events(journal) if persistent else ():
            if row.get("event") == "gh-read-backoff" and row.get("reset", 0) > time.time():
                self.reset = max(self.reset or 0, row["reset"])
                self.reason = "rate_limited"

    @property
    def stopped(self):
        return self.reason is not None

    def emit(self, event, **fields):
        if not self.persistent:
            return
        execution = lifecycle.current()
        if execution:
            execution.commit()
        lifecycle.append(self.journal, {
            "event": event, "at": lifecycle._now(), "stage": self.stage,
            **({"execution_id": execution.execution_id, "dispatcher_run_id": execution.dispatcher_run_id}
               if execution else {}), **fields,
        })

    def failure(self, cmd, exc, env):
        # Use the same host and credentials as the failed read, never a separate login.
        probe_cmd = ["gh", "api", "rate_limit"]
        for flag in ("--hostname",):
            if flag in cmd:
                probe_cmd.extend([flag, cmd[cmd.index(flag) + 1]])
        probe = {}
        probe_error = None
        try:
            result = subprocess.run(probe_cmd, env=env, capture_output=True, text=True, timeout=10, check=False)
            if result.returncode:
                probe_error = f"exit {result.returncode}"
            else:
                resources = json.loads(result.stdout)["resources"]
                for name in ("core", "graphql"):
                    budget = resources[name]
                    if type(budget["remaining"]) is not int or type(budget["reset"]) is not int:
                        raise ValueError("invalid rate-limit budget")
                    probe[name] = {key: budget[key] for key in ("remaining", "reset")}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
            probe_error = type(error).__name__
        # Reuse the engine's bounded token redactor; stderr is evidence, not a diagnosis.
        from factory.dispatch import stderr_tail

        detail = stderr_tail(getattr(exc, "stderr", None) or str(exc))
        status = getattr(exc, "returncode", None)
        limited = status != 4 and bool(re.search(
            r"rate[ -]?limit|HTTP\s+429|status(?: code)?\s+429", detail, re.IGNORECASE,
        ))
        code = "rate_limited" if limited else "gh_read_failed"
        reset = None
        if limited:
            # Release commands and PR diffs use REST, unlike JSON issue/PR reads.
            resource = "core" if (
                cmd[1] in {"api", "release"} and "graphql" not in cmd
                or cmd[1:3] == ["pr", "diff"]
            ) else "graphql"
            budget = probe.get(resource, {})
            if budget.get("remaining") == 0 and budget.get("reset", 0) > time.time():
                reset = budget["reset"]
            elif match := re.search(r"retry[- ]after[:= ]+(\d+)", detail, re.IGNORECASE):
                reset = time.time() + int(match[1])
        identity = _identity(cmd)
        with self.lock:
            if identity.get("ticket") is not None:
                self.failed_tickets.add((identity.get("repo"), identity["ticket"]))
            self.failed_reads += 1
            self.skipped += 1
            self.codes[code] += 1
            if limited:
                self.reason, self.reset = code, reset
            elif self.reason is None and self.reads >= 3 and self.failed_reads * 2 > self.reads:
                self.reason = "gh_reads_unavailable"
            self.emit("gh-read-skipped", code=code, returncode=status, stderr_tail=detail,
                      rate_limit=probe, rate_limit_probe_error=probe_error, reset=reset,
                      command=cmd[1:3], **identity)
            if reset:
                self.emit("gh-read-backoff", code=code, reset=reset)
        execution = lifecycle.current()
        if execution:
            execution.outcome, execution.reason = "mechanism_failure", code
        print(f"[github] skipped {identity.get('ticket') or self.stage}: {code} (exit {status})", flush=True)
        raise ReadFailed(code) from exc


def _identity(cmd):
    identity = dict(_ITEM.get() or {})
    execution = lifecycle.current()
    ticket = identity.get("ticket", identity.get("pr", execution.ticket if execution else None))
    if ticket is None and cmd[1] in {"issue", "pr"} and len(cmd) > 3 and cmd[3].isdigit():
        ticket = int(cmd[3])
    identity["ticket"] = ticket
    return identity


def _expected_result(cmd, result, check):
    if result.returncode == 0:
        return True
    detail = result.stderr or ""
    if result.returncode == 1:
        if cmd[1:3] == ["pr", "view"] and "no pull requests found" in detail.lower():
            return True
        if cmd[1] == "api" and any("/compare/" in arg for arg in cmd[2:]) and re.search(r"HTTP 404\b", detail):
            return True
        if not check and cmd[1:3] == ["release", "view"] and re.fullmatch(
            r"release not found\.?\s*", detail, re.IGNORECASE,
        ):
            return True
    if check:
        return False
    # Pending/failing CI states describe data, not a failed read.
    if cmd[1:3] == ["pr", "checks"] and result.returncode in (1, 8):
        try:
            return isinstance(json.loads(result.stdout), list)
        except (ValueError, TypeError):
            return False
    return False


def run(cmd, invoke, *, env=None, check=True):
    state = current()
    if state is None or not cmd or cmd[0] != "gh":
        return invoke()
    identity = _identity(cmd)
    with state.lock:
        if state.stopped or (identity.get("repo"), identity["ticket"]) in state.failed_tickets:
            raise ReadFailed(state.reason or "gh_read_failed")
        read = is_read(cmd)
        if read:
            state.reads += 1
    if not read:
        return invoke()
    try:
        result = invoke()
        if _expected_result(cmd, result, check):
            if check:
                result.check_returncode()
            return result
        result.check_returncode()
    except subprocess.CalledProcessError as exc:
        if _expected_result(cmd, exc, check):
            # An expected absence is still raised for checked callers; their
            # operation decides whether the missing resource is acceptable.
            raise
        if exc.returncode < 0:
            raise
        state.failure(cmd, exc, env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        state.failure(cmd, exc, env)
    return result


@contextmanager
def item(ticket=None, **identity):
    token = _ITEM.set({**identity, **({"ticket": ticket} if ticket is not None else {})})
    try:
        yield
    except ReadFailed:
        if current() is None:
            raise
    finally:
        _ITEM.reset(token)


def items(rows):
    for row in rows:
        if (state := current()) is not None and state.stopped:
            break
        yield row


@contextmanager
def scope(journal, stage, *, persistent=True, isolated=False):
    if current() is not None and not isolated:
        yield current()
        return
    state = Pass(journal, stage, persistent=persistent)
    token = _CURRENT.set(state)
    try:
        yield state
    except ReadFailed:
        pass
    finally:
        if state.failed_reads or state.stopped:
            state.emit("pass-summary", reads=state.reads, failed_reads=state.failed_reads,
                       skipped=state.skipped, failure_codes=state.codes, reason=state.reason, reset=state.reset)
            print(f"[github] {stage}: {state.skipped} skipped, {state.failed_reads}/{state.reads} reads failed"
                  + (f"; {state.reason}" if state.reason else ""), flush=True)
        _CURRENT.reset(token)


def guarded(journal, stage, fallback=None, *, persistent=True):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with scope(journal(), stage, persistent=persistent) as state:
                if not state.stopped:
                    try:
                        return function(*args, **kwargs)
                    except ReadFailed:
                        pass
            return fallback() if callable(fallback) else fallback
        return wrapped
    return decorate
