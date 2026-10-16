"""Deterministic quality gate for agent worktrees.

Runs from a worktree root. Exit 0 = all checks pass, 1 = any failure.
Writes a short Markdown report (PASS/FAIL per check + failure excerpts).
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import os
import re
import signal
import subprocess
import tomllib
from pathlib import Path

from factory import config, lifecycle
from factory.config import CONFIG_NAME, Config

cfg: Config
GPU_LOCK: Path
LEAK_RE: re.Pattern[str] | None = None
GPU_CHECKS: set[str] = set()
TAIL_LINES = 80
CHECK_TIMEOUT = 1200  # seconds; overridable via --check-timeout


def configure(c: Config) -> None:
    global cfg, GPU_LOCK, LEAK_RE, GPU_CHECKS, CHECK_TIMEOUT
    cfg = c
    GPU_LOCK = cfg.lock
    LEAK_RE = re.compile(cfg.leak_pattern, re.IGNORECASE) if cfg.leak_pattern else None
    GPU_CHECKS = {check.name for check in cfg.checks if check.exclusive}
    CHECK_TIMEOUT = cfg.check_timeout


def timed(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command with the per-check timeout.

    A wedged check must FAIL, not sit on the GPU lock (ticket #5's gate hung
    for 2h). The check runs in its own session so a timeout kills the whole
    tree (cargo/test grandchildren included), not just the direct child.
    """
    execution = lifecycle.current()
    # The handoff row is still recorded, but the check never inherits the context:
    # a repo's own tests would otherwise append their fixture rows to the live ledger.
    env = execution.env() if execution else dict(os.environ)
    env.pop(lifecycle.CONTEXT_ENV, None)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
        env=env,
    )
    if execution:
        execution.child(proc.pid)
    try:
        out, _ = proc.communicate(timeout=CHECK_TIMEOUT)
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
            execution.emit("timeout", command=cmd, timeout_seconds=CHECK_TIMEOUT)
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        if execution:
            execution.emit("result", command=cmd, returncode=proc.returncode, timed_out=True)
        return subprocess.CompletedProcess(
            cmd, 124, f"{out}\ncheck timed out after {CHECK_TIMEOUT}s", ""
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


def check_protected_paths(base: str) -> tuple[bool, str, str]:
    """(passed, reason, detail): reason is `protected_paths`, or `add_only_paths` when
    every hit is an add-only glob whose change was not purely additive."""
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
    for meta, path in zip(fields[::2], fields[1::2]):
        old_mode, new_mode, _, _, status = meta.lstrip(":").split()
        if any(fnmatch.fnmatchcase(path, g) for g in add_only):
            if status == "M" and old_mode == new_mode:
                additive.append(path)
            elif status != "A":
                not_additive.append(path)
        elif any(fnmatch.fnmatchcase(path, g) for g in protected):
            hits.append(path)
    if additive:
        numstat = timed(["git", "diff", "--no-renames", "--numstat", "-z", f"{base}...HEAD", "--", *additive])
        if numstat.returncode:
            return False, "protected_paths", numstat.stdout + numstat.stderr
        for line in numstat.stdout.split("\0"):
            if line:
                _, deleted, path = line.split("\t")
                if deleted != "0":  # `-` for binary: not provably additive
                    not_additive.append(path)
    if not hits and not not_additive:
        return True, "", ""
    reason = "protected_paths" if hits else "add_only_paths"
    diff = timed(["git", "diff", "--no-renames", "--binary", f"{base}...HEAD", "--", *hits, *not_additive])
    return False, reason, diff.stdout + diff.stderr


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Quality gate for agent worktrees.")
    parser.add_argument("--base", default=None, help="base ref for leak scan")
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
    parser.add_argument("--tier", choices=config.CHECK_TIERS, default="attempt",
                        help="attempt: every check except `when = \"pr\"`/`\"release\"` ones (reported SKIP); "
                        "pr: only the `when = \"pr\"` checks (release ones reported SKIP); "
                        "release: only the `when = \"release\"` checks")
    args = parser.parse_args(argv)
    configure(config.load())
    with lifecycle.scope(cfg.factory / "events.jsonl", "gate") as execution:
        return execute(args, execution)


def toolchain() -> dict[str, str]:
    values = {}
    for name, command in (
        ("rocm", ["hipconfig", "--version"]),
        ("kernel", ["uname", "-r"]),
        ("amdgpu", ["modinfo", "-F", "version", "amdgpu"]),
    ):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
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

    if not args.protected_override and args.tier == "attempt":
        passed, reason, detail = check_protected_paths(args.base)
        if not passed:
            execution.outcome, execution.reason = "project_escalation", reason
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            note = f" ({ADD_ONLY_NOTE})" if reason == "add_only_paths" else ""
            report.write_text(f"# Gate report\n\n- protected-paths: FAIL{note}\n\n"
                              "## protected-paths failure\n\n```diff\n" + detail + "\n```\n")
            print(f"report: {report}\ngate FAIL: protected-paths")
            return 1
    # attempt lists every configured check, pr the pr+release ones, release only its own;
    # listed checks of a later tier are reported `SKIP (<tier> tier)` instead of run.
    rank = config.CHECK_TIERS.index(args.tier)
    listed = [check for check in cfg.checks if config.CHECK_TIERS.index(check.when) >= rank]
    configured = [(check.name, (lambda c=check: run(c.run))) for check in listed]
    checks = [
        ("conflict-markers", check_conflict_markers),
        *configured,
        ("leak-scan", lambda: check_leaks(args.base)),
    ] if args.tier == "attempt" else configured
    later_tier = {check.name: check.when for check in listed if check.when != args.tier}

    results: list[tuple[str, str, str]] = []  # (name, status, output)
    outcome, reason = "completed", None
    gpu_lock = None
    gpu_acquired = False
    try:
        for name, fn in checks:
            if name in skip or name in later_tier:
                results.append((name, f"SKIP ({later_tier[name]} tier)" if name in later_tier else "SKIP", ""))
                continue
            if name in GPU_CHECKS and gpu_lock is None:
                gpu_lock = open(GPU_LOCK, "w")  # noqa: SIM115 — lock must outlive the loop
                requested = execution.resource("requested", GPU_LOCK, scope="host", blocking=True)
                if lifecycle._lock_state(lifecycle._lock(GPU_LOCK)) == "held":
                    execution.wait("exclusive_resource", mode="blocking", resource=requested["resource"])
                fcntl.flock(gpu_lock, fcntl.LOCK_EX)
                gpu_acquired = True
                execution.resource("acquired", GPU_LOCK, scope="host", blocking=True)
                execution.wait_end()
            with lifecycle.scope(
                cfg.factory / "events.jsonl", "gate-check", lock=GPU_LOCK if gpu_lock else None
            ) as check:
                check.emit("check", check=name)
                passed, output = fn()
                if not passed and check.outcome == "completed":
                    check.outcome = "product_feedback"
                    check.reason = "check_failed"
                check.emit("result", check=name, passed=passed)
                results.append((name, "PASS" if passed else "FAIL", output))
            if not passed and (outcome == "completed" or check.outcome == "unknown"):
                outcome, reason = check.outcome, check.reason
    finally:
        if gpu_lock is not None:
            fcntl.flock(gpu_lock, fcntl.LOCK_UN)
            gpu_lock.close()
            if gpu_acquired:
                execution.resource("released", GPU_LOCK, scope="host", blocking=True)

    lines = [
        "# Gate report" if args.tier == "attempt" else f"# Gate report ({args.tier} tier)", "", "## Host toolchain",
        f"ROCm: {host['rocm']}", f"Kernel: {host['kernel']}", f"amdgpu: {host['amdgpu']}", "",
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
