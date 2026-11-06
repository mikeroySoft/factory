"""Darwin observers replay launchctl/lsof on every CI host; native parser proof on macOS."""
from __future__ import annotations

import fcntl
import io
import json
import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory import config, onboard, runtime_local, watch

POPEN = subprocess.Popen
RUN = subprocess.run


def printed(state="not running", exit_code="0", interval=600, pid=321, stderr=""):
    return (f"gui/{os.getuid()}/factory-widgets = {{\n"
            f"\tstate = {state}\n\tpid = {pid}\n\tlast exit code = {exit_code}\n"
            f"\trun interval = {interval} seconds\n\tstderr path = {stderr}\n"
            "\tenvironment = {\n\t\tstate = wrong\n\t}\n}\n")


class LaunchdObserversTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "widgets"
        self.root.mkdir()
        self.cfg = config.Config(self.root, "acme/widgets")
        self.calls = []

    def runner(self, outputs):
        def popen(argv, **kwargs):
            self.calls.append(argv)
            key = argv[0] if argv[0] == "lsof" else argv[-1]
            code, text = outputs.get(key, (1, ""))
            return POPEN([sys.executable, "-c",
                          "import json, sys; sys.stdout.write(json.loads(sys.argv[1])); sys.exit(int(sys.argv[2]))",
                          json.dumps(text), str(code)], **kwargs)
        return patch.object(subprocess, "Popen", side_effect=popen)

    def test_runtime_observes_launchd_instead_of_systemd(self):
        target = f"gui/{os.getuid()}/{self.cfg.unit}"
        with patch.object(sys, "platform", "darwin"), self.runner({target: (0, printed())}):
            state, errors = runtime_local.dispatcher(self.cfg, [], {})
        self.assertIs(state["service_active"], False)
        self.assertIs(state["timer_active"], True)
        self.assertIsNone(state["next_at"])
        self.assertEqual(state["next_at_code"], "not_applicable")
        self.assertEqual(state["last_exit_status"], 0)
        self.assertEqual(state["run_interval"], 600)
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [["launchctl", "print", target]])

    def test_runtime_running_and_transitional_states(self):
        target = f"gui/{os.getuid()}/{self.cfg.unit}"
        for status, expected in [("running", True), ("spawn scheduled", None), ("waiting", None)]:
            with self.subTest(status=status), patch.object(sys, "platform", "darwin"), \
                    self.runner({target: (0, printed(status, "78: EX_CONFIG"))}):
                state, errors = runtime_local.dispatcher(self.cfg, [], {})
            self.assertIs(state["service_active"], expected)
            self.assertEqual(state["last_exit_status"], 78)
            self.assertIs(state["timer_active"], True)
            self.assertEqual([e["code"] for e in errors], [] if expected is True else ["transitioning"])

    def test_capacity_reads_lsof_lock_state_without_acquiring_locks(self):
        directory = self.cfg.factory / "locks"
        directory.mkdir(parents=True)
        held, idle = directory / "1.lock", directory / "2.lock"
        held.touch()
        idle.touch()
        output = f"f3\0lW\0n{held}\0\n"
        with patch.object(sys, "platform", "darwin"), self.runner({"lsof": (0, output)}), \
                patch.object(fcntl, "flock", side_effect=AssertionError("observer took a ticket lock")):
            self.assertEqual(runtime_local._capacity(self.cfg), 1)
        self.assertEqual(self.calls[0][:3], ["lsof", "-nP", "-F0fln"])
        self.assertEqual(self.calls[0][3:], ["+d", str(directory)])

    def test_capacity_does_not_mistake_unreported_flock_for_free(self):
        directory = self.cfg.factory / "locks"
        directory.mkdir(parents=True)
        path = directory / "1.lock"
        path.touch()
        with patch.object(sys, "platform", "darwin"), \
                self.runner({"lsof": (0, f"f3\0l \0n{path}\0\n")}), \
                self.assertRaisesRegex(runtime_local._Unavailable, "lock_state_unavailable"):
            runtime_local._capacity(self.cfg)

    def test_watch_reports_exit_and_log_but_not_retained_running_failure(self):
        log = self.root / "agent.err"
        log.write_text("launch failed: import error\n")
        target = f"gui/{os.getuid()}/{self.cfg.unit}"
        dashboard = f"gui/{os.getuid()}/{self.cfg.unit}-dashboard"
        for code in ("1", "2", "78: EX_CONFIG", "0"):
            with self.subTest(code=code), patch.object(sys, "platform", "darwin"), self.runner({
                    target: (0, printed(exit_code=code, stderr=str(log))),
                    dashboard: (0, printed("running", "1"))}):
                failures = watch.failed_units(self.cfg)
            if code == "0":
                self.assertEqual(failures, [])
            else:
                self.assertEqual([name for name, _ in failures], [self.cfg.unit])
                self.assertIn(f"last exit code = {code}", failures[0][1])
                self.assertIn("launch failed: import error", failures[0][1])

    def test_doctor_accepts_dashboard_pid_from_launchctl_and_lsof(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.cfg.dashboard_port = listener.getsockname()[1]
        target = f"gui/{os.getuid()}/{self.cfg.unit}-dashboard"

        def command(argv, **kwargs):
            self.calls.append(argv)
            if argv[0] == "lsof":
                self.assertEqual(argv, ["lsof", "-nP", f"-iTCP:{self.cfg.dashboard_port}", "-sTCP:LISTEN"])
                text = ("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
                        f"Python 321 user 3u IPv4 0x123 0t0 TCP 127.0.0.1:{self.cfg.dashboard_port} (LISTEN)\n")
            elif argv[0] == "launchctl":
                self.assertEqual(argv, ["launchctl", "print", target])
                text = printed("running")
            elif argv[:3] == ["gh", "label", "list"]:
                text = json.dumps(list(config.LABELS))
            else:
                text = ""
            return subprocess.CompletedProcess(argv, 0, text, "")

        output = io.StringIO()
        with patch.object(sys, "platform", "darwin"), patch.object(config, "load", return_value=self.cfg), \
                patch.object(config, "host_config", return_value={}), \
                patch.object(config, "host_config_path", return_value=self.root / "host.toml"), \
                patch.object(onboard, "sh", side_effect=command), \
                patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                patch.object(onboard.urllib.request, "urlopen", return_value=MagicMock()), redirect_stdout(output):
            onboard.doctor(["--json"])
        row = next(r for r in json.loads(output.getvalue())["rows"]
                   if r["label"] == f"dashboard port {self.cfg.dashboard_port}")
        self.assertEqual(row["status"], "PASS", row)
        self.assertIn("factory-widgets-dashboard (pid 321)", row["detail"])
        self.assertIn(["launchctl", "print", target], self.calls)

    def test_parser_with_native_launchagent_on_macos(self):
        # Linux runs the same parser assertion on canned output; macOS supplies live output.
        text = printed("running", "78: EX_CONFIG")
        if sys.platform == "darwin":
            label = f"factory-observer-test-{uuid.uuid4().hex}"
            target = f"gui/{os.getuid()}/{label}"
            path = self.root / "agent.plist"
            path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/sh", "-c", "exit 78"],
                                             "RunAtLoad": True, "StartInterval": 600}))
            RUN(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True, capture_output=True)
            try:
                deadline = time.monotonic() + 10
                while True:
                    text = RUN(["launchctl", "print", target], check=True, capture_output=True, text=True).stdout
                    if "last exit code = 78" in text:
                        break
                    if time.monotonic() > deadline:
                        self.fail(text)
                    time.sleep(0.05)
            finally:
                RUN(["launchctl", "bootout", target], check=True, capture_output=True)
        from factory import launchd
        props = launchd.parse(text)
        self.assertEqual(launchd.number(props, "last exit code"), 78)
        self.assertEqual(launchd.number(props, "run interval"), 600)
        self.assertNotEqual(props["state"], "wrong")


if __name__ == "__main__":
    unittest.main()
