"""Release failures fail the dispatcher pass without stopping its other work."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, manage
from tests import test_factory


class ReleaseFailurePassTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.repo = test_factory.make_repo(Path(temp.name))
        self.cfg = config.load(self.repo)
        self.cfg.release_auto = True
        dispatch.configure(self.cfg)
        self.logs: list[str] = []
        self.enterContext(mock.patch.object(dispatch, "log", side_effect=self.logs.append))

    def run_pass(self, release_results, *, stale: bool = False, argv: list[str] | None = None):
        installed = "a" * 40
        target = "b" * 40 if stale else installed
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(config, "engine_commit", return_value=installed),
            mock.patch.object(config, "engine_target", return_value=target),
            mock.patch.object(dispatch, "pin_root", return_value=[]),
            mock.patch.object(dispatch, "github_preflight", return_value=None),
            mock.patch.object(dispatch, "_sweep_stale_factory_tempdirs"),
            mock.patch.object(dispatch, "worker_health_pass"),
            mock.patch("factory.results.prune", return_value={"status": "complete"}),
            mock.patch.object(dispatch, "land_pass") as land,
            mock.patch.object(dispatch, "review_intake_pass") as review,
            mock.patch.object(manage, "manage_pass") as manage_pass,
            mock.patch.object(dispatch, "ticket_pass") as ticket,
            mock.patch.object(learn, "learn_pass") as learn_pass,
            mock.patch("factory.release.run", side_effect=release_results) as release_run,
            mock.patch.object(lifecycle, "record_stage_success") as stage_success,
        ):
            result = dispatch.main(argv or [])
        return result, release_run, stage_success, land, review, manage_pass, ticket, learn_pass

    def assert_failed_lifecycle(self) -> None:
        execution = next(
            item for item in lifecycle.observe(self.cfg.factory / "events.jsonl")
            if item["stage"] == "dispatcher"
        )
        self.assertEqual(
            (execution["state"], execution["outcome"], execution["reason"]),
            ("failed", "mechanism_failure", "release_failed"),
        )

    def test_start_release_failure_finishes_scheduled_work_and_fails_pass(self) -> None:
        result, release_run, stage_success, land, review, manage_pass, ticket, learn_pass = self.run_pass(
            [RuntimeError("start broke"), "end ok"]
        )

        self.assertEqual(result, 1)
        self.assertEqual(
            release_run.call_args_list,
            [mock.call(self.cfg, open_pr=False), mock.call(self.cfg, open_pr=True)],
        )
        for scheduled in (land, review, manage_pass, ticket, learn_pass):
            scheduled.assert_called_once_with(False)
        stage_success.assert_not_called()
        self.assert_failed_lifecycle()

    def test_end_release_failure_fails_completed_pass(self) -> None:
        result, release_run, stage_success, land, review, manage_pass, ticket, learn_pass = self.run_pass(
            ["start ok", RuntimeError("end broke")]
        )

        self.assertEqual(result, 1)
        self.assertEqual(release_run.call_count, 2)
        for scheduled in (land, review, manage_pass, ticket, learn_pass):
            scheduled.assert_called_once_with(False)
        stage_success.assert_not_called()
        self.assert_failed_lifecycle()

    def test_release_failure_survives_engine_yield(self) -> None:
        self.cfg.engine_yield_for_update = True
        result, release_run, stage_success, land, review, manage_pass, ticket, learn_pass = self.run_pass(
            [RuntimeError("start broke")], stale=True
        )

        self.assertEqual(result, 1)
        release_run.assert_called_once_with(self.cfg, open_pr=False)
        land.assert_called_once_with(False)
        review.assert_called_once_with(False)
        for deferred in (manage_pass, ticket, learn_pass):
            deferred.assert_not_called()
        stage_success.assert_not_called()
        self.assert_failed_lifecycle()

    def test_successful_release_pass_is_unchanged(self) -> None:
        result, release_run, stage_success, *_ = self.run_pass(["start ok", "end ok"])

        self.assertEqual(result, 0)
        self.assertEqual(release_run.call_count, 2)
        stage_success.assert_called_once_with(self.cfg.factory / "events.jsonl", "dispatch")

    def test_other_release_host_is_still_a_successful_skip(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.release_host = "tank"
        result, release_run, stage_success, *_ = self.run_pass([])

        self.assertEqual(result, 0)
        release_run.assert_not_called()
        stage_success.assert_called_once_with(self.cfg.factory / "events.jsonl", "dispatch")
        self.assertEqual(self.logs.count("release: skipped, release host is tank"), 2)

    def test_dry_run_still_skips_release_and_success_receipt(self) -> None:
        result, release_run, stage_success, land, review, manage_pass, ticket, learn_pass = self.run_pass(
            [], argv=["--dry-run"]
        )

        self.assertEqual(result, 0)
        release_run.assert_not_called()
        stage_success.assert_not_called()
        for scheduled in (land, review, manage_pass, ticket, learn_pass):
            scheduled.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
