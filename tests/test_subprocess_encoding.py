from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, gate, lifecycle

INVALID_OUTPUT = [
    sys.executable,
    "-c",
    "import os; os.write(1, bytes([255]) + b' stdout\\n'); os.write(2, bytes([254]) + b' stderr\\n')",
]


class DispatchOutputEncodingTest(unittest.TestCase):
    def assert_replaced_output(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "\ufffd stdout\n")
        self.assertEqual(result.stderr, "\ufffd stderr\n")

    def test_run_replaces_non_utf8_output(self) -> None:
        self.assert_replaced_output(dispatch.run(INVALID_OUTPUT))

    def test_lifecycle_run_replaces_non_utf8_output(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            lifecycle.scope(Path(directory) / "events.jsonl", "dispatch"),
        ):
            self.assert_replaced_output(dispatch.run(INVALID_OUTPUT))

    def test_activity_replaces_non_utf8_git_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "core.quotePath", "false"], check=True)
            (root / "tracked").write_text("tracked\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "init"],
                check=True,
            )
            descriptor = os.open(os.path.join(os.fsencode(root), b"\xff-status"), os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(descriptor)

            activity = dispatch._activity(root, root / "worker.log", root / "handoff.md")

            self.assertIn("\ufffd-status", activity[2])


class GateToolchainEncodingTest(unittest.TestCase):
    def test_toolchain_replaces_non_utf8_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bindir = Path(directory)
            for name in ("hipconfig", "uname", "modinfo"):
                command = bindir / name
                command.write_text(
                    f"#!{sys.executable}\nimport os\nos.write(1, bytes([255]) + b' version\\n')\n"
                )
                command.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
                values = gate.toolchain()

            self.assertEqual(values, {"rocm": "\ufffd version", "kernel": "\ufffd version", "amdgpu": "\ufffd version"})


if __name__ == "__main__":
    unittest.main()
