from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import dispatch, lifecycle

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

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; from factory import dispatch; import sys; "
                        "root = Path(sys.argv[1]); "
                        "print(repr(dispatch._activity(root, root / 'worker.log', root / 'handoff.md')[2]))"
                    ),
                    str(root),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("\ufffd-status", result.stdout)


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
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from factory import gate; print(repr(gate.toolchain()))",
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PATH": str(bindir)},
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                repr({"rocm": "\ufffd version", "kernel": "\ufffd version", "amdgpu": "\ufffd version"}) + "\n",
            )


if __name__ == "__main__":
    unittest.main()
