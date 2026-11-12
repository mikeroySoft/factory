"""#723: release_host and triage_host gates for review-intake, manager and learn."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, manage
from tests import test_factory
from tests import test_host_config as host_fixture


class ReleaseHostConfigTest(unittest.TestCase):
    def test_host_layer_override_and_doctor(self) -> None:
        host = {
            "defaults": {"host_name": "ultron", "release_host": "default"},
            "repo": {"acme/widgets": {"release_host": "tank"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            cfg = host_fixture.HostConfigTest.load(self, Path(directory), host)
            self.assertEqual(cfg.release_host, "tank")
            rows = host_fixture.HostConfigTest.doctor_rows(self, cfg, host)
        self.assertEqual(
            rows["release_host"],
            {"label": "release_host", "status": "INFO", "detail": "tank"},
        )

    def test_invalid_release_host_is_rejected(self) -> None:
        for value in ("", "tank two", "-tank", "a" * 46, 1, False):
            with (
                self.subTest(value=value),
                tempfile.TemporaryDirectory() as directory,
                self.assertRaisesRegex(config.ConfigError, "release_host"),
            ):
                host_fixture.HostConfigTest.load(
                    self, Path(directory), {"defaults": {"release_host": value}}
                )

    def test_release_host_is_host_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            config.ConfigError, "host-only"
        ):
            host_fixture.HostConfigTest.load(
                self,
                Path(directory),
                {},
                'release_host = "tank"\n[repo]\nslug = "acme/widgets"\n',
            )


class HostGatedLoopsTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.repo = test_factory.make_repo(Path(temp.name))
        self.cfg = config.load(self.repo)
        self.cfg.manager = ["true"]
        dispatch.configure(self.cfg)
        self.logs: list[str] = []
        self.enterContext(
            mock.patch.object(dispatch, "log", side_effect=lambda msg: self.logs.append(msg))
        )

    def _fail_github(self, *args, **kwargs):
        self.fail(f"unexpected GitHub or run call: args={args!r} kwargs={kwargs!r}")

    def test_non_release_host_skips_release_without_git_or_github(self) -> None:
        self.cfg.release_auto = True
        self.cfg.host_name = "ultron"
        self.cfg.release_host = "tank"
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(dispatch, "pin_root", return_value=[]),
            mock.patch.object(dispatch, "land_pass"),
            mock.patch.object(dispatch, "review_intake_pass"),
            mock.patch.object(dispatch, "frontier", return_value=[]),
            mock.patch.object(manage, "manage_pass"),
            mock.patch.object(learn, "learn_pass"),
            mock.patch.object(dispatch, "gh_json", side_effect=self._fail_github),
            mock.patch.object(dispatch, "run", side_effect=self._fail_github),
            mock.patch("factory.release.run") as release_run,
        ):
            self.assertEqual(dispatch.main([]), 0)
        release_run.assert_not_called()
        self.assertTrue(any("release: skipped, release host is tank" in msg for msg in self.logs))

    def test_release_host_runs_and_single_host_path_is_unchanged(self) -> None:
        self.cfg.release_auto = True
        for host_name, release_host in (("tank", "tank"), (None, "tank"), (None, None)):
            with self.subTest(host_name=host_name, release_host=release_host):
                self.cfg.host_name = host_name
                self.cfg.release_host = release_host
                self.logs.clear()
                with (
                    mock.patch.object(config, "load", return_value=self.cfg),
                    mock.patch.object(dispatch, "pin_root", return_value=[]),
                    mock.patch.object(dispatch, "land_pass"),
                    mock.patch.object(dispatch, "review_intake_pass"),
                    mock.patch.object(dispatch, "frontier", return_value=[]),
                    mock.patch.object(manage, "manage_pass"),
                    mock.patch.object(learn, "learn_pass"),
                    mock.patch("factory.release.run", return_value="ok") as release_run,
                ):
                    self.assertEqual(dispatch.main([]), 0)
                self.assertEqual(release_run.call_count, 2)  # start and end of the pass
                self.assertFalse(any("release: skipped" in msg for msg in self.logs))

    def test_non_triage_host_skips_review_intake_without_github(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        with mock.patch.object(dispatch, "gh_json", side_effect=self._fail_github) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_not_called()
        self.assertEqual(self.logs, ["review-intake: skipped, triage host is tank"])

    def test_triage_host_reviews_intake(self) -> None:
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "tank"
        with mock.patch.object(dispatch, "gh_json", return_value=[]) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_called()
        self.assertFalse(any("review-intake: skipped" in msg for msg in self.logs))

    def test_non_triage_host_skips_manager_rounds_but_still_runs_handoffs(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(manage.handoff, "handoff_pass") as handoff_pass,
        ):
            manage.manage_pass(False)
        viability.assert_not_called()
        frontier.assert_not_called()
        escalation.assert_not_called()
        handoff_pass.assert_called_once_with(False)
        self.assertEqual(self.logs, ["manage: skipped, triage host is tank"])

    def test_triage_host_runs_manager(self) -> None:
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(manage.handoff, "handoff_pass") as handoff_pass,
        ):
            manage.manage_pass(False)
        viability.assert_called_once_with(False)
        frontier.assert_called_once_with(False)
        escalation.assert_called_once_with(False)
        handoff_pass.assert_called_once_with(False)

    def test_non_triage_host_skips_learn_without_side_effects(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn") as auto_learn,
            mock.patch.object(learn, "decide_promotions") as decide,
        ):
            learn.learn_pass(False)
        score.assert_not_called()
        auto_learn.assert_not_called()
        decide.assert_not_called()
        self.assertEqual(self.logs, ["learn: skipped, triage host is tank"])

    def test_triage_host_runs_learn(self) -> None:
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn") as auto_learn,
            mock.patch.object(learn, "decide_promotions") as decide,
        ):
            learn.learn_pass(False)
        score.assert_called_once()
        auto_learn.assert_called_once()
        decide.assert_called_once()

    def test_single_host_without_host_name_still_runs_gated_loops(self) -> None:
        self.cfg.host_name = None
        self.cfg.triage_host = "tank"
        self.cfg.release_host = "tank"
        with mock.patch.object(dispatch, "gh_json", return_value=[]) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_called()
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage.handoff, "handoff_pass"),
            mock.patch.object(manage, "frontier_pass"),
            mock.patch.object(manage, "escalation_pass"),
        ):
            manage.manage_pass(False)
        viability.assert_called_once()
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn"),
            mock.patch.object(learn, "decide_promotions"),
        ):
            learn.learn_pass(False)
        score.assert_called_once()


if __name__ == "__main__":
    unittest.main()
