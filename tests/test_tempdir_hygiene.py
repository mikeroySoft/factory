from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TempdirHygieneTest(unittest.TestCase):
    def test_suites_leave_no_tempdirs_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ | {"TMPDIR": tmp}
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "tests/test_o1_observation.py", "tests/test_factory.py"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
