from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from factory import config, dispatch, gate, manage

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / config.CONFIG_NAME).write_text(
        '[gate]\nprotected_paths = [".factory.toml"]\nadd_only_paths = ["tests/**"]\n'
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_old.py").write_text("def test_old():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def run_gate(repo: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "factory", "gate", *args],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    report = repo / ".factory" / "gate-report.md"
    return proc.returncode, report.read_text() if report.exists() else proc.stdout + proc.stderr


class AddOnlyOverrideTest(unittest.TestCase):
    def test_override_allows_existing_test_edit_but_not_protected_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            (repo / "tests" / "test_old.py").write_text("def test_old():\n    assert False\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "edit test")
            with working_directory(repo):
                passed, reason, _ = gate.check_protected_paths("origin/main")
                self.assertFalse(passed)
                self.assertEqual(reason, "add_only_paths")
                self.assertTrue(gate.check_protected_paths(
                    "origin/main", add_only_override=True
                )[0])

            (repo / config.CONFIG_NAME).write_text("[gate]\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "edit config")
            with working_directory(repo):
                passed, reason, _ = gate.check_protected_paths(
                    "origin/main", add_only_override=True
                )
            self.assertFalse(passed)
            self.assertEqual(reason, "protected_paths")

    def test_bug_ticket_override_allows_deleting_existing_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            (repo / "tests" / "test_old.py").write_text("# obsolete test removed\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "delete obsolete test")
            labels = {"bug", config.LABEL_ADD_ONLY_OVERRIDE}
            flags = dispatch.gate_flags(None, labels)

            code, report = run_gate(
                repo,
                "--bug",
                "--no-repro-test",
                "existing-test protection fixture",
                "--add-only-override",
            )

            self.assertTrue(flags["add_only_override"])
            self.assertEqual(code, 0, report)
            self.assertNotIn("- protected-paths: FAIL", report)
            self.assertNotIn("- protected-tests: FAIL", report)

    def test_escalation_names_failed_check_and_clearing_label(self) -> None:
        cases = {
            "- protected-tests: FAIL": (
                "Failed check: `protected-tests`. "
                "Add label `factory-add-only-override` to clear it."
            ),
            "- protected-paths: FAIL (add-only path removed or changed existing lines)": (
                "Failed check: `add-only`. "
                "Add label `factory-add-only-override` to clear it."
            ),
        }
        for report, expected in cases.items():
            with self.subTest(report=report):
                self.assertEqual(dispatch._protected_gate_guidance(report), expected)

    def test_override_rejects_deletion_and_mode_change(self) -> None:
        for change in ("delete", "chmod"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                repo = repository(Path(directory))
                path = repo / "tests" / "test_old.py"
                if change == "delete":
                    path.unlink()
                else:
                    path.chmod(0o755)
                git(repo, "add", "-A")
                git(repo, "commit", "-q", "-m", change)
                with working_directory(repo):
                    passed, reason, _ = gate.check_protected_paths(
                        "origin/main", add_only_override=True
                    )
                self.assertFalse(passed)
                self.assertEqual(reason, "add_only_paths")

    def test_dual_match_is_protected_with_or_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repository(Path(directory))
            path = repo / "tests" / "acceptance" / "x.py"
            path.parent.mkdir()
            path.write_text("def test_x():\n    assert True\n")
            (repo / config.CONFIG_NAME).write_text(
                '[gate]\nprotected_paths = ["**/acceptance/**"]\n'
                'add_only_paths = ["tests/**"]\n'
            )
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "add acceptance test")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            with path.open("a") as stream:
                stream.write("\n# additive change\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "append acceptance test")

            with working_directory(repo):
                for override in (False, True):
                    with self.subTest(override=override):
                        passed, reason, _ = gate.check_protected_paths(
                            "origin/main", add_only_override=override
                        )
                        self.assertFalse(passed)
                        self.assertEqual(reason, "protected_paths")

    def test_manager_parses_add_only_authorization(self) -> None:
        output = (
            'DECISION: AUTHORIZE\n{"justification":"Ticket requires this assertion change",'
            '"guidance":"Update the obsolete assertion"}'
        )
        decision, _, data = manage.parse(output, {})
        self.assertEqual(decision, "AUTHORIZE")
        self.assertEqual(data, {
            "justification": "Ticket requires this assertion change",
            "guidance": "Update the obsolete assertion",
        })
        for justification in (None, ""):
            payload = '{"guidance":"Update it"}' if justification is None else (
                '{"justification":"","guidance":"Update it"}'
            )
            with self.subTest(justification=justification):
                self.assertEqual(manage.parse(f"DECISION: AUTHORIZE\n{payload}", {})[0], "HUMAN")

    def test_manager_authorization_adds_override_and_requeues(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        dispatch.configure(config.Config(Path(root.name), "acme/widgets"))
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        with mock.patch.object(dispatch, "run", return_value=result) as run, \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7,
                {"title": "Update obsolete test", "labels": [{"name": config.LABEL_HUMAN}]},
                "AUTHORIZE",
                "",
                {"justification": "The ticket requires it", "guidance": "Remove the old assertion"},
                Path("unused"),
            )

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            ["gh", "issue", "edit", "7", "--repo", dispatch.REPO,
             "--add-label", config.LABEL_ADD_ONLY_OVERRIDE],
            commands,
        )
        self.assertIn(
            ["gh", "issue", "edit", "7", "--repo", dispatch.REPO,
             "--remove-label", config.LABEL_HUMAN, "--add-label", config.LABEL_AGENT],
            commands,
        )
        comments = [command for command in commands if command[1:3] == ["issue", "comment"]]
        self.assertEqual(len(comments), 1)
        self.assertIn("Justification: The ticket requires it", comments[0][-1])
        self.assertIn("Guidance: Remove the old assertion", comments[0][-1])
        comment_index = next(
            i for i, command in enumerate(commands)
            if command[1:3] == ["issue", "comment"]
        )
        grant_index = commands.index(
            ["gh", "issue", "edit", "7", "--repo", dispatch.REPO,
             "--add-label", config.LABEL_ADD_ONLY_OVERRIDE]
        )
        self.assertLess(comment_index, grant_index)

    def test_gate_flags_maps_add_only_override_label(self) -> None:
        self.assertEqual(
            dispatch.gate_flags(None, {config.LABEL_ADD_ONLY_OVERRIDE}),
            {"protected_override": False, "add_only_override": True},
        )


if __name__ == "__main__":
    unittest.main()
