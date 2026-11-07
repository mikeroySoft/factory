#!/usr/bin/env python3
"""
Darwin passive flock-holder attribution probe (standalone; no product imports required).

What it does:
- Creates one or two files under a temporary directory.
- Acquires a BSD flock (LOCK_EX) in the current process on the first file.
- Optionally starts a child Python process that acquires a flock on the second file.
- For each path, runs `/usr/sbin/lsof -nP -Fpfl -- <path>` and prints the raw output,
  then prints the parsed holder PIDs seen in the `lR` / `lW` lock fields.
- Never acquires a lock from an observer; only the self/child holders do.

Expected observations on macOS where lsof(4.91) leaves the lock field blank for FLOCK:
- Raw lsof shows records for the process(es) with 'p<pid>' and 'f<fd>' but an 'l ' (blank) field.
- Parsed holder PID set is empty ([]) despite a held flock.
- This demonstrates that a documented, passive OS API reporting flock holders is unavailable.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Tuple

L = print


def lsof_fields(path: Path) -> Tuple[int, str, str, List[int]]:
    proc = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-Fpfl", "--", str(path)],
        capture_output=True, text=True, check=False,
    )
    holders: List[int] = []
    pid: int | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line in {"lR", "lW"} and pid is not None:
            holders.append(pid)
    return proc.returncode, proc.stdout, proc.stderr, sorted(set(holders))


def hold_flock(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help="also spawn a child holder on a separate path")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="darwin-flock-probe-") as tmpdir:
        base = Path(tmpdir)
        first = base / "self.lock"
        fd = hold_flock(first)
        L(f"SELF HELD: {first}")
        rc, out, err, holders = lsof_fields(first)
        L(f"  lsof rc={rc}")
        L(f"  lsof stdout={out!r}")
        L(f"  lsof stderr={err!r}")
        L(f"  parsed holders={holders}")

        child = None
        if args.child:
            second = base / "child.lock"
            second.touch()
            code = (
                "import fcntl,os,sys,time;"
                "fd=os.open(sys.argv[1],os.O_RDWR);"
                "fcntl.flock(fd,fcntl.LOCK_EX);"
                "print('held', flush=True);"
                "time.sleep(30)"
            )
            child = subprocess.Popen([sys.executable, "-u", "-c", code, str(second)],
                                     stdout=subprocess.PIPE, text=True)
            # Wait until the child reports it acquired the lock.
            if child.stdout:
                child.stdout.readline()
            time.sleep(0.2)
            L(f"CHILD HELD: {second} pid={child.pid}")
            rc2, out2, err2, holders2 = lsof_fields(second)
            L(f"  lsof rc={rc2}")
            L(f"  lsof stdout={out2!r}")
            L(f"  lsof stderr={err2!r}")
            L(f"  parsed holders={holders2}")

        # Cleanup
        if child and child.poll() is None:
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

