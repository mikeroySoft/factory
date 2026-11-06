"""District drain admission through the CLI and the rendered pass boundary."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, learn, manage, onboard, watch

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
REASON = "engine 2ad8846 -> e84004a"


class DrainPassTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.flag = self.state / "district" / "drain"
        self.markers = self.root / "markers"
        self.signals = self.root / "signals"
        self.env = {
            **os.environ, "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PYTHONPATH": str(ROOT), "FACTORY_NOW": NOW.isoformat(),
        }
        self.env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
        self.cfg = config.Config(self.root, "acme/widgets", triage_service="model.service", eval_schedule=True)
        with mock.patch.dict(os.environ, self.env):
            self.units = onboard.units(self.cfg, "10min", "127.0.0.1")
        self.unit = self.units[f"{self.cfg.unit}.service"]

    def write_flag(self, *, hours: float = 1, age: float = 0, content: str | None = None) -> None:
        self.flag.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.flag.with_suffix(".tmp")
        temporary.write_text(content if content is not None else json.dumps({
            "schema_version": 1, "requested_by": "district apply --drain", "reason": REASON,
            "pid": 12345, "created_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
            "unknown_key": "ignored",
        }), encoding="utf-8")
        temporary.replace(self.flag)
        mtime = (NOW - timedelta(hours=age)).timestamp()
        os.utime(self.flag, (mtime, mtime))

    def command(self, argv: list[str], fault: str | None = None) -> subprocess.CompletedProcess:
        if fault:
            # Inject filesystem/internal failures inside the public command's process.
            injection = {
                "stat": "patch.object(drain.Path, 'stat', side_effect=PermissionError('denied'))",
                "internal": "patch.object(drain.Path, 'stat', side_effect=RuntimeError('broken'))",
                "read": "patch.object(drain.Path, 'read_text', side_effect=PermissionError('denied'))",
                "exit": "patch.object(drain.Path, 'stat', side_effect=SystemExit(7))",
            }[fault]
            argv = [*argv[:argv.index("-m")], "-c", (
                "from unittest.mock import patch; from factory import cli, drain; "
                f"p = {injection}; p.start(); raise SystemExit(cli.main(['drain-check']))"
            )]
        return subprocess.run(argv, cwd=self.root, env=self.env, capture_output=True, text=True, check=False)

    def check(self, fault: str | None = None) -> subprocess.CompletedProcess:
        return self.command([sys.executable, "-P", "-m", "factory", "drain-check"], fault)

    def run_pass(self, *, mid_pass: bool = False, fault: str | None = None) -> tuple[int, list[str]]:
        """Execute rendered conditions before starts, then the post-stop watch, like systemd."""
        starts = []
        result = 0
        service_result = "success"
        for line in self.unit.splitlines():
            if line.startswith("ExecCondition="):
                proc = self.command(shlex.split(line.split("=", 1)[1]), fault)
                if 1 <= proc.returncode <= 254:
                    service_result = "exec-condition"
                    break
                self.assertEqual(proc.returncode, 0, proc.stderr)
            elif line.startswith("ExecStart="):
                stage = shlex.split(line.split("=", 1)[1].lstrip("-"))[-1]
                starts.append(stage)
                script = (
                    "import signal; from pathlib import Path; "
                    f"signal.signal(signal.SIGTERM, lambda *a: Path({str(self.signals)!r}).touch()); "
                    f"signal.signal(signal.SIGINT, lambda *a: Path({str(self.signals)!r}).touch()); "
                    f"p=Path({str(self.markers)!r}); p.write_text((p.read_text() if p.exists() else '')+{stage!r}+'\\n')"
                )
                if mid_pass and stage == "triage":
                    script += (
                        f"; p=Path({str(self.flag)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                        f"t=p.with_suffix('.tmp'); t.write_text({json.dumps({'expires_at': (NOW + timedelta(hours=1)).isoformat(), 'reason': REASON})!r}); "
                        f"t.replace(p); import os; os.utime(p, ({NOW.timestamp()}, {NOW.timestamp()}))"
                    )
                proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                result = proc.returncode
        self.assertIn("ExecStopPost=", self.unit)
        # A skipped condition invokes ExecStopPost too, but must never become a filing.
        dispatch.configure(self.cfg)
        def systemctl(argv, **kwargs):
            self.assertEqual(argv[0], "systemctl")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.dict(os.environ, {**self.env, "SERVICE_RESULT": service_result, "EXIT_CODE": "exited", "EXIT_STATUS": "1"}), \
             mock.patch.object(subprocess, "run", side_effect=systemctl), \
             mock.patch.object(watch, "runtime_errors", return_value=[]), \
             mock.patch.object(dispatch, "run") as gh:
            self.assertEqual(watch.run_pass(self.cfg, now=NOW), [])
            gh.assert_not_called()
        return result, starts

    def test_pass_skips_when_drain_flag_present(self) -> None:
        self.write_flag()
        proc = self.check()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn(f"skipped: district drain ({REASON}, until 2026-11-03T13:00:00Z)", proc.stderr)
        self.assertEqual(self.run_pass(), (0, []))
        self.assertFalse(self.markers.exists())
        self.assertFalse((self.cfg.factory / "events.jsonl").exists())

    def test_pass_runs_when_flag_expired(self) -> None:
        cases = [
            ({"hours": -1 / 3600}, None, "drain flag expired at"),
            ({"hours": 30, "age": 9}, None, "drain flag expired at"),
            ({"content": "{", "age": 9}, None, "drain flag expired at"),
            ({"age": -1 / 6}, None, "WARN drain flag mtime in the future; ignoring"),
            ({}, "stat", "WARN drain flag unreadable; ignoring"),
            ({}, "internal", "ERROR"),
            ({}, "exit", "ERROR"),
        ]
        for flag, fault, log in cases:
            with self.subTest(flag=flag, fault=fault):
                self.write_flag(**flag)
                proc = self.check(fault)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(log, proc.stderr)
                self.assertEqual(self.run_pass(fault=fault), (0, ["triage", "dispatch"]))

    def test_malformed_flags_block_only_until_mtime_cap(self) -> None:
        for content in ("{", "[]", "{}", '{"expires_at": null}', '{"expires_at": "tomorrow"}',
                        '{"expires_at": "2026-11-04"}', '{"expires_at": "2026-11-04T12:00:00"}'):
            with self.subTest(content=content):
                self.write_flag(content=content, age=1 / 60)
                proc = self.check()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("WARN drain flag malformed", proc.stderr)
                self.assertIn("malformed flag, until 2026-11-03T19:59:00Z", proc.stderr)
                self.write_flag(content=content, age=8)
                proc = self.check()
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("drain flag expired at 2026-11-03T12:00:00Z; ignoring", proc.stderr)
        self.write_flag(age=1 / 60)
        proc = self.check("read")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("WARN drain flag malformed", proc.stderr)

    def test_far_future_expiry_is_capped_and_future_mtime_tolerance_is_bounded(self) -> None:
        self.write_flag(hours=30)
        proc = self.check()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("until 2026-11-03T20:00:00Z", proc.stderr)
        self.write_flag(age=-1 / 60)
        self.assertEqual(self.check().returncode, 1)
        self.write_flag(age=-61 / 3600)
        self.assertEqual(self.check().returncode, 0)

    def test_running_pass_ignores_flag_written_mid_pass(self) -> None:
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.run_pass(mid_pass=True), (0, ["triage", "dispatch"]))
        self.assertEqual(self.markers.read_text(), "triage\ndispatch\n")
        self.assertFalse(self.signals.exists())
        self.assertEqual(self.check().returncode, 1)
        # Exercise the real hand-run dispatch entry, stubbing only its project/network work.
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(config, "load", return_value=self.cfg), \
             mock.patch.object(dispatch, "pin_root", return_value=[]), \
             mock.patch.object(dispatch, "land_pass"), \
             mock.patch.object(dispatch, "review_intake_pass"), \
             mock.patch.object(manage, "manage_pass"), \
             mock.patch.object(dispatch, "ticket_pass") as work, \
             mock.patch.object(learn, "learn_pass"):
            self.assertEqual(cli.main(["dispatch"]), 0)
            work.assert_called_once()

    def test_install_gates_only_the_pass_and_keeps_timers_unchanged(self) -> None:
        output = io.StringIO()
        with mock.patch.object(config, "load", return_value=self.cfg), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["install", "--print", "--dashboard"]), 0)
        commands = [line for line in self.unit.splitlines() if line.startswith("Exec")]
        self.assertEqual(commands[0], f"ExecCondition={onboard.systemd_argument(str(onboard.service_python(self.cfg)))} -P -m factory drain-check")
        self.assertEqual(output.getvalue().count("drain-check"), 1)
        for name, body in self.units.items():
            if name != f"{self.cfg.unit}.service":
                self.assertNotIn("drain-check", body)
        self.assertEqual(self.units[f"{self.cfg.unit}.timer"], (
            "[Unit]\nDescription=Run the factory pass for acme/widgets every 10min\n\n"
            "[Timer]\nOnBootSec=5min\nOnUnitActiveSec=10min\nRandomizedDelaySec=90\n\n"
            "[Install]\nWantedBy=timers.target\n"
        ))

    def test_install_probe_refuses_an_engine_without_drain_check(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-P", "-c",
             ("from factory.cli import COMMANDS; COMMANDS.pop('drain-check', None); "
              f"exec({onboard.INTERPRETER_PROBE!r})")],
            cwd=self.root, env=self.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("missing drain-check command", json.loads(proc.stdout)["error"])

    def test_default_state_path_honors_home_when_xdg_is_unset_or_empty(self) -> None:
        self.env["HOME"] = str(self.root / "home")
        self.flag = Path(self.env["HOME"]) / ".local" / "state" / "district" / "drain"
        for xdg in (None, ""):
            with self.subTest(xdg=xdg):
                self.env.pop("XDG_STATE_HOME", None)
                if xdg is not None:
                    self.env["XDG_STATE_HOME"] = xdg
                self.write_flag()
                proc = self.check()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn(f"skipped: district drain ({REASON}, until", proc.stderr)
                self.write_flag(hours=-1)
                proc = self.check()
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("drain flag expired at", proc.stderr)

    def test_missing_or_nontext_reason_keeps_valid_expiry_and_fallback(self) -> None:
        for fields in ({}, {"reason": None}, {"reason": 7}, {"reason": ""}):
            with self.subTest(fields=fields):
                self.write_flag(content=json.dumps({
                    "expires_at": "2026-11-03T13:00:00Z", **fields,
                }))
                proc = self.check()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertEqual(proc.stderr, (
                    "skipped: district drain (malformed flag, until 2026-11-03T13:00:00Z)\n"
                ))

if __name__ == "__main__":
    unittest.main()
