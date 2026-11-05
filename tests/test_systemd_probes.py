"""Missing systemd tools must not crash macOS observations or liveness probes."""
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_factory import factory, make_repo, stub_bin

from factory import __version__, cli, config, dashboard, dispatch, lifecycle, onboard, stats, triage


class SystemdProbesTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.dict(os.environ, {
            lifecycle.CONTEXT_ENV: "", "XDG_STATE_HOME": str(self.root),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        }))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.repo = make_repo(self.root, '[repo]\nslug = "acme/widgets"\n'
                              f'[dashboard]\nport = {port}\n'
                              '[triage]\nservice = "model.service"\n')
        self.cfg = config.Config(self.repo, "acme/widgets", signoff=False, lock=self.root / "gpu.lock")
        self.cfg.triage_service = "model.service"
        for module in (dashboard, dispatch, stats, triage):
            self.enterContext(patch.dict(module.__dict__))
        self.enterContext(patch.object(config, "load", return_value=self.cfg))
        self.enterContext(patch.object(config, "host_config", return_value={}))
        self.enterContext(patch.object(config, "engine_drift", return_value=None))
        self.enterContext(patch.object(onboard, "unit_dir", return_value=self.root / "units"))
        self.cfg.dashboard_port = port
        self.port_check = onboard._dashboard_port_check
        self.enterContext(patch.object(onboard, "_dashboard_port_check", return_value=(True, "available")))
        self.enterContext(patch.object(triage, "probe_endpoint", return_value=(True, "ready")))
        self.calls: list[list[str]] = []

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] in {"systemctl", "journalctl"}:
            if sys.platform != "linux":
                raise FileNotFoundError(2, "No such file or directory", argv[0])
            if argv[0] == "journalctl":
                messages = ["Starting Factory", "Failed with result 'exit-code'"]
                text = "\n".join(json.dumps({
                    "MESSAGE": message, "SYSLOG_IDENTIFIER": "systemd",
                    "__REALTIME_TIMESTAMP": str((index + 1) * 1000000),
                }) for index, message in enumerate(messages))
            else:
                text = "active\n"
        elif argv[:3] == ["gh", "repo", "view"]:
            text = "ADMIN\n"
        elif argv[:3] == ["gh", "label", "list"]:
            text = json.dumps(list(config.LABELS))
        else:
            text = ""
        return subprocess.CompletedProcess(argv, 0, text, "")

    def doctor(self, platform):
        output = io.StringIO()
        with patch.object(sys, "platform", platform), \
                patch.object(subprocess, "run", side_effect=self.runner), \
                patch.object(onboard.shutil, "which", side_effect=lambda name, **kw:
                             None if name in {"systemctl", "journalctl"} else "/bin/tool"), \
                redirect_stdout(output):
            self.assertEqual(cli.main(["doctor", "--json"]), 0)
        return json.loads(output.getvalue())

    def dashboard(self, platform):
        output = io.StringIO()
        with patch.object(sys, "platform", platform), \
                patch.object(subprocess, "run", side_effect=self.runner), \
                patch.object(dashboard, "github", return_value={"repository": {
                    "issues": {"nodes": []}, "pullRequests": {"nodes": []},
                }}), \
                patch.object(dashboard, "triage_llm_online", return_value=False), \
                patch.object(dashboard.stats, "dispatcher_login", return_value=""), \
                redirect_stdout(output):
            self.assertEqual(cli.main(["dashboard", "--json"]), 0)
        return json.loads(output.getvalue())

    def test_dashboard_failure_count_remains_consumable_without_journal(self):
        original = self.runner
        for platform, failure in (
            ("darwin", FileNotFoundError("journalctl")),
            ("linux", FileNotFoundError("journalctl")),
            ("linux", subprocess.CompletedProcess([], 1, "", "journal unavailable")),
        ):
            def unavailable(argv, failure=failure, **kwargs):
                if argv[0] == "journalctl":
                    if isinstance(failure, OSError):
                        raise failure
                    return failure
                return original(argv, **kwargs)

            with self.subTest(platform=platform, failure=failure), \
                    patch.object(self, "runner", side_effect=unavailable):
                data = self.dashboard(platform)
                count = data["dispatcher"]["consecutive_failures"]
                self.assertEqual(int(count), 0)  # District apply/status consumes this as an integer.
                self.assertIs(type(count), int)
                self.assertEqual(data["dispatcher"]["runs"], [])

    def test_dashboard_json_without_systemd_on_darwin(self):
        data = self.dashboard("darwin")
        self.assertEqual(data["version"], __version__)
        self.assertIsNone(data["dispatcher"]["consecutive_failures"])
        self.assertEqual(data["dispatcher"]["runs"], [])
        self.assertFalse(any(argv[0] == "journalctl" for argv in self.calls))

    def test_doctor_json_without_systemd_on_darwin(self):
        data = self.doctor("darwin")
        self.assertEqual(data["version"], __version__)
        rows = {row["label"]: row for row in data["rows"]}
        for label in (f"systemd timer {self.cfg.unit}.timer", "triage liveness probe"):
            self.assertEqual(rows[label]["status"], "WARN")
            self.assertIn("not applicable", rows[label]["detail"])
        self.assertFalse(any(argv[0] == "systemctl" for argv in self.calls))

    def test_probe_threshold_without_systemd_on_darwin(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "platform", "darwin"), \
                patch.object(subprocess, "run", side_effect=self.runner), \
                patch.object(triage, "probe_endpoint", return_value=(False, "connection refused")), \
                redirect_stdout(output), redirect_stderr(errors):
            for now in range(triage.PROBE_FAILURES + 1):
                self.assertEqual(triage.run_probe(self.cfg, now=now), 1)
        self.assertIn("restart not supported on this platform", errors.getvalue())
        state, _ = triage._probe_paths(self.cfg, triage.models_url(self.cfg))
        self.assertEqual(json.loads(state.read_text())["failures"], triage.PROBE_FAILURES)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual([row["succeeded"] for row in rows if row.get("restarted")], [False])
        self.assertFalse(any(argv[0] == "systemctl" for argv in self.calls))

    def test_missing_journal_binary_is_unavailable(self):
        with patch.object(subprocess, "run", side_effect=FileNotFoundError("journalctl")):
            self.assertEqual(dashboard.sh(["journalctl", "--user"]), "")

    def test_linux_missing_systemctl_reports_unavailable(self):
        original = self.runner

        def missing(argv, **kwargs):
            if argv[0] == "systemctl":
                raise FileNotFoundError(2, "No such file or directory", "systemctl")
            return original(argv, **kwargs)

        with patch.object(self, "runner", side_effect=missing):
            data = self.doctor("linux")
        rows = {row["label"]: row for row in data["rows"]}
        for label in (f"systemd timer {self.cfg.unit}.timer", "triage liveness probe"):
            self.assertEqual(rows[label]["status"], "WARN")
            self.assertIn("unavailable:", rows[label]["detail"])

    def test_linux_restart_keeps_success_failure_and_missing_binary_behavior(self):
        for result, expected, message in (
            (subprocess.CompletedProcess([], 0, "", ""), True, ""),
            (subprocess.CompletedProcess([], 1, "", "permission denied"), False, "permission denied"),
            (FileNotFoundError("systemctl"), False, "systemctl"),
        ):
            with self.subTest(result=result), patch.object(sys, "platform", "linux"), \
                    patch.object(subprocess, "run") as run, redirect_stderr(io.StringIO()) as errors:
                if isinstance(result, OSError):
                    run.side_effect = result
                else:
                    run.return_value = result
                self.assertIs(triage._restart_service(self.cfg.triage_service), expected)
                self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "restart", "model.service"])
                self.assertIn(message, errors.getvalue())

    def test_linux_timer_and_journal_output_is_unchanged(self):
        data = self.doctor("linux")
        rows = {row["label"]: row for row in data["rows"]}
        self.assertEqual(rows[f"systemd timer {self.cfg.unit}.timer"], {
            "status": "PASS", "label": f"systemd timer {self.cfg.unit}.timer", "detail": "active",
        })
        self.assertEqual(rows["triage liveness probe"], {
            "status": "PASS", "label": "triage liveness probe",
            "detail": f"{self.cfg.unit}-triage-probe.timer active",
        })
        data = self.dashboard("linux")
        self.assertEqual(data["dispatcher"]["consecutive_failures"], 1)
        self.assertEqual(data["dispatcher"]["runs"], [{
            "started": "1970-01-01T00:00:01Z", "finished": "1970-01-01T00:00:02Z",
            "result": "failed", "lines": [],
        }])
        self.assertIn(["systemctl", "--user", "is-active", f"{self.cfg.unit}.timer"], self.calls)
        self.assertIn(["systemctl", "--user", "is-active", f"{self.cfg.unit}-triage-probe.timer"], self.calls)
        self.assertIn(["journalctl", "--user", "-u", f"{self.cfg.unit}.service",
                       "-o", "json", "-n", "3000", "--no-pager"], self.calls)

    def test_native_fixture_json_commands(self):
        """Actual CLI smoke: also runs on the macOS CI lane without systemd stubs."""
        gh = ('case "$1 $2" in "repo view") echo ADMIN;; '
              '"label list") echo "[]";; "api graphql") '
              'echo \'{"data":{"repository":{"issues":{"nodes":[]},'
              '"pullRequests":{"nodes":[]}}}}\';; esac\nexit 0')
        stubs = stub_bin(self.root, gh=gh, droid="exit 0", omp="exit 0")
        for command in ("dashboard", "doctor"):
            with self.subTest(command=command):
                result = factory(self.repo, command, "--json", path=stubs)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["version"], __version__)
                if command == "dashboard":
                    self.assertIn("consecutive_failures", data["dispatcher"])
                else:
                    self.assertTrue(data["ok"])

    def test_linux_unavailable_journal_is_not_zero_failures(self):
        original = self.runner
        for failure in (FileNotFoundError("journalctl"),
                        subprocess.CompletedProcess([], 1, "", "journal unavailable")):
            def unavailable(argv, failure=failure, **kwargs):
                if argv[0] == "journalctl":
                    if isinstance(failure, OSError):
                        raise failure
                    return failure
                return original(argv, **kwargs)

            with self.subTest(failure=failure), patch.object(self, "runner", side_effect=unavailable):
                data = self.dashboard("linux")
            self.assertIsNone(data["dispatcher"]["consecutive_failures"])
            self.assertEqual(data["dispatcher"]["runs"], [])

    def test_linux_empty_observed_journal_has_zero_failures(self):
        original = self.runner

        def empty(argv, **kwargs):
            if argv[0] == "journalctl":
                return subprocess.CompletedProcess(argv, 0, "", "")
            return original(argv, **kwargs)

        with patch.object(self, "runner", side_effect=empty):
            data = self.dashboard("linux")
        self.assertEqual(data["dispatcher"]["consecutive_failures"], 0)

    def test_darwin_doctor_checks_real_free_port_without_ss(self):
        original = self.runner

        def missing_ss(argv, **kwargs):
            if argv[0] == "ss":
                raise FileNotFoundError("ss")
            return original(argv, **kwargs)

        with patch.object(onboard, "_dashboard_port_check", side_effect=self.port_check), \
                patch.object(self, "runner", side_effect=missing_ss):
            data = self.doctor("darwin")
        rows = {row["label"]: row for row in data["rows"]}
        self.assertTrue(data["ok"])
        self.assertEqual(rows[f"dashboard port {self.cfg.dashboard_port}"]["status"], "PASS")

    def test_unidentified_busy_port_still_fails_without_ss(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            for platform in ("linux", "darwin"):
                with self.subTest(platform=platform), patch.object(sys, "platform", platform), \
                        patch.object(onboard, "sh", side_effect=FileNotFoundError("ss")):
                    ok, _ = self.port_check(self.cfg, "127.0.0.1", listener.getsockname()[1])
                self.assertFalse(ok)

    def test_doctor_does_not_require_linux_renameat2(self):
        stubs = stub_bin(self.root, gh='echo ADMIN\nexit 0', droid="exit 0", omp="exit 0")
        script = """
import ctypes
import runpy
import sys
original = ctypes.CDLL
class WithoutRename:
    def __init__(self, *args, **kwargs):
        self.library = original(*args, **kwargs)
    def __getattr__(self, name):
        if name == "renameat2":
            raise AttributeError(name)
        return getattr(self.library, name)
ctypes.CDLL = WithoutRename
sys.argv = ["factory", "doctor", "--json"]
runpy.run_module("factory", run_name="__main__")
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=self.repo, capture_output=True, text=True,
            env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}",
                 "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
