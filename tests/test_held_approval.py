"""Held PRs never acquire reviewer approval; releasing a hold preserves SHA binding."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle

HEAD = "a" * 40
NEXT_HEAD = "b" * 40


class HeldApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.events = root / "events.jsonl"
        self.enterContext(mock.patch.multiple(
            dispatch, cfg=config.Config(root, "acme/widgets"), REPO="acme/widgets",
            EVENTS=self.events, create=True,
        ))
        self.enterContext(mock.patch.object(lifecycle, "current", return_value=None))
        self.retain = self.enterContext(mock.patch.object(dispatch, "retain_handoff"))
        self.pr = {
            "number": 17, "state": "OPEN", "headRefOid": HEAD, "baseRefName": "main",
            "reviewDecision": "", "isDraft": False, "labels": [{"name": "factory-held"}],
            "files": [{"path": "feature.py"}],
        }
        self.labels = {"factory-held"}
        self.hold_during_edit = False
        self.push_during_edit = False
        self.github = self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.view))
        self.command = self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.edit))
        self.evidence(HEAD)

    def evidence(self, head: str) -> None:
        lifecycle.append(self.events, {
            "event": "attempt", "ticket": 7, "head": head, "actual_head": head, "gate": "PASS",
        })
        lifecycle.append(self.events, {
            "event": "review", "ticket": 7, "head": head, "actual_head": head,
            "verdict": "APPROVE", "accepted": True,
        })

    def view(self, args: list[str]) -> dict:
        self.assertEqual(args[:2], ["pr", "view"])
        self.pr["labels"] = [{"name": label} for label in sorted(self.labels)]
        return {key: self.pr[key] for key in args[args.index("--json") + 1].split(",")}

    def edit(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.assertEqual(args[:3], ["gh", "pr", "edit"])
        if "--add-label" in args:
            self.labels.add(args[args.index("--add-label") + 1])
            if self.hold_during_edit:
                self.labels.add("factory-held")
            if self.push_during_edit:
                self.pr["headRefOid"] = NEXT_HEAD
        elif "--remove-label" in args:
            self.labels.discard(args[args.index("--remove-label") + 1])
        else:
            self.fail(f"Unexpected command: {args}")
        return subprocess.CompletedProcess(args, 0, "", "")

    def approvals(self) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events) if row["event"] == "approved"]

    def assert_held_skip(self, draft: bool) -> None:
        self.pr["isDraft"] = draft
        self.assertTrue(dispatch.approve_pr(7, HEAD))  # Policy wait, not approval drift.
        self.assertNotIn(config.LABEL_APPROVED, self.labels)
        self.command.assert_not_called()
        self.assertEqual(self.approvals(), [])
        self.retain.assert_not_called()

    def test_held_draft_skips_approval(self) -> None:
        self.assert_held_skip(True)

    def test_held_non_draft_skips_approval(self) -> None:
        self.assert_held_skip(False)

    def test_unheld_pr_approves_reviewed_sha(self) -> None:
        self.labels.clear()
        self.assertTrue(dispatch.approve_pr(7, HEAD))
        self.assertIn(config.LABEL_APPROVED, self.labels)
        self.assertEqual(
            [(row["head"], row["gate_head"], row["review_head"]) for row in self.approvals()],
            [(HEAD, HEAD, HEAD)],
        )
        self.retain.assert_called_once_with(7, HEAD)

    def test_hold_release_allows_only_reviewed_sha(self) -> None:
        self.assert_held_skip(False)
        self.labels.clear()
        self.pr["headRefOid"] = NEXT_HEAD
        self.assertFalse(dispatch.approve_pr(7, HEAD))
        self.assertFalse(dispatch.approve_pr(7, NEXT_HEAD))
        self.command.assert_not_called()
        self.assertEqual(self.approvals(), [])
        self.evidence(NEXT_HEAD)
        self.assertTrue(dispatch.approve_pr(7, NEXT_HEAD))
        self.assertIn(config.LABEL_APPROVED, self.labels)
        self.assertEqual([row["head"] for row in self.approvals()], [NEXT_HEAD])

    def test_hold_release_at_same_sha_allows_approval(self) -> None:
        self.assert_held_skip(False)
        self.labels.clear()
        self.assertTrue(dispatch.approve_pr(7, HEAD))
        self.assertIn(config.LABEL_APPROVED, self.labels)
        self.assertEqual([row["head"] for row in self.approvals()], [HEAD])

    def test_racing_hold_withdraws_approval_without_approval_event(self) -> None:
        self.labels.clear()
        self.hold_during_edit = True
        self.assertTrue(dispatch.approve_pr(7, HEAD))
        self.assertIn("factory-held", self.labels)
        self.assertNotIn(config.LABEL_APPROVED, self.labels)
        self.assertEqual(self.approvals(), [])
        self.retain.assert_not_called()

    def test_racing_push_after_hold_release_withdraws_approval(self) -> None:
        self.labels.clear()
        self.push_during_edit = True
        self.assertFalse(dispatch.approve_pr(7, HEAD))
        self.assertNotIn(config.LABEL_APPROVED, self.labels)
        self.assertEqual(self.approvals(), [])
        self.retain.assert_not_called()
