"""Owned host pause metadata reaches every read-only reporting surface."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory import config, dashboard, onboard, runtime_local


class PauseReportingTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        self.cfg.install = {"host": "127.0.0.1", "env": {}}
        self.flag = self.root / "state" / "factory" / "factory.pause"
        self.flag.parent.mkdir(parents=True)

    def pause_case(self, kind: str) -> tuple[dict, float | None]:
        self.flag.unlink(missing_ok=True)
        expected = {
            "paused": False,
            "owner": None,
            "reason": None,
            "created_at": None,
            "age_s": None,
            "unowned": False,
        }
        if kind == "absent":
            return expected, None
        age = {"owned": 321.0, "bare": 45.0, "partial": 90.0}[kind]
        if kind == "owned":
            created_at = (datetime.now(UTC) - timedelta(seconds=age)).isoformat()
            expected.update(
                paused=True,
                owner="maintenance-agent",
                reason="Upgrade <b>workers</b> & verify",
                created_at=created_at,
            )
            self.flag.write_text(json.dumps({
                "owner": expected["owner"],
                "reason": expected["reason"],
                "created_at": created_at,
            }))
        else:
            if kind == "bare":
                self.flag.touch()
            else:
                self.flag.write_text(json.dumps({"owner": "partial-agent"}))
                expected["owner"] = "partial-agent"
            modified = (datetime.now(UTC) - timedelta(seconds=age)).timestamp()
            os.utime(self.flag, (modified, modified))
            expected.update(paused=True, unowned=True)
        return expected, age

    def assert_pause(self, actual: dict, expected: dict, age: float | None) -> None:
        for field in ("paused", "owner", "reason", "created_at", "unowned"):
            self.assertEqual(actual[field], expected[field])
        if age is None:
            self.assertIsNone(actual["age_s"])
        else:
            self.assertAlmostEqual(actual["age_s"], age, delta=5)

    def runtime_state(self) -> tuple[dict, list[dict]]:
        def command(operation: str, **_kwargs) -> str:
            if operation == "schedule":
                return "[]"
            return "LoadState=loaded\nActiveState=inactive\n"

        with patch.object(runtime_local, "_command", side_effect=command), \
                patch.object(runtime_local, "_capacity", return_value=0):
            return runtime_local.dispatcher(self.cfg, [], {})

    def test_runtime_and_dashboard_report_real_owned_unowned_and_absent_flags(self) -> None:
        for kind in ("owned", "bare", "partial", "absent"):
            with self.subTest(kind=kind):
                expected, age = self.pause_case(kind)
                state, errors = self.runtime_state()
                self.assertFalse(errors)
                self.assertEqual(state["paused"], True if expected["paused"] else None)
                self.assert_pause(state["pause"], expected, age)

                with patch.object(dashboard.runtime_local, "dispatcher", return_value=(state, [])), \
                        patch.object(dashboard, "_timer_last", return_value=None), \
                        patch.object(dashboard, "journal_runs", return_value=[]):
                    dashboard.configure(self.cfg)
                    reported = dashboard.dispatcher(
                        {"executions": [], "history": {}, "events": []},
                    )
                self.assertEqual(reported["paused"], True if expected["paused"] else None)
                self.assert_pause(reported["pause"], expected, age)

    def doctor(self, *argv: str) -> tuple[int, str]:
        (self.root / config.CONFIG_NAME).touch()

        def command(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
            output = ""
            if args[:3] == ["gh", "repo", "view"]:
                output = "WRITE\n"
            elif args[:3] == ["gh", "label", "list"]:
                output = json.dumps(list(config.LABELS))
            return subprocess.CompletedProcess(args, 0, output, "")

        output = io.StringIO()
        with patch.object(onboard.config, "load", return_value=self.cfg), \
                patch.object(onboard.config, "host_config", return_value={}), \
                patch.object(onboard.config, "engine_drift", return_value=None), \
                patch.object(onboard, "sh", side_effect=command), \
                patch.object(onboard.shutil, "which", side_effect=lambda exe, **_kwargs: (
                    None if exe == "systemctl" else "/bin/tool"
                )), \
                patch.object(onboard, "unit_dir", return_value=self.root / "units"), \
                patch.object(onboard, "report_stage_success"), \
                patch.object(onboard, "_dashboard_port_check", return_value=(True, "available")), \
                patch("factory.claims.repository_host_problem", return_value=None), \
                patch("factory.triage.probe_endpoint", return_value=(True, "alive")), \
                patch("factory.workspace.key_directory_problem", return_value=None), \
                patch("factory.workspace.access_file_problem", return_value=None), \
                contextlib.redirect_stdout(output):
            result = onboard.doctor(list(argv))
        return result, output.getvalue()

    def test_doctor_text_and_json_report_real_pause_metadata(self) -> None:
        for kind in ("owned", "bare", "partial", "absent"):
            with self.subTest(kind=kind):
                expected, age = self.pause_case(kind)
                _result, raw = self.doctor("--json")
                report = json.loads(raw)
                self.assertEqual(
                    set(report["pause"]),
                    {"paused", "owner", "reason", "created_at", "age_s", "unowned"},
                )
                self.assert_pause(report["pause"], expected, age)
                row = next(row for row in report["rows"] if row["label"] == "dispatcher pause")
                if expected["paused"]:
                    self.assertIn("age", row["detail"])
                    self.assertIn("unowned" if expected["unowned"] else "owned", row["detail"])
                else:
                    self.assertEqual(row["detail"], "not paused")
                if expected["owner"]:
                    self.assertIn(expected["owner"], row["detail"])
                if expected["reason"]:
                    self.assertIn(expected["reason"], row["detail"])

                _result, text = self.doctor()
                self.assertIn("dispatcher pause", text)
                self.assertIn(
                    "unowned" if expected["unowned"] else expected["owner"] or "not paused",
                    text,
                )


if __name__ == "__main__":
    unittest.main()
