"""Dispatcher startup checks the current repository, never a prior pass's config."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, pause


class DispatchPauseStartupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        (self.repo / ".factory.toml").write_text('[repo]\nslug = "local/current"\n')
        self.cfg = config.Config(self.repo, "local/current")
        self.previous = config.Config(self.base / "previous", "local/previous")
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        self.enterContext(contextlib.chdir(self.repo))
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_previous_paused_repository_cannot_block_current_ticket(self) -> None:
        pause.activate(self.previous, "previous hold", who="tester")
        dispatch.configure(self.previous)
        for options in ([], ["--dry-run"]):
            with (
                self.subTest(options=options),
                mock.patch.object(config, "load", return_value=self.cfg),
                mock.patch.object(dispatch, "pin_root", return_value=[]),
                mock.patch.object(dispatch, "issue_is_open", return_value=True),
                mock.patch.object(dispatch, "gh_json", return_value={"number": 7}),
                mock.patch.object(dispatch, "process_ticket") as process,
            ):
                dispatch.configure(self.previous)
                self.assertEqual(dispatch.main(["--ticket", "7", *options]), 0)
                process.assert_called_once()

    def test_current_pause_refuses_before_pin_mutations_or_config_loading(self) -> None:
        pause.activate(self.cfg, "current hold", who="tester")
        dispatch.configure(self.previous)
        for options, expected in (([], 0), (["--ticket", "7"], 1)):
            with (
                self.subTest(options=options),
                mock.patch.object(dispatch, "run", side_effect=AssertionError("pin mutated repo")),
                mock.patch.object(config, "load", side_effect=AssertionError("config loaded")),
                mock.patch.object(dispatch, "process_ticket") as process,
            ):
                self.assertEqual(dispatch.main(options), expected)
                process.assert_not_called()
        self.assertEqual(self.output.getvalue().count("paused (current hold,"), 2)

    def test_dry_run_ticket_uses_current_pause_and_refuses_with_force_hint(self) -> None:
        pause.activate(self.cfg, "current hold", who="tester")
        dispatch.configure(self.previous)
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(dispatch, "pin_root") as pin,
            mock.patch.object(dispatch, "gh_json") as github,
        ):
            self.assertEqual(dispatch.main(["--ticket", "7", "--dry-run"]), 1)
        pin.assert_not_called()
        github.assert_not_called()
        self.assertIn("paused (current hold,", self.output.getvalue())
        self.assertIn("--ticket refused (use --force)", self.output.getvalue())

    def test_force_allows_pinning_and_processing_only_the_requested_ticket(self) -> None:
        pause.activate(self.cfg, "current hold", who="tester")
        dispatch.configure(self.previous)
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess(
                [], 0, "", "",
            )) as run,
            mock.patch.object(dispatch, "issue_is_open", return_value=True),
            mock.patch.object(dispatch, "gh_json", return_value={"number": 7}),
            mock.patch.object(dispatch, "process_ticket") as process,
            mock.patch.object(dispatch, "land_pass") as land,
            mock.patch.object(dispatch, "ticket_pass") as tickets,
        ):
            self.assertEqual(dispatch.main(["--ticket", "7", "--force"]), 0)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["git", "fetch", "origin", "main"],
            ["git", "status", "--porcelain", "--", ".", ":!.factory"],
            ["git", "checkout", "--quiet", "--detach", "origin/main"],
        ])
        process.assert_called_once_with({"number": 7}, self.cfg.budget_min, False, forced=True)
        land.assert_not_called()
        tickets.assert_not_called()
        self.assertEqual(pause.read(self.cfg)["reason"], "current hold")

    def test_first_invocation_uses_loaded_config_after_pinning(self) -> None:
        dispatch.__dict__.pop("cfg", None)
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(config, "repo_root", side_effect=AssertionError("extra discovery")),
            mock.patch.object(dispatch, "pin_root", return_value=[]),
            mock.patch.object(dispatch, "issue_is_open", return_value=True),
            mock.patch.object(dispatch, "gh_json", return_value={"number": 7}),
            mock.patch.object(dispatch, "process_ticket") as process,
        ):
            self.assertEqual(dispatch.main(["--ticket", "7"]), 0)
            process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
