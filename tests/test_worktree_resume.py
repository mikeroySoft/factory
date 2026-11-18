"""#847: reused ticket worktrees must catch up before the worker, gate, and lease."""
from __future__ import annotations

import json
import sys
import unittest

from factory import lifecycle
from tests import test_dispatch_lifecycle as smoke


class WorktreeResumeTest(unittest.TestCase):
    setUp = smoke.LocalCLI.setUp
    git = smoke.LocalCLI.git
    write_config = smoke.LocalCLI.write_config
    commit_config = smoke.LocalCLI.commit_config
    cli = smoke.LocalCLI.cli

    def prepare(self):
        self.worker_head = self.base / "worker-head"
        self.gate_head = self.base / "gate-head"
        probe = (
            "import subprocess\nfrom pathlib import Path\n"
            "head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
        )
        self.worker.write_text(probe + f"Path({str(self.worker_head)!r}).write_text(head)\n")
        self.write_config(check=[sys.executable, "-c", probe + f"Path({str(self.gate_head)!r}).write_text(head)\n"])
        self.commit_config()
        self.state.write_text(json.dumps({"reviews": 1}))
        self.wt = self.repo / ".factory" / "wt-7"
        self.git("worktree", "add", "-b", "agent/7", str(self.wt), "main")
        (self.wt / "change.txt").write_text("original work\n")
        self.git("-C", str(self.wt), "add", "change.txt")
        self.git("-C", str(self.wt), "commit", "-qm", "original work")
        self.git("push", "origin", "agent/7")
        self.parent = self.git("rev-parse", "agent/7").stdout.strip()

    def advance_remote(self):
        # Another host pushes; neither the local branch nor its tracking ref moves.
        origin = self.base / "origin.git"
        tree = self.git("rev-parse", f"{self.parent}^{{tree}}").stdout.strip()
        child = self.git("commit-tree", tree, "-p", self.parent, "-m", "external refresh").stdout.strip()
        self.git("push", "origin", f"{child}:agent/7")
        self.git("update-ref", "refs/remotes/origin/agent/7", self.parent)
        self.assertEqual(self.git("--git-dir", str(origin), "rev-parse", "agent/7").stdout.strip(), child)
        return child

    def events_named(self, name):
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == name]

    def assert_runs_at(self, head):
        self.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.worker_head.read_text(), head)
        self.assertEqual(self.gate_head.read_text(), head)
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), head)
        self.assertEqual(self.git("-C", str(self.wt), "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(self.events_named("claimed")[0]["base"], head)
        self.assertEqual(self.events_named("escalate"), [])
        self.assertTrue(self.events_named("approved"))  # includes a real leased push

    def test_resume_fast_forwards_before_worker_gate_and_lease(self):
        self.prepare()
        child = self.advance_remote()
        self.assert_runs_at(child)
        [receipt] = self.events_named("worktree-fast-forward")
        self.assertEqual((receipt["ticket"], receipt["from_head"], receipt["to_head"]), (7, self.parent, child))

    def test_equal_head_proceeds_unchanged(self):
        self.prepare()
        self.assert_runs_at(self.parent)
        self.assertEqual(self.events_named("worktree-fast-forward"), [])

    def test_local_branch_without_worktree_also_fast_forwards(self):
        self.prepare()
        child = self.advance_remote()
        self.git("worktree", "remove", str(self.wt))
        self.assert_runs_at(child)

    def test_local_ahead_commits_are_kept(self):
        self.prepare()
        self.git("-C", str(self.wt), "commit", "--allow-empty", "-qm", "unpublished work")
        local = self.git("rev-parse", "agent/7").stdout.strip()
        self.assert_runs_at(local)
        self.assertEqual(self.events_named("worktree-fast-forward"), [])

    def assert_refused(self, local, remote):
        before = self.git("-C", str(self.wt), "status", "--porcelain").stdout
        self.cli("dispatch", "--ticket", "7")
        [escalation] = self.events_named("escalate")
        self.assertIn(local, escalation["reason"])
        self.assertIn(remote, escalation["reason"])
        self.assertFalse(self.worker_head.exists())
        self.assertFalse(self.gate_head.exists())
        self.assertEqual(self.events_named("attempt"), [])
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), local)
        self.assertEqual(self.git("-C", str(self.wt), "status", "--porcelain").stdout, before)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], remote)

    def test_diverged_heads_escalate_without_worker_or_gate(self):
        self.prepare()
        remote = self.advance_remote()
        self.git("-C", str(self.wt), "commit", "--allow-empty", "-qm", "local fork")
        local = self.git("rev-parse", "agent/7").stdout.strip()
        self.assert_refused(local, remote)

    def check_dirty(self, kind):
        self.prepare()
        remote = self.advance_remote()
        path = self.wt / ("untracked.txt" if kind == "untracked" else "change.txt")
        path.write_text("keep these edits\n")
        if kind == "staged":
            self.git("-C", str(self.wt), "add", path.name)
        self.assert_refused(self.parent, remote)
        self.assertEqual(path.read_text(), "keep these edits\n")

    def test_unstaged_work_is_preserved(self):
        self.check_dirty("unstaged")

    def test_staged_work_is_preserved(self):
        self.check_dirty("staged")

    def test_untracked_work_is_preserved(self):
        self.check_dirty("untracked")

    def test_missing_remote_branch_preserves_unpublished_work(self):
        self.prepare()
        self.git("push", "origin", "--delete", "agent/7")
        self.assert_runs_at(self.parent)

    def test_fetch_failure_never_runs_stale_code(self):
        self.prepare()
        self.git("remote", "set-url", "origin", str(self.base / "missing-origin"))
        self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertFalse(self.worker_head.exists())
        self.assertFalse(self.gate_head.exists())
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), self.parent)
        self.assertTrue(self.events_named("escalate"))
