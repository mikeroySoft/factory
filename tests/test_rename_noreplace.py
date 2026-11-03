"""No-replace artifact publication across libc implementations."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class RenameNoReplaceTest(unittest.TestCase):
    def test_decisions_imports_and_refuses_overwrite_when_libc_lacks_renameat2(self):
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent("""
                import ctypes
                import json
                import os
                import tempfile
                from pathlib import Path

                ctypes.CDLL = lambda *args, **kwargs: object()
                from factory import decisions

                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        decisions._store_artifact(directory, "receipt.json", {"value": "original"})
                        assert json.loads((root / "receipt.json").read_text()) == {"value": "original"}
                        assert not list(root.glob(".tmp-*"))
                        try:
                            decisions._store_artifact(directory, "receipt.json", {"value": "replacement"})
                        except decisions.DecisionError as exc:
                            assert exc.code == "conflict", exc.code
                        else:
                            raise AssertionError("existing artifact was overwritten")
                        assert json.loads((root / "receipt.json").read_text()) == {"value": "original"}
                        assert not list(root.glob(".tmp-*"))
                    finally:
                        os.close(directory)
            """)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
