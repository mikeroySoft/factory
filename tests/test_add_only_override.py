from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from factory import config, dispatch, gate, manage


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
    (repo / config.CONFIG_NAME).write_text(
        '[gate]\nprotected_paths = [".factory.toml"]\nadd_only_paths = ["tests/**"]\n'
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_old.py").write_text("def test_old():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


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

    def test_manager_fix_parses_add_only_authorization(self) -> None:
        workers = {"ci-fix": ["fix"]}
        decision, _, data = manage.parse(
            'DECISION: FIX\n{"worker":"ci-fix","guidance":"Fix it",'
            '"authorize_add_only":"Ticket requires this assertion change"}',
            workers,
        )
        self.assertEqual(decision, "FIX")
        self.assertEqual(data["authorize_add_only"], "Ticket requires this assertion change")
        self.assertEqual(
            manage.parse(
                'DECISION: FIX\n{"worker":"ci-fix","guidance":"Fix it",'
                '"authorize_add_only":true}',
                workers,
            )[0],
            "HUMAN",
        )

    def test_gate_flags_maps_add_only_override_label(self) -> None:
        self.assertEqual(
            dispatch.gate_flags(None, {config.LABEL_ADD_ONLY_OVERRIDE}),
            {"protected_override": False, "add_only_override": True},
        )


if __name__ == "__main__":
    unittest.main()
