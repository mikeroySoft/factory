"""Gate and mutation test children never inherit a worker's live journal."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import gate, lifecycle
from scripts import mutation

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_TEST = """\
import os
import unittest
from pathlib import Path
from factory import lifecycle

class FixtureTest(unittest.TestCase):
    def test_ticket_seven(self):
        with lifecycle.scope(Path(os.environ["FIXTURE_JOURNAL"]), "fixture", ticket=7):
            pass
"""
RUN_FIXTURE = """\
import sys
from pathlib import Path
from factory import lifecycle
with lifecycle.scope(Path(sys.argv[1]), "fixture", ticket=7):
    pass
"""


class JournalIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.live = self.root / "live" / "events.jsonl"
        self.live.parent.mkdir()
        self.live.write_text('{"event":"sentinel"}\n')
        self.before = self.live.read_bytes()
        self.fixture = self.root / "fixture" / ".factory" / "events.jsonl"
        self.context = json.dumps({
            "schema_version": 1,
            "path": str(self.live),
            "ticket": 566,
            "attempt": 1,
        })

    def assert_isolated_fixture(self) -> None:
        self.assertEqual(self.live.read_bytes(), self.before)
        rows = [json.loads(line) for line in self.fixture.read_text().splitlines()]
        self.assertEqual([row["kind"] for row in rows], ["enter", "exit"])
        self.assertEqual({row["ticket"] for row in rows}, {7})

    def test_gate_check_child_uses_fixture_journal(self) -> None:
        runner = self.root / "fixture.py"
        runner.write_text(RUN_FIXTURE)
        with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: self.context}):
            proc = gate.timed([sys.executable, str(runner), str(self.fixture)], pythonpath=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assert_isolated_fixture()

    def test_mutation_test_runner_uses_fixture_journal(self) -> None:
        copy = self.root / "mutation-copy"
        copy.mkdir()
        (copy / "factory").symlink_to(ROOT / "factory", target_is_directory=True)
        (copy / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
        tests = copy / "tests"
        tests.mkdir()
        (tests / "test_fixture.py").write_text(FIXTURE_TEST)
        (copy / "AGENTS.md").write_text("")
        env = {
            **os.environ,
            lifecycle.CONTEXT_ENV: self.context,
            "FIXTURE_JOURNAL": str(self.fixture),
        }
        proc = subprocess.run(
            shlex.split(mutation.test_command(copy, "factory.gate")),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assert_isolated_fixture()


if __name__ == "__main__":
    unittest.main()
