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
            reviewer=[sys.executable, "-c", "print('```json\\n{\"verdict\":\"APPROVE\",\"findings\":[]}\\n```\\nVERDICT: APPROVE')"],
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



class RefreshGateFailureTest(unittest.TestCase):
    """#381: a refresh gate gets one retry, then returns routine failures to the worker."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.prompt_seen = self.tmp / "prompt-seen.md"
        resolver = self.tmp / "resolver.py"
        resolver.write_text(RESOLVER)
        self.root, self.origin = build_fork(self.tmp)
        git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "feature.txt").write_text("agent change\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-qm", "agent change")
        self.approved_head = git(self.origin, "rev-parse", "HEAD")
        git(self.origin, "checkout", "-q", "main")
        (self.origin / "main.txt").write_text("main change\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-qm", "main moved")
        self.cfg = config.Config(
            root=self.root, repo="acme/widgets", main="main", review_rounds=3,
            idle_timeout=0, exit_grace=0,
            workers={"default": [sys.executable, str(resolver), str(self.prompt_seen)]},
            reviewer=[sys.executable, "-c", "print('```json\\n{\"verdict\":\"APPROVE\",\"findings\":[]}\\n```\\nVERDICT: APPROVE')"],
        )
        dispatch.configure(self.cfg)
        self.github = FakeGitHub(self.origin, dispatch.run)

    def patched(self):
        return mock.patch.multiple(dispatch, gh_json=self.github.gh_json, run=self.github.run)

    def events(self, name: str) -> list[dict]:
        return [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == name]

    def test_flaky_gate_failure_after_refresh_reruns_once_then_pushes_and_merges(self) -> None:
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", side_effect=[(False, "first flaky report"), (True, "second passing report")]
        ) as gate:
            dispatch.merge_pass_locked(False)
            self.assertEqual(gate.call_count, 2)
            [rerun] = self.events("gate-rerun")
            self.assertEqual(rerun["reports"], ["first flaky report", "second passing report"])
            [refreshed] = self.events("refreshed")
            self.assertTrue(refreshed["gate_rerun"])
            self.assertNotEqual(self.github.head(), self.approved_head)
            self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)

            dispatch.merge_pass_locked(False)

        self.assertTrue(self.github.merged)
        self.assertEqual(self.events("escalate"), [])

    def test_repeated_gate_failure_after_refresh_requeues_to_same_worker_with_report(self) -> None:
        first = "# Gate report\n- test: FAIL\n\ntests/test_flaky.py:7 failed"
        second = "# Gate report\n- test: FAIL\n\ntests/test_flaky.py:7 failed again"
        with self.patched(), mock.patch.object(
            dispatch, "run_gate",
            side_effect=[(False, first), (False, second), (True, "worker gate ok")],
        ) as gate:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

            self.assertEqual(gate.call_count, 2)
            self.assertEqual(self.events("escalate"), [])
            [requeue] = self.events("gate-requeue")
            self.assertEqual((requeue["pr"], requeue["head"], requeue["round"]),
                             (102, self.approved_head, 1))
            self.assertEqual(requeue["reports"], [first, second])
            self.assertTrue(requeue["unrelated"])
            self.assertIn(config.LABEL_AGENT, self.github.issue_labels)
            self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)
            self.assertEqual(self.github.assignees, [])

            [ready] = dispatch.frontier()
            dispatch.process_ticket(ready, 30, False)

            resolved = self.github.head()
            self.assertNotEqual(resolved, self.approved_head)
            self.assertEqual(
                git(self.origin, "merge-base", "main", resolved),
                git(self.origin, "rev-parse", "main"),
            )
            prompt = self.prompt_seen.read_text()
            self.assertIn("## Refresh gate failure, round 1", prompt)
            self.assertIn(first, prompt)
            self.assertIn(second, prompt)
            self.assertIn("failure appears unrelated to this PR's changes", prompt)
            self.assertEqual(gate.call_count, 3)
            self.assertEqual(self.events("escalate"), [])
            self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)

            dispatch.merge_pass_locked(False)

        self.assertTrue(self.github.merged)
        self.assertEqual(self.events("escalate"), [])

    def test_gate_failure_after_refresh_flags_failures_outside_pr_diff(self) -> None:
        self.cfg.review_rounds = 0
        report = "# Gate report\n- test: FAIL\n\napps/other/tests/test_status.py:12 failed"
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(False, report)
        ):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        [rerun] = self.events("gate-rerun")
        self.assertTrue(rerun["unrelated"])
        [escalation] = self.events("escalate")
        packet = Path(escalation["packet"]).read_text()
        self.assertIn("failure appears unrelated to this PR's changes", packet)
        self.assertIn("apps/other/tests/test_status.py:12 failed", packet)

    def test_gate_failure_after_refresh_past_round_limit_escalates(self) -> None:
        for round_number in range(1, self.cfg.review_rounds + 1):
            dispatch.record("gate-requeue", ticket=9, pr=102, round=round_number,
                            head=self.approved_head, reports=["old failure", "old failure"])
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(False, "persistent failure")
        ):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "gate_failed")
        self.assertIn("after 3 gate-resolution round(s)", escalation["reason"])
        self.assertEqual(len(self.events("gate-requeue")), 3)
        self.assertIn("persistent failure", Path(escalation["packet"]).read_text())
        self.assertIn(config.LABEL_HUMAN, self.github.issue_labels)

    def test_closed_ticket_gate_failure_after_refresh_escalates_without_requeue(self) -> None:
        closed_issue = {**self.github.issue(), "state": "CLOSED"}
        with self.patched(), mock.patch.object(self.github, "issue", return_value=closed_issue), \
                mock.patch.object(dispatch, "run_gate", return_value=(False, "persistent failure")) as gate:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertEqual(gate.call_count, 2)
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "gate_failed")
        self.assertIn("ticket is not open", escalation["reason"])
        self.assertEqual(self.events("gate-requeue"), [])
        self.assertIn(config.LABEL_HUMAN, self.github.issue_labels)


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



class RefreshGateFailureRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.root, self.origin = build_fork(self.tmp)
        git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "feature.txt").write_text("feature\n")
        git(self.origin, "add", "feature.txt")
        git(self.origin, "commit", "-qm", "agent change")
        self.pr_head = git(self.origin, "rev-parse", "HEAD")
        git(self.origin, "checkout", "-q", "main")
        (self.origin / "main.txt").write_text("main moved\n")
        git(self.origin, "add", "main.txt")
        git(self.origin, "commit", "-qm", "main change")
        self.cfg = config.Config(
            root=self.root,
            repo="acme/widgets",
            main="main",
            review_rounds=0,
            workers={"default": [sys.executable, "-c", "pass"]},
        )
        dispatch.configure(self.cfg)
        self.github = FakeGitHub(self.origin, dispatch.run)

    def patched(self):
        return mock.patch.multiple(dispatch, gh_json=self.github.gh_json, run=self.github.run)

    def events(self, name: str) -> list[dict]:
        return [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == name]

    def fail_refresh_gate(self) -> None:
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(False, "flaky failure")
        ):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

    def test_gate_failure_after_refresh_resets_worktree_to_pr_head_and_keeps_rebased_head_in_side_ref(
        self,
    ) -> None:
        self.fail_refresh_gate()

        wt = dispatch.FACTORY / "wt-9"
        self.assertEqual(git(wt, "rev-parse", "HEAD"), self.pr_head)
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "gate_failed")
        refreshed_head = escalation["refreshed_head"]
        evidence_ref = escalation["evidence_ref"]
        self.assertNotEqual(refreshed_head, self.pr_head)
        self.assertEqual(git(wt, "rev-parse", evidence_ref), refreshed_head)
        packet = Path(escalation["packet"]).read_text()
        self.assertIn(f"Refreshed head: `{refreshed_head}`", packet)
        self.assertIn(f"Evidence ref: `{evidence_ref}`", packet)
        self.assertEqual(
            git(
                wt,
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                ".",
                ":(exclude).factory-prompt.md",
                ":(exclude).factory",
            ),
            "",
        )

    def test_manager_fix_after_refresh_gate_failure_runs_worker_instead_of_refresh_head(self) -> None:
        self.fail_refresh_gate()
        [escalation] = self.events("escalate")
        logfile = self.tmp / "manager.log"
        with self.patched(), mock.patch.object(
            dispatch,
            "worker_round",
            return_value=(False, "still failing", logfile, self.pr_head),
        ) as worker:
            manage.apply(
                9,
                self.github.issue(),
                "FIX",
                "",
                {"worker": "default", "guidance": "fix the gate"},
                Path(escalation["packet"]),
            )

        worker.assert_called_once()
        self.assertNotIn("refresh_head", [event["code"] for event in self.events("escalate")])

    def test_second_refresh_after_gate_failure_does_not_withdraw_for_head_mismatch(self) -> None:
        self.fail_refresh_gate()
        self.fail_refresh_gate()

        self.assertEqual(
            [event["code"] for event in self.events("escalate")],
            ["gate_failed", "gate_failed"],
        )
        self.assertEqual(git(dispatch.FACTORY / "wt-9", "rev-parse", "HEAD"), self.pr_head)

    def assert_recovered(self, code: str, refreshed_head: str) -> None:
        wt = dispatch.FACTORY / "wt-9"
        self.assertEqual(git(wt, "rev-parse", "HEAD"), self.github.head())
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], code)
        self.assertEqual(escalation["refreshed_head"], refreshed_head)
        self.assertEqual(git(wt, "rev-parse", escalation["evidence_ref"]), refreshed_head)
        packet = Path(escalation["packet"]).read_text()
        self.assertIn(refreshed_head, packet)
        self.assertIn(escalation["evidence_ref"], packet)

    def test_failed_merge_refresh_preserves_merged_head_and_restores_pr_head(self) -> None:
        heads = []

        def gate(wt, *args, **kwargs):
            heads.append(git(wt, "rev-parse", "HEAD"))
            return False, "merge gate failure"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, True))

        self.assertEqual(len(git(self.root, "rev-list", "--parents", "-n1", heads[0]).split()), 3)
        self.assert_recovered("gate_failed", heads[0])

    def test_refresh_empty_preserves_refreshed_head_and_restores_pr_head(self) -> None:
        git(self.origin, "merge", "-q", "agent/9")
        main_head = git(self.origin, "rev-parse", "HEAD")
        with self.patched(), mock.patch.object(dispatch, "run_gate") as gate:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        gate.assert_not_called()
        self.assert_recovered("refresh_empty", main_head)

    def test_refresh_lease_refusal_restores_new_remote_head_and_keeps_evidence(self) -> None:
        heads = []

        def gate(wt, *args, **kwargs):
            heads.append(git(wt, "rev-parse", "HEAD"))
            git(self.origin, "checkout", "-q", "agent/9")
            (self.origin / "other.txt").write_text("another push\n")
            git(self.origin, "add", "other.txt")
            git(self.origin, "commit", "-qm", "concurrent push")
            git(self.origin, "checkout", "-q", "main")
            return True, "gate passed"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertNotEqual(self.github.head(), self.pr_head)
        self.assert_recovered("refresh_head", heads[0])

    def test_rejected_refresh_evidence_cleans_gate_artifacts_but_keeps_worker_notes(self) -> None:
        heads = []

        def gate(wt, *args, **kwargs):
            heads.append(git(wt, "rev-parse", "HEAD"))
            (wt / "feature.txt").write_text("gate changed a tracked file\n")
            (wt / "artifact").mkdir()
            (wt / "artifact" / "output.txt").write_text("gate output\n")
            (wt / ".factory-prompt.md").write_text("worker prompt\n")
            (wt / ".factory").mkdir(exist_ok=True)
            (wt / ".factory" / "handoff-9.md").write_text("worker handoff\n")
            return True, "gate passed"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assert_recovered("gate_failed", heads[0])
        wt = dispatch.FACTORY / "wt-9"
        self.assertEqual((wt / "feature.txt").read_text(), "feature\n")
        self.assertFalse((wt / "artifact").exists())
        self.assertEqual((wt / ".factory-prompt.md").read_text(), "worker prompt\n")
        self.assertEqual((wt / ".factory" / "handoff-9.md").read_text(), "worker handoff\n")

    def test_protected_refresh_failure_restores_pr_head_without_retry(self) -> None:
        heads = []

        def gate(wt, *args, **kwargs):
            heads.append(git(wt, "rev-parse", "HEAD"))
            return False, "- protected-paths: FAIL"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate) as check:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        check.assert_called_once()
        self.assert_recovered("protected_paths", heads[0])

    def test_gate_rerun_changing_head_preserves_candidate_before_restoring_pr_head(self) -> None:
        heads = []

        def gate(wt, *args, **kwargs):
            if not heads:
                heads.append(git(wt, "rev-parse", "HEAD"))
            else:
                git(wt, "commit", "--allow-empty", "-qm", "gate changed HEAD")
            return False, "failed gate"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate) as check:
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertEqual(check.call_count, 2)
        self.assert_recovered("refresh_head", heads[0])

    def test_refresh_gate_requeue_preserves_candidate_and_restores_pr_head(self) -> None:
        self.cfg.review_rounds = 3
        heads = []

        def gate(wt, *args, **kwargs):
            heads.append(git(wt, "rev-parse", "HEAD"))
            return False, "persistent failure"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate):
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertEqual(self.events("escalate"), [])
        [requeue] = self.events("gate-requeue")
        self.assertEqual(requeue["refreshed_head"], heads[0])
        self.assertEqual(git(self.root, "rev-parse", requeue["evidence_ref"]), heads[0])
        self.assertEqual(git(dispatch.FACTORY / "wt-9", "rev-parse", "HEAD"), self.pr_head)

if __name__ == "__main__":
    unittest.main()


class TriageExemptionRegateTest(unittest.TestCase):
    def setUp(self) -> None:
        from factory import triage

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.root, self.origin = build_fork(self.tmp)
        git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "fix.txt").write_text("fix\n")
        git(self.origin, "add", "fix.txt")
        git(self.origin, "commit", "-qm", "fix")
        git(self.origin, "checkout", "-q", "main")
        self.cfg = config.Config(root=self.root, repo="acme/widgets", main="main")
        dispatch.configure(self.cfg)
        self.wt = dispatch.ensure_worktree(9)
        self.issue = {
            "title": "bug", "body": "Fix the hardware failure.",
            "labels": [{"name": "bug"}],
            "comments": [{
                "author": {"login": "factory-bot"},
                "body": "Triage: hardware issue\n\nAgent brief: investigate\n"
                "No repro test: hardware-only triage reason\n"
                "factory-protected-override: comment is not override authority",
            }],
        }
        self.requests = []
        self.login = mock.patch.object(triage, "login", return_value="factory-bot")
        self.login.start()
        self.addCleanup(self.login.stop)

    def gh_json(self, args):
        fields = args[args.index("--json") + 1].split(",")
        if args[:2] == ["issue", "view"]:
            self.requests.append(fields)
            return {key: self.issue[key] for key in fields}
        self.assertEqual(args[:2], ["pr", "view"])
        pr = {
            "state": "OPEN", "headRefName": "agent/9", "baseRefName": "main",
            "headRefOid": git(self.wt, "rev-parse", "HEAD"), "reviewDecision": "",
        }
        return {key: pr[key] for key in fields}

    def assert_triage_flags(self, gate):
        self.assertEqual(gate.call_args.kwargs, {
            "protected_override": False, "bug": True,
            "no_repro_test": "hardware-only triage reason",
        })
        self.assertTrue(any("comments" in fields for fields in self.requests))

    def test_manager_fix_fetches_trusted_triage_comments_for_worker_gate(self) -> None:
        packet = self.tmp / "packet.md"
        packet.write_text("manager guidance")
        with mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json), \
                mock.patch.object(dispatch, "build_prompt", return_value="fix the bug"), \
                mock.patch.object(dispatch, "run_worker", return_value=(0, None)), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "ok")) as gate, \
                mock.patch.object(dispatch, "push_agent"), \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "ok", False)), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True):
            manage.apply(9, self.issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)

        self.assert_triage_flags(gate)

    def test_refresh_fetches_trusted_triage_comments_for_gate(self) -> None:
        (self.origin / "moved.txt").write_text("moved main\n")
        git(self.origin, "add", "moved.txt")
        git(self.origin, "commit", "-qm", "main moved")
        real_run = dispatch.run

        def run(cmd, *args, **kwargs):
            if cmd[0] == "gh":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, *args, **kwargs)

        with mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "ok")) as gate, \
                mock.patch.object(dispatch, "push_agent", return_value="pushed"), \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True):
            self.assertTrue(dispatch.refresh_pr_branch(9, 102, False))

        self.assert_triage_flags(gate)
