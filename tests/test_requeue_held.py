"""Explicit held-ticket requeues stay inside the guarded issue-only decision."""
from __future__ import annotations

import copy
import unittest

from factory import config, decisions, dispatch
from tests import test_actions as action_suite


class HeldRequeueTest(unittest.TestCase):
    setUp = action_suite.ActionPathsTest.setUp
    via_mcp = action_suite.ActionPathsTest.via_mcp
    apply_mcp = action_suite.ActionPathsTest.apply_mcp
    via_dashboard = action_suite.ActionPathsTest.via_dashboard
    refusals = action_suite.ActionPathsTest.refusals
    journal = action_suite.ActionPathsTest.journal

    def transport(self, argv: list[str], **kwargs):
        # No PR reads or writes are part of this action, including indirect API edits.
        self.assertTrue(argv[:3] in (["gh", "issue", "view"], ["gh", "issue", "edit"],
                                    ["gh", "issue", "comment"]) or argv == [
                                        "gh", "api", "--hostname", "github.com", "--method", "GET", "user", "--jq", ".login"])
        result = action_suite.ActionPathsTest.transport(self, argv, **kwargs)
        if argv[:3] == ["gh", "issue", "edit"]:
            labels = {label["name"] for label in self.issue["labels"]}
            for index, option in enumerate(argv[:-1]):
                value = argv[index + 1]
                if option == "--add-label":
                    labels.add(value)
                elif option == "--remove-label":
                    labels.discard(value)
                elif option == "--remove-assignee":
                    self.issue["assignees"] = [person for person in self.issue["assignees"] if person["login"] != value]
            self.issue["labels"] = [{"name": name} for name in sorted(labels)]
        return result

    def park(self):
        self.issue["labels"] = [{"name": "factory-held"}, {"name": "host:tank"}]

    def test_explicit_held_requeue_applies_comment_unassignment_and_labels_without_pr_calls(self):
        self.park()
        pr = {"headRefName": "agent/7", "isDraft": True, "labels": [{"name": "factory-held"}]}
        before = copy.deepcopy(pr)
        args = {"number": 7, "from_held": True, "comment": "Resume the parked work"}
        preview = self.via_mcp("requeue_agent", args)
        self.assertNotIn("error", preview, preview)
        self.assertEqual(self.mutations, [])
        self.assertEqual(preview["action"]["args"], args)
        self.assertEqual(preview["targets"][0]["kind"], "issue")
        receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual({label["name"] for label in self.issue["labels"]}, {config.LABEL_AGENT, "host:tank"})
        self.assertEqual(self.issue["assignees"], [])
        self.assertEqual([command[:3] for command in self.mutations], [["gh", "issue", "comment"], ["gh", "issue", "edit"]])
        comment, edit = self.mutations
        self.assertEqual(comment[comment.index("--body") + 1], args["comment"])
        self.assertEqual(edit.count("--remove-label"), 2)
        self.assertEqual(pr, before)
        self.assertTrue(pr["isDraft"])
        self.assertNotIn({"name": "factory-approved"}, pr["labels"])
        self.assertEqual(self.journal()[-1]["action"]["args"], args)
        self.assertEqual(self.apply_mcp(preview), receipt)
        self.assertEqual(len(self.mutations), 2)

    def test_held_requires_true_opt_in_and_the_held_label(self):
        self.park()
        for extra in ({}, {"from_held": False}):
            with self.subTest(extra=extra):
                self.assertEqual(self.refusals("requeue_agent", {"number": 7, **extra}),
                                 ("precondition_failed", "issue is not ready-for-human"))
        self.issue["labels"] = [{"name": "host:tank"}]
        self.assertEqual(self.refusals("requeue_agent", {"number": 7, "from_held": True}),
                         ("precondition_failed", "issue is not ready-for-human"))

    def test_human_requeue_accepts_either_flag_value_and_preserves_other_assignees(self):
        for extra in ({}, {"from_held": False}, {"from_held": True}):
            with self.subTest(extra=extra):
                self.issue["labels"] = [{"name": config.LABEL_HUMAN}, {"name": "factory-held"}, {"name": "host:tank"}]
                self.issue["assignees"] = [{"login": "alice"}, {"login": "bob"}]
                preview = self.via_mcp("requeue_agent", {"number": 7, **extra})
                self.assertNotIn("error", preview, preview)
                self.assertEqual(self.apply_mcp(preview)["status"], "success")
                expected = {config.LABEL_AGENT, "host:tank"}
                if not extra.get("from_held"):
                    expected.add("factory-held")
                self.assertEqual({label["name"] for label in self.issue["labels"]}, expected)
                self.assertEqual(self.issue["assignees"], [{"login": "bob"}])

    def test_opt_in_keeps_closed_sync_and_initiative_refusals(self):
        self.park()
        for changes, code in (({"state": "CLOSED"}, "precondition_failed"),
                              ({"title": dispatch.SYNC_TITLE + "main drifted"}, "routing_refused"),
                              ({"labels": [{"name": "factory-held"}, {"name": config.LABEL_INITIATIVE}]}, "routing_refused")):
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.issue)
                self.issue.update(changes)
                self.assertEqual(self.refusals("requeue_agent", {"number": 7, "from_held": True})[0], code)
                self.issue = original

    def test_from_held_is_strictly_boolean(self):
        self.park()
        for value in (0, 1, "true", "false", None, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.refusals("requeue_agent", {"number": 7, "from_held": value})[0], "invalid_args")

    def test_hold_change_after_prepare_refuses_stale_proposal(self):
        self.park()
        preview = self.via_mcp("requeue_agent", {"number": 7, "from_held": True})
        self.assertNotIn("error", preview, preview)
        self.issue["labels"] = [{"name": "host:tank"}]
        receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["error"]["code"], "stale_target")
        self.assertEqual(self.mutations, [])


if __name__ == "__main__":
    unittest.main()
