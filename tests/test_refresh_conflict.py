"""#295: a refresh conflict onto moved main is routine worker work, not a human escalation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage

os.environ.pop(lifecycle.CONTEXT_ENV, None)  # never journal fixture rows into a live dispatcher's journal


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def build_fork(tmp: Path) -> tuple[Path, Path]:
    """`origin` with one commit on main, and the dispatcher's `root` cloned from it."""
    origin, root = tmp / "origin", tmp / "root"
    git(tmp, "init", "-q", "-b", "main", str(origin))
    git(origin, "config", "user.email", "a@example.com")
    git(origin, "config", "user.name", "A")
    (origin / "u0.txt").write_text("u0")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "u0")
    git(tmp, "clone", "-q", str(origin), str(root))
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    return root, origin


# Rebases like a worker would, keeping both sides of the conflict; records the prompt it got.
RESOLVER = '''import os, subprocess, sys
from pathlib import Path
Path(sys.argv[1]).write_text(Path(".factory-prompt.md").read_text())
env = {**os.environ, "GIT_EDITOR": "true"}
subprocess.run(["git", "fetch", "-q", "origin"], check=True)
if subprocess.run(["git", "rebase", "origin/main"], capture_output=True).returncode:
    Path("shared.txt").write_text("main version\\nagent version\\n")
    subprocess.run(["git", "add", "shared.txt"], check=True)
    subprocess.run(["git", "rebase", "--continue"], check=True, env=env, capture_output=True)
'''


class FakeGitHub:
    """Issue #9 and PR #102 for `agent/9`, with `origin` as the source of truth for heads."""

    def __init__(self, origin: Path, real_run) -> None:
        self.origin, self.real_run = origin, real_run
        self.issue_labels = {config.LABEL_AGENT}
        self.assignees = ["factory"]
        self.pr_labels = {config.LABEL_APPROVED}
        self.calls: list[list[str]] = []
        self.merged = False

    def head(self) -> str:
        return git(self.origin, "rev-parse", "agent/9")

    def pr(self) -> dict:
        return {"number": 102, "state": "MERGED" if self.merged else "OPEN", "headRefName": "agent/9",
                "headRefOid": self.head(), "baseRefName": "main", "isDraft": False, "reviewDecision": "",
                "labels": [{"name": label} for label in sorted(self.pr_labels)], "title": "agent/9: change"}

    def issue(self) -> dict:
        return {"number": 9, "state": "OPEN", "title": "change", "body": "Make the change.",
                "comments": [], "assignees": [{"login": a} for a in self.assignees],
                "labels": [{"name": label} for label in sorted(self.issue_labels)]}

    def gh_json(self, args: list[str]) -> object:
        if args[:2] == ["pr", "list"]:
            return [] if self.merged else [self.pr()]
        if args[:2] == ["pr", "view"]:
            return self.pr()
        if args[:2] == ["issue", "view"]:
            return self.issue()
        if args[:2] == ["issue", "list"]:
            return [self.issue()] if config.LABEL_AGENT in self.issue_labels else []
        if args[0] == "api" and "/compare/" in args[1]:
            head = args[1].rsplit("...", 1)[1]
            return {"behind_by": int(git(self.origin, "rev-list", "--count", f"{head}..main"))}
        raise AssertionError(args)

    def run(self, cmd, *args, **kwargs):
        if cmd[0] != "gh":
            return self.real_run(cmd, *args, **kwargs)
        self.calls.append(cmd)
        if cmd[1] == "api":  # issue dependencies: none
            return subprocess.CompletedProcess(cmd, 0, "[]", "")
        flags = dict(pairwise(cmd))
        if cmd[1:3] == ["issue", "edit"]:
            if "--remove-assignee" in cmd:
                self.assignees = []
            if "--add-assignee" in cmd:
                self.assignees = ["factory"]
            self.issue_labels -= {flags["--remove-label"]} if "--remove-label" in cmd else set()
            self.issue_labels |= {flags["--add-label"]} if "--add-label" in cmd else set()
        elif cmd[1:3] == ["pr", "edit"]:
            self.pr_labels -= {flags["--remove-label"]} if "--remove-label" in cmd else set()
            self.pr_labels |= {flags["--add-label"]} if "--add-label" in cmd else set()
        elif cmd[1:3] == ["pr", "checks"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps([{"name": "ci", "bucket": "pass"}]), "")
        elif cmd[1:3] == ["pr", "merge"]:
            assert cmd[cmd.index("--match-head-commit") + 1] == self.head()
            git(self.origin, "merge", "-q", "--squash", "agent/9")
            git(self.origin, "commit", "-q", "-m", "Squash PR #102")
            self.merged = True
        return subprocess.CompletedProcess(cmd, 0, "", "")


class RefreshConflictTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.root, self.origin = build_fork(self.tmp)
        git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "shared.txt").write_text("agent version\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-qm", "agent change")
        self.approved_head = git(self.origin, "rev-parse", "HEAD")
        git(self.origin, "checkout", "-q", "main")
        (self.origin / "shared.txt").write_text("main version\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-qm", "conflicting main change")
        self.prompt_seen = self.tmp / "prompt-seen.md"
        resolver = self.tmp / "resolver.py"
        resolver.write_text(RESOLVER)
        self.cfg = config.Config(
            root=self.root, repo="acme/widgets", main="main", review_rounds=3, idle_timeout=0, exit_grace=0,
            workers={"default": [sys.executable, str(resolver), str(self.prompt_seen)]},
            reviewer=[sys.executable, "-c", "print('VERDICT: APPROVE')"],
        )
        dispatch.configure(self.cfg)
        self.github = FakeGitHub(self.origin, dispatch.run)

    def patched(self):
        return mock.patch.multiple(dispatch, gh_json=self.github.gh_json, run=self.github.run)

    def events(self, name: str) -> list[dict]:
        return [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == name]

    def test_conflict_requeues_to_a_worker_on_the_same_branch_then_merges_without_a_human(self) -> None:
        with self.patched(), mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")):
            dispatch.merge_pass_locked(False)

            # Re-queued, not escalated: still ready-for-agent, unassigned, approval withdrawn.
            self.assertEqual(self.events("escalate"), [])
            self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)
            self.assertIn(config.LABEL_AGENT, self.github.issue_labels)
            self.assertEqual(self.github.assignees, [])
            self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
            self.assertFalse(any(cmd[1:3] == ["issue", "comment"] for cmd in self.github.calls))
            [requeue] = self.events("conflict-requeue")
            self.assertEqual((requeue["pr"], requeue["files"], requeue["round"]), (102, ["shared.txt"], 1))
            [decision] = [
                event for event in self.events("decision")
                if event.get("stage") == "merge" and event.get("chosen") == "conflict-requeue"
            ]
            self.assertEqual(decision["inputs"][0]["summary"], f"PR head {self.approved_head}")
            self.assertEqual(self.github.head(), self.approved_head)  # nothing pushed yet

            [ready] = dispatch.frontier()
            dispatch.process_ticket(ready, 30, False)

            prompt = self.prompt_seen.read_text()
            self.assertIn("## Refresh conflict, round 1", prompt)
            self.assertIn("- `shared.txt`", prompt)
            self.assertIn("do not widen scope", prompt)
            resolved = self.github.head()
            self.assertNotEqual(resolved, self.approved_head)
            self.assertEqual(git(self.origin, "merge-base", "main", resolved), git(self.origin, "rev-parse", "main"))
            self.assertEqual(git(dispatch.FACTORY / "wt-9", "branch", "--show-current"), "agent/9")
            [review] = self.events("review")
            self.assertEqual((review["head"], review["verdict"]), (resolved, "APPROVE"))
            self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)

            dispatch.merge_pass_locked(False)

        self.assertTrue(self.github.merged)
        self.assertEqual([e["head"] for e in self.events("merged")], [resolved])
        self.assertEqual(git(self.origin, "show", "main:shared.txt"), "main version\nagent version")
        self.assertEqual(self.events("escalate"), [])
        self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)

    def test_fix_approved_conflict_restores_agent_labels_then_resolves_and_merges(self) -> None:
        # Successful manager FIX approves the PR but leaves the issue ready-for-human.
        self.github.issue_labels = {config.LABEL_HUMAN}
        self.assertEqual(self.github.issue()["state"], "OPEN")
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        wt = dispatch.ensure_worktree(9)
        with self.patched(), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "fresh gate ok")) as gate, \
                mock.patch.object(dispatch, "review", wraps=dispatch.review) as review:
            dispatch.merge_pass_locked(False)

            self.assertEqual(self.events("escalate"), [])
            self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)
            self.assertIn(config.LABEL_AGENT, self.github.issue_labels)
            self.assertEqual(self.github.assignees, [])
            self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
            self.assertFalse(any(cmd[1:3] == ["issue", "comment"] for cmd in self.github.calls))
            [requeue] = self.events("conflict-requeue")
            self.assertEqual((requeue["head"], requeue["files"], requeue["round"]),
                             (self.approved_head, ["shared.txt"], 1))
            self.assertEqual(git(wt, "branch", "--show-current"), "agent/9")
            gate.assert_not_called()
            review.assert_not_called()

            [ready] = dispatch.frontier()
            dispatch.process_ticket(ready, 30, False)

            resolved = self.github.head()
            self.assertNotEqual(resolved, self.approved_head)
            self.assertEqual(git(wt, "rev-parse", "HEAD"), resolved)
            self.assertEqual(git(wt, "branch", "--show-current"), "agent/9")
            self.assertIn("## Refresh conflict, round 1", self.prompt_seen.read_text())
            gate.assert_called_once()
            self.assertEqual(gate.call_args.args[:2], (wt, 9))
            review.assert_called_once_with(wt, 9, "fresh gate ok", resolved)
            [attempt] = self.events("attempt")
            self.assertEqual((attempt["head"], attempt["gate"]), (resolved, "PASS"))
            [review_event] = self.events("review")
            self.assertEqual((review_event["head"], review_event["verdict"]), (resolved, "APPROVE"))
            rows = lifecycle.read_events(dispatch.EVENTS)
            self.assertLess(rows.index(attempt), rows.index(review_event))
            self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)

            dispatch.merge_pass_locked(False)

        self.assertTrue(self.github.merged)
        self.assertEqual([e["head"] for e in self.events("merged")], [resolved])
        self.assertEqual(git(self.origin, "show", "main:shared.txt"), "main version\nagent version")
        self.assertEqual(self.events("escalate"), [])
        self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)
        self.assertIn(config.LABEL_AGENT, self.github.issue_labels)

    def test_closed_ticket_conflict_still_escalates_without_requeue(self) -> None:
        closed_issue = {**self.github.issue(), "state": "CLOSED"}
        with self.patched(), mock.patch.object(self.github, "issue", return_value=closed_issue):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "refresh_conflict")
        self.assertIn("ticket is not open", escalation["reason"])
        self.assertIn("- `shared.txt`", Path(escalation["packet"]).read_text())
        self.assertEqual(self.events("conflict-requeue"), [])
        self.assertIn(config.LABEL_HUMAN, self.github.issue_labels)
        self.assertNotIn(config.LABEL_AGENT, self.github.issue_labels)

    def test_conflict_after_the_round_limit_escalates_with_the_file_list(self) -> None:
        for round_number in range(1, self.cfg.review_rounds + 1):
            dispatch.record("conflict-requeue", ticket=9, pr=102, round=round_number, files=["shared.txt"])
        with self.patched():
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "refresh_conflict")
        self.assertIn("after 3 conflict-resolution round(s)", escalation["reason"])
        self.assertIn("## Conflicting files\n\n- `shared.txt`", Path(escalation["packet"]).read_text())
        self.assertIn(config.LABEL_HUMAN, self.github.issue_labels)
        self.assertEqual(len(self.events("conflict-requeue")), 3)

    def test_conflict_on_a_protected_path_escalates_immediately_unless_authorized(self) -> None:
        self.cfg.protected_paths = ["shared.*"]
        with self.patched():
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "refresh_conflict")
        self.assertIn("protected", escalation["reason"])
        self.assertIn("- `shared.txt`", Path(escalation["packet"]).read_text())
        self.assertEqual(self.events("conflict-requeue"), [])

        self.github.issue_labels = {config.LABEL_AGENT, config.LABEL_PROTECTED_OVERRIDE}
        with self.patched():
            dispatch.refresh_pr_branch(9, 102, False)
        self.assertEqual(len(self.events("escalate")), 1)
        self.assertEqual(len(self.events("conflict-requeue")), 1)


class ManagerFixAheadTest(unittest.TestCase):
    """#264: a kept worktree ahead of the PR head with only the worker's own commits."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tmp = Path(temp.name)
        self.root, self.origin = build_fork(tmp)
        git(self.origin, "branch", "agent/7")
        self.pr_head = git(self.origin, "rev-parse", "agent/7")
        dispatch.configure(config.Config(root=self.root, repo="acme/widgets", main="main"))
        self.wt = dispatch.ensure_worktree(7)
        (self.wt / "own.txt").write_text("unpushed\n")
        git(self.wt, "add", "own.txt")
        git(self.wt, "commit", "-qm", "worker commit")
        self.local = git(self.wt, "rev-parse", "HEAD")
        self.packet = tmp / "packet.md"
        self.packet.write_text("fix it")
        self.pushes: list[list[str]] = []

    def fix(self):
        real_run = dispatch.run

        def run(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "push"]:
                self.pushes.append(cmd)
            return real_run(cmd, *args, **kwargs)

        pr = {"state": "OPEN", "headRefName": "agent/7", "headRefOid": self.pr_head,
              "baseRefName": "main", "reviewDecision": ""}
        logfile = self.root / "attempt.log"
        with mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "worker_round",
                                  side_effect=lambda *a, **k: (True, "ok", logfile, git(self.wt, "rev-parse", "HEAD"))) as worker, \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "fine", False)), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True) as approve, \
                mock.patch.object(dispatch, "escalate") as escalate:
            manage.apply(7, {"title": "t", "labels": []}, "FIX", "", {"worker": "default", "guidance": "g"}, self.packet)
        return worker, approve, escalate

    def test_own_unpushed_commits_are_pushed_with_a_lease_and_the_fix_applies(self) -> None:
        dispatch.record("attempt", ticket=7, attempt=4, gate="FAIL", head=self.local)
        worker, approve, escalate = self.fix()
        escalate.assert_not_called()
        self.assertIn(f"--force-with-lease=agent/7:{self.pr_head}", self.pushes[0])
        worker.assert_called_once()
        approve.assert_called_once()
        self.assertEqual(git(self.origin, "rev-parse", "agent/7"), self.local)

    def test_foreign_commits_escalate_without_pushing(self) -> None:
        worker, approve, escalate = self.fix()  # no attempt row: not the worker's own head
        self.assertEqual(escalate.call_args.kwargs["code"], "refresh_head")
        worker.assert_not_called()
        approve.assert_not_called()
        self.assertEqual(self.pushes, [])
        self.assertEqual(git(self.origin, "rev-parse", "agent/7"), self.pr_head)

    def test_lease_failure_escalates(self) -> None:
        dispatch.record("attempt", ticket=7, attempt=4, gate="FAIL", head=self.local)
        git(self.origin, "checkout", "-q", "agent/7")
        (self.origin / "other.txt").write_text("someone else\n")
        git(self.origin, "add", "other.txt")
        git(self.origin, "commit", "-qm", "foreign push")
        git(self.origin, "checkout", "-q", "main")
        moved = git(self.origin, "rev-parse", "agent/7")
        worker, approve, escalate = self.fix()
        self.assertEqual(escalate.call_args.kwargs["code"], "lease_refused")
        worker.assert_not_called()
        approve.assert_not_called()
        self.assertEqual(git(self.origin, "rev-parse", "agent/7"), moved)


if __name__ == "__main__":
    unittest.main()
