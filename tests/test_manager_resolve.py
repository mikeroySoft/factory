from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage


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
        no_pr = subprocess.CalledProcessError(1, ["gh"], stderr='no pull requests found for branch "agent/7"')
        no_branch = subprocess.CalledProcessError(1, ["gh"], stderr="gh: Not Found (HTTP 404)")
        with mock.patch.object(dispatch, "gh_json", side_effect=[no_pr, no_branch]), \
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

    def test_resolve_records_one_manager_pr_close(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        pr = {"number": 17, "state": "OPEN", "changedFiles": 0, "baseRefName": "main"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run", return_value=result), \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7, {"title": "Already fixed"}, "RESOLVE",
                "Present on main in abc123 via PR #12.", None, Path("unused"),
            )

        closes = [row for row in lifecycle.read_events(dispatch.EVENTS)
                  if row.get("event") == "pr-closed"]
        self.assertEqual([{key: row[key] for key in ("pr", "ticket", "by", "reason")}
                          for row in closes], [{
            "pr": 17, "ticket": 7, "by": "manager", "reason": "resolve",
        }])

    def test_close_records_one_manager_pr_close(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        pr = {"number": 17, "state": "OPEN", "baseRefName": "main"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run", return_value=result), \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7, {"title": "Duplicate"}, "CLOSE",
                "Duplicate of shipped work.", None, Path("unused"),
            )

        closes = [row for row in lifecycle.read_events(dispatch.EVENTS)
                  if row.get("event") == "pr-closed"]
        self.assertEqual([{key: row[key] for key in ("pr", "ticket", "by", "reason")}
                          for row in closes], [{
            "pr": 17, "ticket": 7, "by": "manager", "reason": "close",
        }])

    def test_apply_refuses_open_pr_with_changes(self) -> None:
        pr = {"number": 17, "state": "OPEN", "changedFiles": 2, "baseRefName": "main"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaisesRegex(ValueError, "with changes"):
            manage.apply(7, {"title": "Not fixed"}, "RESOLVE", "Claimed present on main.", None, Path("unused"))
        run.assert_not_called()

    def test_apply_refuses_pushed_branch_without_pr(self) -> None:
        no_pr = subprocess.CalledProcessError(1, ["gh"], stderr='no pull requests found for branch "agent/7"')
        with mock.patch.object(dispatch, "gh_json", side_effect=[no_pr, {"ahead_by": 2}]), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaisesRegex(ValueError, "no PR"):
            manage.apply(7, {"title": "Already fixed"}, "RESOLVE", "Present on main.", None, Path("unused"))
        run.assert_not_called()

    def test_apply_refuses_empty_pr_on_another_base(self) -> None:
        pr = {"number": 17, "state": "OPEN", "changedFiles": 0, "baseRefName": "release"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaisesRegex(ValueError, "another base"):
            manage.apply(7, {"title": "Already fixed"}, "RESOLVE", "Present on main.", None, Path("unused"))
        run.assert_not_called()

    def test_apply_does_not_close_when_pr_lookup_fails_for_another_reason(self) -> None:
        error = subprocess.CalledProcessError(1, ["gh"], stderr="error connecting to api.github.com")
        with mock.patch.object(dispatch, "gh_json", side_effect=error), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaises(subprocess.CalledProcessError):
            manage.apply(7, {"title": "Already fixed"}, "RESOLVE", "Present on main.", None, Path("unused"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
