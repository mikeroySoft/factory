#!/usr/bin/env python3
"""B1 isolation boundary (#98): one sandboxed process tree, no network, no credentials.

`run()` launches `argv` inside Bubblewrap (new user/PID/net/IPC/UTS/cgroup namespaces,
`--clearenv`, read-only /usr, private /tmp and /home, no /run, /etc, /sys or host home)
under a transient `systemd-run --user --scope` carrying the memory/CPU/task limits and a
`timeout`. Only three paths are shared: a read-only source checkout at /src, read-only
frozen inputs at /in and a private writable output directory at /out.

Before the sandbox starts, a host-side shim (`_shim`, this file) reads the scope's applied
cgroup limits back and refuses to exec on any mismatch. There is no host-execution
fallback: a boundary that cannot be established is reported as such and nothing runs.
Stdlib only; stdout/stderr go to host files the caller chooses.
"""
from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

BWRAP = "/usr/bin/bwrap"
SYSTEMD_RUN = "/usr/bin/systemd-run"
TIMEOUT = "/usr/bin/timeout"
PYTHON = "/usr/bin/python3"
SHIM_REFUSED = 90  # shim exit: applied cgroup limits differ from the request; payload never ran
KILL_AFTER = 5


def available() -> str | None:
    """None when the boundary can be built on this host; otherwise why not."""
    for tool in (BWRAP, SYSTEMD_RUN, TIMEOUT, PYTHON):
        if not os.access(tool, os.X_OK):
            return f"{tool} not executable"
    probes = (
        [SYSTEMD_RUN, "--user", "--scope", "--quiet", "--collect", "-p", "TasksMax=4", "--", "/usr/bin/true"],
        [BWRAP, "--unshare-user", "--unshare-pid", "--unshare-net", "--ro-bind", "/usr", "/usr",
         "--symlink", "usr/lib", "/lib64", "--symlink", "usr/lib", "/lib", "--", "/usr/bin/true"],
    )
    for argv in probes:
        try:
            done = subprocess.run(argv, capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{Path(argv[0]).name} probe failed: {type(exc).__name__}"
        if done.returncode != 0:
            return f"{Path(argv[0]).name} probe exited {done.returncode}: {done.stderr.decode(errors='replace')[:200]}"
    return None


def expected_limits(limits: dict) -> dict:
    """cgroup v2 file contents the scope must show for the requested limits."""
    return {"memory.max": str(limits["memory_bytes"]), "memory.swap.max": "0",
            "cpu.max": f"{limits['cpu_cores'] * 100000} 100000", "pids.max": str(limits["max_tasks"])}


def bwrap_args(src: Path, inp: Path, out: Path) -> list[str]:
    return [
        BWRAP,
        "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
        "--disable-userns", "--die-with-parent", "--new-session", "--clearenv",
        "--setenv", "PATH", "/usr/bin", "--setenv", "HOME", "/home/sandbox", "--setenv", "LANG", "C.UTF-8",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/bin", "/sbin",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home", "--dir", "/home/sandbox",
        "--ro-bind", str(src), "/src", "--ro-bind", str(inp), "/in", "--bind", str(out), "/out",
        "--chdir", "/src", "--",
    ]


def command(argv: list[str], *, src: Path, inp: Path, out: Path, limits: dict, seconds: int,
            limits_file: Path) -> list[str]:
    scope = [SYSTEMD_RUN, "--user", "--scope", "--quiet", "--collect",
             "-p", f"MemoryMax={limits['memory_bytes']}", "-p", "MemorySwapMax=0",
             "-p", f"CPUQuota={limits['cpu_cores'] * 100}%", "-p", f"TasksMax={limits['max_tasks']}", "--"]
    shim = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_shim", str(limits_file),
            json.dumps(expected_limits(limits)), str(limits["max_file_bytes"]), "--"]
    timer = [TIMEOUT, "--signal=TERM", f"--kill-after={KILL_AFTER}", str(seconds)]
    return [*scope, *shim, *timer, *bwrap_args(src, inp, out), *argv]


def run(argv: list[str], *, src: Path, inp: Path, out: Path, limits: dict, seconds: int,
        evidence_dir: Path, stop_file: Path | None = None) -> dict:
    """Run argv in the sandbox; return how it ended. Never raises for payload failures.

    outcome: exited | time_limit | killed | stopped | limits_mismatch | launch_failed.
    Only `exited` with returncode 0 means the payload ran to completion.
    """
    limits_file = evidence_dir / "limits.json"
    stdout_path, stderr_path = evidence_dir / "sandbox.stdout", evidence_dir / "sandbox.stderr"
    argv_full = command(argv, src=src, inp=inp, out=out, limits=limits, seconds=seconds, limits_file=limits_file)
    started = time.monotonic()
    outcome = None
    with stdout_path.open("wb") as so, stderr_path.open("wb") as se:
        try:
            proc = subprocess.Popen(argv_full, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                    start_new_session=True, env=_host_env())
        except OSError as exc:
            return {"outcome": "launch_failed", "detail": type(exc).__name__, "returncode": None,
                    "elapsed_seconds": 0.0, "limits": None}
        # Backstop beyond `timeout`: the host never waits forever on the scope.
        deadline = started + seconds + KILL_AFTER + 30
        while proc.poll() is None:
            if stop_file is not None and stop_file.exists() and outcome is None:
                outcome = "stopped"
                _signal(proc, signal.SIGTERM)
                deadline = min(deadline, time.monotonic() + KILL_AFTER)
            if time.monotonic() > deadline:
                outcome = outcome or "time_limit"
                _signal(proc, signal.SIGKILL)
                proc.wait()
                break
            time.sleep(0.2)
    code = proc.returncode
    applied = _read_json(limits_file)
    if outcome is None:
        if applied is None:
            outcome = "launch_failed"
        elif code == SHIM_REFUSED and applied.get("ok") is False:
            outcome = "limits_mismatch"
        elif code == 124:
            outcome = "time_limit"
        elif code < 0 or code in (128 + signal.SIGKILL, 128 + signal.SIGTERM):
            # OOM kill of the payload (137), or systemd stopping the whole scope after it (-15/143).
            outcome = "killed"
        elif code in (125, 126, 127) or _bwrap_failed(stderr_path):
            outcome = "launch_failed"
        else:
            outcome = "exited"
    return {"outcome": outcome, "returncode": code, "elapsed_seconds": round(time.monotonic() - started, 3),
            "limits": applied, "argv": argv_full}


def _host_env() -> dict:
    """systemd-run needs the user bus; nothing else from the operator environment is passed on."""
    keep = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "PATH")
    return {key: os.environ[key] for key in keep if key in os.environ}


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _bwrap_failed(stderr_path: Path) -> bool:
    with stderr_path.open("rb") as handle:
        return handle.read(65536).lstrip().startswith(b"bwrap:")


def _shim(limits_file: str, expected_json: str, max_file_bytes: str, rest: list[str]) -> int:
    """Runs on the host inside the new scope: prove the limits, cap file size, then exec."""
    expected = json.loads(expected_json)
    try:
        cgroup = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
        applied = {name: (Path("/sys/fs/cgroup" + cgroup) / name).read_text().strip() for name in expected}
    except (OSError, IndexError) as exc:
        cgroup, applied = None, {"error": type(exc).__name__}
    ok = applied == expected
    Path(limits_file).write_text(json.dumps({"cgroup": cgroup, "expected": expected, "applied": applied,
                                             "max_file_bytes": int(max_file_bytes), "ok": ok}, indent=2) + "\n")
    if not ok:
        return SHIM_REFUSED
    cap = int(max_file_bytes)
    resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execv(rest[0], rest)
    return 127  # unreachable


if __name__ == "__main__":
    if len(sys.argv) >= 6 and sys.argv[1] == "_shim" and sys.argv[5] == "--":
        sys.exit(_shim(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[6:]))
    print(available() or "sandbox available")
