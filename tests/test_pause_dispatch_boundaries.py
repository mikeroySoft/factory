"""Pause arriving during a pass must prevent the next external mutation."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, pause


class PauseDispatchBoundariesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.root / "config"), lifecycle.CONTEXT_ENV: "",
        }))
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        self.cfg = config.Config(self.root, "local/pause", workers={"default": [sys.executable]})
        dispatch.configure(self.cfg)
        self.output = self.enterContext(redirect_stdout(io.StringIO()))

    def hold(self) -> None:
        pause.activate(self.cfg, "operator hold", who="tester")

    def test_pause_during_push_prevents_new_pr(self) -> None:
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ["git", "push"]:
                self.hold()
            return subprocess.CompletedProcess(argv, 0, "1\n", "")

        with (
            mock.patch.object(dispatch, "run", side_effect=run),
            mock.patch.object(dispatch, "gh_json", return_value=[]),
        ):
            self.assertFalse(dispatch.push_and_pr(self.root, "agent/7", "title", "body"))
        self.assertFalse(any(argv[:3] == ["gh", "pr", "create"] for argv in calls))

    def test_explicit_ticket_parks_on_a_new_pause_and_releases_claim(self) -> None:
        issue = {
            "number": 7, "title": "local ticket", "state": "OPEN", "body": "",
            "labels": [{"name": "ready-for-agent"}], "assignees": [], "comments": [],
        }
        wt = self.cfg.factory / "wt-7"
        wt.mkdir(parents=True)

        def worker(*_args, **_kwargs):
            self.hold()
            return True, "PASS", None, "a" * 40

        with (
            mock.patch.object(dispatch, "gh_json", return_value=issue),
            mock.patch.object(dispatch, "admit_plan", return_value=None),
            mock.patch.object(dispatch, "record_worker_selection", return_value="default"),
            mock.patch.object(dispatch, "ensure_worktree", return_value=wt),
            mock.patch.object(dispatch, "agent_lease", return_value="a" * 40),
            mock.patch.object(dispatch, "refresh_requeue_context", return_value=""),
            mock.patch.object(dispatch, "sync_staged", return_value=None),
            mock.patch("factory.schedule.priority", return_value=("normal", "default")),
            mock.patch.object(dispatch, "worker_round", side_effect=worker),
            mock.patch.object(dispatch, "push_and_pr") as publish,
            mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "ok", False)),
            mock.patch.object(dispatch, "pr_comment"),
            mock.patch.object(dispatch, "approve_pr", return_value=True),
            mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "1\n", "")) as run,
        ):
            dispatch.process_ticket(issue, 5, False, forced=True)
        publish.assert_not_called()
        self.assertIn(mock.call([
            "gh", "issue", "edit", "7", "--repo", self.cfg.repo,
            "--remove-assignee", "@me",
        ], check=False), run.call_args_list)
        self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))
        self.assertIn("parked and unclaimed", self.output.getvalue())

    def test_paused_direct_landing_never_stages_sync(self) -> None:
        self.hold()
        with mock.patch.object(dispatch, "sync_pass") as sync:
            self.assertFalse(dispatch.land_pass(False))
        sync.assert_not_called()

    def test_paused_direct_review_intake_never_reads_github(self) -> None:
        self.hold()
        with mock.patch.object(dispatch, "gh_json") as github:
            dispatch.review_intake_pass(False)
        github.assert_not_called()

    def test_pause_resume_preserve_pace_configuration(self) -> None:
        # #442 owns pace semantics; pause only owns its separate local state file.
        path = self.root / ".factory.toml"
        for pace in (0, 3):
            with self.subTest(pace=pace):
                contents = f'[repo]\nslug = "local/pause"\n[dispatch]\npace = {pace}\n'
                path.write_text(contents)
                self.assertIsNone(pause.read(self.cfg))
                self.hold()
                self.assertEqual(path.read_text(), contents)
                pause.resume(self.cfg, who="tester")
                self.assertEqual(path.read_text(), contents)
                self.assertIsNone(pause.read(self.cfg))


if __name__ == "__main__":
    unittest.main()
