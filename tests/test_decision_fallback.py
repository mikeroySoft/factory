"""Portable decision publication recovers only its own temporary hard links."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from factory import decisions


class DecisionFallbackTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.directory)
        rename, flag = decisions._rename_noreplace(object())
        patch = mock.patch.multiple(decisions, _RENAME_NOREPLACE=rename, _NOREPLACE_FLAG=flag)
        patch.start()
        self.addCleanup(patch.stop)
        self.name = "receipt.json"
        self.value = {"status": "success"}

    def test_crashed_publication_is_recovered_on_next_read(self):
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent("""
                import ctypes
                import os
                import sys
                ctypes.CDLL = lambda *args, **kwargs: object()
                from factory import decisions
                directory = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
                link = os.link
                def crash_after_link(*args, **kwargs):
                    link(*args, **kwargs)
                    os._exit(73)
                os.link = crash_after_link
                decisions._store_artifact(directory, "receipt.json", {"status": "success"})
            """), str(self.root)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual((self.root / self.name).stat().st_nlink, 2)
        self.assertEqual(decisions._read_artifact(self.directory, self.name), self.value)
        self.assertEqual(list(self.root.glob(".tmp-*")), [])
        self.assertEqual((self.root / self.name).stat().st_nlink, 1)

    def test_failed_unlink_reports_publication_and_retry_succeeds(self):
        with mock.patch.object(decisions.os, "unlink", side_effect=PermissionError("unlink denied")):
            decisions._store_artifact(self.directory, self.name, self.value)
        self.assertEqual((self.root / self.name).stat().st_nlink, 2)
        decisions._store_artifact(self.directory, self.name, self.value)
        self.assertEqual(decisions._read_artifact(self.directory, self.name), self.value)
        self.assertEqual(list(self.root.glob(".tmp-*")), [])
        with self.assertRaises(decisions.DecisionError) as caught:
            decisions._store_artifact(self.directory, self.name, {"status": "replacement"})
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(decisions._read_artifact(self.directory, self.name), self.value)

    def test_reader_can_recover_while_publisher_is_between_link_and_unlink(self):
        link = os.link

        def read_after_link(*args, **kwargs):
            link(*args, **kwargs)
            self.assertEqual(decisions._read_artifact(self.directory, self.name), self.value)

        with mock.patch.object(decisions.os, "link", side_effect=read_after_link):
            decisions._store_artifact(self.directory, self.name, self.value)
        self.assertEqual(list(self.root.glob(".tmp-*")), [])
        decisions._store_artifact(self.directory, self.name, self.value)
        self.assertEqual(decisions._read_artifact(self.directory, self.name), self.value)

    def test_unrelated_hard_links_are_refused_without_removing_them(self):
        decisions._store_artifact(self.directory, self.name, self.value)
        artifact = self.root / self.name
        for sibling in ("unrelated", ".tmp-other.json-" + "a" * 32, ".tmp-receipt.json-invalid"):
            with self.subTest(sibling=sibling):
                extra = self.root / sibling
                os.link(artifact, extra)
                try:
                    with self.assertRaises(decisions.DecisionError) as caught:
                        decisions._read_artifact(self.directory, self.name)
                    self.assertEqual(caught.exception.code, "unsafe_storage")
                    self.assertTrue(extra.exists())
                finally:
                    extra.unlink()
        extra = self.root / "unrelated"
        os.link(artifact, extra)
        temporary = self.root / (".tmp-receipt.json-" + "a" * 32)
        temporary.symlink_to(artifact.name)
        with self.assertRaises(decisions.DecisionError) as caught:
            decisions._read_artifact(self.directory, self.name)
        self.assertEqual(caught.exception.code, "unsafe_storage")
        self.assertTrue(temporary.is_symlink())
        temporary.unlink()
        os.link(artifact, temporary)
        with self.assertRaises(decisions.DecisionError) as caught:
            decisions._read_artifact(self.directory, self.name)
        self.assertEqual(caught.exception.code, "unsafe_storage")
        self.assertTrue(temporary.exists())
