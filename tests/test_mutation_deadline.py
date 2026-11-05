from __future__ import annotations

import contextlib
import io
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
    INCOMPETENT = "incompetent"
    KILLED = "killed"


class WorkerOutcome(Enum):
    NORMAL = "normal"


class MutationDeadlineTest(unittest.TestCase):
    spec = SimpleNamespace(
        module_path=Path("factory/example.py"),
        operator_name="NumberReplacer",
        occurrence=0,
        start_pos=(1, 0),
        end_pos=(1, 9),
    )

    def run_mutation(
        self,
        result: SimpleNamespace,
        *,
        clock: list[float] | None = None,
        baseline_returncode: int = 0,
    ) -> tuple[int, str, mock.Mock, mock.Mock]:
        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        cosmic_ray = types.ModuleType("cosmic_ray")
        cosmic_ray.__path__ = []
        mutating = types.ModuleType("cosmic_ray.mutating")
        mutating.mutate_and_test = mock.Mock(return_value=result)
        work_item = types.ModuleType("cosmic_ray.work_item")
        work_item.TestOutcome = TestOutcome
        modules = {
            "attrs": attrs,
            "cosmic_ray": cosmic_ray,
            "cosmic_ray.mutating": mutating,
            "cosmic_ray.work_item": work_item,
        }
        timer = (
            mock.patch.object(mutation.time, "monotonic", side_effect=clock)
            if clock is not None
            else mock.patch.object(mutation.time, "monotonic", return_value=0)
        )
        baseline = mock.Mock(return_value=subprocess.CompletedProcess(
            [], baseline_returncode, "baseline output\n", "",
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "factory").mkdir()
            (root / "factory/example.py").write_text("value = 1\n")
            output = io.StringIO()
            with contextlib.chdir(root), mock.patch.dict(sys.modules, modules), timer, \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation, "gate_timeout", return_value=61), \
                    mock.patch.object(
                        mutation, "changed_lines", return_value={"factory/example.py": {1}},
                    ), \
                    mock.patch.object(mutation, "sample", return_value=([self.spec], 1)), \
                    mock.patch.object(mutation.subprocess, "run", baseline), \
                    contextlib.redirect_stdout(output):
                status = mutation.main(["--blocking"])
        return status, output.getvalue(), mutating.mutate_and_test, baseline

    def test_blocking_ignores_mutants_not_started_before_deadline(self) -> None:
        status, output, mutate, _ = self.run_mutation(
            SimpleNamespace(), clock=[0, 0, 0, 0.1, 0.2, 2, 2],
        )

        self.assertEqual(status, 0)
        self.assertIn("0 survived", output)
        self.assertIn("incomplete: 1 mutants not started and 0 cut short", output)
        mutate.assert_not_called()

    def test_blocking_ignores_trial_cut_short_by_deadline(self) -> None:
        cut_result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=TestOutcome.SURVIVED,
            diff="-value = 1\n+value = 2\n",
            output="timeout",
        )
        status, output, mutate, _ = self.run_mutation(
            cut_result, clock=[0, 0, 0, 0.1, 0.2, 0.3, 2, 2],
        )

        self.assertEqual(status, 0)
        self.assertIn("0 survived", output)
        self.assertIn("incomplete: 0 mutants not started and 1 cut short", output)
        mutate.assert_called_once()

    def test_blocking_fails_for_completed_changed_line_survivor(self) -> None:
        survivor = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=TestOutcome.SURVIVED,
            diff="-value = 1\n+value = 2\n",
            output="",
        )

        status, output, _, _ = self.run_mutation(survivor)

        self.assertEqual(status, 1)
        self.assertIn("factory/example.py:1: NumberReplacer", output)
        self.assertIn("1 survived", output)

    def test_blocking_fails_for_mutation_tool_failure(self) -> None:
        broken = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=None,
            diff="",
            output="tool failed",
        )

        status, output, _, _ = self.run_mutation(broken)

        self.assertEqual(status, 1)
        self.assertIn("mutation check broke on 1 mutants", output)
        self.assertIn("tool failed", output)

    def test_blocking_fails_for_baseline_failure(self) -> None:
        status, output, mutate, _ = self.run_mutation(
            SimpleNamespace(), baseline_returncode=1,
        )

        self.assertEqual(status, 1)
        self.assertIn("baseline suite failed", output)
        mutate.assert_not_called()

    def test_blocking_fails_when_baseline_deadline_is_already_exhausted(self) -> None:
        status, output, mutate, baseline = self.run_mutation(
            SimpleNamespace(), clock=[0, 0, 2, 2, 2, 2, 2],
        )

        self.assertEqual(status, 1)
        self.assertIn("baseline suite did not finish", output)
        self.assertIn("incomplete: 1 mutants not started and 0 cut short", output)
        baseline.assert_not_called()
        mutate.assert_not_called()

    def run_storage_mutation(
        self,
        result: SimpleNamespace,
        *,
        baseline: list[object] | None = None,
        sample_error: BaseException | None = None,
        mutation_timeout: subprocess.TimeoutExpired | None = None,
    ) -> tuple[int, str, mock.Mock, mock.Mock]:
        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        cosmic_ray = types.ModuleType("cosmic_ray")
        cosmic_ray.__path__ = []
        mutating = types.ModuleType("cosmic_ray.mutating")
        testing = types.ModuleType("cosmic_ray.testing")
        testing._kill_process_group = mock.Mock()
        work_item = types.ModuleType("cosmic_ray.work_item")
        work_item.TestOutcome = TestOutcome

        def cosmic_run_tests(command: str, timeout: float):
            if mutation_timeout is None:
                return result.test_outcome, result.output
            process = mutation.subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                testing._kill_process_group(process)
                process.communicate()
                return TestOutcome.KILLED, "timeout"
            raise AssertionError("fixture must time out")

        mutating.run_tests = cosmic_run_tests

        def mutate_and_test(_mutations, command, timeout):
            outcome, output = mutating.run_tests(command, timeout)
            return SimpleNamespace(
                worker_outcome=WorkerOutcome.NORMAL,
                test_outcome=outcome,
                diff=result.diff,
                output=output,
            )

        mutating.mutate_and_test = mock.Mock(side_effect=mutate_and_test)
        modules = {
            "attrs": attrs,
            "cosmic_ray": cosmic_ray,
            "cosmic_ray.mutating": mutating,
            "cosmic_ray.testing": testing,
            "cosmic_ray.work_item": work_item,
        }
        clone = subprocess.CompletedProcess([], 0, "", "")
        baseline_run = mock.Mock(side_effect=baseline or [
            clone, subprocess.CompletedProcess([], 0, "baseline output\n", ""),
        ])
        process = mock.Mock()
        if mutation_timeout is not None:
            process.communicate.side_effect = [mutation_timeout, (b"", b"")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "factory").mkdir()
            (root / "factory/example.py").write_text("value = 1\n")
            output = io.StringIO()
            with contextlib.chdir(root), mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(mutation.time, "monotonic", return_value=0), \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation, "gate_timeout", return_value=61), \
                    mock.patch.object(
                        mutation, "changed_lines", return_value={"factory/example.py": {1}},
                    ), \
                    mock.patch.object(
                        mutation, "sample", return_value=([self.spec], 1),
                        side_effect=sample_error,
                    ), \
                    mock.patch.object(mutation.subprocess, "run", baseline_run), \
                    mock.patch.object(mutation.subprocess, "Popen", return_value=process), \
                    contextlib.redirect_stdout(output):
                status = mutation.main(["--blocking"])
        return status, output.getvalue(), mutating.mutate_and_test, baseline_run

    def test_mutation_exhaustion_is_not_a_killed_mutant_or_check_failure(self) -> None:
        diagnostic = b"OSError: [Errno 28] No space left on device\n"
        storage_result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=TestOutcome.INCOMPETENT,
            diff="",
            output=diagnostic.decode(),
        )

        with self.subTest("baseline timeout retains storage output"):
            clone = subprocess.CompletedProcess([], 0, "", "")
            status, output, mutate, _ = self.run_storage_mutation(
                storage_result,
                baseline=[clone, subprocess.TimeoutExpired("baseline", 1, output=diagnostic)],
            )
            self.assertEqual(status, 75)
            self.assertNotIn("baseline suite did not finish", output)
            mutate.assert_not_called()

        with self.subTest("mutation timeout retains storage output"):
            timed_out = SimpleNamespace(
                worker_outcome=WorkerOutcome.NORMAL,
                test_outcome=TestOutcome.KILLED,
                diff="-value = 1\n+value = 2\n",
                output="timeout",
            )
            status, output, _, _ = self.run_storage_mutation(
                timed_out,
                mutation_timeout=subprocess.TimeoutExpired("tests", 1, output=diagnostic),
            )
            self.assertEqual(status, 75)
            self.assertNotIn("killed", output)
            self.assertNotIn("mutation check broke", output)

        with self.subTest("mutation tool storage failure"):
            status, output, _, _ = self.run_storage_mutation(storage_result)
            self.assertEqual(status, 75)
            self.assertNotIn("mutation check broke", output)

        product_failure = (
            "FAIL: test_disk_error (tests.test_example.ExampleTest.test_disk_error)\n"
            "Traceback (most recent call last):\n"
            "  File \"tests/test_example.py\", line 10, in test_disk_error\n"
            "    raise OSError(28, 'No space left on device')\n"
            "OSError: [Errno 28] No space left on device\n"
            "\nFAILED (failures=1)\n"
        )
        completed_kill = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=TestOutcome.KILLED,
            diff="-value = 1\n+value = 2\n",
            output=product_failure,
        )
        with self.subTest("completed mutant product failure"):
            status, output, _, _ = self.run_storage_mutation(completed_kill)
            self.assertEqual(status, 0)
            self.assertIn("1 killed", output)

        with self.subTest("completed baseline product failure"):
            clone = subprocess.CompletedProcess([], 0, "", "")
            failed = subprocess.CompletedProcess([], 1, product_failure, "")
            status, output, _, _ = self.run_storage_mutation(
                completed_kill, baseline=[clone, failed],
            )
            self.assertEqual(status, 1)
            self.assertIn("baseline suite failed", output)

        with self.subTest("preparation storage failure"):
            status, _, mutate, baseline = self.run_storage_mutation(
                storage_result, sample_error=OSError(28, "No space left on device"),
            )
            self.assertEqual(status, 75)
            baseline.assert_not_called()
            mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
