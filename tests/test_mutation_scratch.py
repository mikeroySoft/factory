from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts import mutation

ROOT = Path(__file__).resolve().parents[1]


class MutationScratchTest(unittest.TestCase):
    def test_baseline_keeps_git_and_calibration_tests_in_isolated_scratch(self) -> None:
        # Sampling no mutants isolates the real baseline, without requiring cosmic-ray.
        modules = {
            "attrs": types.SimpleNamespace(evolve=None),
            "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.mutating": types.SimpleNamespace(mutate_and_test=None),
            "cosmic_ray.work_item": types.SimpleNamespace(TestOutcome=None),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            for name in ("factory", "scripts", "experiments/reviewer-calibration"):
                shutil.copytree(
                    ROOT / name, root / name,
                    ignore=shutil.ignore_patterns("__pycache__", "runs"),
                )
            (root / "tests").mkdir()
            shutil.copy2(
                ROOT / "tests/test_reviewer_calibration_model.py",
                root / "tests/test_reviewer_calibration_model.py",
            )
            (root / "AGENTS.md").write_text("")
            (root / "tracked.txt").write_text("committed\n")
            (root / "deleted.txt").write_text("delete me\n")
            (root / "tests/test_scratch_git.py").write_text('''\
import io
import subprocess
import tarfile
import unittest
from pathlib import Path

class ScratchGitTest(unittest.TestCase):
    def test_git_snapshot_and_current_files(self):
        root = Path.cwd()
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True)
        git_dir = subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"], text=True)
        self.assertEqual(Path(git_dir.strip()).parent.resolve(), root.resolve())
        self.assertEqual(Path(top.strip()).resolve(), root.resolve())
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        archive = subprocess.check_output(["git", "archive", head, "tracked.txt"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            self.assertEqual(tree.extractfile("tracked.txt").read(), b"committed\\n")
        self.assertEqual((root / "tracked.txt").read_text(), "dirty\\n")
        self.assertEqual((root / "untracked.txt").read_text(), "untracked\\n")
        self.assertFalse((root / "deleted.txt").exists())
''')
            git = [
                "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                "-c", "commit.gpgsign=false", "-C", str(root),
            ]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "baseline fixture"], check=True)
            linked = Path(directory) / "linked"
            subprocess.run([*git, "worktree", "add", "-q", "--detach", str(linked)], check=True)
            for source in (root, linked):
                with self.subTest(linked=source == linked):
                    (source / "tracked.txt").write_text("dirty\n")
                    (source / "untracked.txt").write_text("untracked\n")
                    (source / "deleted.txt").unlink()
                    output = io.StringIO()
                    with contextlib.chdir(source), mock.patch.dict(sys.modules, modules), \
                            mock.patch.object(mutation, "changed_lines", return_value={"factory/config.py": {1}}), \
                            mock.patch.object(mutation, "sample", return_value=([], 0)), \
                            contextlib.redirect_stdout(output):
                        status = mutation.main(["--blocking"])
                    self.assertEqual(status, 0, output.getvalue())
                    self.assertIn("baseline:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
