from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, lifecycle, onboard, triage, watch


class GitHub:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        if command[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if command[:3] == ["gh", "issue", "create"]:
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/mikeroySoft/factory/issues/901\n", "",
            )
        raise AssertionError(command)


class WatchExpectationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(self.root, "mikeroySoft/factory")
        self.cfg.factory.mkdir()
        self.github = GitHub()
        dispatch.configure(self.cfg)
        for patcher in (
            mock.patch.object(dispatch, "run", self.github),
            mock.patch.object(watch, "failed_units", return_value=[]),
            mock.patch.object(watch, "runtime_errors", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def append(self, row: dict) -> None:
        lifecycle.append(self.cfg.factory / "events.jsonl", row)

    @staticmethod
    def eval_summary(at: datetime) -> dict:
        return {
            "event": "eval-summary",
            "profile": "default",
            "status": "completed",
            "at": at.isoformat(),
        }

    def test_enabled_eval_summary_nine_days_old_is_act_signal(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append(self.eval_summary(now - timedelta(days=9)))

        signals = watch.run_pass(self.cfg, dry_run=True, now=now)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["tier"], "act")
        self.assertEqual(signals[0]["name"], "eval suite")
        self.assertEqual(self.github.calls, [])

    def test_enabled_eval_summary_one_day_old_is_not_overdue(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append(self.eval_summary(now - timedelta(days=1)))

        self.assertEqual(watch.run_pass(self.cfg, dry_run=True, now=now), [])
        self.assertEqual(self.github.calls, [])

    def test_watch_files_and_journals_intended_switch_mismatch(self) -> None:
        self.append({
            "event": "lifecycle",
            "kind": "exit",
            "stage": "dispatch",
            "outcome": "completed",
            "reason": None,
            "at": lifecycle._now(),
        })

        signals = watch.run_pass(self.cfg)

        self.assertEqual([signal["name"] for signal in signals], ["eval.schedule"])
        creates = [call for call in self.github.calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("eval.schedule", " ".join(creates[0]))
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual([row["issue"] for row in rows if row.get("event") == "watch"], [901])


class IntendedStateCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / config.CONFIG_NAME).write_text("# [eval] intentionally omitted\n")
        self.cfg = config.Config(self.root, "mikeroySoft/factory", signoff=False)
        self.cfg.factory.mkdir()
        self.host_path = self.root / "config" / "factory" / "config.toml"

    def command(self, command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(command, 0, "ADMIN\n", "")
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(list(config.LABELS)), "")
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(command, 0, "active\n", "")
        if command[0] == "loginctl":
            return subprocess.CompletedProcess(command, 0, "yes\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_cli_doctor_fails_once_for_omitted_intended_eval_switch(self) -> None:
        output = io.StringIO()
        with mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard.config, "host_config", return_value={}), \
                mock.patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=self.command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ready")), \
                redirect_stdout(output):
            code = cli.main(["doctor"])

        lines = [
            line for line in output.getvalue().splitlines()
            if "FAIL" in line and "eval.schedule" in line
        ]
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertIn("FAIL", lines[0])

    def test_install_keeps_intended_but_disabled_eval_units_off(self) -> None:
        units = self.root / "units"
        units.mkdir()
        eval_service = units / f"{self.cfg.unit}-eval.service"
        eval_timer = units / f"{self.cfg.unit}-eval.timer"
        eval_service.write_text("stale\n")
        eval_timer.write_text("stale\n")
        calls: list[list[str]] = []

        def command(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
            calls.append(argv)
            return self.command(argv, cwd)

        with mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard, "unit_dir", return_value=units), \
                mock.patch.object(onboard, "sh", side_effect=command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/systemctl"), \
                mock.patch.dict(os.environ, {"PATH": os.environ["PATH"]}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(onboard.install(["--no-dashboard"]), 0)

        self.assertTrue((units / f"{self.cfg.unit}.service").exists())
        self.assertFalse(eval_service.exists())
        self.assertFalse(eval_timer.exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", eval_timer.name], calls)


if __name__ == "__main__":
    unittest.main()
