"""#631: on a host with no systemd (macOS), systemd-only probes report unavailable and never raise.

Every case fakes `sys.platform == "darwin"` and runs with a PATH that holds no systemctl,
journalctl, ss or loginctl, so the calls raise the same FileNotFoundError Ultron sees.
The Linux cases keep the systemd calls and their arguments exactly as they were.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, dashboard, dispatch, lifecycle, onboard, triage

SYSTEMD_TOOLS = ("systemctl", "journalctl", "ss", "loginctl")

# `sys.platform` is read by the standard library at import time; the child imports every module
# it needs first and only then claims to be darwin.
DARWIN_CHILD = (
    "import importlib, runpy, sys\n"
    "for name in ('urllib.request', 'factory.dashboard', 'factory.onboard', 'factory.triage'):\n"
    "    importlib.import_module(name)\n"
    "sys.platform = 'darwin'\n"
    "sys.argv = ['factory', *sys.argv[1:]]\n"
    "runpy.run_module('factory', run_name='__main__')\n"
)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


class NoSystemdCase(unittest.TestCase):
    """A scratch repo, an empty tool directory and a PATH that points only at it."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.state = self.root / "state"
        (self.tools / "git").symlink_to(shutil.which("git"))
        offline = self.tools / "gh"
        offline.write_text("#!/bin/sh\nexit 1\n")
        offline.chmod(0o755)
        self.enterContext(patch.dict(os.environ, {
            "PATH": str(self.tools),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
            lifecycle.CONTEXT_ENV: "",
        }))
        self.enterContext(patch.dict(dashboard.__dict__))
        self.enterContext(patch.dict(dispatch.__dict__))
        self.enterContext(patch.dict(triage.__dict__))
        for tool in SYSTEMD_TOOLS:
            self.assertIsNone(shutil.which(tool), f"{tool} must be absent for this case")

    def fake_darwin(self):
        return patch.object(sys, "platform", "darwin")

    def repo(self, toml: str = "") -> Path:
        repo = self.root / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "T")
        git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
        (repo / "README.md").write_text("hello\n")
        if toml:
            (repo / config.CONFIG_NAME).write_text(toml)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        return repo

    def child_env(self) -> dict[str, str]:
        return {
            "PATH": str(self.tools),
            "PYTHONPATH": str(ROOT),
            "HOME": str(self.root),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
            "XDG_STATE_HOME": str(self.state),
        }

    def run_darwin(self, repo: Path, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", DARWIN_CHILD, *argv],
            cwd=repo, env=self.child_env(), capture_output=True, text=True, check=False,
        )

    def configure_dashboard(self) -> config.Config:
        cfg = config.Config(self.root, "acme/widgets", lock=self.root / "gpu.lock")
        dashboard.configure(cfg)
        return cfg

    def stub_tool(self, name: str, body: str) -> Path:
        script = self.tools / name
        log = self.tools / f"{name}.log"
        script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n{body}\n')
        script.chmod(0o755)
        return log


class DashboardJournalTest(NoSystemdCase):
    def test_journal_runs_is_empty_when_there_is_no_journalctl(self) -> None:
        self.configure_dashboard()
        with self.fake_darwin():
            self.assertEqual(dashboard.journal_runs(), [])

    def test_dispatcher_section_reports_zero_failures_without_journalctl(self) -> None:
        self.configure_dashboard()
        with self.fake_darwin():
            section = dashboard.dispatcher({"executions": [], "history": {}})
        self.assertEqual(section["runs"], [])
        self.assertEqual(section["consecutive_failures"], 0)

    def test_dashboard_json_command_exits_zero_with_version_and_failures(self) -> None:
        repo = self.repo()
        result = self.run_darwin(repo, "dashboard", "--json")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        snapshot = json.loads(result.stdout)
        self.assertIn("version", snapshot)
        self.assertEqual(snapshot["dispatcher"]["consecutive_failures"], 0)

    def test_linux_still_reads_the_user_journal_with_the_same_arguments(self) -> None:
        cfg = self.configure_dashboard()
        log = self.stub_tool("journalctl", "exit 0")
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(dashboard.journal_runs(), [])
        self.assertEqual(
            log.read_text().splitlines(),
            [f"--user -u {cfg.unit}.service -o json -n 3000 --no-pager"],
        )


class DoctorPortTest(NoSystemdCase):
    def held_port(self) -> tuple[socket.socket, int]:
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(holder.close)
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        return holder, holder.getsockname()[1]

    def test_port_row_names_the_conflict_not_the_missing_ss_binary(self) -> None:
        cfg = config.Config(self.root, "acme/widgets", signoff=False)
        _, port = self.held_port()
        with self.fake_darwin():
            ok, detail = onboard._dashboard_port_check(cfg, "127.0.0.1", port)
        self.assertFalse(ok)
        self.assertIn(f"port {port} on 127.0.0.1 is in use", detail)
        self.assertNotIn("Errno", detail)
        self.assertNotIn("No such file", detail)
        self.assertNotIn("ss:", detail)

    def test_free_port_is_still_available(self) -> None:
        cfg = config.Config(self.root, "acme/widgets", signoff=False)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.fake_darwin():
            self.assertEqual(
                onboard._dashboard_port_check(cfg, "127.0.0.1", port),
                (True, "available on 127.0.0.1"),
            )

    def test_linux_without_ss_still_reports_it_could_not_check(self) -> None:
        cfg = config.Config(self.root, "acme/widgets", signoff=False)
        _, port = self.held_port()
        with patch.object(sys, "platform", "linux"):
            ok, detail = onboard._dashboard_port_check(cfg, "127.0.0.1", port)
        self.assertFalse(ok)
        self.assertIn("could not be checked (ss: [Errno 2] No such file or directory: 'ss')", detail)

    def test_doctor_json_is_valid_with_launchd_timer_and_no_exception_rows(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        repo = self.repo(f"[dashboard]\nport = {port}\n")
        result = self.run_darwin(repo, "doctor", "--json")
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads(result.stdout)
        rows = {row["label"]: row for row in report["rows"]}
        timer = next(row for label, row in rows.items() if label.startswith("systemd timer "))
        self.assertEqual((timer["status"], timer["detail"]), ("INFO", "n/a (launchd)"))
        self.assertEqual(rows[f"dashboard port {port}"]["status"], "PASS")
        for row in report["rows"]:
            self.assertNotIn("No such file", row["detail"], row)


class TriageRestartTest(NoSystemdCase):
    def refuse(self, request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    def probe_config(self) -> config.Config:
        cfg = config.Config(root=self.root, repo="local/triage")
        cfg.factory.mkdir(parents=True)
        cfg.triage_service = "rocm-serve-ornith.service"
        return cfg

    def test_restart_service_reports_failure_instead_of_raising(self) -> None:
        err = io.StringIO()
        with self.fake_darwin(), contextlib.redirect_stderr(err):
            self.assertIs(triage._restart_service("rocm-serve-ornith.service"), False)
        self.assertIn("rocm-serve-ornith.service", err.getvalue())
        self.assertIn("systemctl unavailable", err.getvalue())

    def test_probe_survives_every_restart_cooldown_on_a_host_without_systemctl(self) -> None:
        cfg = self.probe_config()
        out = io.StringIO()
        times = [*range(triage.PROBE_FAILURES), triage.PROBE_COOLDOWN + 10, 2 * triage.PROBE_COOLDOWN + 20]
        with self.fake_darwin(), patch.object(triage.urllib.request, "urlopen", self.refuse), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            codes = [triage.run_probe(cfg, now=now) for now in times]
        self.assertEqual(codes, [1] * len(times))
        restarts = [row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                    if row.get("kind") == "result" and row.get("restarted")]
        self.assertEqual([(row["service"], row["succeeded"]) for row in restarts],
                         [("rocm-serve-ornith.service", False)] * 3)
        self.assertEqual(out.getvalue().count("restart failed for rocm-serve-ornith.service"), 3)

    def test_linux_still_restarts_through_systemctl_user(self) -> None:
        log = self.stub_tool("systemctl", "exit 0")
        with patch.object(sys, "platform", "linux"):
            self.assertIs(triage._restart_service("rocm-serve-ornith.service"), True)
        self.assertEqual(log.read_text().splitlines(), ["--user restart rocm-serve-ornith.service"])


if __name__ == "__main__":
    unittest.main()
