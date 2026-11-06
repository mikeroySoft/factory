"""Slow-suite sampling and the gate's budget-coverage journal contract (#528)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import gate, lifecycle
from scripts import mutation
from tests import test_gate as gate_test
from tests import test_mutation_deadline as deadline


class MutationBudgetTest(unittest.TestCase):
    def run_slow_suite(self, survive=False):
        now = [0.0]
        trials = []
        specs = [SimpleNamespace(**{**vars(deadline.MutationDeadlineTest.spec),
                                  "start_pos": (i + 1, 0), "end_pos": (i + 1, 9)})
                 for i in range(200)]

        def run(cmd, **kwargs):
            if cmd[0] != "git":
                now[0] += 221
            return subprocess.CompletedProcess(cmd, 0, "")

        def mutate(specs, command, timeout):
            trials.append(specs[0])
            now[0] += min(221, timeout)
            return SimpleNamespace(
                test_outcome=(deadline.TestOutcome.SURVIVED if survive and len(trials) == 1
                              else deadline.TestOutcome.KILLED),
                worker_outcome=deadline.WorkerOutcome.NORMAL,
                output="timeout" if timeout < 221 else "",
                diff="-value = 1\n+value = 2\n",
            )

        modules = {name: types.ModuleType(name) for name in
                   ("attrs", "cosmic_ray", "cosmic_ray.mutating", "cosmic_ray.work_item")}
        modules["attrs"].evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        modules["cosmic_ray.mutating"].mutate_and_test = mutate
        modules["cosmic_ray.work_item"].TestOutcome = deadline.TestOutcome
        output = io.StringIO()
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(mutation.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(mutation, "WORKERS", 1), \
                mock.patch.object(mutation, "gate_timeout", return_value=1200), \
                mock.patch.object(mutation, "changed_lines", return_value={"factory/example.py": set(range(1, 201))}), \
                mock.patch.object(mutation, "sample", return_value=(specs, 200)), \
                mock.patch.object(mutation.shutil, "copytree"), \
                mock.patch.object(mutation.subprocess, "run", side_effect=run), \
                contextlib.redirect_stdout(output):
            code = mutation.main(["--blocking"])
        return code, output.getvalue(), trials, now[0]

    def test_slow_hot_sample_fits_budget_and_preserves_survivor_failure(self):
        for survive in (False, True):
            with self.subTest(survive=survive):
                code, report, trials, elapsed = self.run_slow_suite(survive)
                self.assertEqual(code, int(survive), report)
                self.assertLessEqual(elapsed, 1140)
                self.assertEqual(len(trials), 4, report)
                self.assertIn("budget-limited: 196 of 200 not run", report)
                self.assertIn("baseline 221s, 1 workers, 1140s", report)
                self.assertNotIn("cut short", report)
                if survive:
                    self.assertIn("1 survived", report)

    def test_gate_journals_budget_limited_coverage_and_elapsed(self):
        now = [0.0]
        summary = {"status": "budget_limited", "budget_seconds": 1140,
                   "baseline_seconds": 221, "workers": 1,
                   "mutants_run": 4, "mutants_not_run": 196}

        def timed(cmd):
            now[0] += 1141
            return subprocess.CompletedProcess(cmd, 0, "mutation-budget: " + json.dumps(summary), "")

        with tempfile.TemporaryDirectory() as directory:
            root = gate_test.repo_with_test(Path(directory), (
                '[gate]\ntimeout = 1200\n[[gate.check]]\nname = "mutation"\n'
                'run = ["stub-mutation"]\nwhen = "pr"\nexclusive = false\n'
            ))
            with contextlib.chdir(root), mock.patch.dict(gate.__dict__), \
                    mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                    mock.patch.object(gate, "toolchain", return_value={"rocm": "n/a", "kernel": "n/a", "amdgpu": "n/a"}), \
                    mock.patch.object(gate, "timed", side_effect=timed), \
                    mock.patch("time.monotonic", side_effect=lambda: now[0]):
                self.assertEqual(gate.main(["--tier", "pr", "--report", str(root / "report.md")]), 0)
            rows = lifecycle.read_events(root / ".factory/events.jsonl")
            [result] = [row for row in rows if row.get("stage") == "gate-check"
                        and row.get("kind") == "result" and row.get("check") == "mutation"]
            self.assertEqual(result.get("status"), "budget_limited")
            self.assertEqual(result["elapsed_seconds"], 1141)
            for key, value in summary.items():
                self.assertEqual(result[key], value)
            [exit_row] = [row for row in rows if row.get("stage") == "gate-check" and row.get("kind") == "exit"]
            self.assertEqual(exit_row["reason"], "budget_limited")

    def test_outer_timeouts_keep_watch_filing_and_runtime_reason(self):
        from factory import runtime_events, watch

        for name in ("test", "ruff", "mutation"):
            with self.subTest(check=name), tempfile.TemporaryDirectory() as directory:
                command = json.dumps([sys.executable, "-c", "import time; time.sleep(30)"])
                root = gate_test.repo_with_test(Path(directory), (
                    f'[[gate.check]]\nname = "{name}"\nrun = {command}\n'
                    'when = "pr"\nexclusive = false\n'
                ))
                with contextlib.chdir(root), mock.patch.dict(gate.__dict__), \
                        mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                        mock.patch.object(gate, "toolchain", return_value={
                            "rocm": "n/a", "kernel": "n/a", "amdgpu": "n/a",
                        }):
                    self.assertEqual(gate.main([
                        "--tier", "pr", "--check-timeout", "1",
                        "--report", str(root / "report.md"),
                    ]), 1)
                journal = root / ".factory/events.jsonl"
                rows = lifecycle.read_events(journal)
                [result] = [row for row in rows if row.get("kind") == "result"
                            and row.get("check") == name]
                self.assertFalse(result["passed"])
                self.assertEqual(result["status"], "budget_limited")
                self.assertEqual(result["budget_seconds"], 1)
                self.assertGreaterEqual(result["elapsed_seconds"], 1)
                exits = [row for row in rows if row.get("kind") == "exit"]
                self.assertEqual({row["stage"] for row in exits}, {"gate", "gate-check"})
                for row in exits:
                    self.assertEqual(row["outcome"], "unknown")
                    self.assertEqual(row["reason"], "check_timeout")
                    self.assertTrue(watch._failing(row))
                projected = runtime_events.project(journal)["executions"]
                self.assertEqual({row["reason"] for row in projected}, {"check_timeout"})

    def test_passing_budget_limited_reason_survives_runtime_projection(self):
        from factory import runtime_events, watch

        summary = {"status": "budget_limited", "budget_seconds": 1140,
                   "baseline_seconds": 221, "workers": 1,
                   "mutants_run": 4, "mutants_not_run": 196}
        with tempfile.TemporaryDirectory() as directory:
            root = gate_test.repo_with_test(Path(directory), (
                '[[gate.check]]\nname = "mutation"\nrun = ["stub-mutation"]\n'
                'when = "pr"\nexclusive = false\n'
            ))
            with contextlib.chdir(root), mock.patch.dict(gate.__dict__), \
                    mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                    mock.patch.object(gate, "toolchain", return_value={
                        "rocm": "n/a", "kernel": "n/a", "amdgpu": "n/a",
                    }), mock.patch.object(gate, "timed", return_value=subprocess.CompletedProcess(
                        [], 0, "mutation-budget: " + json.dumps(summary), "",
                    )):
                self.assertEqual(gate.main(["--tier", "pr", "--report", str(root / "report.md")]), 0)
            journal = root / ".factory/events.jsonl"
            [check] = [row for row in runtime_events.project(journal)["executions"]
                       if row["stage"] == "gate-check"]
            self.assertEqual(check["outcome"], "completed")
            self.assertEqual(check["reason"], "budget_limited")
            [exit_row] = [row for row in lifecycle.read_events(journal)
                          if row.get("kind") == "exit" and row["stage"] == "gate-check"]
            self.assertFalse(watch._failing(exit_row))
