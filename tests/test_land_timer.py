"""Independent short-cadence landing systemd units and installation state."""
from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import config, onboard


class LandUnitTest(unittest.TestCase):
    def test_land_service_is_independent_dispatch_with_dispatch_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", signoff=False)
            cfg.install = {
                "env": {"UV_EXCLUDE_NEWER": "7 days"},
                "dispatch_env": {"GH_CONFIG_DIR": "/machine/gh"},
            }
            exe = f"{onboard.systemd_argument(str(onboard.service_python(cfg)))} -P -m factory"
            with patch.dict(onboard.os.environ, {"PATH": "/usr/bin"}):
                rendered = onboard.units(cfg, "10min", "127.0.0.1")

        self.assertEqual(rendered[f"{cfg.unit}-land.service"], (
            "[Unit]\nDescription=Land eligible factory PRs for acme/widgets (one pass)\n\n"
            f"[Service]\nType=oneshot\nWorkingDirectory={cfg.root}\n"
            "Environment=PATH=/usr/bin\n"
            'Environment="UV_EXCLUDE_NEWER=7 days"\n'
            "Environment=GH_CONFIG_DIR=/machine/gh\n"
            f"ExecStart={exe} dispatch --land-only\n"
        ))
        self.assertEqual(rendered[f"{cfg.unit}-land.timer"], (
            "[Unit]\nDescription=Land eligible factory PRs for acme/widgets every 2 minutes\n\n"
            "[Timer]\nOnBootSec=2min\nOnUnitActiveSec=2min\nRandomizedDelaySec=30\n\n"
            "[Install]\nWantedBy=timers.target\n"
        ))
        self.assertIn(f"ExecStart={exe} dispatch\n", rendered[f"{cfg.unit}.service"])
        self.assertNotIn("--land-only", rendered[f"{cfg.unit}.service"])


class LandInstallStateTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.udir = self.root / "units"
        self.udir.mkdir()
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        self.cfg.install = {
            "every": "10min", "dashboard": False, "host": "127.0.0.1", "env": {},
        }
        self.main_timer = f"{self.cfg.unit}.timer"
        self.land_timer = f"{self.cfg.unit}-land.timer"
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
        action, name = cmd[2], cmd[-1]
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

    def test_fresh_install_activates_both_timers(self) -> None:
        self.install()
        for timer in (self.main_timer, self.land_timer):
            self.assertEqual(self.states[timer], ("enabled", "active"))
            self.assertIn(["systemctl", "--user", "enable", "--now", timer], self.calls)

    def test_install_preserves_land_timer_pause_independently(self) -> None:
        self.existing(self.main_timer, "enabled", "active")
        self.existing(self.land_timer, "disabled", "inactive")
        output = self.install()

        self.assertEqual(self.states[self.main_timer], ("enabled", "active"))
        self.assertEqual(self.states[self.land_timer], ("disabled", "inactive"))
        self.assertIn(f"left paused {self.land_timer}", output)
        self.assertNotIn(["systemctl", "--user", "enable", "--now", self.land_timer], self.calls)

        self.calls.clear()
        self.install("--enable")
        self.assertEqual(self.states[self.land_timer], ("enabled", "active"))
        self.assertIn(["systemctl", "--user", "enable", "--now", self.land_timer], self.calls)


if __name__ == "__main__":
    unittest.main()
