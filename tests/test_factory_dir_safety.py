"""An unsafe .factory directory yields an actionable dashboard error and a doctor FAIL."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, dashboard, onboard


class FactoryDirSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n')
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        self.cfg.install = {
            "every": "10min",
            "dashboard": False,
            "host": "127.0.0.1",
            "env": {},
        }
        self.factory = self.cfg.factory
        self.factory.mkdir(exist_ok=True)

    def dashboard_message(self) -> str:
        err = io.StringIO()
        with (
            patch.object(dashboard.config, "load", return_value=self.cfg),
            patch.object(dashboard, "CodebaseMonitor", MagicMock()),
            redirect_stderr(err),
            redirect_stdout(io.StringIO()),
        ):
            code = dashboard.main(["--no-open"])
        self.assertEqual(code, 1)
        return err.getvalue()

    def doctor_row(self) -> dict:
        def command(cmd, cwd=None):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        out = io.StringIO()
        with (
            patch.object(onboard.config, "load", return_value=self.cfg),
            patch.object(onboard.config, "host_config", return_value={}),
            patch.object(
                onboard.config,
                "host_config_path",
                return_value=self.root / "c.toml",
            ),
            patch.object(onboard, "sh", side_effect=command),
            patch.object(onboard.shutil, "which", return_value="/bin/tool"),
            patch.object(onboard.urllib.request, "urlopen", return_value=MagicMock()),
            redirect_stdout(out),
        ):
            onboard.doctor(["--json"])
        rows = json.loads(out.getvalue())["rows"]
        return next(row for row in rows if row["label"].endswith("directory safe"))

    def test_group_writable_directory(self) -> None:
        os.chmod(self.factory, 0o775)
        message = self.dashboard_message()
        self.assertIn("group/other-writable", message)
        self.assertIn(f"chmod go-w {self.factory}", message)
        row = self.doctor_row()
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("group/other-writable", row["detail"])
        self.assertIn(f"chmod go-w {self.factory}", row["detail"])

    def test_private_directory_passes(self) -> None:
        os.chmod(self.factory, 0o700)
        self.assertEqual(self.doctor_row()["status"], "PASS")

    def test_loose_key_file_fails(self) -> None:
        os.chmod(self.factory, 0o700)
        key = self.factory / "workspace-access.key"
        key.write_text("x")
        os.chmod(key, 0o644)
        row = self.doctor_row()
        self.assertEqual(row["status"], "FAIL")
        self.assertIn(f"chmod 600 {key}", row["detail"])

    def test_dashboard_and_doctor_agree_on_every_unsafe_case(self) -> None:
        os.chmod(self.factory, 0o700)
        key = self.factory / "workspace-access.key"
        key.write_text("x" * 43)

        def read_only() -> str:
            os.chmod(key, 0o400)
            return f"chmod 600 {key}"

        def hard_linked() -> str:
            os.chmod(key, 0o600)
            os.link(key, self.root / "extra-link")
            return "extra hard links"

        for case in (read_only, hard_linked):
            with self.subTest(case=case.__name__):
                expected = case()
                self.assertIn(expected, self.dashboard_message())
                row = self.doctor_row()
                self.assertEqual(row["status"], "FAIL")
                self.assertIn(expected, row["detail"])

    def test_symlinked_directory_fails_in_both(self) -> None:
        real = self.root / "elsewhere"
        real.mkdir(mode=0o700)
        os.rmdir(self.factory)
        os.symlink(real, self.factory)
        self.assertIn("not a real directory", self.dashboard_message())
        row = self.doctor_row()
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("not a real directory", row["detail"])


if __name__ == "__main__":
    unittest.main()
