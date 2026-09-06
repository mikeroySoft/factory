"""Deterministic quality gate for agent worktrees.

Runs from a worktree root. Exit 0 = all checks pass, 1 = any failure.
Writes a short Markdown report (PASS/FAIL per check + failure excerpts).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import signal
import subprocess
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
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
        env=execution.env() if execution else None,
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
    hits = [
        line
        for line in proc.stdout.splitlines()
        if line.startswith("+") and not line.startswith("+++") and LEAK_RE.search(line)
    ]
    execution = lifecycle.current()
    if hits and execution:
        execution.outcome = "product_feedback"
        execution.reason = "leak_scan_matches"
    return not hits, "\n".join(hits)


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
    args = parser.parse_args(argv)
    configure(config.load())
    with lifecycle.scope(cfg.factory / "events.jsonl", "gate") as execution:
        return execute(args, execution)


def execute(args: argparse.Namespace, execution) -> int:
    global CHECK_TIMEOUT
    if args.base is None:
        args.base = f"origin/{cfg.main}"
    if args.check_timeout is not None:
        CHECK_TIMEOUT = args.check_timeout
    skip = {name.strip() for name in args.skip.split(",") if name.strip()}
    if LEAK_RE is None:
        skip.add("leak-scan")

    checks = [
        ("conflict-markers", check_conflict_markers),
        *((check.name, (lambda c=check: run(c.run))) for check in cfg.checks),
        ("leak-scan", lambda: check_leaks(args.base)),
    ]

    results: list[tuple[str, str, str]] = []  # (name, status, output)
    outcome, reason = "completed", None
    gpu_lock = None
    gpu_acquired = False
    try:
        for name, fn in checks:
            if name in skip:
                results.append((name, "SKIP", ""))
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

    lines = ["# Gate report", ""]
    for name, status, _ in results:
        lines.append(f"- {name}: {status}")
    for name, status, output in results:
        if status == "FAIL":
            tail = "\n".join(output.splitlines()[-TAIL_LINES:])
            lines += ["", f"## {name} failure", "```", tail, "```"]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n")

    failed = [name for name, status, _ in results if status == "FAIL"]
    summary = "gate FAIL: " + ", ".join(failed) if failed else "gate PASS"
    print(f"report: {report}")
    print(summary)
    execution.outcome, execution.reason = outcome, reason
    return 1 if failed else 0
