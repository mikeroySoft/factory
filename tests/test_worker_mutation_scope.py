from __future__ import annotations

import contextlib
import io
import os
import shlex
import shutil
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


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "-c", "commit.gpgsign=false", "-C", str(repo), *args,
        ],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def repo_with_base(directory: str) -> Path:
    repo = Path(directory) / "repo"
    (repo / "factory").mkdir(parents=True)
    for name in ("committed.py", "staged.py", "dirty.py", "upstream.py"):
        (repo / "factory" / name).write_text("value = 0\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "switch", "-q", "-c", "worker")
    return repo


class WorkingTreeSelectionTest(unittest.TestCase):
    def test_working_tree_adds_staged_dirty_and_untracked_to_committed_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_base(directory)
            (repo / "factory/committed.py").write_text("value = 1\n")
            git(repo, "commit", "-qam", "worker commit")
            with contextlib.chdir(repo):
                committed = mutation.changed_lines()

            (repo / "factory/staged.py").write_text("value = 2\n")
            git(repo, "add", "factory/staged.py")
            (repo / "factory/dirty.py").write_text("value = 3\n")
            (repo / "factory/untracked.py").write_text("first = 1\nsecond = 2\n")
            with contextlib.chdir(repo):
                working = mutation.changed_lines(working_tree=True)

            self.assertEqual(committed, {"factory/committed.py": {1}})
            self.assertEqual(working, {
                "factory/committed.py": {1},
                "factory/dirty.py": {1},
                "factory/staged.py": {1},
                "factory/untracked.py": {1, 2},
            })

    def test_working_tree_matches_pr_selection_when_upstream_has_moved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_base(directory)
            base = git(repo, "rev-parse", "HEAD")
            (repo / "factory/committed.py").write_text("value = 1\n")
            git(repo, "commit", "-qam", "worker commit")
            git(repo, "switch", "-q", "main")
            self.assertEqual(git(repo, "rev-parse", "HEAD"), base)
            (repo / "factory/upstream.py").write_text("value = 4\n")
            git(repo, "commit", "-qam", "upstream commit")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(repo, "switch", "-q", "worker")

            with contextlib.chdir(repo):
                self.assertEqual(
                    set(mutation.changed_lines()),
                    {"factory/committed.py", "factory/upstream.py"},
                )
                self.assertEqual(
                    mutation.changed_lines(working_tree=True),
                    mutation.changed_lines(),
                )

    def test_mutant_outcome_uses_current_source_with_same_size_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("factory", "scripts", "tests"):
                (root / name).mkdir()
            shutil.copy2(Path(mutation.__file__), root / "scripts/mutation.py")
            (root / "factory/__init__.py").write_text("")
            (root / "AGENTS.md").write_text("")
            source = root / "factory/example.py"
            source.write_text("def answer():\n    return 2\n")
            os.utime(source, (1_000_000_000, 1_000_000_000))
            (root / "tests/test_example.py").write_text(
                "import unittest\nfrom factory.example import answer\n"
                "class AnswerTest(unittest.TestCase):\n"
                "    def test_answer(self):\n"
                "        self.assertEqual(answer(), 2)\n"
            )
            environment = dict(os.environ)
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            command = shlex.split(mutation.test_command(root))
            baseline = subprocess.run(command, env=environment, capture_output=True, text=True)
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            source.write_text("def answer():\n    return 3\n")
            os.utime(source, (1_000_000_000, 1_000_000_000))
            mutant = subprocess.run(command, env=environment, capture_output=True, text=True)
            self.assertEqual(mutant.returncode, 1, mutant.stderr)
            self.assertIn("3 != 2", mutant.stderr)


class TestOutcome(Enum):
    SURVIVED = "survived"
    KILLED = "killed"


class WorkerOutcome(Enum):
    NORMAL = "normal"


class WorkerMutationReportTest(unittest.TestCase):
    spec = SimpleNamespace(
        module_path=Path("factory/example.py"), operator_name="NumberReplacer",
        occurrence=0, start_pos=(1, 0), end_pos=(1, 9),
    )

    @contextlib.contextmanager
    def cosmic_ray(self, result: SimpleNamespace):
        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        cosmic_ray = types.ModuleType("cosmic_ray")
        cosmic_ray.__path__ = []
        mutating = types.ModuleType("cosmic_ray.mutating")
        mutating.mutate_and_test = mock.Mock(return_value=result)
        work_item = types.ModuleType("cosmic_ray.work_item")
        work_item.TestOutcome = TestOutcome
        with mock.patch.dict(sys.modules, {
            "attrs": attrs,
            "cosmic_ray": cosmic_ray,
            "cosmic_ray.mutating": mutating,
            "cosmic_ray.work_item": work_item,
        }):
            yield mutating.mutate_and_test

    def run_mutation(self, result: SimpleNamespace, worker_deadline: float) -> tuple[int, str, str, mock.Mock]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "factory").mkdir()
            (root / "factory/example.py").write_text("value = 1\n")
            report = root / ".factory/mutation-545.md"
            output = io.StringIO()
            baseline = subprocess.CompletedProcess([], 0, "baseline output\n", "")
            with self.cosmic_ray(result) as mutate, contextlib.chdir(root), \
                    mock.patch.dict(os.environ, {"FACTORY_WORKER_DEADLINE": str(worker_deadline)}), \
                    mock.patch.object(mutation.time, "monotonic", return_value=100), \
                    mock.patch.object(mutation, "WORKERS", 1), \
                    mock.patch.object(mutation, "gate_timeout", return_value=1200), \
                    mock.patch.object(mutation, "changed_lines", return_value={"factory/example.py": {1}}) as changed, \
                    mock.patch.object(mutation, "sample", return_value=([self.spec], 1)), \
                    mock.patch.object(mutation.subprocess, "run", return_value=baseline), \
                    contextlib.redirect_stdout(output):
                status = mutation.main([
                    "--working-tree", "--blocking", "--report", str(report),
                ])
            changed.assert_called_once_with(working_tree=True)
            return status, output.getvalue(), report.read_text(), mutate

    def test_worker_deadline_cuts_trial_and_report_fails_incomplete_without_survivor(self) -> None:
        result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.SURVIVED,
            diff="-value = 1\n+value = 2\n", output="timeout",
        )
        status, output, report, mutate = self.run_mutation(result, worker_deadline=105)

        self.assertEqual(status, 1)
        self.assertEqual(report, output)
        self.assertIn("incomplete: 0 mutants not started and 1 cut short", report)
        self.assertIn("0 survived", report)
        self.assertNotIn("factory/example.py:1: NumberReplacer", report)
        self.assertTrue(report.endswith("worker mutation: FAIL\n"))
        self.assertEqual(mutate.call_args.args[2], 5)

    def test_survivor_row_is_durable_and_terminal_status_fails(self) -> None:
        result = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.SURVIVED,
            diff="-value = 1\n+value = 2\n", output="",
        )
        status, output, report, _ = self.run_mutation(result, worker_deadline=1000)

        self.assertEqual(status, 1)
        self.assertEqual(report, output)
        self.assertIn("factory/example.py:1: NumberReplacer", report)
        self.assertTrue(report.endswith("worker mutation: FAIL\n"))

    def test_preparation_exception_is_durable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / ".factory/mutation-545.md"
            output = io.StringIO()
            with mock.patch.object(mutation, "changed_lines", side_effect=RuntimeError("cannot diff")), \
                    contextlib.redirect_stdout(output):
                status = mutation.main(["--working-tree", "--report", str(report)])

            self.assertEqual(status, 1)
            self.assertEqual(report.read_text(), output.getvalue())
            self.assertIn("mutation check failed: RuntimeError: cannot diff", output.getvalue())
            self.assertTrue(output.getvalue().endswith("worker mutation: FAIL\n"))

    def test_empty_scope_writes_terminal_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / ".factory/mutation-545.md"
            output = io.StringIO()
            with mock.patch.object(mutation, "changed_lines", return_value={}), \
                    contextlib.redirect_stdout(output):
                status = mutation.main(["--working-tree", "--report", str(report)])

            self.assertEqual(status, 0)
            self.assertEqual(report.read_text(), output.getvalue())
            self.assertEqual(output.getvalue(), "no mutable files\nworker mutation: PASS\n")


if __name__ == "__main__":
    unittest.main()
