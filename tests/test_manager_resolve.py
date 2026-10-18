from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, manage


class ManagerResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        dispatch.configure(config.Config(Path(self.root.name), "acme/widgets"))

    def test_parse_is_escalation_only_and_requires_evidence(self) -> None:
        output = "DECISION: RESOLVE\nAlready shipped by PR #12 at abc123."
        self.assertEqual(manage.parse(output, {})[:2], (
            "RESOLVE", "Already shipped by PR #12 at abc123.",
        ))
        self.assertEqual(manage.parse("DECISION: RESOLVE\n", {})[0], "HUMAN")
        self.assertEqual(manage.parse(output, {}, approval=True)[0], "HUMAN")

    def test_apply_comments_before_close_and_does_not_requeue(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        with mock.patch.object(dispatch, "gh_json", return_value={"state": "CLOSED"}), \
                mock.patch.object(dispatch, "run", return_value=result) as run, \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7, {"title": "Already fixed"}, "RESOLVE",
                "Present on main in abc123 via PR #12.", None, Path("unused"),
            )

        commands = [call.args[0] for call in run.call_args_list]
        comment = next(i for i, command in enumerate(commands)
                       if command[1:3] == ["issue", "comment"])
        close = commands.index([
            "gh", "issue", "close", "7", "--repo", dispatch.REPO,
            "--reason", "completed",
        ])
        self.assertLess(comment, close)
        self.assertIn("Factory manager: Resolved without a code change.", commands[comment][-1])
        self.assertFalse(any("--add-label" in command and config.LABEL_AGENT in command
                             for command in commands))

    def test_apply_refuses_open_pr_with_commits(self) -> None:
        pr = {"number": 17, "state": "OPEN", "headRefOid": "deadbeef"}
        result = subprocess.CompletedProcess([], 0, stdout="1\n")
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run", return_value=result) as run:
            with self.assertRaisesRegex(ValueError, "commits over main"):
                manage.apply(
                    7, {"title": "Not fixed"}, "RESOLVE",
                    "Claimed present on main.", None, Path("unused"),
                )

        self.assertEqual(run.call_args_list[0].args[0], [
            "git", "rev-list", "--count", "origin/main..deadbeef",
        ])
        self.assertFalse(any(call.args[0][1:3] in (["issue", "comment"], ["issue", "close"])
                             for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
