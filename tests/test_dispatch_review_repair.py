"""Review regressions: retry cleanup, displaced owners and retained publication."""
from __future__ import annotations

import copy
import subprocess
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import lifecycle
from factory.config import LABEL_APPROVED
from tests import test_dispatch_requeue_survivors as publication
from tests import test_host_claims


class RetainedRemote(publication.PublicationRemote):
    """A closed PR cannot accept rewritten history before it is reopened."""

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
        if command[:2] == ["git", "push"] and any(
            pr["state"] == "CLOSED" for pr in self.prs.values()
        ):
            raise RuntimeError("rewritten head published while retained PR is closed")
        return super().run(command, *args, **kwargs)


class RetainedPublicationRepair(unittest.TestCase):
    setUp = publication.DispatchRequeuePublicationTest.setUp
    opened_prs = publication.DispatchRequeuePublicationTest.opened_prs

    def remote(self, branch="agent/7"):
        return RetainedRemote([
            {"number": 42, "state": "CLOSED", "headRefName": branch,
             "baseRefName": "main", "labels": [LABEL_APPROVED],
             "reviewDecision": "CHANGES_REQUESTED"},
        ])

    def publish(self, remote):
        self.module.gh_json = remote.gh_json
        self.module.run = remote.run
        return self.module.push_and_pr(
            self.worktree, "agent/7", "title", "body", ticket=7, lease="d" * 40,
        )

    def test_retained_pr_is_reopened_before_rewritten_head_is_published(self):
        remote = self.remote()
        self.module.record("pr-requeued", ticket=7, pr=42)

        self.assertTrue(self.publish(remote))

        self.assertEqual(remote.remote_head, remote.head)
        self.assertEqual(set(remote.prs), {42})
        self.assertEqual(remote.prs[42]["state"], "OPEN")
        self.assertEqual(remote.prs[42]["reviewDecision"], "CHANGES_REQUESTED")
        self.assertEqual(remote.labels[42], set())

    def test_another_host_without_local_receipt_retains_human_blocking_review(self):
        remote = self.remote()
        self.assertEqual(lifecycle.read_events(self.module.EVENTS), [])

        self.assertTrue(self.publish(remote))

        self.assertEqual(remote.remote_head, remote.head)
        self.assertEqual(set(remote.prs), {42})
        self.assertEqual(remote.prs[42]["reviewDecision"], "CHANGES_REQUESTED")
        self.assertEqual(remote.labels[42], set())
        self.assertEqual([pr["number"] for pr in self.opened_prs(remote, "agent/7")], [42])
        [opened] = [row for row in lifecycle.read_events(self.module.EVENTS)
                    if row.get("event") == "pr-opened"]
        self.assertEqual((opened["ticket"], opened["pr"]), (7, 42))

    def test_remote_retained_pr_wins_over_this_hosts_obsolete_merged_receipt(self):
        remote = self.remote()
        remote.prs[41] = {
            "number": 41, "state": "MERGED", "headRefName": "agent/7", "baseRefName": "main",
        }
        remote.labels[41] = set()
        self.module.record("pr-requeued", ticket=7, pr=41)

        self.assertTrue(self.publish(remote))

        self.assertEqual(remote.remote_head, remote.head)
        self.assertEqual(set(remote.prs), {41, 42})
        self.assertEqual(remote.prs[41]["state"], "MERGED")
        self.assertEqual(remote.prs[42]["state"], "OPEN")
        self.assertEqual(remote.prs[42]["reviewDecision"], "CHANGES_REQUESTED")

    def test_higher_sorting_retained_branch_is_not_published_as_this_ticket(self):
        remote = publication.PublicationRemote([
            {"number": 42, "state": "CLOSED", "headRefName": "agent/8",
             "baseRefName": "main", "labels": [LABEL_APPROVED]},
        ])
        self.module.record("pr-requeued", ticket=7, pr=42)

        self.assertTrue(self.publish(remote))

        self.assertEqual(remote.prs[42]["state"], "CLOSED")
        self.assertEqual(remote.labels[42], {LABEL_APPROVED})
        [replacement] = self.opened_prs(remote, "agent/7")
        self.assertNotEqual(replacement["number"], 42)

    def test_failed_reopen_never_publishes_rewritten_head(self):
        remote = self.remote()
        before = remote.remote_head
        self.module.record("pr-requeued", ticket=7, pr=42)
        run = remote.run

        def fail_reopen(command, *args, **kwargs):
            if command[:3] == ["gh", "pr", "reopen"]:
                raise subprocess.CalledProcessError(1, command, stderr="reopen refused")
            return run(command, *args, **kwargs)

        with mock.patch.object(remote, "run", side_effect=fail_reopen), \
                self.assertRaises(subprocess.CalledProcessError):
            self.publish(remote)

        self.assertEqual(remote.remote_head, before)
        self.assertEqual(remote.prs[42]["state"], "CLOSED")


class ClaimCleanupRepair(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def test_forced_human_bot_assignment_survives_failed_cleanup_and_final_retry(self):
        module = self.dispatcher("tank")
        self.github.issue["assignees"] = [{"login": "@me"}]
        run = module.run
        failed = False

        def fail_host_cleanup_once(command, *args, **kwargs):
            nonlocal failed
            if "--remove-label" in command and "host:tank" in command and not failed:
                failed = True
                raise subprocess.CalledProcessError(1, command, stderr="host label unavailable")
            return run(command, *args, **kwargs)

        with mock.patch.object(module, "run", side_effect=fail_host_cleanup_once), \
                mock.patch.object(module, "worker_round", side_effect=FileNotFoundError("worker")), \
                self.assertRaises(subprocess.CalledProcessError):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)

        self.assertTrue(failed)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.comments, [])
        self.assertFalse(module.lock_held(module.ticket_lock(7)))
        self.assertFalse(any(row.get("event") == "assignee-cleared"
                             for row in lifecycle.read_events(module.EVENTS)))

    def test_displaced_worker_cannot_close_replacements_pr_or_publish_requeue(self):
        module = self.dispatcher("tank")
        old = self.github.add_claim("tank", datetime.now(UTC))
        nonce = old["body"].split("nonce=", 1)[1].split()[0]
        module._HOST_CLAIMS[7] = module.HostClaim(7, {**old, "nonce": nonce})
        module._ASSIGNMENTS[7] = "@me"
        self.github.comments.clear()
        replacement = self.github.add_claim("ultron", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        before = copy.deepcopy((self.github.issue, self.github.comments))

        module.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent")

        self.assertEqual(self.github.open_prs, [{"number": 70}])
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertIn(replacement, self.github.comments)
        self.assertEqual(module.frontier()[0]["held"], "open agent PR #70")
        self.assertFalse(any(row.get("event") == "pr-requeued"
                             for row in lifecycle.read_events(module.EVENTS)))

    def test_ownership_loss_during_cleanup_read_cannot_close_replacements_pr(self):
        module = self.dispatcher("tank")
        old = self.github.add_claim("tank", datetime.now(UTC))
        nonce = old["body"].split("nonce=", 1)[1].split()[0]
        module._HOST_CLAIMS[7] = module.HostClaim(7, {**old, "nonce": nonce})
        module._ASSIGNMENTS[7] = "@me"
        self.github.open_prs = [{"number": 70}]
        read = module.gh_json
        replacement = None

        def replace_during_read(args):
            nonlocal replacement
            result = read(args)
            if args[:2] == ["issue", "view"] and args[-1] == "labels,assignees":
                self.github.comments.clear()
                replacement = self.github.add_claim("ultron", datetime.now(UTC))
            return result

        with mock.patch.object(module, "gh_json", side_effect=replace_during_read):
            module.unclaim(7, "--add-label", "ready-for-agent")

        self.assertEqual(self.github.open_prs, [{"number": 70}])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.comments, [replacement])
        self.assertFalse(any(row.get("event") == "pr-requeued"
                             for row in lifecycle.read_events(module.EVENTS)))


if __name__ == "__main__":
    unittest.main()
