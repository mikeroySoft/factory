"""Landing drains one finite PR snapshot without weakening per-PR safety gates."""
from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle


class FakeGitHub:
    def __init__(self) -> None:
        self.prs: dict[int, dict] = {}
        self.main_version = 0
        self.branch_versions: dict[int, int] = {}
        self.forced_behind: dict[int, int] = {}
        self.merged: list[int] = []
        self.refreshed: list[int] = []
        self.compares: list[tuple[int, int]] = []
        self.commands: list[list[str]] = []
        self.list_calls = 0

    def add(
        self,
        ticket: int,
        *,
        pr: int | None = None,
        branch: str | None = None,
        approved: bool = True,
        held: bool = False,
        draft: bool = False,
        base: str = "main",
        review: str = "APPROVED",
    ) -> dict:
        number = pr if pr is not None else 100 + ticket
        labels = ([{"name": config.LABEL_APPROVED}] if approved else [])
        if held:
            labels.append({"name": "factory-held"})
        row = {
            "number": number,
            "state": "OPEN",
            "headRefName": branch or f"agent/{ticket}",
            "headRefOid": f"{ticket:040x}",
            "baseRefName": base,
            "isDraft": draft,
            "labels": labels,
            "reviewDecision": review,
            "title": f"ticket {ticket}",
            "files": [{"path": f"feature-{ticket}.py"}],
        }
        self.prs[number] = row
        self.branch_versions[number] = self.main_version
        return row

    def gh_json(self, args: list[str]):
        if args[:2] == ["pr", "list"]:
            self.list_calls += 1
            return [dict(pr) for pr in self.prs.values() if pr["state"] == "OPEN"]
        if args[:2] == ["pr", "view"]:
            number = int(args[2])
            if args[args.index("--json") + 1] == "mergeCommit":
                return {"mergeCommit": {"oid": f"merge-{number}"}}
            return dict(self.prs[number])
        if args[0] == "api" and "/compare/" in args[1]:
            head = args[1].rsplit("...", 1)[1]
            number = next(number for number, pr in self.prs.items() if pr["headRefOid"] == head)
            behind = self.forced_behind.get(number, self.main_version - self.branch_versions[number])
            self.compares.append((number, behind))
            return {"behind_by": behind}
        if args[:2] == ["issue", "view"]:
            return {"labels": []}
        raise AssertionError(args)

    def run(self, command: list[str], *args, **kwargs) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if command[:3] == ["gh", "pr", "merge"]:
            number = int(command[3])
            pr = self.prs[number]
            assert pr["state"] == "OPEN"
            assert command[command.index("--match-head-commit") + 1] == pr["headRefOid"]
            pr["state"] = "MERGED"
            self.merged.append(number)
            self.main_version += 1
        elif command[:3] == ["gh", "pr", "edit"] and "--remove-label" in command:
            number = int(command[3])
            removed = command[command.index("--remove-label") + 1]
            self.prs[number]["labels"] = [
                label for label in self.prs[number]["labels"] if label["name"] != removed
            ]
        return subprocess.CompletedProcess(command, 0, "", "")

    def refresh(self, ticket: int, pr: int, carries_upstream: bool) -> bool:
        del ticket, carries_upstream
        self.refreshed.append(pr)
        self.branch_versions[pr] = self.main_version
        return True


class LandDrainTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        dispatch.configure(config.Config(root=self.root, repo="acme/widgets", signoff=False))
        self.github = FakeGitHub()
        self.logs: list[str] = []
        self.escalations: list[tuple] = []
        self.enterContext(mock.patch("factory.learn.promote_pass"))
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.github.gh_json))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.github.run))
        self.enterContext(mock.patch.object(
            dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}],
        ))
        self.enterContext(mock.patch.object(dispatch, "refresh_pr_branch", side_effect=self.github.refresh))
        self.enterContext(mock.patch.object(dispatch, "cleanup_after_merge"))
        self.enterContext(mock.patch.object(dispatch, "escalate", side_effect=lambda *a, **kw: self.escalations.append((a, kw))))
        self.enterContext(mock.patch.object(dispatch, "log", side_effect=self.logs.append))

    def approve(self, ticket: int, pr: int | None = None) -> dict:
        row = self.github.add(ticket, pr=pr)
        head = row["headRefOid"]
        lifecycle.append(dispatch.EVENTS, {
            "event": "attempt", "ticket": ticket, "gate": "PASS", "head": head, "actual_head": head,
        })
        lifecycle.append(dispatch.EVENTS, {
            "event": "review", "ticket": ticket, "verdict": "APPROVE", "accepted": True,
            "head": head, "actual_head": head,
        })
        lifecycle.append(dispatch.EVENTS, {
            "event": "approved", "ticket": ticket, "pr": row["number"], "head": head,
            "gate_head": head, "review_head": head,
        })
        return row

    def test_two_current_approved_prs_both_merge(self) -> None:
        first, second = self.approve(1), self.approve(2)
        self.github.forced_behind = {first["number"]: 0, second["number"]: 0}

        dispatch.merge_pass_locked(False)

        self.assertEqual(self.github.merged, [first["number"], second["number"]])
        self.assertEqual(self.github.refreshed, [])
        self.assertEqual(self.github.list_calls, 1)

    def test_two_behind_prs_both_refresh(self) -> None:
        first, second = self.approve(1), self.approve(2)
        self.github.forced_behind = {first["number"]: 1, second["number"]: 1}

        dispatch.merge_pass_locked(False)

        self.assertEqual(self.github.refreshed, [first["number"], second["number"]])
        self.assertEqual(self.github.merged, [])
        self.assertEqual(self.github.list_calls, 1)

    def test_merge_then_refresh_observes_main_after_each_candidate(self) -> None:
        first, second = self.approve(1), self.approve(2)

        dispatch.merge_pass_locked(False)

        self.assertEqual(self.github.merged, [first["number"]])
        self.assertEqual(self.github.refreshed, [second["number"]])
        first_compare = {number: behind for number, behind in self.github.compares}
        self.assertEqual(first_compare, {first["number"]: 0, second["number"]: 1})

    def test_policy_skips_are_journaled_without_mutation(self) -> None:
        self.github.add(1, branch="feature/not-factory", approved=True)
        self.github.add(2, draft=True)
        self.github.add(3, base="release")
        self.github.add(4, approved=False)
        self.github.add(5, held=True)
        self.github.add(6, review="CHANGES_REQUESTED")

        dispatch.merge_pass_locked(False)

        journal = "\n".join(self.logs)
        expected = {
            101: ("unsupported", "unsupported_branch"),
            102: ("draft", "draft"),
            103: ("release", "wrong_base"),
            104: ("factory-approved", "not_approved"),
            105: ("factory-held", "held"),
            106: ("requested changes", "human_veto"),
        }
        skipped = {
            row["pr"]: row["reason"]
            for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "merge-skipped"
        }
        for number, (message, reason) in expected.items():
            with self.subTest(pr=number):
                self.assertIn(f"PR #{number}", journal)
                self.assertIn(message, journal)
                self.assertEqual(skipped[number], reason)
        self.assertEqual((self.github.merged, self.github.refreshed, self.escalations), ([], [], []))
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in self.github.commands))

    def test_active_ticket_is_journaled_and_never_touched(self) -> None:
        row = self.approve(7)
        lock = dispatch.ticket_lock(7).open("w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        dispatch.merge_pass_locked(False)

        self.assertTrue(any(f"PR #{row['number']}" in line and "active ticket" in line for line in self.logs))
        [skipped] = [
            event for event in lifecycle.read_events(dispatch.EVENTS)
            if event.get("event") == "merge-skipped"
        ]
        self.assertEqual(
            (skipped["ticket"], skipped["pr"], skipped["reason"]),
            (7, row["number"], "active_ticket"),
        )
        self.assertEqual((self.github.merged, self.github.refreshed), ([], []))
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in self.github.commands))

    def test_hold_appearing_on_fresh_read_blocks_before_git(self) -> None:
        row = self.approve(8)
        original = self.github.gh_json

        def hold_on_view(args: list[str]):
            if args[:3] == ["pr", "view", str(row["number"])]:
                self.github.prs[row["number"]]["labels"].append({"name": "factory-held"})
            return original(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=hold_on_view):
            dispatch.merge_pass_locked(False)

        self.assertEqual((self.github.merged, self.github.refreshed), ([], []))
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in self.github.commands))
        [skipped] = [
            event for event in lifecycle.read_events(dispatch.EVENTS)
            if event.get("event") == "merge-skipped"
        ]
        self.assertEqual(skipped["reason"], "held")

    def test_hold_appearing_on_final_read_blocks_merge(self) -> None:
        row = self.approve(9)
        original = self.github.gh_json
        views = 0

        def hold_on_final_view(args: list[str]):
            nonlocal views
            if args[:3] == ["pr", "view", str(row["number"])]:
                views += 1
                if views == 2:
                    self.github.prs[row["number"]]["labels"].append({"name": "factory-held"})
            return original(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=hold_on_final_view):
            dispatch.merge_pass_locked(False)

        self.assertEqual((self.github.merged, self.github.refreshed), ([], []))
        self.assertTrue(any(command[:2] == ["git", "fetch"] for command in self.github.commands))
        [skipped] = [
            event for event in lifecycle.read_events(dispatch.EVENTS)
            if event.get("event") == "merge-skipped"
        ]
        self.assertEqual(skipped["reason"], "held")

    def test_fresh_snapshot_without_state_cannot_authorize_merge(self) -> None:
        row = self.approve(10)
        original = self.github.gh_json

        def missing_state(args: list[str]):
            value = original(args)
            if args[:3] == ["pr", "view", str(row["number"])]:
                value.pop("state")
            return value

        with mock.patch.object(dispatch, "gh_json", side_effect=missing_state):
            dispatch.merge_pass_locked(False)

        self.assertEqual((self.github.merged, self.github.refreshed), ([], []))
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in self.github.commands))
        skipped = [event for event in lifecycle.read_events(dispatch.EVENTS)
                   if event.get("event") == "merge-skipped"]
        self.assertEqual([(event["pr"], event["reason"]) for event in skipped],
                         [(row["number"], "state_changed")])


if __name__ == "__main__":
    unittest.main()
