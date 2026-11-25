"""Publication behavior for retained PRs requeued to the dispatcher."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import LABEL_APPROVED, Config
from tests import test_host_claims


class PublicationRemote:
    """Small stateful GitHub/git boundary that rejects every unmodelled operation."""

    def __init__(self, prs: list[dict], listed: list[int] = ()) -> None:
        self.prs = {pr["number"]: dict(pr) for pr in prs}
        self.labels = {pr["number"]: set(pr.get("labels", ())) for pr in prs}
        self.listed = set(listed)
        self.next_number = max(self.prs, default=100) + 1
        self.head = "f" * 40
        self.remote_head = "e" * 40

    @staticmethod
    def _json(value):
        return json.loads(json.dumps(value))

    def gh_json(self, args):
        if args[:2] == ["pr", "list"]:
            head = args[args.index("--head") + 1]
            return self._json([
                pr for number, pr in self.prs.items()
                if number in self.listed and pr["state"] == "OPEN" and pr["headRefName"] == head
            ])
        if args[:2] == ["pr", "view"]:
            return self._json(self.prs[int(args[2])])
        raise AssertionError(f"unexpected GitHub read: {args!r}")

    def run(self, command, *args, **kwargs):
        if command[:3] == ["git", "fetch", "origin"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(command, 0, "1\n", "")
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, self.head + "\n", "")
        if command[:2] == ["git", "push"]:
            self.remote_head = self.head
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["gh", "pr", "edit"]:
            number = int(command[3])
            if command[-2:] != ["--remove-label", LABEL_APPROVED]:
                raise AssertionError(f"unexpected PR edit: {command!r}")
            self.labels[number].discard(LABEL_APPROVED)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["gh", "pr", "reopen"]:
            number = int(command[3])
            pr = self.prs[number]
            if pr["state"] != "CLOSED":
                raise AssertionError(f"cannot reopen {pr['state']} PR #{number}")
            if LABEL_APPROVED in self.labels[number]:
                raise AssertionError(f"PR #{number} reopened with stale approval")
            pr["state"] = "OPEN"
            self.listed.add(number)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["gh", "pr", "create"]:
            number = self.next_number
            self.next_number += 1
            self.prs[number] = {
                "number": number,
                "state": "OPEN",
                "headRefName": command[command.index("--head") + 1],
                "baseRefName": command[command.index("--base") + 1],
            }
            self.labels[number] = set()
            self.listed.add(number)
            return subprocess.CompletedProcess(command, 0, f"https://example.invalid/pull/{number}\n", "")
        raise AssertionError(f"unexpected command: {command!r}")


class DispatchRequeuePublicationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        spec = importlib.util.spec_from_file_location(
            f"factory._dispatch_requeue_{id(self)}", dispatch.__file__
        )
        assert spec and spec.loader
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.module.configure(Config(root=self.root, repo="local/publication"))
        self.worktree = self.root / ".factory" / "wt"
        self.worktree.mkdir(parents=True)

    def publish(self, ticket: int, branch: str, remote: PublicationRemote, retained: int) -> None:
        self.module.record("pr-requeued", ticket=ticket, pr=retained)
        self.module.gh_json = remote.gh_json
        self.module.run = remote.run
        self.assertTrue(self.module.push_and_pr(
            self.worktree, branch, "title", "body", ticket=ticket, lease="d" * 40,
        ))
        self.assertEqual(remote.remote_head, remote.head)

    def opened_prs(self, remote: PublicationRemote, branch: str) -> list[dict]:
        return [
            pr for pr in remote.prs.values()
            if pr["state"] == "OPEN" and pr["headRefName"] == branch
        ]

    def test_existing_open_pr_wins_over_a_retained_closed_receipt(self) -> None:
        ticket = int("1000")
        branch = f"agent/{ticket}"
        remote = PublicationRemote([
            {"number": 41, "state": "OPEN", "headRefName": branch, "baseRefName": "main"},
            {"number": 42, "state": "CLOSED", "headRefName": branch, "baseRefName": "main",
             "labels": [LABEL_APPROVED]},
        ], listed=[41])

        self.publish(ticket, branch, remote, retained=42)

        self.assertEqual({pr["number"] for pr in self.opened_prs(remote, branch)}, {41})
        self.assertEqual(remote.prs[42]["state"], "CLOSED")
        self.assertEqual(remote.labels[42], {LABEL_APPROVED})
        [opened] = [row for row in self.module.lifecycle.read_events(self.module.EVENTS)
                    if row.get("event") == "pr-opened"]
        self.assertEqual(opened["pr"], 41)

    def test_matching_noninterned_ticket_reopens_the_closed_pr_without_stale_approval(self) -> None:
        ticket = int("987654321")
        branch = f"agent/{ticket}"
        remote = PublicationRemote([
            {"number": 42, "state": "CLOSED", "headRefName": branch, "baseRefName": "main",
             "labels": [LABEL_APPROVED]},
        ])

        self.publish(ticket, branch, remote, retained=42)

        self.assertEqual(remote.prs[42]["state"], "OPEN")
        self.assertEqual(remote.labels[42], set())
        self.assertEqual(set(remote.prs), {42})
        [opened] = [row for row in self.module.lifecycle.read_events(self.module.EVENTS)
                    if row.get("event") == "pr-opened"]
        self.assertEqual(opened["pr"], 42)

    def test_retained_merged_pr_is_not_reopened(self) -> None:
        ticket = int("1000")
        branch = f"agent/{ticket}"
        remote = PublicationRemote([
            {"number": 42, "state": "MERGED", "headRefName": branch, "baseRefName": "main",
             "labels": [LABEL_APPROVED]},
        ])

        self.publish(ticket, branch, remote, retained=42)

        self.assertEqual(remote.prs[42]["state"], "MERGED")
        self.assertEqual(remote.labels[42], {LABEL_APPROVED})
        [replacement] = self.opened_prs(remote, branch)
        self.assertNotEqual(replacement["number"], 42)

    def test_retained_closed_pr_for_another_branch_is_not_reopened(self) -> None:
        ticket = int("1000")
        branch = f"agent/{ticket}"
        remote = PublicationRemote([
            {"number": 42, "state": "CLOSED", "headRefName": "agent/0999",
             "baseRefName": "main", "labels": [LABEL_APPROVED]},
        ])

        self.publish(ticket, branch, remote, retained=42)

        self.assertEqual(remote.prs[42]["state"], "CLOSED")
        self.assertEqual(remote.labels[42], {LABEL_APPROVED})
        [replacement] = self.opened_prs(remote, branch)
        self.assertNotEqual(replacement["number"], 42)


class DispatchRequeueAdmissionTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def test_failed_close_response_rechecks_before_requeueing(self):
        for concurrently_closed in (False, True):
            with self.subTest(concurrently_closed=concurrently_closed):
                self.setUp()
                module = self.dispatcher(None)
                self.github.open_prs = [{"number": 70}]
                self.github.issue["labels"] = []
                run = module.run

                def close(cmd, *args, run=run, concurrently_closed=concurrently_closed, **kwargs):
                    if cmd[:3] != ["gh", "pr", "close"]:
                        return run(cmd, *args, **kwargs)
                    if concurrently_closed:
                        self.github.open_prs.clear()
                    # A real command honors check=True, unlike the shared happy-path fixture.
                    return subprocess.run(
                        [sys.executable, "-c", "raise SystemExit(1)"],
                        check=kwargs.get("check", True), capture_output=True, text=True,
                    )

                with mock.patch.object(module, "run", side_effect=close):
                    if concurrently_closed:
                        module.unclaim(7, "--add-label", module.LABEL_AGENT)
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            module.unclaim(7, "--add-label", module.LABEL_AGENT)

                requeues = [row["pr"] for row in lifecycle.read_events(module.EVENTS)
                            if row.get("event") == "pr-requeued"]
                if concurrently_closed:
                    self.assertEqual(requeues, [70])
                    self.assertNotIn("held", module.frontier()[0])
                    self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
                else:
                    self.assertEqual(requeues, [])
                    self.assertEqual(self.github.open_prs, [{"number": 70}])
                    self.assertEqual(self.github.issue["labels"], [])

    def test_any_matching_open_pr_remains_a_hold_at_execution(self):
        module = self.dispatcher(None)
        self.github.open_prs = [{"number": 70}, {"number": 71}]

        self.assertIn(module.open_agent_pr_hold(7), {"open agent PR #70", "open agent PR #71"})
        with mock.patch.object(module, "worker_round") as worker, \
                mock.patch.object(module, "push_agent") as push:
            for forced in (False, True):
                module.process_ticket(self.github.issue, 1, False, forced=forced)
            worker.assert_not_called()
            push.assert_not_called()
        self.assertEqual(self.github.issue["assignees"], [])


if __name__ == "__main__":
    unittest.main()
