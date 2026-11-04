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

    def test_import_only_leaves_no_tempdirs_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ | {"TMPDIR": tmp}
            proc = subprocess.run(
                [sys.executable, "-c", "import test_factory"],
                cwd=ROOT / "tests",
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_killed_gate_fixture_leaves_no_tempdirs_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "unittest",
                    (
                        "tests.test_gate_resources.GateResourcesTest."
                        "test_uninstrumented_holder_and_killed_requester_remain_distinct"
                    ),
                ],
                cwd=ROOT,
                env=os.environ | {"TMPDIR": tmp},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    @unittest.skipIf(os.environ.get("FACTORY_TEMPDIR_HYGIENE_CHILD") == "1", "recursive suite")
    def test_full_suite_leaves_no_tempdirs_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(Path(tmp).iterdir())
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=ROOT,
                env=os.environ | {"TMPDIR": tmp, "FACTORY_TEMPDIR_HYGIENE_CHILD": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(set(Path(tmp).iterdir()) - before, set())



if __name__ == "__main__":
    unittest.main()
