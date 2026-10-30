from __future__ import annotations

import contextlib
import io
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import mutation


class MutationSandboxTest(unittest.TestCase):
    def test_baseline_copy_has_isolated_git_context(self) -> None:
        try:
            import cosmic_ray  # noqa: F401
        except ImportError:
            self.skipTest("requires the mutation dependency group")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "tracked.txt").write_text("original\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
            head = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
            ).strip()
            (root / "untracked.txt").write_text("included\n")

            def baseline(copy: Path, module: str | None = None) -> str:
                code = (
                    "import subprocess; from pathlib import Path; "
                    f"assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == {head!r}; "
                    "assert subprocess.check_output(['git', 'archive', 'HEAD']); "
                    "subprocess.run(['git', 'diff', '--quiet', 'HEAD'], check=True); "
                    "assert Path('untracked.txt').read_text() == 'included\\n'; "
                    "Path('tracked.txt').write_text('mutated\\n'); "
                    "assert 'tracked.txt' in subprocess.check_output(['git', 'diff', '--name-only'], text=True)"
                )
                return shlex.join(["env", "-C", str(copy), sys.executable, "-c", code])

            output = io.StringIO()
            with (
                mock.patch.object(mutation.Path, "cwd", return_value=root),
                mock.patch.object(mutation, "changed_lines", return_value={"factory/gate.py": {1}}),
                mock.patch.object(mutation, "sample", return_value=([], 0)),
                mock.patch.object(mutation, "test_command", side_effect=baseline),
                contextlib.redirect_stdout(output),
            ):
                status = mutation.main([])
            self.assertEqual(status, 0, output.getvalue())
            self.assertEqual((root / "tracked.txt").read_text(), "original\n")
            self.assertEqual(subprocess.check_output(
                ["git", "-C", str(root), "status", "--porcelain"], text=True,
            ), "?? untracked.txt\n")


if __name__ == "__main__":
    unittest.main()
