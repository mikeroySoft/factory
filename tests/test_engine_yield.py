"""#539: stale shared engines finish intake without admitting more work."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle

INSTALLED = "a" * 40
TARGET = "b" * 40
SWITCHED = "c" * 40


class EngineYieldTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "alpha"
        self.root.mkdir()
        self.host = self.base / "host/factory/config.toml"
        self.host.parent.mkdir(parents=True)
        self.enterContext(patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.base / "host"), lifecycle.CONTEXT_ENV: "",
        }))
        self.enterContext(patch.dict(dispatch.__dict__))
        self.enterContext(patch.object(config, "repo_root", side_effect=lambda *_: self.root))
        self.enterContext(patch.object(dispatch, "pin_root", return_value=[]))
        self.enterContext(patch.object(dispatch, "github_preflight", return_value=None))
        self.stages = []
        self.enterContext(patch.object(dispatch, "land_pass", side_effect=lambda _: self.stages.append("land")))
        self.enterContext(patch.object(dispatch, "review_intake_pass", side_effect=lambda _: self.stages.append("review")))
        self.enterContext(patch("factory.manage.manage_pass", side_effect=lambda _: self.stages.append("manage")))
        self.enterContext(patch("factory.learn.learn_pass", side_effect=lambda _: self.stages.append("learn")))
        self.issue = {"number": 7, "title": "ready ticket", "body": "", "labels": [], "assignees": []}
        self.frontier = self.enterContext(patch.object(dispatch, "frontier", return_value=[self.issue]))
        self.process = self.enterContext(patch.object(dispatch, "process_ticket", return_value=None))
        self.enterContext(patch.object(dispatch, "issue_is_open", return_value=True))
        self.enterContext(patch.object(dispatch, "gh_json", return_value=self.issue))
        self.repo_policy(True)
        self.host_state()

    def repo_policy(self, enabled):
        (self.root / ".factory.toml").write_text(
            f'[repo]\nslug = "local/{self.root.name}"\n[dispatch]\nmax_active = 1\n'
            f'[engine]\nyield_for_update = {str(enabled).lower()}\n'
        )

    def host_state(self, installed=INSTALLED, target=TARGET, ref="main", policy=None):
        text = f'[defaults]\nfactory_ref = "{ref}"\n[defaults.engine]\nref = "old-channel"\n'
        if installed is not None:
            text += f'sha = "{installed}"\n'
        if target is not None:
            text += f'target = "{target}"\ntarget_ref = "{ref}"\n'
        if policy is not None:
            text += f'yield_for_update = {str(policy).lower()}\n'
        self.host.write_text(text)

    def stale_rows(self):
        return [row for row in lifecycle.read_events(self.root / ".factory/events.jsonl")
                if row.get("event") == "engine-stale"]

    def test_stale_engine_yields_without_claiming(self):
        self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.stages, ["land", "review"])
        self.frontier.assert_not_called()
        self.process.assert_not_called()
        [row] = self.stale_rows()
        self.assertEqual((row["installed_commit"], row["target_commit"]), (INSTALLED, TARGET))

    def test_current_engine_runs_normal_pass(self):
        self.host_state(target=INSTALLED)
        self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.stages, ["land", "review", "manage", "learn"])
        self.process.assert_called_once()
        self.assertEqual(self.process.call_args.args[0]["number"], 7)
        self.assertEqual(self.stale_rows(), [])

    def test_disabled_policy_signals_but_still_admits_work(self):
        self.repo_policy(False)
        self.assertEqual(dispatch.main([]), 0)
        self.process.assert_called_once()
        [row] = self.stale_rows()
        self.assertEqual(row["target_commit"], TARGET)

    def test_omitted_policy_signals_without_yielding(self):
        (self.root / ".factory.toml").write_text(
            f'[repo]\nslug = "local/{self.root.name}"\n[dispatch]\nmax_active = 1\n'
        )
        self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.stages, ["land", "review", "manage", "learn"])
        self.process.assert_called_once()
        [row] = self.stale_rows()
        self.assertIs(row["yield_for_update"], False)

    def test_older_target_still_yields(self):
        self.host_state(installed=TARGET, target=INSTALLED)
        self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.stages, ["land", "review"])
        self.frontier.assert_not_called()
        self.process.assert_not_called()
        [row] = self.stale_rows()
        self.assertEqual((row["installed_commit"], row["target_commit"]), (TARGET, INSTALLED))

    def test_ref_switch_uses_district_recorded_target_not_repo_main(self):
        self.assertEqual(dispatch.main([]), 0)
        self.host_state(target=SWITCHED, ref="release-candidate")
        self.assertEqual(dispatch.main([]), 0)
        self.assertEqual([row["target_commit"] for row in self.stale_rows()], [TARGET, SWITCHED])
        self.process.assert_not_called()
        self.host_state(installed=SWITCHED, target=SWITCHED, ref="release-candidate")
        self.assertEqual(dispatch.main([]), 0)
        self.process.assert_called_once()

    def test_shared_host_policy_yields_every_service_despite_repo_opt_out(self):
        self.host_state(policy=True)
        for name in ("alpha", "beta"):
            with self.subTest(service=f"factory-{name}.service"):
                self.root = self.base / name
                self.root.mkdir(exist_ok=True)
                self.repo_policy(False)
                self.assertEqual(dispatch.main([]), 0)
                [row] = self.stale_rows()
                self.assertEqual((row["installed_commit"], row["target_commit"]), (INSTALLED, TARGET))
        self.assertEqual(self.stages, ["land", "review"] * 2)
        self.frontier.assert_not_called()
        self.process.assert_not_called()

    def test_forced_ticket_cannot_bypass_host_yield(self):
        self.assertEqual(dispatch.main(["--ticket", "7"]), 0)
        self.assertEqual(self.stages, ["land", "review"])
        self.process.assert_not_called()
        self.assertEqual(len(self.stale_rows()), 1)

    def test_dry_run_has_no_event_or_state_writes(self):
        self.assertEqual(dispatch.main(["--dry-run"]), 0)
        self.process.assert_not_called()
        self.assertFalse((self.root / ".factory").exists())

    def test_missing_or_invalid_target_does_not_invent_staleness(self):
        for target in (None, "", "pending", "b" * 7):
            with self.subTest(target=target):
                self.host_state(target=target)
                self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.process.call_count, 4)
        self.assertEqual(self.stale_rows(), [])

    def test_unknown_installed_commit_does_not_invent_staleness(self):
        self.host_state(installed=None)
        with patch.object(config.metadata, "distribution", side_effect=config.metadata.PackageNotFoundError):
            self.assertEqual(dispatch.main([]), 0)
        self.process.assert_called_once()
        self.assertEqual(self.stale_rows(), [])

    def test_policy_requires_boolean(self):
        for value in ('"true"', "1"):
            with self.subTest(value=value):
                self.host.write_text(f"[defaults.engine]\nyield_for_update = {value}\n")
                with self.assertRaisesRegex(config.ConfigError, "engine.yield_for_update must be true or false"):
                    config.load()


if __name__ == "__main__":
    unittest.main()
