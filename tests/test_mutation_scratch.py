from __future__ import annotations

import contextlib
import io
import json
import shlex
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

    def test_isolated_child_imports_the_copy_but_runs_the_trusted_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory).resolve() / "copy"
            (copy / "factory").mkdir(parents=True)
            (copy / "factory/__init__.py").write_text("")
            (copy / "factory/config.py").write_text("ORIGIN = 'copy'\n")
            argv = shlex.split(mutation.test_command(copy, "tests.test_x"))
            launcher = argv.index("-c")
            flags, tail = argv[3:launcher], argv[launcher + 2:]
            self.assertEqual(argv[:3], ["env", "-C", str(copy)])
            self.assertTrue({"-I", "-S"} <= set(flags), flags)
            self.assertNotIn("-m", argv)
            self.assertEqual(tail[0], str(copy))  # first on the child's import path
            self.assertIn("--", tail)
            dash = tail.index("--")
            self.assertEqual(tail[dash:], ["--", str(Path(mutation.__file__).resolve()),
                                           "--test-module", "tests.test_x"])
            # Run the real launcher with a probe standing in for the trusted producer.
            probe = Path(directory) / "probe.py"
            probe.write_text(
                "import json, sys\nfrom factory import config\n"
                "print(json.dumps([config.ORIGIN, sys.argv, sys.flags.isolated, sys.flags.no_site]))\n"
            )
            argv[argv.index(str(Path(mutation.__file__).resolve()))] = str(probe)
            result = subprocess.run(argv, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), [
                "copy", [str(probe), "--test-module", "tests.test_x"], 1, 1,
            ])

    def test_child_harness_uses_producer_site_packages_not_interpreter_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provisioned = Path(directory) / "provisioned" / "lib" / "python3.12" / "site-packages"
            provisioned.mkdir(parents=True)
            interpreter = Path(directory) / "interpreter"
            wrong = interpreter / "lib" / "python3.12" / "site-packages"
            wrong.mkdir(parents=True)
            copy = Path(directory) / "copy"
            copy.mkdir()
            fake_exec = interpreter / "bin" / "python"
            fake_exec.parent.mkdir(parents=True)
            fake_exec.write_text("")
            with mock.patch.object(sys, "path", [str(provisioned), *sys.path]), \
                    mock.patch.object(sys, "executable", str(fake_exec)):
                argv = shlex.split(mutation.test_command(copy, "tests.test_x"))
            self.assertEqual(argv[:3], ["env", "-C", str(copy)])
            self.assertIn(str(copy), argv)
            self.assertIn(str(provisioned), argv)
            self.assertNotIn(str(wrong), argv)


if __name__ == "__main__":
    unittest.main()
