from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import dispatch, lifecycle, platform_identity

INVALID_OUTPUT = [
    sys.executable,
    "-c",
    "import os; os.write(1, bytes([255]) + b' stdout\\n'); os.write(2, bytes([254]) + b' stderr\\n')",
]

ACTIVITY = (
    "from pathlib import Path; from factory import dispatch; import sys; "
    "root = Path(sys.argv[1]); "
    "print(repr(dispatch._activity(root, root / 'worker.log', root / 'handoff.md')[2]))"
)


def stage_name(root: Path, name: bytes, content: bytes) -> None:
    """Put ``name`` in git's index without asking the filesystem to create it."""
    blob = subprocess.run(
        ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
        input=content, capture_output=True, check=True,
    ).stdout.strip()
    subprocess.run(
        [b"git", b"-C", os.fsencode(root), b"update-index", b"--add", b"--cacheinfo",
         b"100644," + blob + b"," + name],
        check=True,
    )


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
            name = os.path.join(os.fsencode(root), b"\xff-status")
            if platform_identity.darwin():
                # Darwin filesystems positively refuse a non-UTF-8 name; git still carries it.
                with self.assertRaises(OSError) as refused:
                    os.open(name, os.O_WRONLY | os.O_CREAT, 0o600)
                self.assertEqual(refused.exception.errno, errno.EILSEQ)
                self.assertFalse(os.path.lexists(name))
                stage_name(root, b"\xff-status", b"status\n")
            else:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT, 0o600)
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

    def test_activity_replaces_non_utf8_path_from_git_index(self) -> None:
        """Portable: the invalid byte reaches git output from its index, not the filesystem."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "core.quotePath", "false"], check=True)
            stage_name(root, b"\xff-status", b"status\n")
            self.assertEqual(os.listdir(root), [".git"])

            result = subprocess.run(
                [sys.executable, "-c", ACTIVITY, str(root)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, repr("AD \ufffd-status\n") + "\n")


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
