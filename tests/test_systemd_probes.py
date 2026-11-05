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


if __name__ == "__main__":
    unittest.main()
