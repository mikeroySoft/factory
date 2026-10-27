"""Triage needs-triage issues using the local model endpoint.

Stateless worker: reads issues via gh, asks the local model for a decision,
applies labels/comments via gh. wontfix is only ever proposed, never applied.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path

from factory import config, lifecycle, onboard
from factory.config import (
    LABEL_AGENT,
    LABEL_APPROVED,
    LABEL_CHORE,
    LABEL_HUMAN,
    LABEL_INFO,
    LABEL_REVIEW,
    LABEL_TRIAGE,
    LABEL_VIABILITY,
    Config,
)

cfg: Config
LLM_URL = os.environ.get("FACTORY_LLM_URL", config.DEFAULT_LLM_URL)
LLM_MODEL = os.environ.get("FACTORY_LLM_MODEL", config.DEFAULT_LLM_MODEL)
LOGIN: str | None = None  # `gh` login this process posts as; read once per process
PROBE_FAILURES = 3
PROBE_COOLDOWN = 15 * 60
PROBE_TIMEOUT = 3


def configure(c: Config) -> None:
    global cfg, LLM_URL, LLM_MODEL
    cfg = c
    LLM_URL = os.environ.get("FACTORY_LLM_URL", cfg.llm_url)
    LLM_MODEL = os.environ.get("FACTORY_LLM_MODEL", cfg.llm_model)


def models_url(c: Config) -> str:
    return f"{c.llm_url.rsplit('/chat/completions', 1)[0].rstrip('/')}/models"


def probe_endpoint(c: Config, *, opener=None) -> tuple[bool, str]:
    endpoint = models_url(c)
    opener = opener or urllib.request.urlopen
    try:
        with opener(endpoint, timeout=PROBE_TIMEOUT):
            return True, "ready"
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(1024).decode(errors="replace") or str(exc.reason)
        finally:
            exc.close()
        if exc.code == 503 and "loading model" in detail.casefold():
            return True, "loading model"
        return False, f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _probe_state(path) -> dict:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return {"failures": 0, "last_restart": None}
    if not isinstance(state, dict):
        return {"failures": 0, "last_restart": None}
    failures = state.get("failures")
    last_restart = state.get("last_restart")
    return {
        "failures": failures if type(failures) is int and failures >= 0 else 0,
        "last_restart": last_restart if type(last_restart) in (int, float) else None,
    }


def _write_probe_state(path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def _restart_service(service: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "restart", service],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print(
            f"triage probe: could not restart {service}: "
            f"{result.stderr.strip() or result.returncode}",
            file=sys.stderr,
        )
    return result.returncode == 0


def _probe_paths(c: Config, endpoint: str) -> tuple[Path, Path]:
    if not c.triage_service:
        base = c.factory / "triage-probe"
    else:
        state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        key = hashlib.sha256(f"{c.triage_service}\n{endpoint}".encode()).hexdigest()[:16]
        base = state_home / "factory" / f"triage-probe-{key}"
    return base.with_suffix(".json"), base.with_suffix(".lock")


def run_probe(c: Config, *, now: float | None = None) -> int:
    """Probe the model endpoint once, persisting failure and restart-cooldown state."""
    endpoint = models_url(c)
    state_path, lock_path = _probe_paths(c, endpoint)
    c.factory.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as state_lock:
        fcntl.flock(state_lock, fcntl.LOCK_EX)
        state = _probe_state(state_path)
        alive, detail = probe_endpoint(c)
        if alive:
            state["failures"] = 0
            _write_probe_state(state_path, state)
            print(f"triage probe: {detail} at {endpoint}")
            return 0

        state["failures"] = min(state["failures"] + 1, PROBE_FAILURES)
        current = time.time() if now is None else now
        service = c.triage_service
        with lifecycle.scope(c.factory / "events.jsonl", "triage-probe", lazy=True) as execution:
            execution.outcome = "mechanism_failure"
            execution.reason = "triage_endpoint_unavailable"
            execution.emit(
                "result",
                endpoint=endpoint,
                error=detail,
                failures=state["failures"],
                service=service,
            )
            last_restart = state["last_restart"]
            if (
                service
                and state["failures"] >= PROBE_FAILURES
                and (last_restart is None or current - last_restart >= PROBE_COOLDOWN)
            ):
                state["last_restart"] = current
                _write_probe_state(state_path, state)
                succeeded = _restart_service(service)
                execution.emit("result", service=service, restarted=True, succeeded=succeeded)
                print(f"triage probe: {'restarted' if succeeded else 'restart failed for'} {service}")
            else:
                _write_probe_state(state_path, state)
                print(f"triage probe: failure {state['failures']}/{PROBE_FAILURES}: {detail}")
        return 1


def login() -> str:
    global LOGIN
    if LOGIN is None:
        LOGIN = onboard.gh_login(dict(os.environ))
    return LOGIN


def identity_mismatch() -> str | None:
    """Why this process must not post, or None. With `[install].dispatch_env` the factory
    posts as the dispatcher account; a triage whose `gh` authenticates as anyone else (an
    owner shell, a unit installed without that env) would post triage as the wrong login."""
    dispatch_env = cfg.install.get("dispatch_env")
    if not dispatch_env:
        return None  # no separate dispatcher identity is configured
    expected = onboard.gh_login({**os.environ, **cfg.install["env"], **dispatch_env})
    if expected and login().casefold() == expected.casefold():
        return None
    return f"gh authenticates as {login() or 'nobody'}, not the configured dispatcher {expected or '(unknown)'}"


LABEL_TABLE = "\n".join(
    [
        "| Label | Meaning |",
        "|-------|---------|",
        *(
            f"| {label} | {meaning} |"
            for label, (_color, meaning) in config.LABELS.items()
            if label not in (LABEL_APPROVED, LABEL_CHORE, LABEL_REVIEW, LABEL_VIABILITY, config.LABEL_WONTFIX,
                             config.LABEL_EXPERIMENT)
        ),
        "| wontfix | Will not be actioned |",
    ]
)

DECISIONS = (LABEL_AGENT, LABEL_INFO, LABEL_HUMAN, config.LABEL_WONTFIX)


class _Decision(dict):
    def __init__(self, value: dict, *, actor: str, name: str, cost: dict | None = None) -> None:
        super().__init__(value)
        self.trace_actor = actor
        self.trace_name = name
        self.trace_cost = cost


class _ModelReply(str):
    def __new__(cls, value: str, cost: dict | None = None):
        reply = super().__new__(cls, value)
        reply.cost = cost
        return reply

ACCEPTANCE_HINTS = re.compile(
    r"acceptance|exit gate|verification|expected behavior|steps to reproduce",
    re.IGNORECASE,
)


WATCH_TITLE = "factory runtime failure:"
WATCH_FINGERPRINT = re.compile(r"^Fingerprint: \S+(?:\n|$)")
WATCH_BRIEF = (
    "Reproduce the failure against current main and the installed engine using the evidence in "
    "the issue. If it still occurs, fix the root cause with a regression test. If it does not "
    "reproduce, or the evidence is a host condition outside the repository (network, a missing "
    "executable on PATH, model endpoint down, or SIGTERM from a systemd stop), commit nothing "
    "and state that in the handoff so the manager can resolve it."
)


def watch_filed(issue: dict) -> bool:
    """Filed by `factory watch`: this repository's journal recorded the issue number, or the
    account this triage posts as wrote it (another install's watch routed it here), so a
    hand-written issue cannot copy the format to skip triage."""
    labels = {label.get("name") for label in issue.get("labels", [])}
    if not (WATCH_FINGERPRINT.match(issue.get("body") or "")
            and str(issue.get("title") or "").startswith(WATCH_TITLE) and "bug" in labels):
        return False
    if issue.get("number") in {row.get("issue") for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                               if row.get("event") == "watch"}:
        return True
    author = (issue.get("author") or {}).get("login") or ""
    return bool(author) and author.casefold() == login().casefold()

def deterministic_needs_info(body: str, comments: str) -> str | None:
    """Cheap lint before the LLM: obviously under-specified -> needs-info."""
    if len(body.strip()) < 80:
        return (
            "The issue body is too short to act on. Describe the problem and "
            "add acceptance criteria (an observable done-condition) plus the "
            "command that verifies it."
        )
    text = f"{body}\n{comments}"
    if not ACCEPTANCE_HINTS.search(text) and "```" not in text:
        return (
            "No acceptance criteria found. Add an observable done-condition "
            "and the exact command that verifies it."
        )
    return None


def gh(*args: str, execution=None) -> str:
    command = ["gh", *args, "--repo", cfg.repo]
    if execution:
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=execution.env()
        ) as proc:
            try:
                execution.child(proc.pid)
                out, err = proc.communicate()
            except BaseException:
                proc.kill()
                proc.wait()
                raise
            finally:
                if proc.poll() is not None:
                    execution.child_done(proc.pid)
    else:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        out, err = proc.stdout, proc.stderr
    if execution:
        execution.emit("result", command="gh", returncode=proc.returncode)
    if proc.returncode != 0:
        if execution:
            execution.outcome = "unknown"
            execution.reason = "github_command_failed"
        print(f"gh {' '.join(args)} failed: {err.strip()}", file=sys.stderr)
        raise SystemExit(1)
    return out


def system_prompt() -> str:
    return f"""You are the triage bot for the {cfg.repo} issue tracker.

Label reference:

{LABEL_TABLE}

Choose exactly one decision for the issue:
- "ready-for-agent": the issue is fully specified — it has a problem statement AND acceptance criteria or an observable done-condition. Also fill "brief": a short restatement of the acceptance criteria and the exact verification command, for the implementing agent. For a bug where an automated reproduction test is impractical, end "rationale" with a separate `No repro test: <reason>` line; omit that line when reproduction is practical.
- "needs-info": information is missing; state the specific missing information as a question.
- "ready-for-human": needs design judgment, touches release, signing, or security policy, or has blast radius beyond this repository.
- "wontfix-proposal": the issue should not be actioned; explain why.

Respond with strict JSON only, no markdown, no prose outside the JSON:
{{"decision": "<one of ready-for-agent|needs-info|ready-for-human|wontfix-proposal>", "rationale": "<one short paragraph, optionally followed by the No repro test line>", "question": "<the question for the reporter, or empty string if decision is not needs-info>", "brief": "<agent brief for ready-for-agent, else empty string>"}}"""


def _usage_fields(body: object) -> dict | None:
    if not isinstance(body, dict) or not isinstance(body.get("usage"), dict):
        return None
    usage = body["usage"]
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if (
        type(prompt) is not int
        or type(completion) is not int
        or prompt < 0
        or completion < 0
    ):
        return None
    fields = {"prompt_tokens": prompt, "completion_tokens": completion}
    rate = usage.get("prefix_cache_hit_rate")
    if type(rate) in (int, float) and 0 <= rate <= 1:
        fields["prefix_cache_hit_rate"] = rate
    else:
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if type(cached) is int and 0 <= cached <= prompt and prompt:
            fields["prefix_cache_hit_rate"] = cached / prompt
    return fields


def _record_usage(execution, usage: dict | None) -> None:
    if execution is None or execution.ticket is None or usage is None:
        return
    execution.commit()
    lifecycle.append(
        execution.path,
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "llm-usage",
            "ticket": execution.ticket,
            "stage": "triage",
            "model": LLM_MODEL,
            "local": config.local_endpoint(LLM_URL),
            "execution_id": execution.execution_id,
            "root_execution_id": execution.root_execution_id,
            **usage,
        },
    )


class EndpointTimeout(SystemExit):
    """The model timed out on one request; the endpoint is up, so the pass can go on."""


def call_llm(messages: list[dict], execution=None) -> str:
    started = time.monotonic()
    payload = json.dumps(
        {
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.1,
        }
    ).encode()
    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.triage_timeout) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        timed_out = isinstance(exc, TimeoutError) or (
            isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
        )
        if execution:
            execution.outcome = "unknown" if timed_out else "mechanism_failure"
            execution.reason = "triage_endpoint_timeout" if timed_out else "triage_endpoint_unavailable"
            execution.emit("result", timed_out=timed_out, timeout_seconds=cfg.triage_timeout)
        if timed_out:
            where = "connecting to" if isinstance(exc, urllib.error.URLError) else "at"
            print(
                f"local model request timed out after {cfg.triage_timeout}s {where} {LLM_URL}\n"
                "The model server may be busy or the request too large; raise [triage].timeout if this persists.",
                file=sys.stderr,
            )
        else:
            print(
                f"cannot reach local model at {LLM_URL}: {exc}\n"
                "Is the model server running? Set [triage].url in .factory.toml if the endpoint differs.",
                file=sys.stderr,
            )
        # URLError(TimeoutError) is a connect timeout (endpoint unreachable); a bare one is a slow model.
        raise (EndpointTimeout if isinstance(exc, TimeoutError) else SystemExit)(2) from exc
    usage = _usage_fields(body)
    _record_usage(execution, usage)
    cost = None
    if usage is not None:
        reported = body["usage"]
        details = reported.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if type(cached) is not int or not 0 <= cached <= usage["prompt_tokens"]:
            rate = usage.get("prefix_cache_hit_rate")
            cached = int(usage["prompt_tokens"] * rate) if type(rate) in (int, float) and 0 <= rate <= 1 else 0
        cost = {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "cached_tokens": cached,
            "seconds": time.monotonic() - started,
        }
    return _ModelReply(body["choices"][0]["message"]["content"], cost)


def parse_decision(text: str) -> dict | None:
    # ponytail: naive fence strip; structured-output API if the model misbehaves
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("decision") not in DECISIONS:
        return None
    obj.setdefault("rationale", "")
    obj.setdefault("question", "")
    obj.setdefault("brief", "")
    return obj


def fetch_issue(number: int, execution=None) -> dict:
    raw = gh(
        "issue",
        "view",
        str(number),
        "--json",
        "number,title,body,comments,labels,state,updatedAt,author",
        execution=execution,
    )
    return json.loads(raw)


# ponytail: our own label swap lands seconds after the comment; a human change inside this
# window goes unnoticed until the next change. Read the issue timeline if that ever matters.
SETTLE = timedelta(minutes=1)


def unchanged_since_triage(issue: dict) -> bool:
    """The last comment is ours and the issue has not been updated since (no comment, body
    or title edit, label change): triaging again would only repeat the same comment."""
    comments = issue.get("comments") or []
    if not comments or not comments[-1].get("body", "").startswith(("Triage: ", "Triage proposal: ")):
        return False
    posted = datetime.fromisoformat(comments[-1]["createdAt"])
    return datetime.fromisoformat(issue["updatedAt"]) - posted < SETTLE


def triage_issue(issue: dict, execution=None) -> dict | None:
    """Choose a deterministic route where possible, otherwise ask the model."""
    if watch_filed(issue):
        return _Decision({
            "decision": LABEL_AGENT,
            "rationale": "Factory watch evidence requires an agent investigation.",
            "question": "",
            "brief": WATCH_BRIEF,
        }, actor="deterministic", name="watch_filed")
    comments = "\n\n".join(
        f"Comment by {c.get('author', {}).get('login', '?')}:\n{c.get('body', '')}"
        for c in issue.get("comments", [])
    )
    body = issue.get("body") or ""
    question = deterministic_needs_info(body, comments)
    if question:
        return _Decision({
            "decision": LABEL_INFO,
            "rationale": "Deterministic pre-check: under-specified for the factory.",
            "question": question,
            "brief": "",
        }, actor="deterministic", name="deterministic_needs_info")
    user_msg = (
        f"Issue #{issue['number']}: {issue['title']}\n\n"
        f"Body:\n{body or '(empty)'}\n\n"
        f"Comments:\n{comments or '(none)'}"
    )
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_msg},
    ]
    costs = []
    for _attempt in range(2):
        reply = call_llm(messages, execution=execution)
        if getattr(reply, "cost", None) is not None:
            costs.append(reply.cost)
        decision = parse_decision(reply)
        if decision is not None:
            cost = None if not costs else {
                key: sum(value[key] for value in costs)
                for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "seconds")
            }
            return _Decision(decision, actor="model", name="triage", cost=cost)
        messages.append({"role": "assistant", "content": reply})
        messages.append(
            {
                "role": "user",
                "content": "That was not valid JSON matching the required schema. "
                "Reply with ONLY the JSON object.",
            }
        )
    return None


def apply_decision(
    number: int, decision: dict, dry_run: bool, execution=None, issue: dict | None = None,
) -> None:
    label = decision["decision"]
    rationale = decision["rationale"]
    if label == LABEL_INFO and decision["question"]:
        comment = f"Triage: {rationale}\n\nQuestion: {decision['question']}"
    elif label == LABEL_AGENT and decision.get("brief"):
        comment = f"Triage: {rationale}\n\nAgent brief: {decision['brief']}"
    elif label == "wontfix-proposal":
        comment = f"Triage proposal: wontfix — {rationale}"
    else:
        comment = f"Triage: {rationale}"

    if dry_run:
        print(f"#{number} [dry-run] decision: {json.dumps(decision)}")
        print(f"#{number} [dry-run] comment: {comment}")
        return
    from factory import __version__, dispatch, trace

    issue = issue or {}
    dispatch.configure(cfg)
    rows = lifecycle.read_events(cfg.factory / "events.jsonl")
    parent = next((row.get("parent") for row in reversed(rows)
                   if row.get("event") == "issue-created" and row.get("ticket") == number), None)
    actor_kind = getattr(decision, "trace_actor", "model")
    actor_name = getattr(decision, "trace_name", "triage")
    payload = trace.decision(
        ticket=number,
        stage="triage",
        inputs=[{
            "kind": "github_issue",
            "ref": f"https://github.com/{cfg.repo}/issues/{number}",
            "summary": issue.get("title") or f"issue #{number}",
        }],
        options=[{
            "option": option,
            "eligible": actor_kind == "model" or option == label,
            "reason": rationale if option == label else f"{label} was selected instead",
        } for option in DECISIONS],
        chosen=label,
        rationale=rationale,
        causes=trace.ticket_causes(issue.get("title"), issue.get("body"), parent=parent),
        cost=getattr(decision, "trace_cost", None),
        actor={"kind": actor_kind, "name": actor_name,
               "model": LLM_MODEL if actor_kind == "model" else None, "version": __version__},
    )
    dispatch.record("decision", **payload)

    gh("issue", "comment", str(number), "--body", comment, execution=execution)
    gh(
        "issue",
        "edit",
        str(number),
        "--remove-label",
        LABEL_TRIAGE,
        "--add-label",
        label,
        execution=execution,
    )
    if label == config.LABEL_WONTFIX:
        terminal = trace.decision(
            ticket=number,
            stage="wontfix",
            inputs=[{
                "kind": "github_issue",
                "ref": f"https://github.com/{cfg.repo}/issues/{number}",
                "summary": issue.get("title") or f"issue #{number}",
            }],
            options=[
                {"option": config.LABEL_WONTFIX, "eligible": True, "reason": rationale},
                {"option": "continue", "eligible": False, "reason": "triage proposed wontfix"},
            ],
            chosen=config.LABEL_WONTFIX,
            rationale=rationale,
            causes=[{"decision_id": payload["decision_id"], "relation": "follows"}],
            cost=None,
            actor={"kind": "deterministic", "name": "triage.apply_decision",
                   "model": None, "version": __version__},
        )
        dispatch.record("decision", **terminal)


def list_needs_triage(execution=None) -> list[int]:
    raw = gh(
        "issue",
        "list",
        "--label",
        LABEL_TRIAGE,
        "--state",
        "open",
        "--json",
        "number",
        execution=execution,
    )
    return [item["number"] for item in json.loads(raw)]


def timeouts_last(numbers: list[int]) -> list[int]:
    """Tickets whose last triage attempt timed out go after the rest, so one slow ticket cannot starve older ones."""
    last = {}
    for row in lifecycle.read_events(cfg.factory / "events.jsonl"):
        if row.get("stage") == "triage-ticket" and row.get("kind") == "exit":
            last[row.get("ticket")] = row.get("reason")
    return sorted(numbers, key=lambda n: last.get(n) == "triage_endpoint_timeout")


def probe_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory triage-probe",
        description="Probe the configured triage endpoint and restart its service after repeated failures.",
    )
    parser.parse_args(argv)
    return run_probe(config.load())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory triage",
        description="Triage needs-triage issues with the local model.",
    )
    parser.add_argument("--issue", type=int, help="triage a single issue")
    parser.add_argument(
        "--replay", help="comma-separated issue numbers: print decisions, no writes"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print decisions instead of applying"
    )
    args = parser.parse_args(argv)
    configure(config.load())

    recording = not (args.replay or args.dry_run)
    with (
        lifecycle.scope(cfg.factory / "events.jsonl", "triage", lazy=True) if recording else nullcontext()
    ) as execution:
        result = execute(args, execution)
    if recording and result == 0:
        lifecycle.record_stage_success(cfg.factory / "events.jsonl", "triage")
    return result


def execute(args: argparse.Namespace, execution) -> int:
    replay = bool(args.replay)
    from factory.plan import is_initiative  # plan -> evidence -> dashboard: import lazily

    if not (replay or args.dry_run) and (refused := identity_mismatch()):
        print(f"triage refused to post: {refused}. Run it from the factory unit, whose "
              "[install].dispatch_env selects the dispatcher's gh config.", file=sys.stderr)
        if execution:
            execution.outcome, execution.reason = "not_admitted", "identity_mismatch"
        return 1

    if replay:
        numbers = [int(n) for n in args.replay.split(",")]
    elif args.issue:
        numbers = [args.issue]
    else:
        numbers = timeouts_last(list_needs_triage(execution=execution))

    if not numbers:
        if execution:
            execution.reason = "no_tickets"
        print("no needs-triage issues")
        return 0

    exit_code = 0
    outcome, reason = "completed", None
    # One local model serves every repo on this host; hold the host lock so
    # simultaneous timer passes queue on it instead of hammering the endpoint.
    with open(cfg.lock, "w") as host_lock:
        fcntl.flock(host_lock, fcntl.LOCK_EX)
        if execution:
            execution.emit("lock_acquired", lock=str(cfg.lock))
        try:
            for number in numbers:
                ticket_execution = None
                try:
                    with (
                        lifecycle.scope(
                            cfg.factory / "events.jsonl", "triage-ticket", ticket=number, lock=cfg.lock
                        ) if execution else nullcontext()
                    ) as ticket_execution:
                        issue = fetch_issue(number, execution=ticket_execution)
                        if is_initiative(issue):
                            print(f"#{number}: initiative record; triage refused (never promoted to {LABEL_AGENT})")
                            if ticket_execution:
                                ticket_execution.outcome, ticket_execution.reason = "not_admitted", "initiative"
                            continue
                        if not replay and unchanged_since_triage(issue):
                            print(f"#{number}: unchanged since its triage comment; skipping")
                            if ticket_execution:
                                ticket_execution.outcome = "not_admitted"
                                ticket_execution.reason = "unchanged_since_triage"
                            continue
                        try:
                            decision = triage_issue(issue, execution=ticket_execution)
                        except EndpointTimeout:
                            outcome, reason = "unknown", "triage_endpoint_timeout"
                            print(f"#{number}: model timed out; skipping, tried after untried tickets next pass",
                                  file=sys.stderr)
                            exit_code = 1
                            continue
                        if decision is None:
                            if ticket_execution:
                                ticket_execution.outcome = "unknown"
                                ticket_execution.reason = "unparseable_decision"
                            outcome, reason = "unknown", "unparseable_decision"
                            print(
                                f"#{number}: model returned unparseable JSON twice; skipping",
                                file=sys.stderr,
                            )
                            exit_code = 1
                            continue
                        if replay:
                            print(f"#{number}: {json.dumps(decision)}")
                        else:
                            apply_decision(
                                number, decision, args.dry_run, execution=ticket_execution, issue=issue,
                            )
                        if ticket_execution:
                            ticket_execution.reason = decision["decision"]
                            if decision["decision"] != LABEL_AGENT:
                                ticket_execution.outcome = "product_feedback"
                                if outcome == "completed":
                                    outcome, reason = "product_feedback", "triage_feedback"
                except BaseException:
                    if execution and ticket_execution:
                        execution.outcome = ticket_execution.outcome
                        execution.reason = ticket_execution.reason
                    raise
        finally:
            # Closing the existing host lock still releases it on every exit.
            host_lock.close()
            if execution:
                execution.emit("lock_released", lock=str(cfg.lock))
    if execution:
        execution.outcome, execution.reason = outcome, reason
    return exit_code
