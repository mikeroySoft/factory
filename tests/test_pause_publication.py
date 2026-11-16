"""Mid-flight pauses stop learn and release publication."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, pause, release, stats, triage


class PausePublicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {
            lifecycle.CONTEXT_ENV: "", "XDG_CONFIG_HOME": str(self.root / "config"),
        }))
        for module in (dispatch, stats, triage):
            self.enterContext(mock.patch.dict(module.__dict__))
        self.cfg = config.Config(
            self.root, "acme/widgets", manager=["manager"], release_min_tickets=1,
        )
        self.cfg.factory.mkdir()

    def activate(self) -> None:
        pause.activate(self.cfg, "operator hold", who="tester")

    @staticmethod
    def completed(args: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout, "")

    def test_learn_pause_after_gate_prevents_pr_and_second_proposal(self) -> None:
        def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[:3] == ["git", "worktree", "add"]:
                Path(args[-2]).mkdir(parents=True)
            head = "a" * 40 if args[1:3] == ["rev-parse", "HEAD"] else ""
            return self.completed(args, head)

        def gate(*_args: object, **_kwargs: object) -> str:
            self.activate()
            return "a" * 40

        with mock.patch.object(learn, "evidence", return_value=([1], "evidence")), \
                mock.patch.object(learn, "manager_llm", return_value=lambda _prompt: ""), \
                mock.patch.object(learn, "propose", return_value=(["Keep the gate."], "curate diff")), \
                mock.patch.object(learn, "gate_head", side_effect=gate), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "push_and_pr", return_value=False) as publish, \
                mock.patch.object(dispatch, "record") as record:
            result = learn.run(self.cfg, last=1, evaluate=False)

        self.assertIsNone(result)
        publish.assert_called_once()
        record.assert_not_called()

    def test_dispatch_promotion_pause_blocks_merge_and_stops_before_second_pr(self) -> None:
        prs = [
            {
                "number": number,
                "headRefName": f"agent/lessons-2026-11-16-t{number}",
                "headRefOid": str(number) * 40,
                "baseRefName": "main",
                "isDraft": False,
                "labels": [],
                "reviewDecision": "",
            }
            for number in (1, 2)
        ]
        with (self.cfg.factory / "events.jsonl").open("w") as events:
            for pr in prs:
                for row in (
                    {
                        "event": "gate", "branch": pr["headRefName"],
                        "head": pr["headRefOid"], "gate": "PASS",
                    },
                    {
                        "event": "review-result", "pr": pr["number"],
                        "head": pr["headRefOid"], "verdict": "APPROVE",
                    },
                ):
                    events.write(json.dumps({"at": "2026-11-16T00:00:00Z", **row}) + "\n")

        def github(args: list[str]) -> object:
            if args[0] == "pr":
                return prs
            return {"behind_by": 0, "files": [{"filename": config.LESSONS_NAME}]}

        def checks(_number: int) -> list[dict]:
            self.activate()
            return [{"name": "gate", "bucket": "pass"}]

        with mock.patch.multiple(
                dispatch, create=True, cfg=self.cfg, ROOT=self.root, FACTORY=self.cfg.factory,
                EVENTS=self.cfg.factory / "events.jsonl", REPO=self.cfg.repo), \
                mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "pr_checks", side_effect=checks) as pr_checks, \
                mock.patch.object(dispatch, "run") as run:
            dispatch.merge_pass_locked(False)

        pr_checks.assert_called_once_with(1)
        self.assertFalse(any(call.args[0][0] == "gh" for call in run.call_args_list))
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertFalse(any(row.get("event") == "merged" for row in rows))

    def release_state(self, *, pulls: list[dict]) -> tuple:
        latest = {
            "tag": "v0.0.1",
            "version": (0, 0, 1),
            "date": "2026-01-01T00:00:00Z",
            "message": "Release 0.0.1",
            "sha": "old",
        }

        def git(_root: Path, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[:2] == ("rev-parse", "origin/main"):
                stdout = "head"
            else:
                stdout = "1" if args[:2] == ("rev-list", "--count") else ""
            return self.completed(list(args), stdout)
        return (
            mock.patch.object(release, "_git", side_effect=git),
            mock.patch.object(release, "_latest", return_value=latest),
            mock.patch.object(release, "_bar", return_value=None),
            mock.patch.object(release, "_version_at", return_value=(0, 0, 1)),
            mock.patch.object(release, "measure", return_value={
                "tickets": 1, "human_rate": 0.0, "first_pass": 1.0,
                "first_passes": 1, "attempts": 1, "human_tickets": 0,
            }),
            mock.patch.object(release, "_pulls", return_value=pulls),
            mock.patch.object(release, "_unpublished", return_value=False),
        )

    def test_release_pause_during_ci_read_prevents_merge(self) -> None:
        pull = {
            "number": 7, "head": {"sha": "candidate"},
            "mergeable": True, "mergeable_state": "clean",
        }
        patches = self.release_state(pulls=[{"number": 7}])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(
                    release, "ci_passes",
                    side_effect=lambda _cfg, _sha: (self.activate() or True),
                ), \
                mock.patch.object(release, "_merge_pull") as merge:
            reason = release.run(self.cfg, open_pr=False)

        self.assertTrue(reason.startswith("paused (operator hold,"))
        merge.assert_not_called()

    def test_release_pause_during_gate_prevents_branch_push_and_pr(self) -> None:
        patches = self.release_state(pulls=[])
        with patches[0] as git, patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_update_release_files"), \
                mock.patch.object(
                    release, "gate_passes",
                    side_effect=lambda _worktree: (self.activate() or True),
                ), \
                mock.patch.object(release, "_open_pull") as open_pull:
            reason = release.run(self.cfg)

        self.assertTrue(reason.startswith("paused (operator hold,"))
        self.assertFalse(any("push" in call.args[1] for call in git.call_args_list))
        open_pull.assert_not_called()


if __name__ == "__main__":
    unittest.main()
