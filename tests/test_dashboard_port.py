"""Public doctor/install checks for dashboard port ownership."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, onboard  # noqa: E402


class DashboardPortTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n')
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        self.cfg.install = {"every": "10min", "dashboard": False, "host": "127.0.0.1", "env": {}}
        self.host_path = self.root / "config.toml"

    def listener(self, host: str = "127.0.0.1") -> socket.socket:
        listener = socket.socket()
        listener.bind((host, 0))
        listener.listen()
        self.addCleanup(listener.close)
        return listener

    @staticmethod
    def ss_line(host: str, port: int, name: str | None = None, pid: int | None = None) -> str:
        process = "" if name is None else f' users:(("{name}",pid={pid},fd=3))'
        return (
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            f"LISTEN 0 5 {host}:{port} 0.0.0.0:*{process}\n"
        )

    def doctor(self, ss: subprocess.CompletedProcess, main_pid: subprocess.CompletedProcess | None = None,
               *, text: bool = False) -> tuple[int, str, list[list[str]]]:
        calls: list[list[str]] = []

        def command(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
            calls.append(cmd)
            if cmd[0] == "ss":
                self.assertEqual(cmd, ["ss", "-ltnp", f"sport = :{self.cfg.dashboard_port}"])
                return ss
            if cmd[:4] == ["systemctl", "--user", "show", "-p"]:
                self.assertEqual(
                    cmd,
                    ["systemctl", "--user", "show", "-p", "MainPID", "--value",
                     "factory-widgets-dashboard.service"],
                )
                return main_pid or subprocess.CompletedProcess(cmd, 0, "0\n", "")
            if cmd[:3] == ["gh", "repo", "view"]:
                return subprocess.CompletedProcess(cmd, 0, "ADMIN\n", "")
            if cmd[:3] == ["gh", "label", "list"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(list(config.LABELS)), "")
            if cmd[:3] == ["systemctl", "--user", "is-active"]:
                return subprocess.CompletedProcess(cmd, 0, "active\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        output = io.StringIO()
        with patch.object(onboard.config, "load", return_value=self.cfg), \
                patch.object(onboard.config, "host_config", return_value={}), \
                patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                patch.object(onboard, "sh", side_effect=command), \
                patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                patch.object(onboard.urllib.request, "urlopen", return_value=MagicMock()), \
                redirect_stdout(output):
            code = onboard.doctor([] if text else ["--json"])
        return code, output.getvalue(), calls

    def install(self, argv: list[str], ss: subprocess.CompletedProcess) -> tuple[int, str, str, list[list[str]], Path]:
        calls: list[list[str]] = []
        units = self.root / "units"

        def command(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
            calls.append(cmd)
            if cmd[0] == "ss":
                self.assertEqual(cmd, ["ss", "-ltnp", f"sport = :{self.cfg.dashboard_port}"])
                return ss
            if cmd[:4] == ["systemctl", "--user", "show", "-p"]:
                self.assertEqual(
                    cmd,
                    ["systemctl", "--user", "show", "-p", "MainPID", "--value",
                     "factory-widgets-dashboard.service"],
                )
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
            if cmd[0] == "loginctl":
                return subprocess.CompletedProcess(cmd, 0, "yes\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(onboard.config, "load", return_value=self.cfg), \
                patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                patch.object(onboard, "unit_dir", return_value=units), \
                patch.object(onboard, "sh", side_effect=command), \
                patch.object(onboard.shutil, "which", return_value="/bin/systemctl"), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = onboard.install(argv)
        return code, stdout.getvalue(), stderr.getvalue(), calls, units

    def test_doctor_json_passes_a_free_port(self) -> None:
        probe = self.listener()
        self.cfg.dashboard_port = probe.getsockname()[1]
        probe.close()
        code, output, _ = self.doctor(subprocess.CompletedProcess([], 0, "", ""))
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (0, "PASS"))

    def test_doctor_text_fails_with_the_foreign_holder_name(self) -> None:
        listener = self.listener()
        self.cfg.dashboard_port = listener.getsockname()[1]
        ss = subprocess.CompletedProcess([], 0, self.ss_line("127.0.0.1", self.cfg.dashboard_port, "rocm-dashboard", 4321), "")
        code, output, _ = self.doctor(ss, text=True)
        self.assertEqual(code, 1)
        self.assertIn(f"FAIL  dashboard port {self.cfg.dashboard_port}", output)
        self.assertIn("rocm-dashboard", output)

    def test_doctor_passes_its_own_dashboard_service(self) -> None:
        listener = self.listener()
        self.cfg.dashboard_port = listener.getsockname()[1]
        ss = subprocess.CompletedProcess([], 0, self.ss_line("127.0.0.1", self.cfg.dashboard_port, "python", os.getpid()), "")
        main_pid = subprocess.CompletedProcess([], 0, f"{os.getpid()}\n", "")
        code, output, _ = self.doctor(ss, main_pid)
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (0, "PASS"))
        self.assertIn("factory-widgets-dashboard.service", row["detail"])

    def test_doctor_fails_closed_when_dashboard_pid_cannot_be_read(self) -> None:
        listener = self.listener()
        self.cfg.dashboard_port = listener.getsockname()[1]
        ss = subprocess.CompletedProcess(
            [], 0, self.ss_line("127.0.0.1", self.cfg.dashboard_port, "python", os.getpid()), ""
        )
        main_pid = subprocess.CompletedProcess([], 1, "", "user manager unavailable")
        code, output, _ = self.doctor(ss, main_pid)
        row = next(
            row for row in json.loads(output)["rows"]
            if row["label"] == f"dashboard port {self.cfg.dashboard_port}"
        )
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("python", row["detail"])
        self.assertIn("could not verify", row["detail"])

    def test_doctor_fails_closed_when_listener_pid_is_invisible(self) -> None:
        listener = self.listener()
        self.cfg.dashboard_port = listener.getsockname()[1]
        ss = subprocess.CompletedProcess([], 0, self.ss_line("127.0.0.1", self.cfg.dashboard_port), "")
        code, output, _ = self.doctor(ss)
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("unknown process", row["detail"])

    def test_doctor_fails_closed_when_ss_fails(self) -> None:
        probe = self.listener()
        self.cfg.dashboard_port = probe.getsockname()[1]
        probe.close()
        ss = subprocess.CompletedProcess([], 1, "", "permission denied")
        code, output, _ = self.doctor(ss)
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("ss", row["detail"])

    def test_doctor_ignores_a_listener_on_an_unrelated_address(self) -> None:
        listener = self.listener("127.0.0.2")
        self.cfg.dashboard_port = listener.getsockname()[1]
        ss = subprocess.CompletedProcess([], 0, self.ss_line("127.0.0.2", self.cfg.dashboard_port, "foreign", 4321), "")
        code, output, _ = self.doctor(ss)
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (0, "PASS"))

    def test_doctor_ignores_other_address_when_own_service_holds_target(self) -> None:
        listener = self.listener()
        self.cfg.dashboard_port = listener.getsockname()[1]
        output = self.ss_line("127.0.0.1", self.cfg.dashboard_port, "python", os.getpid())
        output += self.ss_line("127.0.0.2", self.cfg.dashboard_port, "foreign", 4321)
        code, output, _ = self.doctor(
            subprocess.CompletedProcess([], 0, output, ""),
            subprocess.CompletedProcess([], 0, f"{os.getpid()}\n", ""),
        )
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (0, "PASS"))

    def test_doctor_detects_dual_stack_wildcard_holder(self) -> None:
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        listener.bind(("::", 0))
        listener.listen()
        self.cfg.dashboard_port = listener.getsockname()[1]
        ss = subprocess.CompletedProcess([], 0, self.ss_line("[::]", self.cfg.dashboard_port, "foreign", 4321), "")
        code, output, _ = self.doctor(ss)
        row = next(row for row in json.loads(output)["rows"] if row["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("foreign", row["detail"])

    def test_install_refuses_foreign_holder_before_writing_or_mutating_systemd(self) -> None:
        listener = self.listener("127.0.0.2")
        self.cfg.dashboard_port = listener.getsockname()[1]
        units = self.root / "units"
        units.mkdir()
        sentinel = units / "factory-widgets.service"
        sentinel.write_text("unchanged\n")
        before = {path.name: path.read_text() for path in units.iterdir()}
        ss = subprocess.CompletedProcess([], 0, self.ss_line("127.0.0.2", self.cfg.dashboard_port, "foreign-server", 4321), "")
        code, _, stderr, calls, _ = self.install(["--dashboard", "--host", "127.0.0.2"], ss)
        self.assertEqual(code, 1)
        self.assertEqual(
            stderr,
            f"factory dashboard: port {self.cfg.dashboard_port} on 127.0.0.2 is in use by foreign-server; "
            f'set [repo."acme/widgets".dashboard] port in {self.host_path} '
            "(each factory needs its own port)\n",
        )
        self.assertEqual({path.name: path.read_text() for path in units.iterdir()}, before)
        self.assertFalse(any(cmd[0] == "systemctl" and "show" not in cmd for cmd in calls))

    def test_install_writes_and_starts_dashboard_when_port_is_free(self) -> None:
        probe = self.listener()
        self.cfg.dashboard_port = probe.getsockname()[1]
        probe.close()
        code, _, stderr, calls, units = self.install(
            ["--dashboard"], subprocess.CompletedProcess([], 0, "", "")
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue((units / "factory-widgets-dashboard.service").exists())
        self.assertIn(
            ["systemctl", "--user", "enable", "--now", "factory-widgets-dashboard.service"], calls
        )

    def test_install_skips_preflight_for_print_and_no_dashboard(self) -> None:
        failure = subprocess.CompletedProcess([], 1, "", "must not run")
        code, output, stderr, calls, _ = self.install(["--print", "--dashboard"], failure)
        self.assertEqual((code, stderr, calls), (0, "", []))
        self.assertIn("factory-widgets-dashboard.service", output)

        code, _, stderr, calls, units = self.install(["--no-dashboard"], failure)
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse(any(cmd[0] == "ss" or "show" in cmd for cmd in calls))
        self.assertFalse((units / "factory-widgets-dashboard.service").exists())


if __name__ == "__main__":
    unittest.main()
