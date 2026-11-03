#!/usr/bin/env python3
"""Run the cross-platform import smoke and complete unittest suite."""

from __future__ import annotations

import importlib
import os
import pkgutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import factory


def import_smoke() -> None:
    modules = sorted(
        module.name
        for module in pkgutil.walk_packages(factory.__path__, f"{factory.__name__}.")
        if module.name != "factory.__main__"
    )
    for name in modules:
        print(f"Importing {name}", flush=True)
        importlib.import_module(name)
    print(f"Imported factory and {len(modules)} factory.* modules", flush=True)

    subprocess.run([sys.executable, "-m", "factory", "--help"], check=True)
    print("factory.__main__ --help passed", flush=True)


def run_tests() -> bool:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    print("\nExplicit unittest skips:")
    for test, reason in result.skipped:
        print(f"- {test.id()}: {reason}")
    if not result.skipped:
        print("- none")

    platform_skips = sum(reason.startswith("Linux only:") for _, reason in result.skipped)
    print(f"Platform skips (Linux only:): {platform_skips}")
    return result.wasSuccessful()


def main() -> int:
    import_smoke()
    return 0 if run_tests() else 1


if __name__ == "__main__":
    raise SystemExit(main())
