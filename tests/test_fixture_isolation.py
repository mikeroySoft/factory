"""#253: fixture `factory` runs never inherit the worker's lifecycle context.

A worker runs this suite with FACTORY_LIFECYCLE_CONTEXT set by the dispatcher. Without
isolation every fixture gate/dispatch run journals into the live events.jsonl under the
ticket, and the 1-second timeout fixtures file `check_timeout` runtime failures.

Run: python -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import lifecycle

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    "import pathlib, sys, test_factory, test_gate\n"
    "repo = test_factory.make_repo(pathlib.Path(sys.argv[1]))\n"
    "print(test_factory.gate(repo)[0], test_gate.gate(repo)[0], test_factory.factory(repo, 'gate').returncode)\n"
)


class FixtureIsolationTest(unittest.TestCase):
    def test_gate_helpers_journal_into_the_fixture_repo_not_the_inherited_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / "live" / "events.jsonl"
            context = json.dumps({"schema_version": 1, "path": str(live), "ticket": 253, "attempt": 1})
            proc = subprocess.run(
                [sys.executable, "-c", SCRIPT, d], cwd=ROOT / "tests", capture_output=True, text=True,
                env={**os.environ, "PYTHONPATH": str(ROOT), lifecycle.CONTEXT_ENV: context}, check=False,
            )
            self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "0 0 0"), proc.stderr)
            self.assertFalse(live.exists())
            rows = (Path(d) / "repo" / ".factory" / "events.jsonl").read_text().splitlines()
            self.assertEqual({json.loads(r)["ticket"] for r in rows}, {None})
