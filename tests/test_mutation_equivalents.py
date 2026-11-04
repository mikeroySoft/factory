from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import mutation


class TestOutcome(Enum):
    SURVIVED = "survived"
    KILLED = "killed"


class MutationEquivalentsTest(unittest.TestCase):
    entry = {
        "file": "factory/example.py", "line": 1, "operator": "NumberReplacer",
        "original": "value = 1", "mutated": "value = 2",
        "argument": "The value is never read.", "reviewer": "Fixture reviewer",
    }

    def run_gate(self, entries, *, outcome=TestOutcome.SURVIVED, diff=None, changed=True,
                 blocking=True):
        spec = SimpleNamespace(
            module_path=Path(self.entry["file"]), operator_name=self.entry["operator"],
            start_pos=(1, 0), end_pos=(1, 9),
        )
        result = SimpleNamespace(
            test_outcome=outcome,
            diff=diff if diff is not None else "--- before\n+++ after\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
        )
        modules = {
            "attrs": SimpleNamespace(evolve=lambda value, **kw: SimpleNamespace(**{**vars(value), **kw})),
            "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=lambda *args: result),
            "cosmic_ray.work_item": SimpleNamespace(TestOutcome=TestOutcome),
        }
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            Path("reviewed.json").write_text(json.dumps(entries))
            output = io.StringIO()
            with mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(mutation, "changed_lines", return_value={spec.module_path.as_posix(): {1}} if changed else {}), \
                    mock.patch.object(mutation, "sample", return_value=([spec], 1)), \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation.shutil, "copytree"), \
                    mock.patch.object(mutation.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")), \
                    contextlib.redirect_stdout(output):
                status = mutation.main([*( ["--blocking"] if blocking else []), "--equivalents", "reviewed.json"])
            return status, output.getvalue()

    def test_only_reviewed_equivalent_survives(self):
        status, report = self.run_gate([self.entry])
        self.assertEqual(status, 0, report)
        self.assertIn("accepted equivalent", report)
        self.assertIn("Fixture reviewer", report)
        self.assertIn("The value is never read.", report)

    def test_stale_entry_fails_even_in_report_only_mode(self):
        for blocking in (False, True):
            with self.subTest(blocking=blocking):
                status, report = self.run_gate([self.entry], outcome=TestOutcome.KILLED, blocking=blocking)
                self.assertNotEqual(status, 0, report)
                self.assertIn("stale equivalent", report)

    def test_stale_entry_fails_without_mutable_files(self):
        status, report = self.run_gate([self.entry], changed=False)
        self.assertNotEqual(status, 0, report)
        self.assertIn("stale equivalent", report)

    def test_identity_requires_location_operator_and_exact_untruncated_text(self):
        for field, value in (("file", "factory/other.py"), ("line", 2),
                             ("operator", "OtherReplacer"), ("original", "value = 0"),
                             ("mutated", "value = 3")):
            with self.subTest(field=field):
                status, report = self.run_gate([{**self.entry, field: value}])
                self.assertNotEqual(status, 0, report)
                self.assertNotIn("accepted equivalent", report)
        prefix = "value = '" + "a" * 100
        status, report = self.run_gate(
            [{**self.entry, "original": prefix + "x'", "mutated": prefix + "y'"}],
            diff=f"-{prefix}x'\n+{prefix}z'\n",
        )
        self.assertNotEqual(status, 0, report)
        self.assertNotIn("accepted equivalent", report)

    def test_unreviewed_survivor_still_blocks(self):
        status, report = self.run_gate([])
        self.assertNotEqual(status, 0, report)
        self.assertIn("NumberReplacer", report)

    def test_missing_or_invalid_review_fields_fail_closed(self):
        for field in self.entry:
            with self.subTest(missing=field):
                entry = {key: value for key, value in self.entry.items() if key != field}
                status, report = self.run_gate([entry])
                self.assertNotEqual(status, 0, report)
                self.assertIn("invalid equivalents", report)
        for change in ({"reviewer": " "}, {"argument": "two\nlines"}, {"line": True},
                       {"file": "../example.py"}, {"mutated": 2}):
            with self.subTest(change=change):
                status, report = self.run_gate([{**self.entry, **change}])
                self.assertNotEqual(status, 0, report)
                self.assertIn("invalid equivalents", report)


if __name__ == "__main__":
    unittest.main()
