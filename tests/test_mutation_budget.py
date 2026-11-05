from __future__ import annotations

import contextlib
import io
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

    def test_slow_baseline_caps_hang_and_leaves_budget_for_other_mutants(self) -> None:
        now = [0.0]
        timeouts = []
        completed = []
        specs = [SimpleNamespace(
            module_path=Path("factory/example.py"), operator_name="NumberReplacer",
            start_pos=(n, 0), end_pos=(n, 9), occurrence=n,
        ) for n in range(1, 4)]

        def run(command, **kwargs):
            if command[0] != "git":
                now[0] += 80  # deadline 120, remaining 40: cap 10, below baseline
            return subprocess.CompletedProcess(command, 0, "", "")

        def mutate_and_test(local, command, timeout):
            timeouts.append(timeout)
            number = local[0].occurrence
            duration = 100 if number == 1 else 1
            now[0] += min(duration, timeout)
            completed.append(number)
            return SimpleNamespace(
                worker_outcome=WorkerOutcome.NORMAL,
                test_outcome=TestOutcome.SURVIVED if number == 3 else TestOutcome.KILLED,
                output="timeout" if duration > timeout else "", diff="",
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
            (root / "factory/example.py").write_text("a = 1\nb = 1\nc = 1\n")
            output = io.StringIO()
            with contextlib.chdir(root), mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(mutation.time, "monotonic", side_effect=lambda: now[0]), \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation, "gate_timeout", return_value=180), \
                    mock.patch.object(mutation, "changed_lines", return_value={"factory/example.py": {1, 2, 3}}), \
                    mock.patch.object(mutation, "sample", return_value=(specs, 3)), \
                    mock.patch.object(mutation.subprocess, "run", side_effect=run), \
                    contextlib.redirect_stdout(output):
                status = mutation.main(["--blocking"])

        self.assertEqual(timeouts, [10.0, 10.0, 10.0])
        self.assertEqual(completed, [1, 2, 3])
        self.assertEqual(now[0], 92)
        self.assertEqual(status, 1)  # the completed survivor still blocks
        text = output.getvalue()
        self.assertIn("baseline: 80s, per-mutant timeout 10s, 40s left", text)
        self.assertIn("ran 3 of 3 mutants", text)
        self.assertIn("2 killed", text)  # includes the per-mutant timeout
        self.assertIn("1 timeouts", text)
        self.assertIn("1 survived", text)
        self.assertNotIn("incomplete:", text)


if __name__ == "__main__":
    unittest.main()
