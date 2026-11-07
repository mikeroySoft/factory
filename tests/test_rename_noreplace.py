"""No-replace artifact publication across libc implementations."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class _Rename:
    def __init__(self, flag):
        self.flag = flag

    def __call__(self, source_fd, source, target_fd, target, flags):
        try:
            if flags == self.flag:
                os.stat(target, dir_fd=target_fd)
                ctypes.set_errno(errno.EEXIST)
                return -1
        except FileNotFoundError:
            pass
        os.rename(source, target, src_dir_fd=source_fd, dst_dir_fd=target_fd)
        return 0


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

    def test_atomic_libc_rename_publishes_once_without_linking_or_overwriting(self):
        from factory import decisions

        for symbols in (
            {"renameat2": _Rename(1)},
            {"renameatx_np": _Rename(0x4)},
            {"renameat2": _Rename(1), "renameatx_np": None},
        ):
            with self.subTest(symbols=symbols), tempfile.TemporaryDirectory() as temporary:
                rename, flag = decisions._rename_noreplace(SimpleNamespace(**symbols))
                directory = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
                self.addCleanup(os.close, directory)
                with (
                    mock.patch.multiple(decisions, _RENAME_NOREPLACE=rename, _NOREPLACE_FLAG=flag),
                    mock.patch.object(decisions.os, "link", side_effect=AssertionError("not atomic rename")),
                ):
                    decisions._store_artifact(directory, "receipt.json", {"value": "original"})
                    self.assertEqual(
                        decisions._read_artifact(directory, "receipt.json"), {"value": "original"},
                    )
                    with self.assertRaises(decisions.DecisionError) as caught:
                        decisions._store_artifact(directory, "receipt.json", {"value": "replacement"})
                    self.assertEqual(caught.exception.code, "conflict")
                    self.assertEqual(
                        json.loads((Path(temporary) / "receipt.json").read_text()),
                        {"value": "original"},
                    )
                    self.assertEqual(list(Path(temporary).glob(".tmp-*")), [])

    def test_native_publication_error_preserves_errno_and_cleans_temporary(self):
        from factory import decisions

        def denied(*args):
            ctypes.set_errno(errno.EACCES)
            return -1

        with tempfile.TemporaryDirectory() as temporary:
            directory = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            self.addCleanup(os.close, directory)
            with (
                mock.patch.object(decisions, "_RENAME_NOREPLACE", side_effect=denied),
                self.assertRaises(decisions.DecisionError) as caught,
            ):
                decisions._store_artifact(directory, "receipt.json", {"value": "original"})
            self.assertEqual(caught.exception.code, "storage_unavailable")
            self.assertEqual(caught.exception.__cause__.errno, errno.EACCES)
            self.assertEqual(list(Path(temporary).iterdir()), [])
