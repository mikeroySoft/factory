
from __future__ import annotations

import contextlib
import io
import json

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import mutation
from tests import test_mutation_deadline
from tests.test_mutation_deadline import TestOutcome, WorkerOutcome


class MutationBudgetTest(unittest.TestCase):
    def test_deadline_shortened_timeout_stays_cut_not_killed(self) -> None:
        result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.KILLED,
            diff="", output="timeout",
        )
        status, output, mutate, _ = test_mutation_deadline.MutationDeadlineTest().run_mutation(
            result, clock=[0, 0, 0, 0.1, 0.2, 0.9, 2, 2],
        )
        self.assertEqual(status, 0)
        self.assertAlmostEqual(mutate.call_args.args[2], 0.1)
        self.assertIn("ran 0 of 1 mutants", output)
        self.assertIn("0 timeouts", output)
        self.assertIn("incomplete: 0 mutants not started and 1 cut short", output)

    def test_timeout_returned_after_deadline_is_cut_not_killed(self) -> None:
        result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.KILLED,
            diff="", output="timeout",
        )
        status, output, mutate, _ = test_mutation_deadline.MutationDeadlineTest().run_mutation(
            result, clock=[0, 0, 0, 0.1, 0.2, 0.3, 2, 2],
        )
        self.assertEqual(status, 0)
        self.assertAlmostEqual(mutate.call_args.args[2], 0.2)
        self.assertIn("ran 0 of 1 mutants", output)
        self.assertIn("0 timeouts", output)
        self.assertIn("incomplete: 0 mutants not started and 1 cut short", output)

    def test_sub_baseline_timeouts_leave_equivalents_incomplete_and_continue(self) -> None:
        now = [0.0]
        timeouts = []
        completed = []
        specs = [SimpleNamespace(
            module_path=Path("factory/example.py"), operator_name="NumberReplacer",
            start_pos=(n, 0), end_pos=(n, 9), occurrence=n,
        ) for n in range(1, 5)]

        def run(command, **kwargs):
            if command[0] != "git":
                now[0] += 80  # deadline 120, remaining 40: cap 10, below baseline
            return subprocess.CompletedProcess(command, 0, "", "")

        def mutate_and_test(local, command, timeout):
            timeouts.append(timeout)
            number = local[0].occurrence
            duration = 100 if number == 1 else 80 if number == 3 else 1
            now[0] += min(duration, timeout)
            completed.append(number)
            return SimpleNamespace(
                worker_outcome=WorkerOutcome.NORMAL,
                test_outcome=(
                    TestOutcome.SURVIVED if number == 3 and duration <= timeout else TestOutcome.KILLED
                ),
                output="timeout" if duration > timeout else "",
                diff=f"-{'abcd'[number - 1]} = 1\n+{'abcd'[number - 1]} = 2\n",
            )

        modules = {
            "attrs": types.SimpleNamespace(evolve=lambda value, **changes: SimpleNamespace(
                **{**vars(value), **changes},
            )),
            "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.mutating": types.SimpleNamespace(mutate_and_test=mutate_and_test),
            "cosmic_ray.work_item": types.SimpleNamespace(TestOutcome=TestOutcome),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "factory").mkdir()
            (root / "factory/example.py").write_text("a = 1\nb = 1\nc = 1\nd = 1\n")
            (root / "reviewed.json").write_text(json.dumps([{
                "file": "factory/example.py", "line": 3, "operator": "NumberReplacer",
                "original": "c = 1", "mutated": "c = 2",
                "argument": "The value is unused.", "reviewer": "Fixture reviewer",
            }]))
            output = io.StringIO()
            with contextlib.chdir(root), mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(mutation.time, "monotonic", side_effect=lambda: now[0]), \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation, "gate_timeout", return_value=180), \
                    mock.patch.object(mutation, "changed_lines", return_value={"factory/example.py": {1, 2, 3, 4}}), \
                    mock.patch.object(mutation, "sample", return_value=(specs, 4)), \
                    mock.patch.object(mutation.subprocess, "run", side_effect=run), \
                    contextlib.redirect_stdout(output):
                status = mutation.main(["--blocking", "--equivalents", "reviewed.json"])

        self.assertEqual(timeouts, [10.0] * 4)
        self.assertEqual(completed, [1, 2, 3, 4])
        self.assertEqual(now[0], 102)
        text = output.getvalue()
        self.assertEqual(status, 0, text)  # an unfinished equivalent is not disproven
        self.assertIn("baseline: 80s, per-mutant timeout 10s, 40s left", text)
        self.assertIn("ran 2 of 4 mutants", text)
        self.assertIn("2 killed", text)
        self.assertIn("0 timeouts", text)  # sub-baseline timeouts belong only in cut
        self.assertIn("0 survived", text)
        self.assertIn("incomplete: 0 mutants not started and 2 cut short", text)
        self.assertNotIn("stale equivalent:", text)

    def test_timeout_with_baseline_sized_budget_still_counts_as_killed(self) -> None:
        result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.KILLED,
            diff="", output="timeout",
        )
        status, output, mutate, _ = test_mutation_deadline.MutationDeadlineTest().run_mutation(
            result, clock=[0, 0, 0, 0.1, 0.2, 0.3, 0.5, 0.5, 0.5],
        )
        self.assertEqual(status, 0, output)
        self.assertAlmostEqual(mutate.call_args.args[2], 0.2)  # baseline 0.1s fits
        self.assertIn("1 timeouts (included in killed); 1 killed; 0 survived", output)
        self.assertNotIn("incomplete:", output)

    def test_completed_survivor_output_remains_actionable_for_dispatch(self) -> None:
        from factory import dispatch

        survivor = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.SURVIVED,
            diff="-value = 1\n+value = 2\n", output="",
        )
        status, output, _, _ = test_mutation_deadline.MutationDeadlineTest().run_mutation(survivor)

        self.assertEqual(status, 1)
        self.assertTrue(dispatch._actionable_mutation(output), output)


if __name__ == "__main__":
    unittest.main()
