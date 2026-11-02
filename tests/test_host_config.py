"""Isolated host identity, claim TTL, doctor, and triage-host behavior."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, onboard, triage


class HostConfigTest(unittest.TestCase):
    def load(self, root: Path, host: dict, repo_toml: str = '[repo]\nslug = "acme/widgets"\n') -> config.Config:
        (root / config.CONFIG_NAME).write_text(repo_toml)
        with mock.patch.object(config, "repo_root", return_value=root), \
                mock.patch.object(config, "host_config", return_value=host):
            return config.load(root)

    def test_scalar_defaults_and_repo_override_layer(self) -> None:
        host = {
            "defaults": {"host_name": "default", "triage_host": "triage", "claim_ttl": "1h30m"},
            "repo": {"acme/widgets": {"host_name": "tank", "claim_ttl": "45m"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.load(Path(directory), host)
        self.assertEqual((cfg.host_name, cfg.triage_host, cfg.claim_ttl), ("tank", "triage", "45m"))
        self.assertEqual(cfg.claim_ttl_seconds, 45 * 60)

    def test_invalid_host_identity_and_claim_ttl_are_rejected(self) -> None:
        invalid = (
            ("host_name", ""),
            ("host_name", "-tank"),
            ("host_name", "tänk"),
            ("host_name", "a" * 46),
            ("triage_host", "tank two"),
            ("claim_ttl", ""),
            ("claim_ttl", "0s"),
            ("claim_ttl", "1"),
            ("claim_ttl", "1fortnight"),
            ("claim_ttl", 2),
        )
        for key, value in invalid:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory, \
                    self.assertRaises(config.ConfigError):
                self.load(Path(directory), {"defaults": {key: value}})

    def test_claim_ttl_units_define_exact_recovery_deadline(self) -> None:
        for duration, seconds in (("1s", 1), ("1m", 60), ("1h", 3600), ("1d", 86400),
                                  ("1d2h3m4s", 93784)):
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as directory:
                cfg = self.load(Path(directory), {"defaults": {"claim_ttl": duration}})
                self.assertEqual(cfg.claim_ttl_seconds, seconds)

    def test_host_scalars_are_not_allowed_in_repository_config(self) -> None:
        for key, value in (("host_name", '"tank"'), ("triage_host", '"tank"'), ("claim_ttl", '"2h"')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory, \
                    self.assertRaisesRegex(config.ConfigError, "host-only"):
                self.load(
                    Path(directory), {},
                    f'{key} = {value}\n[repo]\nslug = "acme/widgets"\n',
                )

    def doctor_rows(self, cfg: config.Config, host: dict) -> dict[str, dict]:
        def command(argv, **_kwargs):
            stdout = ""
            if argv[:3] == ["gh", "repo", "view"]:
                stdout = "ADMIN\n"
            elif argv[:3] == ["gh", "label", "list"]:
                stdout = json.dumps([*config.LABELS])
            elif argv[:4] == ["git", "config", "user.name"]:
                stdout = "Factory\n"
            elif argv[:3] == ["systemctl", "--user", "is-active"]:
                stdout = "active\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        output = io.StringIO()
        with mock.patch.object(onboard.config, "load", return_value=cfg), \
                mock.patch.object(onboard.config, "host_config", return_value=host), \
                mock.patch.object(onboard.config, "host_config_path", return_value=cfg.root / "host.toml"), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=command), \
                mock.patch.object(onboard, "sha256", return_value=None), \
                mock.patch.object(onboard, "workflows", return_value=[]), \
                mock.patch.object(onboard, "report_stage_success"), \
                mock.patch.object(onboard, "ssh_signing_key_status", return_value=None), \
                mock.patch.object(onboard, "unit_dir", return_value=cfg.root / "units"), \
                mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ok")), \
                mock.patch("factory.workspace.key_directory_problem", return_value=None), \
                mock.patch("factory.workspace.access_file_problem", return_value=None), \
                contextlib.redirect_stdout(output):
            onboard.doctor(["--json"])
        return {row["label"]: row for row in json.loads(output.getvalue())["rows"]}

    def test_doctor_reports_effective_host_claim_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(root, "acme/widgets", host_name="tank", triage_host="triage", claim_ttl="90m")
            rows = self.doctor_rows(cfg, {"defaults": {"host_name": "tank", "triage_host": "triage", "claim_ttl": "90m"}})
        self.assertEqual(rows["host_name"]["detail"], "tank")
        self.assertEqual(rows["triage_host"]["detail"], "triage")
        self.assertEqual(rows["claim_ttl"]["detail"], "90m")

    def test_doctor_hides_default_ttl_without_multi_host_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", triage_host="triage")
            rows = self.doctor_rows(cfg, {"defaults": {"triage_host": "triage"}})
        self.assertIn("triage_host", rows)
        self.assertNotIn("host_name", rows)
        self.assertNotIn("claim_ttl", rows)

    def test_doctor_reports_explicit_ttl_without_host_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", claim_ttl="15m")
            rows = self.doctor_rows(cfg, {"repo": {"acme/widgets": {"claim_ttl": "15m"}}})
        self.assertEqual(rows["claim_ttl"]["detail"], "15m")



if __name__ == "__main__":
    unittest.main()
