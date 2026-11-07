"""Standalone dispatcher fixtures cannot journal into their worker's ledger (#620)."""
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
SCRIPT = """
import json, pathlib, sys, unittest
import test_actionable_rework
from factory import dispatch, lifecycle

class RecordingTest(test_actionable_rework.ActionableReworkTest):
    def tearDown(self):
        pathlib.Path(sys.argv[1]).write_text(json.dumps(lifecycle.read_events(dispatch.EVENTS)))

suite = unittest.TestSuite([RecordingTest('test_round_budget_counts_only_this_tickets_rework_events')])
result = unittest.TextTestRunner().run(suite)
raise SystemExit(not result.wasSuccessful())
"""


class DispatcherContextIsolationTest(unittest.TestCase):
    def test_standalone_lifecycle_fixture_leaves_inherited_live_journal_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live" / ".factory" / "events.jsonl"
            live.parent.mkdir(parents=True)
            before = b'{"event":"claimed","ticket":620}\n'
            live.write_bytes(before)
            fixture = root / "fixture-rows.json"
            context = {"schema_version": 1, "path": str(live), "ticket": 620, "attempt": 1,
                       "dispatcher_run_id": "outside-dispatcher", "root_execution_id": "outside-root",
                       "parent_execution_id": "outside-worker", "execution_ids": ["outside-worker"],
                       "locks": []}
            proc = subprocess.run(
                [sys.executable, "-c", SCRIPT, str(fixture)], cwd=ROOT / "tests",
                env={**os.environ, "PYTHONPATH": str(ROOT), lifecycle.CONTEXT_ENV: json.dumps(context)},
                capture_output=True, text=True, check=False, timeout=60,
            )
            self.assertEqual(live.read_bytes(), before, proc.stdout + proc.stderr)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            rows = json.loads(fixture.read_text())
            events = [row for row in rows if row["event"] == "lifecycle"]
            self.assertIn(10000, {row["ticket"] for row in events})
            self.assertEqual({row["dispatcher_run_id"] for row in events}, {None})

    def test_standalone_modules_leave_inherited_live_journal_unchanged(self):
        for module in ("test_prime_pilot", "test_stage_success", "test_subprocess_encoding"):
            for args in ([module], ["discover", "-s", ".", "-p", f"{module}.py"]):
                with self.subTest(module=module, args=args), tempfile.TemporaryDirectory() as directory:
                    live = Path(directory) / "live" / ".factory" / "events.jsonl"
                    live.parent.mkdir(parents=True)
                    before = b'{"event":"claimed","ticket":620}\n'
                    live.write_bytes(before)
                    context = {"schema_version": 1, "path": str(live), "ticket": 620,
                               "dispatcher_run_id": "outside-dispatcher",
                               "root_execution_id": "outside-root",
                               "parent_execution_id": "outside-worker", "locks": []}
                    proc = subprocess.run(
                        [sys.executable, "-m", "unittest", *args], cwd=ROOT / "tests",
                        env={**os.environ, "PYTHONPATH": str(ROOT),
                             lifecycle.CONTEXT_ENV: json.dumps(context)},
                        capture_output=True, text=True, check=False, timeout=60,
                    )
                    self.assertEqual(live.read_bytes(), before, proc.stdout + proc.stderr)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_eval_standalone_and_discovery_keep_lifecycle_rows_in_fixture_journals(self):
        script = """
import json, pathlib, sys, unittest
from factory import lifecycle

written = []
write = lifecycle._write
def record(handle, row):
    write(handle, row)
    if row.get("event") == "lifecycle":
        written.append({"path": str(handle.name), "row": row})
lifecycle._write = record
result = unittest.main(module=None, argv=["unittest", *sys.argv[2:]], exit=False).result
pathlib.Path(sys.argv[1]).write_text(json.dumps(written))
raise SystemExit(not result.wasSuccessful())
"""
        for args in (["test_eval"], ["discover", "-s", ".", "-p", "test_eval.py"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                live = root / "live" / ".factory" / "events.jsonl"
                live.parent.mkdir(parents=True)
                before = b'{"event":"claimed","ticket":620}\n'
                live.write_bytes(before)
                fixtures = root / "fixtures"
                fixtures.mkdir()
                recorded = root / "recorded.json"
                context = {"schema_version": 1, "path": str(live), "ticket": 620,
                           "dispatcher_run_id": "outside-dispatcher",
                           "root_execution_id": "outside-root",
                           "parent_execution_id": "outside-worker", "locks": []}
                proc = subprocess.run(
                    [sys.executable, "-c", script, str(recorded), *args], cwd=ROOT / "tests",
                    env={**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": str(fixtures),
                         lifecycle.CONTEXT_ENV: json.dumps(context)},
                    capture_output=True, text=True, check=False, timeout=60,
                )
                self.assertEqual(live.read_bytes(), before, proc.stdout + proc.stderr)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                rows = json.loads(recorded.read_text())
                self.assertIn("merge-eligibility", {entry["row"]["stage"] for entry in rows})
                for entry in rows:
                    self.assertTrue(Path(entry["path"]).is_relative_to(fixtures), entry)
                    self.assertNotEqual(entry["row"]["dispatcher_run_id"], "outside-dispatcher")
