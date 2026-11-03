"""Exercise Darwin's no-replace publication path, including on Linux CI."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


def exercise_missing_renameat2() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        # Adapt the Darwin ABI to a real Linux syscall; macOS uses its actual libc.
        linux_rename = libc.renameat2
        linux_rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        linux_rename.restype = ctypes.c_int

        def darwin_rename(source_fd, source, target_fd, target, flags):
            assert flags == 4, "Darwin RENAME_EXCL is 4, not Linux RENAME_NOREPLACE (1)"
            return linux_rename(source_fd, source, target_fd, target, 1)
    else:
        darwin_rename = libc.renameatx_np

    class WithoutRenameat2:
        def __getattr__(self, name):
            if name == "renameat2":
                raise AttributeError("renameat2 is unavailable on macOS")
            if name == "renameatx_np":
                return darwin_rename
            return getattr(libc, name)

    with mock.patch.object(ctypes, "CDLL", return_value=WithoutRenameat2()):
        from factory import decisions

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            value = {"payload": "complete" * 8192}
            write = os.write

            def partial_write(fd, payload):
                assert not (root / "state.json").exists(), "partial state became visible"
                return write(fd, payload[:97])

            with mock.patch.object(decisions.os, "write", side_effect=partial_write):
                decisions._store_artifact(directory, "state.json", value)
            assert decisions._read_artifact(directory, "state.json") == value
            assert (root / "state.json").stat().st_nlink == 1

            try:
                decisions._store_artifact(directory, "state.json", {"clobbered": True})
            except decisions.DecisionError as exc:
                assert exc.code == "conflict", exc.code
            else:
                raise AssertionError("immutable state was overwritten")
            assert json.loads((root / "state.json").read_text()) == value

            def contender(number):
                try:
                    decisions._store_artifact(directory, "race.json", {"winner": number})
                    return number
                except decisions.DecisionError as exc:
                    assert exc.code == "conflict", exc.code
                    return None

            with ThreadPoolExecutor(max_workers=8) as pool:
                winners = [result for result in pool.map(contender, range(8)) if result is not None]
            assert len(winners) == 1, winners
            assert decisions._read_artifact(directory, "race.json") == {"winner": winners[0]}

            def interrupted_write(fd, payload):
                write(fd, payload[:1])
                raise OSError(errno.ENOSPC, "injected interrupted write")

            with mock.patch.object(decisions.os, "write", side_effect=interrupted_write):
                try:
                    decisions._store_artifact(directory, "interrupted.json", value)
                except decisions.DecisionError as exc:
                    assert exc.code == "storage_unavailable", exc.code
                else:
                    raise AssertionError("interrupted write succeeded")
            assert not (root / "interrupted.json").exists()
            assert not list(root.glob(".tmp-*")), "temporary artifacts leaked"
        finally:
            os.close(directory)
    print("missing renameat2: complete atomic publication, concurrent no-clobber, interrupted-write cleanup")


class MacosRenameTest(unittest.TestCase):
    def test_missing_renameat2_atomic_publication(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", (
                "from tests.test_macos_portability import exercise_missing_renameat2; "
                "exercise_missing_renameat2()"
            )],
            cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
