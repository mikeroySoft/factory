from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class FullSuiteTempdirHygieneTest(unittest.TestCase):
    # Re-runs the whole suite (~10 min), so it lives outside the attempt-tier `test` check and
    # the mutation baseline: `tests/pr_tier/` has no `__init__.py`, and unittest discovery only
    # recurses into packages. For the same reason the child `discover -s tests` below can never
    # reach this test again, so it needs no recursion guard.
    def test_full_suite_leaves_no_tempdirs_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(Path(tmp).iterdir())
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=ROOT,
                env=os.environ | {"TMPDIR": tmp},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(set(Path(tmp).iterdir()) - before, set())


if __name__ == "__main__":
    unittest.main()
