"""#638: exercise the encoding fixtures on filesystems that reject byte names."""
from __future__ import annotations

import errno
import os
import unittest
from unittest import mock

from tests import test_gate, test_subprocess_encoding


class APFSEncodingTest(unittest.TestCase):
    def run_on_apfs(self, case: unittest.TestCase) -> None:
        real_open = os.open

        def apfs_open(path, *args, **kwargs):
            # APFS accepts UTF-8 only; model that boundary on Linux too.
            if isinstance(path, (str, bytes, os.PathLike)):
                try:
                    os.fsencode(path).decode("utf-8")
                except UnicodeDecodeError:
                    raise OSError(errno.EILSEQ, "Illegal byte sequence", path) from None
            return real_open(path, *args, **kwargs)

        with mock.patch("sys.platform", "darwin"), mock.patch("os.open", apfs_open):
            case.debug()

    def test_leak_scan_fixture_on_apfs(self) -> None:
        self.run_on_apfs(test_gate.GateOutputEncodingTest(
            "test_leak_scan_finds_pattern_beside_non_utf8_filename"
        ))

    def test_activity_fixture_on_apfs(self) -> None:
        self.run_on_apfs(test_subprocess_encoding.DispatchOutputEncodingTest(
            "test_activity_replaces_non_utf8_git_output"
        ))
