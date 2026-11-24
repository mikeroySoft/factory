#!/usr/bin/env python
"""Ablation harness for #903 — single-factor full-baseline runs.

Runs the full unittest suite with one optional intervention.
No product, config, or test changes; harness-only.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path


def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        else:
            yield item


def main():
    arm = sys.argv[1]       # control | ram | git-cache | scratch-throttle
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    load_before = list(os.getloadavg())
    ts_before = time.time()

    # Apply arm-specific in-process patches before test discovery
    if arm == "scratch-throttle":
        import factory.runner
        factory.runner._users = lambda root: False

    # Discover and run the full test suite (matches run_tests('') ordering)
    suite = unittest.TestSuite(
        sorted(_cases(unittest.defaultTestLoader.discover("tests")),
               key=lambda t: type(t).__module__)
    )

    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    elapsed = time.monotonic() - started

    load_after = list(os.getloadavg())

    data = {
        "arm": arm,
        "seconds": elapsed,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
        "load_before": load_before,
        "load_after": load_after,
        "python": sys.version,
        "timestamp_before": ts_before,
        "timestamp_after": time.time(),
        "hostname": os.uname().nodename.split(".")[0],
        "tmpdir": os.environ.get("TMPDIR", "(default)"),
        "arm_detail": _arm_detail(arm),
    }
    (output_dir / "result.json").write_text(json.dumps(data, indent=2) + "\n")

    # Write stderr tail for diagnostics
    return 0 if result.wasSuccessful() else 1


def _arm_detail(arm):
    if arm == "ram":
        return f"TMPDIR={os.environ.get('TMPDIR', '(default)')}"
    if arm == "git-cache":
        return f"PATH prepended with git-cache wrapper"
    if arm == "scratch-throttle":
        return "factory.runner._users patched to return False"
    return "no intervention"


if __name__ == "__main__":
    sys.exit(main())
