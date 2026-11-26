"""Publication regressions for retained PRs whose remote branch was deleted."""
from __future__ import annotations

import subprocess
import unittest

from factory.config import LABEL_APPROVED
from tests import test_dispatch_requeue_survivors as publication


class DeletedBranchRemote(publication.PublicationRemote):
    """GitHub refuses to reopen a PR until its deleted head branch exists."""

    def __init__(self, prs: list[dict]) -> None:
        super().__init__(prs)
        self.remote_head = ""
        self.fail_push = False
        self.reopen_attempts = 0

    def gh_json(self, args):
        if args[:2] == ["pr", "list"] and "--state" in args:
            state = args[args.index("--state") + 1].upper()
            head = args[args.index("--head") + 1]
            return self._json([
                pr for pr in self.prs.values()
                if pr["state"] == state and pr["headRefName"] == head
            ])
        return super().gh_json(args)

    def run(self, command, *args, **kwargs):
        if command[:2] == ["git", "push"] and self.fail_push:
            return subprocess.CompletedProcess(command, 1, "", "push refused")
        if command[:3] == ["gh", "pr", "reopen"]:
            self.reopen_attempts += 1
            if not self.remote_head:
                raise subprocess.CalledProcessError(
                    1, command, stderr="head branch was deleted",
                )
        return super().run(command, *args, **kwargs)


class DeletedBranchPublicationTest(unittest.TestCase):
    setUp = publication.DispatchRequeuePublicationTest.setUp
    opened_prs = publication.DispatchRequeuePublicationTest.opened_prs

    def remote(self) -> DeletedBranchRemote:
        return DeletedBranchRemote([{
            "number": 42,
            "state": "CLOSED",
            "headRefName": "agent/7",
            "baseRefName": "main",
            "labels": [LABEL_APPROVED],
            "reviewDecision": "CHANGES_REQUESTED",
        }])

    def publish(self, remote: DeletedBranchRemote) -> bool:
        self.module.gh_json = remote.gh_json
        self.module.run = remote.run
        return self.module.push_and_pr(
            self.worktree, "agent/7", "title", "body", ticket=7, lease="",
        )

    def test_deleted_head_is_restored_before_retained_pr_is_reopened(self) -> None:
        remote = self.remote()

        self.assertTrue(self.publish(remote))

        self.assertEqual(remote.remote_head, remote.head)
        self.assertEqual(remote.reopen_attempts, 1)
        self.assertEqual(set(remote.prs), {42})
        self.assertEqual(remote.prs[42]["state"], "OPEN")
        self.assertEqual(remote.prs[42]["reviewDecision"], "CHANGES_REQUESTED")
        self.assertEqual(remote.labels[42], set())
        self.assertEqual(
            [pr["number"] for pr in self.opened_prs(remote, "agent/7")], [42],
        )

    def test_failed_branch_restore_never_reopens_retained_pr(self) -> None:
        remote = self.remote()
        remote.fail_push = True

        with self.assertRaises(subprocess.CalledProcessError):
            self.publish(remote)

        self.assertEqual(remote.remote_head, "")
        self.assertEqual(remote.reopen_attempts, 0)
        self.assertEqual(remote.prs[42]["state"], "CLOSED")
        self.assertEqual(remote.prs[42]["reviewDecision"], "CHANGES_REQUESTED")
        self.assertEqual(remote.labels[42], {LABEL_APPROVED})


if __name__ == "__main__":
    unittest.main()
