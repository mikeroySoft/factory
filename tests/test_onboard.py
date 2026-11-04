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

    def test_install_fresh_timer_enables_and_starts(self) -> None:
        self.install()
        self.assertEqual(self.states[self.timer], ("enabled", "active"))
        self.assertIn(["systemctl", "--user", "enable", "--now", self.timer], self.calls)
        self.assertNotIn(["systemctl", "--user", "restart", self.timer], self.calls)

    def test_install_active_timer_restarts_only_when_changed(self) -> None:
        self.existing(self.timer, "enabled", "active")
        self.install()
        self.assertEqual(self.states[self.timer], ("enabled", "active"))
        self.assertIn(["systemctl", "--user", "restart", self.timer], self.calls)
        self.calls.clear()
        self.install()
        self.assertEqual(self.states[self.timer], ("enabled", "active"))
        self.assertIn(["systemctl", "--user", "enable", "--now", self.timer], self.calls)
        self.assertNotIn(["systemctl", "--user", "restart", self.timer], self.calls)

    def test_install_preserves_independent_stop_and_disable_states(self) -> None:
        for state in (("enabled", "inactive"), ("disabled", "active"), ("masked", "inactive")):
            with self.subTest(state=state):
                self.existing(self.timer, *state)
                self.calls.clear()
                self.install()
                self.assertEqual(self.states[self.timer], state)
                self.assertNotIn(["systemctl", "--user", "enable", "--now", self.timer], self.calls)
                self.assertNotIn(["systemctl", "--user", "restart", self.timer], self.calls)

    def test_install_optional_units_preserve_pauses_and_enable_explicitly(self) -> None:
        self.cfg.triage_service = "model.service"
        self.cfg.eval_schedule = True
        activation = [
            self.timer, f"{self.cfg.unit}-triage-probe.timer", f"{self.cfg.unit}-eval.timer",
            f"{self.cfg.unit}-dashboard.service",
        ]
        for name in activation:
            self.existing(name, "disabled", "inactive")
        with patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")):
            output = self.install("--dashboard")
            for name in activation:
                self.assertEqual(self.states[name], ("disabled", "inactive"))
                self.assertIn(f"left paused {name}", output)
                self.assertNotIn(["systemctl", "--user", "enable", "--now", name], self.calls)
                self.assertNotIn(["systemctl", "--user", "restart", name], self.calls)
            self.calls.clear()
            self.install("--dashboard", "--enable")
        for name in activation:
            self.assertEqual(self.states[name], ("enabled", "active"))
            self.assertIn(["systemctl", "--user", "enable", "--now", name], self.calls)

    def test_install_fresh_optional_units_activate(self) -> None:
        self.cfg.triage_service = "model.service"
        self.cfg.eval_schedule = True
        with patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")):
            self.install("--dashboard")
        for name in (
            self.timer, f"{self.cfg.unit}-triage-probe.timer", f"{self.cfg.unit}-eval.timer",
            f"{self.cfg.unit}-dashboard.service",
        ):
            self.assertEqual(self.states[name], ("enabled", "active"))
            self.assertIn(["systemctl", "--user", "enable", "--now", name], self.calls)

    def test_install_snapshots_all_units_before_writing(self) -> None:
        wanted = onboard.units(self.cfg, "10min", "127.0.0.1")
        wanted.pop(f"{self.cfg.unit}-dashboard.service")
        for name in wanted:
            self.existing(name, "enabled", "active")
        write = Path.write_text

        def checked_write(path: Path, body: str) -> int:
            for name in wanted:
                self.assertIn(["systemctl", "--user", "is-enabled", name], self.calls)
                self.assertIn(["systemctl", "--user", "is-active", name], self.calls)
            return write(path, body)

        with patch.object(Path, "write_text", checked_write):
            self.install()

    def test_install_print_does_not_query_or_activate_units(self) -> None:
        self.existing(self.timer, "disabled", "inactive")
        self.install("--print", "--enable")
        self.assertEqual(self.states[self.timer], ("disabled", "inactive"))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
