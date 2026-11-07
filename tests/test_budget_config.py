"""Dispatch budget floor, hard cap, and doctor reporting (#564)."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, onboard, triage


class BudgetConfigTest(unittest.TestCase):
    def load(self, toml: str) -> config.Config:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / config.CONFIG_NAME).write_text(toml)
        with mock.patch.object(config, "repo_root", return_value=root), \
                mock.patch.object(config, "host_config", return_value={}):
            return config.load(root)

    def test_default_and_custom_hard_cap(self) -> None:
        default = self.load('[repo]\nslug = "acme/widgets"\n')
        custom = self.load(
            '[repo]\nslug = "acme/widgets"\n'
            '[dispatch]\nbudget_min = 120\nbudget_cap_min = 180\n'
        )

        self.assertEqual((default.budget_min, default.budget_cap_min), (90, 720))
        self.assertEqual((custom.budget_min, custom.budget_cap_min), (120, 180))

    def test_cap_must_be_a_positive_integer(self) -> None:
        for value in ("0", "-1", "1.5", '"720"', "true"):
            with self.subTest(value=value), self.assertRaisesRegex(
                config.ConfigError, "dispatch.budget_cap_min must be a positive integer"
            ):
                self.load(
                    '[repo]\nslug = "acme/widgets"\n'
                    f'[dispatch]\nbudget_cap_min = {value}\n'
                )

    def test_floor_cannot_exceed_hard_cap(self) -> None:
        with self.assertRaisesRegex(
            config.ConfigError, "dispatch.budget_min must be <= dispatch.budget_cap_min"
        ):
            self.load(
                '[repo]\nslug = "acme/widgets"\n'
                '[dispatch]\nbudget_min = 91\nbudget_cap_min = 90\n'
            )


class BudgetDoctorTest(unittest.TestCase):
    def test_reports_effective_budget_cap_and_inputs(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n')
        cfg = config.Config(root, "acme/widgets", signoff=False)

        def command(argv, **_kwargs):
            stdout = ""
            if argv[:3] == ["gh", "repo", "view"]:
                stdout = "ADMIN\n"
            elif argv[:3] == ["gh", "label", "list"]:
                stdout = json.dumps([*config.LABELS])
            elif argv[:3] == ["systemctl", "--user", "is-active"]:
                stdout = "active\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        status = {
            "floor_seconds": 90 * 60,
            "cap_seconds": 720 * 60,
            "effective_seconds": 130 * 60,
            "measured_seconds": {"attempt": 10 * 60, "pr": 20 * 60, "review": 10 * 60},
        }
        lifecycle.append(cfg.factory / "events.jsonl", {"event": "dispatch-budget", **status})
        output = io.StringIO()
        with mock.patch.object(onboard.config, "load", return_value=cfg), \
                mock.patch.object(onboard.config, "host_config", return_value={}), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=command), \
                mock.patch.object(onboard, "sha256", return_value=None), \
                mock.patch.object(onboard, "workflows", return_value=[]), \
                mock.patch.object(onboard, "report_stage_success"), \
                mock.patch.object(onboard, "ssh_signing_key_status", return_value=None), \
                mock.patch.object(onboard, "unit_dir", return_value=root / "units"), \
                mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ok")), \
                mock.patch("factory.workspace.key_directory_problem", return_value=None), \
                mock.patch("factory.workspace.access_file_problem", return_value=None), \
                contextlib.redirect_stdout(output):
            onboard.doctor(["--json"])

        rows = {row["label"]: row for row in json.loads(output.getvalue())["rows"]}
        self.assertEqual(
            rows["dispatch budget"],
            {
                "status": "INFO",
                "label": "dispatch budget",
                "detail": (
                    "effective 130m; cap 720m; floor 90m; "
                    "measured attempt 10m, pr 20m, review 10m"
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
