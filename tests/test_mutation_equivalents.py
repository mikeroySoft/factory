from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest import mock

from scripts import mutation


class TestOutcome(Enum):
    SURVIVED = "survived"
    KILLED = "killed"
    INCOMPETENT = "incompetent"


class MutationEquivalentsTest(unittest.TestCase):
    entry: ClassVar[dict] = {
        "file": "factory/example.py", "line": 1, "operator": "NumberReplacer",
        "original": "value = 1", "mutated": "value = 2",
        "argument": "The value is never read.", "reviewer": "Fixture reviewer",
    }

    def run_gate(self, entries, *, outcome=TestOutcome.SURVIVED, diff=None, changed=True,
                 blocking=True, sampled=True, hot=1, clock=None, output=""):
        spec = SimpleNamespace(
            module_path=Path(self.entry["file"]), operator_name=self.entry["operator"],
            start_pos=(1, 0), end_pos=(1, 9),
        )
        result = SimpleNamespace(
            test_outcome=outcome,
            diff=diff if diff is not None else "--- before\n+++ after\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
            output=output,
        )
        modules = {
            "attrs": SimpleNamespace(evolve=lambda value, **kw: SimpleNamespace(**{**vars(value), **kw})),
            "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=lambda *args: result),
            "cosmic_ray.work_item": SimpleNamespace(TestOutcome=TestOutcome),
        }
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            Path("reviewed.json").write_text(json.dumps(entries))
            report = io.StringIO()
            timer = mock.patch.object(mutation.time, "monotonic", side_effect=clock) if clock else contextlib.nullcontext()
            with mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(mutation, "changed_lines", return_value={spec.module_path.as_posix(): {1}} if changed else {}), \
                    mock.patch.object(mutation, "sample", return_value=([spec] if sampled else [], hot)), \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation.shutil, "copytree"), \
                    mock.patch.object(mutation.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "")), \
                    mock.patch.object(mutation, "gate_timeout", return_value=61), timer, \
                    contextlib.redirect_stdout(report):
                status = mutation.main([
                    *(["--blocking"] if blocking else []), "--equivalents", "reviewed.json",
                ])
            return status, report.getvalue()

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

    def test_untested_entries_are_not_stale(self):
        for changes in ({"changed": False}, {"sampled": False}, {"hot": 0},
                        {"clock": [0, 0, 0, 0.1, 0.2, 2, 2]},
                        {"clock": [0, 0, 0, 0.1, 0.2, 0.3, 2, 2], "output": "timeout"}):
            with self.subTest(changes=changes):
                status, report = self.run_gate([self.entry], **changes)
                self.assertEqual(status, 0, report)
                self.assertNotIn("stale equivalent", report)

    def test_unchanged_file_entry_does_not_fail_report_only_run(self):
        status, report = self.run_gate(
            [{**self.entry, "file": "factory/unchanged.py"}], blocking=False,
        )
        self.assertEqual(status, 0, report)
        self.assertNotIn("stale equivalent", report)

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

    def test_real_cli_accepts_survivors_and_rejects_tested_stale_entries(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="mutation-equivalents-cli-") as directory:
            root = Path(directory)
            for name in ("factory", "scripts", "tests"):
                (root / name).mkdir()
                (root / name / "__init__.py").touch()
            shutil.copyfile(repository / "scripts/mutation.py", root / "scripts/mutation.py")
            (root / "factory/example.py").touch()
            (root / "AGENTS.md").write_text("`factory/example.py` `tests/test_example.py`\n")
            (root / ".factory.toml").write_text("[gate]\ntimeout = 120\n")

            def git(*args):
                subprocess.run(
                    ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                    cwd=root, check=True, capture_output=True, text=True,
                )

            git("init", "-b", "main")
            git("add", "factory", "scripts", "tests", "AGENTS.md", ".factory.toml")
            git("commit", "-m", "fixture baseline")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            (root / "factory/example.py").write_text("unused = 1\ndef answer():\n    return True\n")
            test = (
                "import unittest\nfrom factory.example import answer\n"
                "class ExampleTest(unittest.TestCase):\n"
                "    def test_answer(self):\n        self.assertTrue(answer())\n"
            )
            (root / "tests/test_example.py").write_text(test)
            git("add", "factory/example.py", "tests/test_example.py")
            git("commit", "-m", "fixture change")
            entries = [
                {**self.entry, "operator": "core/NumberReplacer", "original": "unused = 1",
                 "mutated": f"unused = {value}",
                 "argument": "Only literal 1 reaches this assignment; unused is not read by the fixture.",
                 "reviewer": "Synthetic CLI fixture reviewer"}
                for value in (0, 2)
            ]
            command = [
                "uv", "run", "--project", str(repository), "--frozen", "--no-managed-python",
                "--group", "mutation", "python", "scripts/mutation.py",
                "--blocking", "--equivalents", "reviewed.json",
            ]

            def run(entries, expected, label):
                (root / "reviewed.json").write_text(json.dumps(entries))
                result = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, timeout=90,
                )
                report = result.stdout + result.stderr
                self.assertEqual(result.returncode, expected, report)
                print(f"real mutation CLI {label}: exit {result.returncode}\n{report}")
                return report

            self.assertNotIn("accepted equivalent", run([], 1, "unreviewed"))
            accepted = run(entries, 0, "accepted")
            self.assertEqual(accepted.count("accepted equivalent:"), 2, accepted)
            # Widen the fixture's observation domain: the formerly unused value is now read.
            (root / "tests/test_example.py").write_text(
                test + "    def test_value(self):\n"
                "        from factory.example import unused\n        self.assertEqual(unused, 1)\n"
            )
            stale = run(entries, 1, "stale")
            self.assertEqual(stale.count("stale equivalent:"), 2, stale)
            self.assertNotIn("accepted equivalent", stale)


if __name__ == "__main__":
    unittest.main()
