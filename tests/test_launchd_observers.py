"""Read-only launchd observers, exercised on every host with recorded runners."""
from __future__ import annotations

import fcntl
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory import config, onboard, runtime_local, watch


class LaunchdObserversTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        self.cfg.install = {"every": "10min", "dashboard": True, "host": "127.0.0.1", "env": {}}
        self.calls = []
        self.outputs = {}
        self.popen = subprocess.Popen
        self.enterContext(patch.object(sys, "platform", "darwin"))
        self.enterContext(patch.object(subprocess, "Popen", side_effect=self.runner))

    def job(self, label=None, *, state="not running", exit_code=0, pid=None, interval=600):
        label = label or self.cfg.unit
        text = (f"gui/{os.getuid()}/{label} = {{\n\tactive count = 0\n\tstate = {state}\n"
                f"\tlast exit code = {exit_code}\n\trun interval = {interval} seconds\n")
        if pid is not None:
            text += f"\tpid = {pid}\n"
        text += f"\tstderr path = {self.root / (label + '.err')}\n"
        # Nested process/environment properties must not replace the job's state/PID.
        text += "\tenvironment = {\n\t\tstate = running\n\t\tpid = 99999\n\t}\n}\n"
        self.outputs[label] = text
        return text

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] != ["launchctl", "print"]:
            raise FileNotFoundError(argv[0])
        label = argv[-1].rsplit("/", 1)[-1]
        text = self.outputs.get(label)
        return self.popen([sys.executable, "-c", f"import sys; sys.stdout.write({text or ''!r}); sys.exit({0 if text else 113})"], **kwargs)

    def test_runtime_observes_loaded_interval_agent(self):
        self.job()
        state, errors = runtime_local.dispatcher(self.cfg, [], {})
        self.assertIs(state["service_active"], False)
        self.assertIs(state["timer_active"], True)
        self.assertIsNone(state["next_at"])
        self.assertEqual(state["next_at_code"], "not_applicable")
        self.assertEqual(state["observation"], "fresh")
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [["launchctl", "print", f"gui/{os.getuid()}/{self.cfg.unit}"]])

    def test_runtime_observes_running_and_failed_agents(self):
        self.job(state="running", pid=123)
        state, errors = runtime_local.dispatcher(self.cfg, [], {})
        self.assertIs(state["service_active"], True)
        self.assertIs(state["timer_active"], True)
        self.assertEqual(errors, [])
        self.job(exit_code=1)
        state, errors = runtime_local.dispatcher(self.cfg, [], {})
        self.assertIs(state["service_active"], False)
        self.assertIn({"source": "launchctl", "scope": "service", "code": "unit_failed"}, errors)
        self.assertFalse(any(error["source"] == "systemctl" for error in errors))

    def test_runtime_counts_held_admission_locks_without_proc(self):
        self.job()
        locks = self.cfg.factory / "locks"
        locks.mkdir(parents=True)
        (locks / "8.lock").touch()
        (locks / "merge.lock").touch()
        with (locks / "7.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state, errors = runtime_local.dispatcher(self.cfg, [], {})
        self.assertEqual(state["capacity"], {"configured": self.cfg.max_active, "active": 1, "complete": True})
        self.assertEqual(errors, [])
        state, errors = runtime_local.dispatcher(self.cfg, [], {})
        self.assertEqual(state["capacity"]["active"], 0)
        self.assertEqual(errors, [])

    def test_watch_reports_failed_pass_with_stderr_and_clears_success(self):
        self.job(exit_code=1)
        self.job(self.cfg.unit + "-dashboard")
        (self.root / (self.cfg.unit + ".err")).write_text("old line\n" * 100 + "ImportError: renameat2\n")
        units = watch.failed_units(self.cfg)
        self.assertEqual([name for name, _ in units], [self.cfg.unit])
        self.assertIn("ImportError: renameat2", units[0][1])
        self.assertLessEqual(len(units[0][1].splitlines()), watch.MAX_LOG_LINES + 5)
        self.job(exit_code=0)
        self.assertEqual(watch.failed_units(self.cfg), [])
        self.assertTrue(all(argv[0] == "launchctl" for argv in self.calls))

    def test_watch_reports_failed_dashboard_even_without_log(self):
        self.job()
        self.job(self.cfg.unit + "-dashboard", exit_code=2)
        units = watch.failed_units(self.cfg)
        self.assertEqual([name for name, _ in units], [self.cfg.unit + "-dashboard"])
        self.assertIn("2", units[0][1])

    def test_doctor_accepts_launchd_dashboard_listener_and_rejects_foreign_pid(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.cfg.dashboard_port = listener.getsockname()[1]
        self.job(self.cfg.unit + "-dashboard", state="running", pid=os.getpid(), interval=0)
        port = self.cfg.dashboard_port
        lsof = ("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
                f"Python {os.getpid()} mike 3u IPv4 0x123 0t0 TCP 127.0.0.1:{port} (LISTEN)\n")
        commands = []

        def command(argv, **kwargs):
            commands.append(argv)
            if argv[0] == "lsof":
                return subprocess.CompletedProcess(argv, 0, lsof, "")
            if argv[:3] == ["gh", "repo", "view"]:
                return subprocess.CompletedProcess(argv, 0, "ADMIN\n", "")
            if argv[:3] == ["gh", "label", "list"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps(list(config.LABELS)), "")
            return subprocess.CompletedProcess(argv, 0, "active\n", "")

        with patch.object(onboard.config, "load", return_value=self.cfg), \
                patch.object(onboard.config, "host_config", return_value={}), \
                patch.object(onboard.config, "host_config_path", return_value=self.root / "host.toml"), \
                patch.object(onboard, "sh", side_effect=command), \
                patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                patch.object(onboard.urllib.request, "urlopen", return_value=MagicMock()):
            output = io.StringIO()
            with redirect_stdout(output):
                onboard.doctor(["--json"])
            row = next(row for row in json.loads(output.getvalue())["rows"] if row["label"] == f"dashboard port {port}")
            self.assertEqual(row["status"], "PASS", row)
            self.assertIn(self.cfg.unit + "-dashboard", row["detail"])
            lsof = lsof.replace(f"Python {os.getpid()} ", "foreign 99999 ")
            ok, detail = onboard._dashboard_port_check(self.cfg, "127.0.0.1", port)
            self.assertFalse(ok)
            self.assertIn("foreign", detail)
        self.assertIn(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], commands)


if __name__ == "__main__":
    unittest.main()
