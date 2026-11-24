"""macOS may refuse group signals when only reparented zombies remain."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch


class WorktreeStopEdgesTest(unittest.TestCase):
    def test_zombie_only_group_preserves_worker_exit_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = mock.Mock(pid=410001, returncode=7)
            with mock.patch.object(dispatch.os, "killpg", side_effect=[None, PermissionError()]), \
                    mock.patch.object(dispatch.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, "410001 Z\n410002 S\n", "")), \
                    mock.patch.object(dispatch.subprocess, "Popen", return_value=proc), \
                    mock.patch.object(dispatch, "_watch", return_value=None):
                result = dispatch.run_worker(["worker"], root, root / "worker.log", root / "handoff",
                                             journal=False)
        self.assertEqual(result, (7, None))

    def test_permission_error_for_live_group_is_not_suppressed(self):
        proc = mock.Mock(pid=410001)
        with mock.patch.object(dispatch.os, "killpg", side_effect=[None, PermissionError()]), \
                mock.patch.object(dispatch.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, "410001 Z\n410001 S\n", "")), \
                self.assertRaises(PermissionError):
            dispatch._stop(proc)

    def test_failed_group_inventory_does_not_claim_success(self):
        proc = mock.Mock(pid=410001)
        with mock.patch.object(dispatch.os, "killpg", side_effect=[None, PermissionError()]), \
                mock.patch.object(dispatch.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "ps")), \
                self.assertRaises(subprocess.CalledProcessError):
            dispatch._stop(proc)
