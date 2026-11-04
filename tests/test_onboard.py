"""Install preserves operator-owned systemd activation state."""
from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import config, onboard


class InstallStateTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.udir = self.root / "units"
        self.udir.mkdir()
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        self.cfg.install = {"every": "10min", "dashboard": False, "host": "127.0.0.1", "env": {}}
        self.timer = f"{self.cfg.unit}.timer"
        self.states: dict[str, tuple[str, str]] = {}
        self.calls: list[list[str]] = []

    def existing(self, name: str, enabled: str, active: str) -> None:
        (self.udir / name).write_text("old unit contents\n")
        self.states[name] = (enabled, active)

    def command(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        if cmd[0] == "loginctl":
            return subprocess.CompletedProcess(cmd, 0, "yes\n", "")
        self.assertEqual(cmd[:2], ["systemctl", "--user"])
        action = cmd[2]
        name = cmd[-1]
        enabled, active = self.states.get(name, ("not-found", "inactive"))
        if action == "is-enabled":
            return subprocess.CompletedProcess(cmd, 0 if enabled == "enabled" else 1, enabled + "\n", "")
        if action == "is-active":
            return subprocess.CompletedProcess(cmd, 0 if active == "active" else 3, active + "\n", "")
        if action == "enable":
            self.assertEqual(cmd[3], "--now")
            self.states[name] = ("enabled", "active")
        elif action == "restart":
            self.states[name] = (enabled, "active")
        else:
            self.assertEqual(action, "daemon-reload")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def install(self, *args: str) -> str:
        output = io.StringIO()
        with patch.object(onboard.config, "load", return_value=self.cfg), \
                patch.object(onboard, "unit_dir", return_value=self.udir), \
                patch.object(onboard, "sh", side_effect=self.command), \
                patch.object(onboard.shutil, "which", return_value="/bin/systemctl"), \
                redirect_stdout(output):
            self.assertEqual(onboard.install(list(args)), 0)
        return output.getvalue()

    def test_install_preserves_stopped_disabled_timer(self) -> None:
        self.existing(self.timer, "disabled", "inactive")
        output = self.install()
        self.assertEqual(self.states[self.timer], ("disabled", "inactive"))
        self.assertNotIn(["systemctl", "--user", "enable", "--now", self.timer], self.calls)
        self.assertNotIn(["systemctl", "--user", "restart", self.timer], self.calls)
        self.assertIn(["systemctl", "--user", "daemon-reload"], self.calls)
        self.assertIn("[Timer]", (self.udir / self.timer).read_text())
        self.assertIn(f"left paused {self.timer}", output)
        self.assertIn(f"systemctl --user enable --now {self.timer}", output)


if __name__ == "__main__":
    unittest.main()
