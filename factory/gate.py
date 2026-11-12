"""Deterministic quality gate for agent worktrees.

Runs from a worktree root. Exit 0 = all checks pass, 1 = any failure.
Writes a short Markdown report (PASS/FAIL per check + failure excerpts).
"""

from __future__ import annotations

import argparse
import ast
import fcntl
import fnmatch
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from factory import config, lifecycle, mutation_scope, runner
from factory.config import CONFIG_NAME, Config

cfg: Config
GPU_LOCK: Path
LEAK_RE: re.Pattern[str] | None = None
GPU_CHECKS: set[str] = set()
TAIL_LINES = 80
CHECK_TIMEOUT = 1200  # seconds; overridable via --check-timeout
# Completion-record transport for `proof = "mutation-v1"` checks (scripts/mutation.py writes it).
MUTATION_PROOF_ENV = "FACTORY_MUTATION_PROOF"
MUTATION_PROOF_BASE_ENV = "FACTORY_MUTATION_PROOF_BASE"  # the caller base, resolved by the gate
MUTATION_PROOF_MAX_BYTES = 64 * 1024
MUTATION_RESERVE = 60  # scripts/mutation.py's cleanup reserve; an explicit total must exceed it
MUTATION_PROOF_COUNTS = ("planned", "ran", "killed", "survived", "timeouts", "blocking_survivors",
                         "not_started", "cut_short", "broken")
MUTATION_PROOF_KEYS = {"protocol", "version", "mode", "status", "reason", "exit", "files", "counts",
                       "scope"}
SKIP_NO_FACTORY_CODE = "SKIP (no changed factory code)"


def configure(c: Config) -> None:
    global cfg, GPU_LOCK, LEAK_RE, GPU_CHECKS, CHECK_TIMEOUT
    cfg = c
    GPU_LOCK = cfg.lock
    LEAK_RE = re.compile(cfg.leak_pattern, re.IGNORECASE) if cfg.leak_pattern else None
    GPU_CHECKS = {check.name for check in cfg.checks if check.exclusive}
    CHECK_TIMEOUT = cfg.check_timeout


def mutation_duration(value: str) -> float:
    """`--mutation-duration`: an explicit total for the opted proof check, finite and longer
    than the runner's existing cleanup reserve (scripts/mutation.py RESERVE)."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not math.isfinite(seconds) or seconds <= MUTATION_RESERVE:
        raise argparse.ArgumentTypeError(
            f"must be finite and greater than the {MUTATION_RESERVE}s cleanup reserve: {value!r}")
    return seconds


def timed(
    cmd: list[str], cwd: Path | None = None, pythonpath: Path | None = None,
    proof_path: Path | None = None, proof_base: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run a command with the per-check timeout (`timeout`, when given, for this call only).

    A wedged check must FAIL, not sit on the GPU lock (ticket #5's gate hung
    for 2h). The check runs in its own session so a timeout kills the whole
    tree (cargo/test grandchildren included), not just the direct child.
    """
    limit = CHECK_TIMEOUT if timeout is None else timeout
    execution = lifecycle.current()
    # The handoff row is still recorded, but the check never inherits the context:
    # a repo's own tests would otherwise append their fixture rows to the live ledger.
    env = execution.env() if execution else dict(os.environ)
    env.pop(lifecycle.CONTEXT_ENV, None)
    # Only the gate names a completion-record path, and only for the opted-in check.
    env.pop(MUTATION_PROOF_ENV, None)
    env.pop(MUTATION_PROOF_BASE_ENV, None)
    if proof_path is not None:
        env[MUTATION_PROOF_ENV] = str(proof_path)
        if proof_base is not None:
            env[MUTATION_PROOF_BASE_ENV] = proof_base
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", start_new_session=True, env=env,
    )
    if execution:
        execution.child(proc.pid)
    try:
        out, _ = proc.communicate(timeout=limit)
        if execution:
            execution.emit("result", command=cmd, returncode=proc.returncode, timed_out=False)
            if proc.returncode < 0:
                execution.outcome = "unknown"
                execution.reason = "check_terminated_by_signal"
        return subprocess.CompletedProcess(cmd, proc.returncode, out, "")
    except subprocess.TimeoutExpired:
        if execution:
            execution.outcome = "unknown"
            execution.reason = "check_timeout"
            execution.emit("timeout", command=cmd, timeout_seconds=limit)
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        if execution:
            execution.emit("result", command=cmd, returncode=proc.returncode, timed_out=True)
        return subprocess.CompletedProcess(
            cmd, 124, f"{out}\ncheck timed out after {limit}s", ""
        )
    finally:
        if execution and proc.poll() is not None:
            execution.child_done(proc.pid)


def run(cmd: list[str]) -> tuple[bool, str]:
    """Run a command, return (passed, combined output)."""
    proc = timed(cmd)
    execution = lifecycle.current()
    if execution and proc.returncode != 0 and execution.outcome == "completed":
        execution.outcome = "product_feedback"
        execution.reason = "configured_check_failed"
    return proc.returncode == 0, proc.stdout + proc.stderr

def _check_status(name: str, passed: bool, output: str, blocking_mutation: bool) -> str:
    if (
        blocking_mutation
        and name == "mutation"
        and passed
        and "no mutable files" in output.splitlines()
    ):
        return SKIP_NO_FACTORY_CODE
    return "PASS" if passed else "FAIL"


def _proof_counts(counts: object) -> dict[str, int] | None:
    if not isinstance(counts, dict) or counts.keys() != set(MUTATION_PROOF_COUNTS):
        return None
    if any(type(value) is not int or value < 0 for value in counts.values()):
        return None
    return counts


def read_mutation_proof(
    data: bytes | None, returncode: int, expected: mutation_scope.Scope,
) -> tuple[str, str]:
    """`("PASS" | SKIP_NO_FACTORY_CODE | "FAIL", why)` from a mutation-v1 completion record.

    Exit status and stdout are never proof. A missing, oversized, malformed, report-only,
    incomplete or self-contradictory record fails, as does one whose `exit` differs from
    the observed returncode (the gate reports a timeout as 124, a signal as negative), and
    a skip or complete record whose scope or files differ from `expected`, the gate's own
    factory.mutation_scope computation for the caller's base."""
    if data is None:
        return "FAIL", "missing completion record"
    if len(data) > MUTATION_PROOF_MAX_BYTES:
        return "FAIL", "oversized completion record"
    try:
        record = json.loads(data)
    except ValueError:
        return "FAIL", "malformed completion record"
    if not isinstance(record, dict) or record.keys() != MUTATION_PROOF_KEYS:
        return "FAIL", "malformed completion record"
    counts, files = _proof_counts(record["counts"]), record["files"]
    if (
        record["protocol"] != "factory-mutation-proof"
        or type(record["version"]) is not int or record["version"] != 1
        or type(record["exit"]) is not int
        or not all(isinstance(record[key], str) and record[key] for key in ("mode", "status", "reason"))
        or counts is None
        or not isinstance(files, list) or not all(isinstance(path, str) and path for path in files)
    ):
        return "FAIL", "malformed or unsupported completion record"
    if record["exit"] != returncode:
        return "FAIL", f"record exit {record['exit']} contradicts observed exit {returncode}"
    if record["mode"] != "blocking":
        return "FAIL", f"{record['mode']} run is not blocking mutation proof"
    status, reason = record["status"], record["reason"]
    if status not in {"skip", "complete"}:
        return "FAIL", f"{status} ({reason})"
    if record["scope"] != expected.receipt():
        return "FAIL", f"stale or mismatched scope: record {record['scope']!r}, gate {expected.receipt()!r}"
    applicable = expected.line_count
    if status == "skip":
        if (applicable == 0 and reason == "no-applicable-files" and not files
                and not any(counts.values()) and returncode == 0):
            return SKIP_NO_FACTORY_CODE, "no added or modified factory/*.py destination lines"
        return "FAIL", f"contradictory skip record ({applicable} applicable changed lines)"
    if files != sorted(expected.lines):
        return "FAIL", "record files differ from the applicable files"
    blocking = counts["blocking_survivors"]
    expected_reason = ("blocking-survivors" if blocking
                       else "no-mutation-candidates" if counts["planned"] == 0
                       else "no-blocking-survivors")
    if not (
        applicable
        and counts["ran"] == counts["planned"]
        and counts["killed"] + counts["survived"] == counts["ran"]
        and counts["not_started"] == counts["cut_short"] == counts["broken"] == 0
        and counts["timeouts"] <= counts["killed"]
        and blocking <= counts["survived"]
        and reason == expected_reason
        and returncode == (1 if blocking else 0)
    ):
        return "FAIL", "contradictory complete record"
    if blocking:
        return "FAIL", f"complete with {blocking} blocking survivors"
    if counts["planned"] == 0:
        return "PASS", (f"complete: 0 mutation candidates enumerated on {applicable} applicable "
                        f"changed lines (not a skip)")
    # Timeout-scored kills are the runner's legacy scoring, disclosed rather than re-judged.
    return "PASS", (f"complete: ran {counts['ran']} of {counts['planned']} mutants, "
                    f"{counts['killed']} killed ({counts['timeouts']} by timeout), "
                    f"{counts['survived']} survived")


def _read_proof_file(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"unreadable: {exc}".encode()  # parsed as malformed: fails closed
    try:
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return b"unreadable: completion record is not a regular file"
        return os.read(fd, MUTATION_PROOF_MAX_BYTES + 1)
    except OSError as exc:
        return f"unreadable: {exc}".encode()
    finally:
        os.close(fd)


# Record-reading tests pass this stub; it does not name a producer. Every other
# command that lacks the exact token `scripts/mutation.py` fails closed.
_PLACEHOLDER_MUTATION_COMMANDS = (["mutation"],)


def _provision_trusted_uv_environment(
    uv: str, options: list[str], project: Path, environment: Path,
) -> None:
    """Build the base lock's dependency environment without installing the project.

    `uv run` on 0.12.21 has no `--no-install-project` (that flag exists on `uv sync`).
    Installing the temp checkout would try to build an incomplete project, and running
    `uv` without the `run` subcommand drops the environment provisioning entirely.
    """
    command = [
        uv, "sync", "--no-config", *options, "--project", str(project),
        "--no-install-project", "--python", sys.executable,
    ]
    env = dict(os.environ)
    env["UV_PROJECT_ENVIRONMENT"] = str(environment)
    # uv writes cache lock files into TMPDIR; keep them beside the environment
    # (not inside it: a pre-existing environment dir without a Python executable
    # makes uv sync refuse the project venv) so a suite that isolates TMPDIR
    # does not see leftover `uv-*.lock` files.
    scratch = environment.parent / "uv-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = env["TMP"] = env["TEMP"] = str(scratch)
    for name in ("UV_NO_SYNC", "VIRTUAL_ENV", "UV_CONFIG_FILE", "UV_PROJECT",
                 "UV_WORKING_DIR", "UV_PYTHON"):
        env.pop(name, None)
    proc = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise mutation_scope.ScopeError(
            "trusted mutation environment provisioning timed out"
        ) from None
    if proc.returncode != 0:
        detail = (out or "").strip() or f"exit {proc.returncode}"
        raise mutation_scope.ScopeError(
            f"trusted mutation environment could not be provisioned: {detail}"
        )


def _isolated_environment_paths(environment: Path) -> list[str]:
    """Site paths from `environment` itself, not this interpreter's packaging scheme.

    Debian and Ubuntu interpreters report a posix_local/dist-packages layout that does
    not match a uv virtualenv's `lib/pythonX.Y/site-packages`. Glob that layout first.
    A `python -I -S` probe is not used: without site.py the venv is never activated, so
    sysconfig reports the base interpreter's site-packages when that directory exists.
    """
    paths = [
        str(path)
        for pattern in ("lib/python*/site-packages", "lib64/python*/site-packages")
        for path in environment.glob(pattern)
        if path.is_dir()
    ]
    if not paths:
        venv_python = environment / "bin" / "python"
        if venv_python.exists():
            try:
                proc = subprocess.run(
                    [str(venv_python), "-I", "-c",
                     ("import sysconfig; print(sysconfig.get_path('purelib')); "
                      "print(sysconfig.get_path('platlib'))")],
                    check=False, capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                proc = None
            if proc is not None and proc.returncode == 0:
                paths = [
                    line.strip() for line in proc.stdout.splitlines()
                    if line.strip() and Path(line.strip()).is_dir()
                ]
    paths = list(dict.fromkeys(paths))
    if not paths:
        raise mutation_scope.ScopeError(
            "trusted mutation environment has no site-packages"
        )
    return paths


def _trusted_mutation_command(
    cmd: list[str], base: str, directory: Path,
) -> list[str]:
    """Run the proof producer, interpreter environment and imports from trusted inputs.

    Keep cwd at the reviewed head: it supplies mutation targets, never producer code.
    Python starts without site processing; the launcher explicitly adds only the isolated
    environment provisioned from the base commit's frozen dependency configuration."""
    if "scripts/mutation.py" not in cmd:
        if cmd in _PLACEHOLDER_MUTATION_COMMANDS:
            return cmd
        raise mutation_scope.ScopeError(
            "mutation proof command must name the producer as the exact token "
            "`scripts/mutation.py`"
        )
    for name in ("scripts/mutation.py", "factory/__init__.py", "factory/mutation_scope.py"):
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(mutation_scope._git(None, "show", f"{base}:{name}"))
    index = cmd.index("scripts/mutation.py")
    uv = len(cmd) > 2 and Path(cmd[0]).name == "uv" and cmd[1] == "run"
    if uv:
        if cmd[index - 1] != "python":
            raise mutation_scope.ScopeError("trusted mutation command has no Python entry point")
        for name in ("pyproject.toml", "uv.lock"):
            (directory / name).write_text(mutation_scope._git(None, "show", f"{base}:{name}"))
        environment = directory / "environment"
        _provision_trusted_uv_environment(cmd[0], cmd[2:index - 1], directory, environment)
        paths = _isolated_environment_paths(environment)
    elif index == 1 and Path(cmd[0]).resolve() == Path(sys.executable).resolve():
        # This interpreter's own paths, not parent.parent (which can resolve to a
        # different /usr/bin/python and a Debian dist-packages layout).
        import sysconfig
        paths = list(dict.fromkeys(
            path for path in (sysconfig.get_path("purelib"), sysconfig.get_path("platlib"))
            if path and Path(path).is_dir()
        ))
        if not paths:
            raise mutation_scope.ScopeError(
                "trusted mutation environment has no site-packages"
            )
    else:
        raise mutation_scope.ScopeError("unsupported trusted mutation command")
    launcher = directory / "launch.py"
    launcher.write_text(
        "import runpy, sys\n"
        f"sys.path[:0] = [{str(directory)!r}, *{paths!r}]\n"
        f"runpy.run_path({str(directory / 'scripts/mutation.py')!r}, run_name='__main__')\n"
    )
    return [sys.executable, "-I", "-S", str(launcher), *cmd[index + 1:]]


def _proof_run(
    cmd: list[str], base: str, duration: float | None = None,
) -> tuple[str, str, str, mutation_scope.Scope | None]:
    """`(output, status, why, scope)`: the caller base is resolved to a commit once, before
    the run, and the scope recomputed from that commit after it (a moved HEAD fails).
    `duration` (explicit `--mutation-duration`) is this check's enclosing timeout."""
    try:
        expected = mutation_scope.compute(base)
        dirty = mutation_scope.tracked_mismatches(expected.head, Path.cwd())
    except mutation_scope.ScopeError as exc:
        return "", "FAIL", f"caller base {base!r} has no resolvable scope: {exc}", None
    if dirty:  # the scope names committed blobs: never certify other bytes; nothing runs
        return "", "FAIL", (f"tracked inputs differ from HEAD {expected.head}: "
                            f"{', '.join(dirty[:20])}"), None
    with tempfile.TemporaryDirectory(prefix="factory-mutation-proof-") as directory:
        path = Path(directory) / "proof.json"
        try:
            cmd = _trusted_mutation_command(cmd, expected.base, Path(directory))
        except mutation_scope.ScopeError as exc:
            return "", "FAIL", f"trusted mutation producer unavailable: {exc}", expected
        limit = {} if duration is None else {"timeout": duration}
        proc = timed(
            cmd, proof_path=path, proof_base=expected.base, **limit,
        )
        data = _read_proof_file(path)
    status, why = read_mutation_proof(data, proc.returncode, expected)
    try:
        after = mutation_scope.compute(expected.base)
        changed = mutation_scope.tracked_mismatches(expected.head, Path.cwd())
    except mutation_scope.ScopeError as exc:
        after, changed = exc, []
    if status != "FAIL" and after != expected:
        status, why = "FAIL", "HEAD or the applicable scope changed during the run"
    elif status != "FAIL" and changed:
        status, why = "FAIL", f"tracked inputs changed during the run: {', '.join(changed[:20])}"
    return f"{proc.stdout}{proc.stderr}", status, why, expected


def run_mutation_proof(
    cmd: list[str], base: str, duration: float | None = None,
) -> tuple[bool, str, str, str]:
    """Like `run`, plus the report status decided by the runner's completion record and,
    for PASS/SKIP, the ` base=… head=… scope=…` binding the dispatcher checks."""
    output, status, why, scope = _proof_run(cmd, base, duration)
    execution = lifecycle.current()
    if execution and execution.reason in {"check_timeout", "check_terminated_by_signal"}:
        status, why = "FAIL", f"{execution.reason} overrides any completion record"
    passed = status != "FAIL"
    if execution and not passed and execution.outcome == "completed":
        execution.outcome = "product_feedback"
        execution.reason = "configured_check_failed"
    binding = (f" base={scope.base} head={scope.head} scope={scope.fingerprint}"
               if passed and scope else "")
    return passed, f"{output}\nmutation proof: {status} ({why})\n", status, binding



def failed_test_ids(output: str) -> list[str]:
    """Canonical unittest, pytest and cargo libtest IDs, in report order."""
    found = []
    for line in output.splitlines():
        match = re.match(r"^(?:FAIL|ERROR): \S+ \(([^()\s]+)\)", line)
        if match is None:
            match = re.match(r"^FAILED ([^\s]+::[^\s]+)(?:\s+-|\s*$)", line)
        if match is None:
            match = re.match(r"^---- ([^\s]+) stdout ----$", line)
        if match is not None and match[1] not in found:
            found.append(match[1])
    return found


def changed_paths(base: str) -> list[str]:
    proc = timed(["git", "diff", "--name-only", "-z", f"{base}...HEAD"])
    return proc.stdout.rstrip("\0").split("\0") if proc.returncode == 0 and proc.stdout else []



def _acquire_exclusive(execution):
    lock = open(GPU_LOCK, "w")  # noqa: SIM115 — caller controls lock lifetime
    try:
        requested = execution.resource("requested", GPU_LOCK, scope="host", blocking=True)
        if lifecycle._lock_state(lifecycle._lock(GPU_LOCK)) == "held":
            execution.wait(
                "exclusive_resource", mode="blocking", resource=requested["resource"]
            )
        fcntl.flock(lock, fcntl.LOCK_EX)
        execution.resource("acquired", GPU_LOCK, scope="host", blocking=True)
        execution.wait_end()
        return lock
    except BaseException:
        lock.close()
        raise


def _release_exclusive(execution, lock) -> None:
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    execution.resource("released", GPU_LOCK, scope="host", blocking=True)



def check_conflict_markers() -> tuple[bool, str]:
    proc = timed(
        # Exactly-7-char markers only; long ===== separator lines are legit.
        ["git", "grep", "-nE", r"^(<{7}|={7}|>{7})( |$)"],
    )
    # git grep exits 1 when nothing matches — that is the pass case.
    if proc.returncode == 1:
        return True, ""
    execution = lifecycle.current()
    if execution and execution.outcome == "completed":
        execution.outcome = "product_feedback" if proc.returncode == 0 else "unknown"
        execution.reason = "conflict_markers" if proc.returncode == 0 else "git_grep_failed"
    return False, proc.stdout + proc.stderr


def check_leaks(base: str) -> tuple[bool, str]:
    # .factory.toml is always excluded: its `pattern` literal matches the scan.
    # Other excludes come from `leak_exclude` (paths that never go upstream).
    proc = timed(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-prefix",
            f"{base}..HEAD",
            "--",
            f":(exclude){CONFIG_NAME}",
            *(f":(exclude){p}" for p in cfg.leak_exclude),
        ],
    )
    if proc.returncode != 0:
        execution = lifecycle.current()
        if execution and execution.outcome == "completed":
            execution.outcome = "unknown"
            execution.reason = "git_diff_failed"
        return False, proc.stdout + proc.stderr
    hits, path, header, n = [], "", True, 0
    for line in proc.stdout.splitlines():
        if line.startswith("diff --git "):
            header = True
        elif header and line.startswith("+++ "):
            path = line[4:]
        elif line.startswith("@@ "):
            header, n = False, int(re.match(r"@@ -\S+ \+(\d+)", line).group(1))
        elif not header and line.startswith("+"):
            if m := LEAK_RE.search(line[1:]):
                hits.append(f"{path}:{n}: [{m.group(0)}] {line[1:]}")
            n += 1
    execution = lifecycle.current()
    if hits and execution:
        execution.outcome = "product_feedback"
        execution.reason = "leak_scan_matches"
    return not hits, "\n".join(hits)


ADD_ONLY_NOTE = "add-only path removed or changed existing lines"
_BLOB = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def parse_add_only_allow(value: str) -> tuple[str, str]:
    """`<path>=<blob-sha>`: a canonical repository-relative path and a full lowercase blob id."""
    path, sep, sha = value.rpartition("=")
    if not sep or not _BLOB.fullmatch(sha):
        raise argparse.ArgumentTypeError(f"expected <path>=<full blob sha>: {value!r}")
    parts = path.split("/")
    if not path or "\\" in path or any(part in ("", ".", "..") for part in parts):
        raise argparse.ArgumentTypeError(f"non-canonical path: {path!r}")
    return path, sha


# The one PM-reviewed protected change the product itself knows: `proof = "mutation-v1"` added
# to the committed `.factory.toml` of the reviewed base. No other tuple can ever be exempted.
REVIEWED_TRANSITION = (CONFIG_NAME, "36dee1c6386165f2f434c32d09ee98d26c88e7d4",
                       "7c1c0aad5e8bf221df02c1c2329cda354530f1c5")
REVIEWED_TRANSITION_BASE = "19457e840460e0163e1172f08580538c4dcae767"


def parse_protected_transition(value: str) -> tuple[str, str, str]:
    """`.factory.toml=<pre blob>:<post blob>`: full lowercase blob ids, pre differing from post."""
    path, sep, pins = value.partition("=")
    pre, colon, post = pins.partition(":")
    if (not sep or not colon or path != CONFIG_NAME or pre == post
            or not _BLOB.fullmatch(pre) or not _BLOB.fullmatch(post)):
        raise argparse.ArgumentTypeError(f"expected {CONFIG_NAME}=<pre blob>:<post blob>: {value!r}")
    return path, pre, post


def _verify_transition(
    base: str, transition: tuple[str, str, str], transition_base: str | None,
    raw: dict[str, tuple[str, str, str]],
) -> str:
    """Empty when `transition` is the reviewed tuple observed exactly: the supplied reviewed
    base, the gate base and the merge-base all resolve to the reviewed commit, the path is an
    in-place 100644 edit from `pre` to `post`, and the working tree is a non-executable regular
    file hashing to `post`; otherwise the refusal."""
    path, pre, post = transition
    refused = f"protected transition {path}={pre}:{post} (base {transition_base}) refused: "
    if tuple(transition) != REVIEWED_TRANSITION:
        return refused + "not the reviewed transition"
    commits = [timed(["git", "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"]).stdout.strip()
               for ref in (transition_base, base)]
    commits.append(timed(["git", "merge-base", base, "HEAD"]).stdout.strip())
    if commits != [REVIEWED_TRANSITION_BASE] * 3:
        return refused + f"reviewed base, gate base, merge-base are {commits}, not {REVIEWED_TRANSITION_BASE}"
    old_mode, new_mode, status = raw.get(path, ("", "", ""))
    if (old_mode, new_mode, status) != ("100644", "100644", "M"):
        return refused + f"not an in-place 100644 edit ({status or 'unchanged'} {old_mode}->{new_mode})"
    blobs = [timed(["git", "rev-parse", "--verify", "-q", f"{ref}:{path}"]).stdout.strip()
             for ref in (REVIEWED_TRANSITION_BASE, "HEAD")]
    if blobs != [pre, post]:
        return refused + f"committed {blobs[0] or '-'} -> {blobs[1] or '-'}"
    if os.path.islink(path) or not os.path.isfile(path) or os.stat(path).st_mode & 0o111:
        return refused + "working tree is not a non-executable regular file"
    worktree = timed(["git", "hash-object", "--no-filters", "--", path]).stdout.strip()
    if worktree != post:
        return refused + f"working tree {worktree or '-'}"
    return ""


def _verify_allows(
    base: str, allow: dict[str, str], allow_base: str | None, raw: dict[str, tuple[str, str, str]],
    protected: list[str], add_only: list[str],
) -> str:
    """Empty when every approved `{path: blob}` names an in-place text edit of an add-only file
    whose committed and working-tree bytes both equal the approved blob, against the approved
    merge-base; otherwise the first refusal."""
    common = timed(["git", "merge-base", base, "HEAD"])
    pinned = timed(["git", "rev-parse", "--verify", "-q", f"{allow_base}^{{commit}}"]) if allow_base else None
    if common.returncode or pinned is None or pinned.returncode or pinned.stdout.strip() != common.stdout.strip():
        return f"add-only allow base {allow_base!r} is not the merge-base {common.stdout.strip()!r}"
    for path, sha in allow.items():
        if any(fnmatch.fnmatchcase(path, g) for g in protected) or not any(
            fnmatch.fnmatchcase(path, g) for g in add_only
        ):
            return f"{path}: outside add-only scope"
        old_mode, new_mode, status = raw.get(path, ("", "", ""))
        if status != "M" or old_mode != new_mode or new_mode not in ("100644", "100755"):
            return f"{path}: not an in-place regular-file edit ({status or 'unchanged'} {old_mode}->{new_mode})"
        numstat = timed(["git", "diff", "--no-renames", "--numstat", f"{base}...HEAD", "--", path])
        if numstat.returncode or numstat.stdout.startswith("-\t-\t"):
            return f"{path}: binary or unreadable diff"
        committed = timed(["git", "rev-parse", "--verify", "-q", f"HEAD:{path}"]).stdout.strip()
        if not os.path.isfile(path) or os.path.islink(path):
            return f"{path}: working tree is not a regular file"
        worktree = timed(["git", "hash-object", "--no-filters", "--", path]).stdout.strip()
        if committed != sha or worktree != sha:
            return f"{path}: approved {sha}, committed {committed or '-'}, working tree {worktree or '-'}"
    return ""


def check_protected_paths(
    base: str, upstream: list[str] = (), add_only_override: bool = False,
    allow: dict[str, str] | None = None, allow_base: str | None = None,
    transition: tuple[str, str, str] | None = None, transition_base: str | None = None,
) -> tuple[bool, str, str]:
    """(passed, reason, detail): reason is `protected_paths`, or `add_only_paths` when
    every hit is an add-only glob whose change was not purely additive. With `upstream`
    (the tip, then the staged head) a hit is ignored when its HEAD content equals the
    staged head's, or equals the tip's for a path upstream itself changed since it
    diverged from `base`; fork-only paths the worker deletes or resets still count.
    `allow` ({path: blob}, externally approved) exempts exactly those add-only files once
    `_verify_allows` accepts every entry; any refusal fails the whole check. `transition`
    ((path, pre, post), with `transition_base`) exempts only that path's protected hit, only
    for the reviewed tuple `_verify_transition` accepts; otherwise the check fails."""
    listing = timed(["git", "ls-tree", "--name-only", base, "--", CONFIG_NAME])
    if listing.returncode:
        # A new repository has no base commit yet; no worker diff exists to guard.
        unborn = timed(["git", "rev-parse", "--verify", "HEAD"])
        return (True, "", "") if unborn.returncode else (False, "protected_paths", listing.stdout + listing.stderr)
    protected, add_only = [], []
    if listing.stdout.strip():
        source = timed(["git", "show", f"{base}:{CONFIG_NAME}"])
        if source.returncode:
            return False, "protected_paths", source.stdout + source.stderr
        try:
            gate = tomllib.loads(source.stdout).get("gate", {})
        except tomllib.TOMLDecodeError as exc:
            return False, "protected_paths", f"base {CONFIG_NAME}: {exc}"
        protected, add_only = gate.get("protected_paths", []), gate.get("add_only_paths", [])
        for key, globs in (("protected_paths", protected), ("add_only_paths", add_only)):
            if not isinstance(globs, list) or any(not isinstance(p, str) or not p for p in globs):
                return False, "protected_paths", f"base gate.{key} must be an array of nonempty globs"
    proc = timed(["git", "diff", "--no-renames", "--raw", "-z", f"{base}...HEAD"])
    if proc.returncode:
        return False, "protected_paths", proc.stdout + proc.stderr
    fields = proc.stdout.split("\0")
    hits, not_additive, additive = [], [], []  # additive: add-only files modified in place, pending numstat
    raw = {}
    for meta, path in zip(fields[::2], fields[1::2]):
        old_mode, new_mode, _, _, status = meta.lstrip(":").split()
        raw[path] = (old_mode, new_mode, status)
        if any(fnmatch.fnmatchcase(path, g) for g in protected):
            hits.append(path)
        elif path in (allow or {}):
            continue
        elif any(fnmatch.fnmatchcase(path, g) for g in add_only):
            if status == "M" and old_mode == new_mode:
                additive.append(path)
            elif status != "A":
                not_additive.append(path)
    if allow and (refusal := _verify_allows(base, allow, allow_base, raw, protected, add_only)):
        return False, "add_only_paths", refusal
    if transition:
        if refusal := _verify_transition(base, transition, transition_base, raw):
            return False, "protected_paths", refusal
        hits = [path for path in hits if path != transition[0]]
    if upstream and (candidates := hits + not_additive + additive):
        tip = upstream[0]
        fork_point = timed(["git", "merge-base", base, tip])
        if fork_point.returncode:
            return False, "protected_paths", fork_point.stdout + fork_point.stderr
        changed = timed(["git", "diff", "--no-renames", "--name-only", "-z",
                         fork_point.stdout.strip(), tip, "--", *candidates])
        if changed.returncode:
            return False, "protected_paths", changed.stdout + changed.stderr
        upstream_changed = set(changed.stdout.split("\0"))
        authored = set(candidates)
        for ref in upstream:
            diff = timed(["git", "diff", "--no-renames", "--name-only", "-z", ref, "HEAD", "--", *candidates])
            if diff.returncode:
                return False, "protected_paths", diff.stdout + diff.stderr
            same = authored - set(diff.stdout.split("\0"))
            if ref == tip:  # absent-at-both or reset-to-upstream is not upstream authorship
                same &= upstream_changed
            authored -= same
        hits, not_additive, additive = ([p for p in group if p in authored] for group in (hits, not_additive, additive))
    if additive:
        numstat = timed(["git", "diff", "--no-renames", "--numstat", "-z", f"{base}...HEAD", "--", *additive])
        if numstat.returncode:
            return False, "protected_paths", numstat.stdout + numstat.stderr
        for line in numstat.stdout.split("\0"):
            if line:
                added, deleted, path = line.split("\t")
                if added == "-" or deleted == "-" or (not add_only_override and deleted != "0"):
                    not_additive.append(path)
    if not hits and not not_additive:
        return True, "", ""
    reason = "protected_paths" if hits else "add_only_paths"
    diff = timed(["git", "diff", "--no-renames", "--binary", f"{base}...HEAD", "--", *hits, *not_additive])
    return False, reason, diff.stdout + diff.stderr


def _python_test_path(path: str) -> bool:
    file = Path(path)
    return (
        any(part in {"test", "tests"} for part in file.parts[:-1])
        or file.stem.startswith("test_")
        or file.stem.endswith("_test")
    )


def _python_tests(path: str, source: str) -> dict[str, dict]:
    if not _python_test_path(path):
        return {}
    tree = ast.parse(source, filename=path)
    lines = source.splitlines(keepends=True)
    module = path.removesuffix(".py").replace("/", ".")
    found = {}

    def is_pytestmark(node: ast.stmt) -> bool:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign))
            else []
        )
        return any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets)

    def visit(
        nodes: list[ast.stmt],
        parents: tuple[str, ...] = (),
        enclosing_source: str = "",
    ) -> None:
        scope_source = enclosing_source + "".join(
            "".join(lines[node.lineno - 1:node.end_lineno])
            for node in nodes
            if is_pytestmark(node)
        )
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                visit(
                    node.body,
                    (*parents, node.name),
                    scope_source + "".join(lines[start - 1:node.lineno - 1]),
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                qualified = ".".join((*parents, node.name))
                found[f"python:{path}:{qualified}"] = {
                    "source": scope_source + "".join(lines[start - 1:node.end_lineno]),
                    "aliases": {
                        f"{module}.{qualified}",
                        f"{path}::{'::'.join((*parents, node.name))}",
                    },
                    "language": "python",
                    "display": f"{path}:{qualified}",
                    "path": path,
                    "qualified": qualified,
                }
    visit(tree.body)
    return found


_RUST_TEST = re.compile(
    r"(?m)(?P<attrs>(?:^[ \t]*#\[[^\n]+\][ \t]*\n)+)"
    r"^[ \t]*(?:(?:pub(?:\([^)]*\))?)[ \t]+)?(?:async[ \t]+)?fn[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_RUST_MOD = re.compile(
    r"(?m)(?P<attrs>(?:^[ \t]*#\[[^\n]+\][ \t]*\n)*)"
    r"^[ \t]*(?:(?:pub(?:\([^)]*\))?)[ \t]+)?mod[ \t]+"
    r"[A-Za-z_][A-Za-z0-9_]*[ \t]*\{"
)
_RUST_MASK = re.compile(
    r"//[^\n]*|/\*.*?\*/|r(?P<hashes>#+)?\".*?\"(?P=hashes)|\"(?:\\.|[^\"\\])*\"|"
    r"'(?:[^'\\\r\n\t]|\\(?:[nrt0\\'\"]|x[0-7][0-9a-fA-F]|u\{[0-9a-fA-F]{1,6}\}))'",
    re.DOTALL,
)


def _rust_tests(path: str, source: str) -> dict[str, dict]:
    masked = _RUST_MASK.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), source)

    def block_end(brace: int) -> int | None:
        depth = 0
        for index in range(brace, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return None

    modules = []
    for module in _RUST_MOD.finditer(masked):
        end = block_end(module.end() - 1)
        if end is not None:
            modules.append((
                module.end() - 1,
                end,
                source[module.start("attrs"):module.end("attrs")],
            ))

    found, occurrences = {}, {}
    for match in _RUST_TEST.finditer(source):
        if not re.search(r"#\[(?:[A-Za-z_][A-Za-z0-9_]*::)*test(?:\([^]]*\))?\]", match["attrs"]):
            continue
        brace = masked.find("{", match.end("name"))
        end = block_end(brace) if brace >= 0 else None
        if end is None:
            continue
        name = match["name"]
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        enclosing = "".join(
            attrs for start, stop, attrs in modules
            if start < match.start() < stop
        )
        found[f"rust:{path}:{name}:{occurrence}"] = {
            "source": enclosing + source[match.start():end],
            "aliases": {name},
            "language": "rust",
            "display": f"{path}:{name}",
        }
    return found


def _tests_at(ref: str) -> tuple[dict[str, dict], str]:
    listing = timed(["git", "ls-tree", "-r", "--name-only", "-z", ref])
    if listing.returncode:
        return {}, listing.stdout + listing.stderr
    found = {}
    for path in listing.stdout.split("\0"):
        if not path.endswith((".py", ".rs")):
            continue
        blob = timed(["git", "show", f"{ref}:{path}"])
        if blob.returncode:
            return {}, blob.stdout + blob.stderr
        try:
            tests = _python_tests(path, blob.stdout) if path.endswith(".py") else _rust_tests(path, blob.stdout)
        except SyntaxError:
            continue
        found.update(tests)
    return found, ""


def check_existing_tests(base: str, exempt: frozenset[str] = frozenset()) -> tuple[bool, str]:
    """`exempt`: paths whose exact content was externally approved (`--add-only-allow`); test
    functions in them may change, every other existing test may not."""
    common = timed(["git", "merge-base", base, "HEAD"])
    if common.returncode:
        return False, common.stdout + common.stderr
    before, error = _tests_at(common.stdout.strip())
    if error:
        return False, error
    after, error = _tests_at("HEAD")
    if error:
        return False, error
    changed = [
        f"{record['display']}: {'deleted' if key not in after else 'modified'}"
        for key, record in before.items()
        if (key not in after or record["source"] != after[key]["source"])
        and record.get("path") not in exempt
    ]
    return not changed, "\n".join(changed)


def _matches_test_id(test_id: str, record: dict) -> bool:
    return test_id in record["aliases"] or (
        record["language"] == "rust" and test_id.rsplit("::", 1)[-1] in record["aliases"]
    )


_UNITTEST_ONE = """import importlib.util, sys, unittest
path, name = sys.argv[1:]
spec = importlib.util.spec_from_file_location("_factory_repro", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(name, module))
raise SystemExit(not result.wasSuccessful())
"""


def _repro_command(test_id: str, record: dict) -> tuple[list[str], bool]:
    language = record["language"]
    for check in cfg.checks:
        if language == "python" and "pytest" in check.run:
            selector = next(alias for alias in record["aliases"] if "::" in alias)
            return (
                [*check.run[:check.run.index("pytest") + 1], selector],
                check.name in GPU_CHECKS,
            )
        if language == "python" and "unittest" in check.run:
            runner = check.run.index("unittest")
            prefix = check.run[:runner - 1] if runner and check.run[runner - 1] == "-m" else check.run[:runner]
            return (
                [*(prefix or [sys.executable]), "-c", _UNITTEST_ONE, record["path"], record["qualified"]],
                check.name in GPU_CHECKS,
            )
        if language == "rust" and "cargo" in check.run and "test" in check.run:
            separator = check.run.index("--") if "--" in check.run else len(check.run)
            return (
                [*check.run[:separator], test_id, *check.run[separator:]],
                check.name in GPU_CHECKS,
            )
    if language == "python" and "::" not in test_id:
        return [sys.executable, "-c", _UNITTEST_ONE, record["path"], record["qualified"]], False
    if language == "python":
        return [sys.executable, "-m", "pytest", test_id], False
    return ["cargo", "test", test_id], False


def _run_at(
    ref: str, command: list[str]
) -> tuple[subprocess.CompletedProcess, bool]:
    with tempfile.TemporaryDirectory(prefix="factory-repro-") as directory:
        checkout = Path(directory) / "checkout"
        added = timed(["git", "worktree", "add", "--detach", str(checkout), ref])
        if added.returncode:
            return added, False
        try:
            return timed(command, cwd=checkout, pythonpath=checkout), True
        finally:
            timed(["git", "worktree", "remove", "--force", str(checkout)])


def check_bug_reproduction(
    base: str, test_id: str | None, no_repro_test: str | None, execution=None
) -> list[tuple[str, str, str]]:
    if no_repro_test:
        return [("bug-repro", f"SKIP (No repro test: {no_repro_test})", "")]
    if not test_id:
        return [(
            "bug-repro",
            "FAIL",
            "bug ticket needs a `Repro test:` handoff line or `No repro test:` ticket line",
        )]
    commits = timed(["git", "rev-list", "--reverse", "--first-parent", f"{base}..HEAD"])
    if commits.returncode:
        return [("bug-repro", "FAIL", commits.stdout + commits.stderr)]
    revisions = commits.stdout.split()
    if len(revisions) < 2:
        return [(
            "bug-repro",
            "FAIL",
            "bug ticket needs a test commit followed by a separate fix commit",
        )]
    before, error = _tests_at(base)
    if error:
        return [("bug-repro", "FAIL", error)]
    for test_commit in revisions:
        with_test, error = _tests_at(test_commit)
        if error:
            return [("bug-repro", "FAIL", error)]
        added = [
            (key, record) for key, record in with_test.items()
            if key not in before and _matches_test_id(test_id, record)
        ]
        if added:
            break
    else:
        return [(
            "bug-repro",
            "FAIL",
            f"no bug-ticket commit adds declared reproduction test `{test_id}`",
        )]
    if test_commit == revisions[-1]:
        return [(
            "bug-repro",
            "FAIL",
            "bug ticket needs a test commit followed by a separate fix commit",
        )]
    test_key, test_record = added[0]
    command, exclusive = _repro_command(test_id, test_record)
    gpu_lock = _acquire_exclusive(execution) if exclusive else None
    try:
        reproduced, ran = _run_at(test_commit, command)
        if not ran or reproduced.returncode == 124:
            return [(
                "bug-repro-before",
                "FAIL",
                reproduced.stdout + reproduced.stderr,
            )]
        if reproduced.returncode == 0:
            return [(
                "bug-repro-before",
                "FAIL",
                "test does not reproduce the bug: it passes at the test commit",
            )]
        results = [(
            "bug-repro-before",
            "EXPECTED FAIL",
            reproduced.stdout + reproduced.stderr,
        )]
        head, error = _tests_at("HEAD")
        if error:
            return [*results, ("bug-repro-head", "FAIL", error)]
        head_record = head.get(test_key)
        if head_record is None or not _matches_test_id(test_id, head_record):
            return [*results, (
                "bug-repro-head",
                "FAIL",
                f"declared reproduction test `{test_id}` is missing at HEAD",
            )]
        if head_record["source"] != test_record["source"]:
            return [*results, (
                "bug-repro-head",
                "FAIL",
                f"declared reproduction test changed at HEAD: `{test_id}`",
            )]
        fixed = timed(command, cwd=Path.cwd(), pythonpath=Path.cwd())
        return [
            *results,
            (
                "bug-repro-head",
                "PASS" if fixed.returncode == 0 else "FAIL",
                "" if fixed.returncode == 0 else "reproduction test still fails at HEAD\n" + fixed.stdout + fixed.stderr,
            ),
        ]
    finally:
        if gpu_lock is not None:
            _release_exclusive(execution, gpu_lock)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Quality gate for agent worktrees.")
    parser.add_argument("--base", default=None, help="base ref for leak scan and protected paths")
    parser.add_argument(
        "--report", default=".factory/gate-report.md", help="report output path"
    )
    parser.add_argument(
        "--skip",
        default="",
        help="comma-separated check names to skip "
        "(conflict-markers, leak-scan, <configured names>)",
    )
    parser.add_argument(
        "--check-timeout",
        type=int,
        default=None,
        help="per-check timeout in seconds",
    )
    parser.add_argument("--protected-override", action="store_true",
                        help="human-authorized protected-path override")
    parser.add_argument("--test-override", action="store_true",
                        help="ticket-authorized existing-test override")
    parser.add_argument("--add-only-override", action="store_true",
                        help="manager- or human-authorized edits to add-only paths")
    parser.add_argument("--add-only-allow", action="append", default=[], type=parse_add_only_allow,
                        metavar="PATH=BLOB",
                        help="externally reviewed add-only edit (repeatable): PATH may change in place only "
                        "when its committed and working-tree bytes both hash to BLOB; needs --add-only-allow-base")
    parser.add_argument("--add-only-allow-base", metavar="COMMIT",
                        help="merge-base the --add-only-allow blobs were reviewed against")
    parser.add_argument("--protected-transition", action="append", default=[],
                        type=parse_protected_transition, metavar="PATH=PRE:POST",
                        help="the one product-known reviewed protected change (given once): PATH must "
                        "change in place from blob PRE to POST, committed and working tree, against "
                        "the reviewed base; needs --protected-transition-base")
    parser.add_argument("--protected-transition-base", action="append", default=[], metavar="COMMIT",
                        help="reviewed merge-base of --protected-transition (given once)")
    parser.add_argument("--bug", action="store_true",
                        help="enforce failing-test-first and existing-test protection")
    parser.add_argument("--repro-test", help="test ID named by the worker handoff")
    parser.add_argument("--no-repro-test", help="ticket-author or authenticated-triage reason a reproduction test is impractical")
    parser.add_argument("--upstream", action="append", default=[], metavar="REF",
                        help="upstream sync: first REF is the tip, later REFs the staged head (repeatable); "
                        "protected-path hits equal to the staged head, or to the tip for paths upstream changed, "
                        "are upstream-authored, not worker edits; the leak scan diffs from the tip")
    parser.add_argument("--tier", choices=config.CHECK_TIERS, default="attempt",
                        help="attempt: every check except `when = \"pr\"`/`\"release\"` ones (reported SKIP); "
                        "pr: only the `when = \"pr\"` checks (release ones reported SKIP); "
                        "release: only the `when = \"release\"` checks")
    parser.add_argument("--blocking-mutation", action="store_true", help=argparse.SUPPRESS)
    # Passed by the dispatcher only for High heads of repositories whose check declares
    # `proof = "mutation-v1"`; that check's status then comes from its completion record.
    parser.add_argument("--mutation-proof", action="store_true", help=argparse.SUPPRESS)
    # Explicit total seconds for the opted proof check only: its enclosing timeout and the
    # runner's `--duration`. Absent: the legacy budgets. Never changes other checks.
    parser.add_argument("--mutation-duration", type=mutation_duration, default=None,
                        metavar="SECONDS", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.mutation_duration is not None and not args.mutation_proof:
        parser.error("--mutation-duration needs --mutation-proof")
    allowed = [path for path, _ in args.add_only_allow]
    if len(set(allowed)) != len(allowed):
        parser.error("--add-only-allow names a path more than once")
    if allowed and not args.add_only_allow_base:
        parser.error("--add-only-allow needs --add-only-allow-base")
    transitions, transition_bases = args.protected_transition, args.protected_transition_base
    if len(transitions) > 1 or len(transition_bases) > 1:
        parser.error("--protected-transition and --protected-transition-base are each given at most once")
    if len(transitions) != len(transition_bases):
        parser.error("--protected-transition and --protected-transition-base need each other")
    args.protected_transition = transitions[0] if transitions else None
    args.protected_transition_base = transition_bases[0] if transition_bases else None
    configure(config.load())
    previous_tmpdir = os.environ.get("TMPDIR")
    with runner.temp_root("gate") as tmpdir:
        os.environ["TMPDIR"] = tmpdir
        try:
            with lifecycle.scope(cfg.factory / "events.jsonl", "gate") as execution:
                return execute(args, execution)
        finally:
            if previous_tmpdir is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = previous_tmpdir


def toolchain() -> dict[str, str]:
    values = {}
    for name, command in (
        ("rocm", ["hipconfig", "--version"]),
        ("kernel", ["uname", "-r"]),
        ("amdgpu", ["modinfo", "-F", "version", "amdgpu"]),
    ):
        try:
            result = subprocess.run(
                command, capture_output=True, encoding="utf-8", errors="replace",
                timeout=5, check=False,
            )
            values[name] = result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            values[name] = "unavailable"
    return values


def execute(args: argparse.Namespace, execution) -> int:
    global CHECK_TIMEOUT
    host = toolchain()
    execution.emit("toolchain", **host)
    if args.base is None:
        args.base = f"origin/{cfg.main}"
    if args.check_timeout is not None:
        CHECK_TIMEOUT = args.check_timeout
    skip = {name.strip() for name in args.skip.split(",") if name.strip()}
    if LEAK_RE is None:
        skip.add("leak-scan")
    audit = []  # the reviewed protected transition this run actually verified and exempted

    if args.tier == "attempt":
        if not args.protected_override:
            passed, reason, detail = check_protected_paths(
                args.base, args.upstream, args.add_only_override,
                dict(args.add_only_allow), args.add_only_allow_base,
                args.protected_transition, args.protected_transition_base,
            )
            if passed and args.protected_transition:
                path, pre, post = REVIEWED_TRANSITION
                audit.append(f"- protected-transition: {path} {pre} -> {post} "
                             f"(reviewed base {REVIEWED_TRANSITION_BASE})")
            if not passed:
                execution.outcome, execution.reason = "project_escalation", reason
                report = Path(args.report)
                report.parent.mkdir(parents=True, exist_ok=True)
                note = f" ({ADD_ONLY_NOTE})" if reason == "add_only_paths" else ""
                report.write_text(f"# Gate report\n\n- protected-paths: FAIL{note}\n\n"
                                  "## protected-paths failure\n\n```diff\n" + detail + "\n```\n")
                print(f"report: {report}\ngate FAIL: protected-paths")
                return 1
        if args.bug and not (
            args.protected_override or args.test_override or args.add_only_override
        ):
            passed, detail = check_existing_tests(args.base, frozenset(dict(args.add_only_allow)))
            if not passed:
                execution.outcome, execution.reason = "project_escalation", "protected_tests"
                report = Path(args.report)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    "# Gate report\n\n"
                    "- protected-tests: FAIL (existing test deleted or modified)\n\n"
                    "## protected-tests failure\n\n```\n" + detail + "\n```\n"
                )
                print(f"report: {report}\ngate FAIL: protected-tests")
                return 1
    # attempt lists every configured check, pr the pr+release ones, release only its own;
    # listed checks of a later tier are reported `SKIP (<tier> tier)` instead of run.
    rank = config.CHECK_TIERS.index(args.tier)
    listed = [check for check in cfg.checks if config.CHECK_TIERS.index(check.when) >= rank]
    proofs: dict[str, tuple[str, str]] = {}  # check name -> (record-decided status, binding)

    def proved(check: config.Check) -> bool:
        return args.mutation_proof and check.proof == config.MUTATION_PROOF

    def command(check: config.Check) -> list[str]:
        blocking = args.blocking_mutation and (check.name == "mutation" or proved(check))
        return [*check.run, *(["--blocking"] if blocking else [])]

    def run_proved(check: config.Check) -> tuple[bool, str]:
        duration = args.mutation_duration
        cmd = [*command(check), *([] if duration is None else ["--duration", str(duration)])]
        passed, output, status, binding = run_mutation_proof(cmd, args.base, duration)
        proofs[check.name] = (status, binding)
        return passed, output

    configured = [
        (check.name, (lambda c=check: run_proved(c)) if proved(check) else (lambda c=check: run(command(c))))
        for check in listed
    ]
    checks = [
        ("conflict-markers", check_conflict_markers),
        *configured,
        ("leak-scan", lambda: check_leaks(args.upstream[0] if args.upstream else args.base)),
    ] if args.tier == "attempt" else configured
    later_tier = {check.name: check.when for check in listed if check.when != args.tier}

    results: list[tuple[str, str, str]] = (
        check_bug_reproduction(
            args.base, args.repro_test, args.no_repro_test, execution
        )
        if args.bug and args.tier == "attempt" else []
    )
    outcome, reason = (
        ("product_feedback", "bug_reproduction_failed")
        if any(status == "FAIL" for _, status, _ in results)
        else ("completed", None)
    )
    gpu_lock = None
    try:
        for name, fn in checks:
            if name in skip or name in later_tier:
                results.append((name, f"SKIP ({later_tier[name]} tier)" if name in later_tier else "SKIP", ""))
                continue
            if name in GPU_CHECKS and gpu_lock is None:
                gpu_lock = _acquire_exclusive(execution)
            with lifecycle.scope(
                cfg.factory / "events.jsonl", "gate-check", lock=GPU_LOCK if gpu_lock else None
            ) as check:
                check.emit("check", check=name)
                passed, output = fn()
                if not passed and check.outcome == "completed":
                    check.outcome = "product_feedback"
                    check.reason = "check_failed"
                fields = {"failed_tests": failed_test_ids(output)} if not passed else {}
                if fields.get("failed_tests"):
                    fields["changed_paths"] = changed_paths(args.base)
                check.emit("result", check=name, passed=passed, **fields)
                status = proofs[name][0] if name in proofs else _check_status(
                    name, passed, output, args.blocking_mutation,
                )
                results.append((name, status, output))
            if not passed and (outcome == "completed" or check.outcome == "unknown"):
                outcome, reason = check.outcome, check.reason
    finally:
        if gpu_lock is not None:
            _release_exclusive(execution, gpu_lock)

    # Gate-written, above every check output: the dispatcher reads proof status only here.
    proof_lines = [f"Mutation proof: {name} {status.split()[0]}{binding}"
                   for name, (status, binding) in proofs.items()]
    lines = [
        "# Gate report" if args.tier == "attempt" else f"# Gate report ({args.tier} tier)", "",
        *([*proof_lines, ""] if proof_lines else []), "## Host toolchain",
        f"ROCm: {host['rocm']}", f"Kernel: {host['kernel']}", f"amdgpu: {host['amdgpu']}", "",
        *audit,
    ]
    for name, status, _ in results:
        lines.append(f"- {name}: {status}")
    for name, status, output in results:
        # pr-tier checks may be report-only (exit 0, findings on stdout); the reviewer sees
        # them only through this report, so keep their output when they pass too.
        if status == "FAIL" or (args.tier == "pr" and status == "PASS" and output.strip()):
            tail = "\n".join(output.splitlines()[-TAIL_LINES:])
            lines += ["", f"## {name} {'failure' if status == 'FAIL' else 'output'}", "```", tail, "```"]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n")

    failed = [name for name, status, _ in results if status == "FAIL"]
    summary = "gate FAIL: " + ", ".join(failed) if failed else "gate PASS"
    print(f"report: {report}")
    print(summary)
    execution.outcome, execution.reason = outcome, reason
    return 1 if failed else 0
