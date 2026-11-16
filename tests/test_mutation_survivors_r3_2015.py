"""Pin unclaim's retry/partitioning, github_write backoff, ticket_comment's signature and redaction."""
import inspect
import os
import subprocess
import tempfile
import types
import typing
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config

VIEW = ["issue", "view", "7", "--repo", "local/safety", "--json", "labels"]


def edit(*args):
    return ["gh", "issue", "edit", "7", "--repo", "local/safety", *args]


class Base(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        saved = getattr(dispatch, "cfg", None)
        if saved is not None:
            self.addCleanup(dispatch.configure, saved)
        dispatch.configure(Config(root=root, repo="local/safety"))
        self.sleep = self.enterContext(mock.patch.object(dispatch.time, "sleep"))
        self.record = self.enterContext(mock.patch.object(dispatch, "record"))


class UnclaimPartitionTest(Base):
    def test_only_add_label_pairs_lead_and_other_flags_follow_in_order(self):
        suffix = "label"
        add_label = f"--add-{suffix}"  # equal to, but not the same object as, the literal
        with mock.patch.object(dispatch, "github_write") as write, \
                mock.patch.object(dispatch, "gh_json") as gh_json:
            dispatch.unclaim(7, "--add-assignee", "bob", add_label, "triaged", check=True)
        gh_json.assert_not_called()
        self.assertEqual([c.args[1] for c in write.call_args_list], [
            edit("--remove-assignee", "@me", "--add-label", "triaged"),
            edit("--add-assignee", "bob"),
        ])


class UnclaimLabelsReadFailureTest(Base):
    def test_malformed_labels_read_fails_with_exit_one(self):
        with mock.patch.object(dispatch, "gh_json", side_effect=ValueError("bad json")), \
                mock.patch.object(dispatch, "github_write") as write, \
                self.assertRaises(dispatch.GitHubWriteFailed) as raised:
            dispatch.unclaim(7, "--add-label", "ready-for-human", check=True)
        write.assert_not_called()
        self.assertEqual(raised.exception.returncode, 1)
        rows = [c.kwargs for c in self.record.call_args_list if c.args == ("github-write-failed",)]
        self.assertEqual([(r["returncode"], r["attempt"]) for r in rows], [(1, 1), (1, 2)])
        self.sleep.assert_called_once_with(1)

    def retried_sleep(self, stderr):
        failure = subprocess.CalledProcessError(1, ["gh", *VIEW], stderr=stderr)
        with mock.patch.object(dispatch, "gh_json",
                               side_effect=[failure, {"labels": [{"name": "ready-for-agent"}]}]), \
                mock.patch.object(dispatch, "github_write"):
            dispatch.unclaim(7, "--add-label", "ready-for-human", check=True)
        self.assertEqual(len(self.sleep.call_args_list), 1)
        return self.sleep.call_args.args[0]

    def test_rate_limited_labels_read_waits_the_sixty_second_floor(self):
        self.assertEqual(self.retried_sleep("API rate limit exceeded"), 60)

    def test_abuse_limited_labels_read_waits_the_sixty_second_floor(self):
        self.assertEqual(self.retried_sleep("secondary abuse detection"), 60)

    def test_ordinary_labels_read_failure_waits_the_first_attempt_delay(self):
        self.assertEqual(self.retried_sleep("HTTP 502: Something went wrong"), 1)


class GitHubWriteBackoffTest(Base):
    def retried_sleep(self, stderr):
        command = ["gh", "issue", "comment", "7"]
        failure = subprocess.CalledProcessError(1, command, stderr=stderr)
        with mock.patch.object(dispatch, "run",
                               side_effect=[failure, subprocess.CompletedProcess(command, 0, "", "")]):
            dispatch.github_write(7, command)
        self.assertEqual(len(self.sleep.call_args_list), 1)
        return self.sleep.call_args.args[0]

    def test_rate_limited_write_waits_the_sixty_second_floor(self):
        self.assertEqual(self.retried_sleep("API rate limit exceeded"), 60)

    def test_ordinary_write_failure_waits_the_first_attempt_delay(self):
        self.assertEqual(self.retried_sleep("HTTP 502"), 1)


class TicketCommentSignatureTest(unittest.TestCase):
    def test_kind_is_keyword_only(self):
        kind = inspect.signature(dispatch.ticket_comment).parameters["kind"]
        self.assertIs(kind.kind, inspect.Parameter.KEYWORD_ONLY)
        with mock.patch.object(dispatch, "github_write") as write, self.assertRaises(TypeError):
            dispatch.ticket_comment(7, "reason", "exit")
        write.assert_not_called()


class RedactCommandTest(unittest.TestCase):
    def test_separate_value_hides_the_next_arg_not_the_flag(self):
        self.assertEqual(lifecycle.redact_command(["gh", "--header", "x"]), ["gh", "--header", "[REDACTED]"])

    def test_non_sensitive_assignment_is_kept(self):
        self.assertEqual(lifecycle.redact_command(["gh", "state=open"]), ["gh", "state=open"])

    def test_sensitive_assignment_hides_only_the_value(self):
        self.assertEqual(lifecycle.redact_command(["gh", "body=hi", "x"]), ["gh", "body=[REDACTED]", "x"])


class LifecycleAnnotationsTest(unittest.TestCase):
    # lifecycle uses `from __future__ import annotations`, so these hints are only strings until
    # evaluated; resolving them proves they are real `X | None` unions that typing can consume.
    def test_optional_hints_resolve_to_unions(self):
        redact = typing.get_type_hints(lifecycle.redact_command)
        done = typing.get_type_hints(lifecycle.Execution.child_done)
        for hint, expected in ((redact["redact_value"], str), (done["redact_value"], str),
                               (done["returncode"], int)):
            self.assertIsInstance(hint, types.UnionType)
            self.assertEqual(hint, expected | None)


class RefreshRequeueAssigneeOrderTest(unittest.TestCase):
    """Every refresh requeue adds the agent label and unassigns in one write, before removals."""

    def test_requeue_unassigns_with_the_agent_label_add(self):
        from tests import test_refresh_conflict as refresh

        for exit_path in ("conflict", "gate", "risk"):
            with self.subTest(exit=exit_path), ExitStack() as stack:
                harness = refresh.RefreshConflictTest() if exit_path == "conflict" else refresh.RefreshGateFailureTest()
                stack.callback(harness.doCleanups)
                harness.setUp()
                github = harness.github
                github.issue_labels = {"ready-for-human", "factory-held"}
                edits = []
                original = github.run

                def command(cmd, edits=edits, original=original, **kwargs):
                    if cmd[:3] == ["gh", "issue", "edit"]:
                        edits.append(cmd[6:])
                    return original(cmd, **kwargs)

                stack.enter_context(mock.patch.object(dispatch, "run", side_effect=command))
                stack.enter_context(mock.patch.object(dispatch, "gh_json", side_effect=github.gh_json))
                stack.enter_context(mock.patch.object(
                    dispatch, "run_gate", return_value=(exit_path != "gate", "gate report"),
                ))
                stack.enter_context(mock.patch.object(dispatch, "record_risk"))
                if exit_path == "risk":
                    stack.enter_context(mock.patch.object(dispatch, "risk_for_head", return_value=("High", [])))

                    def disagree(*args, **kwargs):
                        dispatch._PENDING_RISK[9] = {"head": args[3], "disagreement": True, "packet": "packet"}
                        return "REVISE", "packet", True

                    stack.enter_context(mock.patch.object(dispatch, "pr_gate_and_review", side_effect=disagree))
                else:
                    stack.enter_context(mock.patch.object(dispatch, "risk_for_head", return_value=("Low", [])))
                dispatch.refresh_pr_branch(9, 102, False)
                self.assertEqual(edits, [
                    ["--add-label", "ready-for-agent", "--remove-assignee", "@me"],
                    ["--remove-label", "factory-held"],
                    ["--remove-label", "ready-for-human"],
                ])


if __name__ == "__main__":
    unittest.main()
