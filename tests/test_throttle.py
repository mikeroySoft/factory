from __future__ import annotations

import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import cli, config, lifecycle, onboard, throttle
from tests import test_host_config


class ThrottleTest(unittest.TestCase):
    def cfg(self, root: Path, **values) -> config.Config:
        return config.Config(root=root, repo="acme/widgets", **values)

    def load(self, root: Path, text: str) -> config.Config:
        (root / config.CONFIG_NAME).write_text(text)
        with (
            mock.patch.object(config, "repo_root", return_value=root),
            mock.patch.object(config, "remote_slug", return_value="acme/widgets"),
            mock.patch.object(config, "host_config", return_value={}),
        ):
            return config.load(root)

    def test_config_defaults_overrides_unknowns_and_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self.load(root, "")
            self.assertEqual(cfg.throttle["pace"], {
                "low": {"max_active": 1}, "normal": {}, "high": {"max_active": 3},
            })
            self.assertEqual(cfg.throttle["aggression"]["low"], {
                "review_rounds": 3, "manager_rounds": 3, "max_attempts": 3, "budget_min": 90,
            })
            self.assertEqual(cfg.throttle["aggression"]["high"], {
                "review_rounds": 5, "manager_rounds": 5, "max_attempts": 4, "budget_min": 150,
            })
            cfg = self.load(root, """
[throttle]
maintenance_labels = ["chore", "deps"]
[throttle.pace.low]
max_active = 0
[throttle.pace.normal]
max_active = 7
[throttle.aggression.high]
budget_min = 210
""")
            self.assertEqual(cfg.throttle["maintenance_labels"], ["chore", "deps"])
            self.assertEqual(cfg.throttle["pace"]["low"]["max_active"], 0)
            self.assertEqual(cfg.throttle["pace"]["normal"]["max_active"], 7)
            self.assertEqual(cfg.throttle["aggression"]["high"]["budget_min"], 210)
            self.assertEqual(
                config.unknown_keys({"throttle": {
                    "pace": {"medium": {}, "low": {"max_actve": 2}},
                    "aggression": {"high": {"budegt_min": 3}},
                }}),
                [
                    "throttle.pace.medium",
                    "throttle.pace.low.max_actve",
                    "throttle.aggression.high.budegt_min",
                ],
            )
            for text in (
                "[throttle]\nmaintenance_labels = [1]\n",
                "[throttle.pace.low]\nmax_active = -1\n",
                "[throttle.pace.high]\nmax_active = true\n",
                "[throttle.aggression.low]\nmax_attempts = 0\n",
            ):
                with self.subTest(text=text), self.assertRaises(config.ConfigError):
                    self.load(root, text)

    def test_presets_are_isolated_between_repositories_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = self.cfg(Path(directory))
            first.throttle["pace"]["normal"]["max_active"] = 0
            first.throttle["maintenance_labels"].append("extra")
            second = self.cfg(Path(directory))
            self.assertEqual(throttle.effective(second).max_active, 2)
            self.assertEqual(second.throttle["maintenance_labels"], [])
            configured = config.throttle_settings({"pace": {"normal": {"max_active": 7}}})
            self.assertEqual(configured["pace"]["normal"]["max_active"], 7)
            self.assertEqual(throttle.effective(second).max_active, 2)

    def test_doctor_accepts_known_presets_and_refuses_invalid_presets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self.load(root, '[throttle.pace.low]\nmax_active = 0\n')
            rows = test_host_config.HostConfigTest.doctor_rows(self, cfg, {})
            self.assertEqual(rows[".factory.toml keys"]["status"], "PASS")
            for text in (
                '[throttle]\nunknown = 1\n',
                '[throttle.pace.fast]\nmax_active = 2\n',
                '[throttle.aggression.high]\nbudegt_min = 10\n',
                '[throttle.pace.low]\nmax_active = -1\n',
            ):
                (root / config.CONFIG_NAME).write_text(text)
                with (
                    self.subTest(text=text),
                    mock.patch.object(config, "repo_root", return_value=root),
                    mock.patch.object(config, "remote_slug", return_value="acme/widgets"),
                    mock.patch.object(config, "host_config", return_value={}),
                    self.assertRaises(config.ConfigError),
                ):
                    onboard.doctor(["--json"])

    def test_effective_preserves_normal_and_scopes_feature_aggression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # Tank's 2026-10-30 limits, before later capacity/budget overrides.
            cfg = self.load(
                Path(directory),
                '[dispatch]\nreview_rounds = 3\n[manager]\nrounds = 3\n',
            )
            normal = throttle.effective(cfg)
            self.assertEqual(
                (normal.max_active, normal.review_rounds, normal.manager_rounds,
                 normal.max_attempts, normal.budget_min),
                (2, 3, 3, 3, 90),
            )
            maintenance = throttle.effective(cfg, state={"mode": "maintenance", "feature": None})
            self.assertEqual(
                (maintenance.max_active, maintenance.review_rounds, maintenance.manager_rounds,
                 maintenance.max_attempts, maintenance.budget_min),
                (1, 3, 3, 3, 90),
            )
            feature = {"mode": "feature-drive", "feature": 10}
            driven = throttle.effective(cfg, 12, state=feature, parents={12: 11, 11: 10})
            self.assertEqual(
                (driven.max_active, driven.review_rounds, driven.manager_rounds,
                 driven.max_attempts, driven.budget_min),
                (2, 5, 5, 4, 150),
            )
            ordinary = throttle.effective(cfg, 99, state=feature, parents={12: 10})
            self.assertEqual(
                (ordinary.max_active, ordinary.review_rounds, ordinary.manager_rounds,
                 ordinary.max_attempts, ordinary.budget_min),
                (2, 3, 3, 3, 90),
            )
            cfg.manager_max_active_cap = 2
            self.assertEqual(throttle.effective(cfg, 10, state=feature, parents={}).max_active, 2)
            cfg.throttle["pace"]["low"]["max_active"] = 0
            self.assertEqual(throttle.effective(
                cfg, state={"mode": "maintenance", "feature": None}
            ).max_active, 0)

    def test_state_changes_are_atomic_concurrent_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.cfg(Path(directory))
            self.assertEqual(throttle.read(cfg), {"mode": "normal", "feature": None})
            with ThreadPoolExecutor(max_workers=8) as pool:
                states = list(pool.map(
                    lambda _: throttle.set_mode(cfg, "maintenance", who="alice"), range(16)
                ))
            self.assertTrue(all(
                state == {"mode": "maintenance", "feature": None} for state in states
            ))
            self.assertEqual(json.loads((cfg.factory / "mode.json").read_text()), states[0])
            rows = [
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "mode"
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                {key: rows[0][key] for key in ("who", "mode", "feature")},
                {"who": "alice", "mode": "maintenance", "feature": None},
            )
            throttle.set_mode(cfg, "normal", who="bob")
            self.assertEqual(len([
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "mode"
            ]), 2)

    def test_target_validation_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.cfg(Path(directory))
            with self.assertRaisesRegex(config.ConfigError, "not configured repository"):
                throttle.parse_target(cfg, "https://github.com/other/repo/issues/7")
            ready = {
                "number": 7,
                "state": "open",
                "html_url": "https://github.com/acme/widgets/issues/7",
                "labels": [{"name": config.LABEL_AGENT}],
            }
            with mock.patch.object(throttle, "_issue", return_value=ready):
                self.assertEqual(throttle.validate_target(cfg, "acme/widgets#7"), 7)
            for change in (
                {"state": "closed"},
                {"labels": []},
                {"pull_request": {"url": "x"}},
            ):
                with (
                    self.subTest(change=change),
                    mock.patch.object(throttle, "_issue", return_value={**ready, **change}),
                    self.assertRaises(config.ConfigError),
                ):
                    throttle.validate_target(cfg, "7")
            out = io.StringIO()
            with (
                mock.patch.object(config, "load", return_value=cfg),
                mock.patch.object(throttle, "validate_target", return_value=7),
                mock.patch.object(throttle, "_actor", return_value="alice"),
                redirect_stdout(out),
            ):
                self.assertEqual(throttle.main(["feature-drive", "#7"]), 0)
            self.assertEqual(out.getvalue(), "feature-drive #7\n")
            self.assertEqual(cli.COMMANDS["mode"][:2], ("throttle", "main"))

            with mock.patch.object(throttle, "_github", return_value={}), \
                    self.assertRaisesRegex(config.ConfigError, "invalid subissues"):
                throttle._subissues(cfg, 7)
            foreign = [[{
                "number": 8,
                "repository_url": "https://api.github.com/repos/other/repo",
            }]]
            with mock.patch.object(throttle, "_github", return_value=foreign), \
                    self.assertRaisesRegex(config.ConfigError, "foreign subissues"):
                throttle._subissues(cfg, 7)

    def test_refresh_waits_for_recursive_native_and_journal_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.cfg(Path(directory))
            throttle.set_mode(cfg, "feature-drive", 10, who="alice")
            lifecycle.append(cfg.factory / "events.jsonl", {
                "event": "issue-created", "ticket": 13, "parent": 10,
            })
            issues = {
                10: {"number": 10, "state": "closed"},
                11: {"number": 11, "state": "closed"},
                12: {"number": 12, "state": "closed"},
                13: {"number": 13, "state": "closed"},
            }
            children = {10: [11], 11: [12], 12: [], 13: []}

            def github(_cfg, endpoint, *, paginate=False):
                number = int(endpoint.split("/issues/", 1)[1].split("/", 1)[0])
                if "/sub_issues" in endpoint:
                    return [[{
                        "number": child,
                        "repository_url": "https://api.github.com/repos/acme/widgets",
                    } for child in children[number]]]
                return issues[number]

            with mock.patch.object(throttle, "_github", side_effect=github):
                issues[10]["state"] = "open"
                self.assertEqual(throttle.refresh(cfg), {
                    "mode": "feature-drive", "feature": 10,
                })
                issues[10]["state"] = "closed"
                issues[12]["state"] = "open"
                self.assertEqual(throttle.refresh(cfg)["mode"], "feature-drive")
                issues[12]["state"] = "closed"
                self.assertEqual(
                    throttle.refresh(cfg, dry_run=True),
                    {"mode": "normal", "feature": None},
                )
                self.assertEqual(throttle.read(cfg)["mode"], "feature-drive")
                self.assertEqual(throttle.refresh(cfg), {"mode": "normal", "feature": None})
            rows = [
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "mode"
            ]
            self.assertEqual([(row["mode"], row["who"]) for row in rows], [
                ("feature-drive", "alice"), ("normal", "auto"),
            ])

    def test_refresh_fails_closed_on_github_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.cfg(Path(directory))
            throttle.set_mode(cfg, "feature-drive", 10, who="alice")
            with mock.patch.object(throttle, "_issue", side_effect=config.ConfigError("offline")), \
                    self.assertRaises(config.ConfigError):
                throttle.refresh(cfg)
            self.assertEqual(throttle.read(cfg), {"mode": "feature-drive", "feature": 10})


if __name__ == "__main__":
    unittest.main()
