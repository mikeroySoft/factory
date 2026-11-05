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
        baseline.side_effect = lambda command, **_kwargs: (
            subprocess.CompletedProcess(command, 0, "", "")
            if command[:2] == ["git", "clone"] else baseline.return_value
        )
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

    def test_mutation_exhaustion_is_not_a_killed_mutant_or_check_failure(self) -> None:
        exhausted = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=TestOutcome.KILLED,
            diff="",
            output="OSError: [Errno 28] No space left on device\n",
        )

        status, output, _, _ = self.run_mutation(exhausted)

        self.assertEqual(status, 75)
        self.assertIn("runner_storage_low", output)
        self.assertIn("[Errno 28] No space left on device", output)
        self.assertNotIn("1 killed", output)
        self.assertNotIn("mutation check broke", output)

    def test_mutant_timeout_preserves_captured_storage_output(self) -> None:
        testing = types.ModuleType("cosmic_ray.testing")
        testing._kill_process_group = mock.Mock()
        work_item = types.ModuleType("cosmic_ray.work_item")
        work_item.TestOutcome = TestOutcome
        timeout = subprocess.TimeoutExpired(
            "tests", 1,
            output=b"write failed\n",
            stderr=b"OSError: [Errno 28] No space left on device\n",
        )
        process = mock.Mock()
        process.communicate.side_effect = [timeout, (b"", b"")]

        with mock.patch.dict(sys.modules, {
            "cosmic_ray.testing": testing,
            "cosmic_ray.work_item": work_item,
        }), mock.patch.object(mutation.subprocess, "Popen", return_value=process):
            outcome, output = mutation.run_cosmic_tests("tests", 1)

        self.assertEqual(outcome, TestOutcome.KILLED)
        self.assertIn("write failed", output)
        self.assertIn("[Errno 28] No space left on device", output)
        testing._kill_process_group.assert_called_once_with(process)

    def test_copy_exhaustion_exits_for_runner_storage_low(self) -> None:
        with mock.patch.object(
            mutation.shutil, "copytree",
            side_effect=OSError(28, "No space left on device"),
        ):
            status, output, mutate, _ = self.run_mutation(SimpleNamespace())

        self.assertEqual(status, 75)
        self.assertIn("runner_storage_low", output)
        self.assertIn("[Errno 28] No space left on device", output)
        mutate.assert_not_called()

    def test_runner_storage_low_copytree_aggregate_is_infrastructure(self) -> None:
        error = mutation.shutil.Error([
            ("source", "copy", "[Errno 28] No space left on device"),
        ])
        with mock.patch.object(mutation.shutil, "copytree", side_effect=error):
            status, output, mutate, _ = self.run_mutation(SimpleNamespace())
        self.assertEqual(status, 75, output)
        self.assertIn("runner_storage_low", output)
        self.assertNotIn("mutation check broke", output)
        mutate.assert_not_called()

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


class BaselineStorageTimeoutTest(unittest.TestCase):
    def test_runner_storage_low_baseline_timeout_retains_bytes_before_deadline_return(self):
        modules = {
            "attrs": SimpleNamespace(evolve=None),
            "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=mock.Mock()),
            "cosmic_ray.work_item": SimpleNamespace(TestOutcome=TestOutcome),
        }
        output = io.StringIO()
        timeout = subprocess.TimeoutExpired(
            "baseline", 1, output=b"baseline output\n",
            stderr=b"OSError: [Errno 28] No space left on device\n",
        )
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(mutation, "changed_lines", return_value={"factory/example.py": {1}}), \
                mock.patch.object(mutation, "sample", return_value=([], 0)), \
                mock.patch.object(mutation.shutil, "copytree"), \
                mock.patch.object(mutation.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""), timeout,
                ]), contextlib.redirect_stdout(output):
            status = mutation.main(["--blocking"])
        self.assertEqual(status, 75, output.getvalue())
        self.assertIn("baseline output", output.getvalue())
        self.assertIn("[Errno 28] No space left on device", output.getvalue())
        self.assertNotIn("baseline suite did not finish", output.getvalue())
        modules["cosmic_ray.mutating"].mutate_and_test.assert_not_called()


if __name__ == "__main__":
    unittest.main()
