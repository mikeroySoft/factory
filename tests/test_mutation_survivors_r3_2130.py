"""Pin child_done's keyword-only parameters and the refresh gate-requeue unclaim write path."""
import inspect
import unittest
from unittest import mock

from factory import dispatch, lifecycle


class ChildDoneSignatureTest(unittest.TestCase):
    def test_options_are_keyword_only(self):
        params = inspect.signature(lifecycle.Execution.child_done).parameters
        for name in ("command", "returncode", "redact_value"):
            self.assertIs(params[name].kind, inspect.Parameter.KEYWORD_ONLY, name)
        self.assertIsNot(params["pid"].kind, inspect.Parameter.KEYWORD_ONLY)


class RefreshGateRequeueWritePathTest(unittest.TestCase):
    """The gate-failed-twice requeue sends every label edit through github_write, unassign first."""

    def test_gate_requeue_edits_go_through_github_write(self):
        from tests import test_refresh_conflict as refresh

        harness = refresh.RefreshGateFailureTest()
        self.addCleanup(harness.doCleanups)
        harness.setUp()
        harness.github.issue_labels = {"ready-for-human", "factory-held"}
        prefix = ["gh", "issue", "edit", "9", "--repo", harness.cfg.repo]
        original = dispatch.github_write
        edits = []

        def write(n, cmd, *args, **kwargs):
            if cmd[:6] == prefix:
                edits.append(cmd[6:])
            return original(n, cmd, *args, **kwargs)

        with harness.patched(), \
                mock.patch.object(dispatch, "github_write", side_effect=write), \
                mock.patch.object(dispatch, "run_gate", side_effect=[(False, "first"), (False, "second")]):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        self.assertEqual(edits, [
            ["--add-label", "ready-for-agent", "--remove-assignee", "@me"],
            ["--remove-label", "factory-held"],
            ["--remove-label", "ready-for-human"],
        ])
        self.assertEqual(len(harness.events("gate-requeue")), 1)


if __name__ == "__main__":
    unittest.main()
