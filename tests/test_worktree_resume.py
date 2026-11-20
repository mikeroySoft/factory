"""#847: reused ticket worktrees must catch up before the worker, gate, and lease."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch, lifecycle
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

    def test_main_only_fetch_refspec_updates_stale_ticket_ref(self):
        self.prepare()
        child = self.advance_remote()
        self.git("config", "--replace-all", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
        self.assert_runs_at(child)

    def test_main_only_fetch_refspec_fetches_missing_ticket_ref(self):
        self.prepare()
        child = self.advance_remote()
        self.git("config", "--replace-all", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
        self.git("update-ref", "-d", "refs/remotes/origin/agent/7")
        self.assert_runs_at(child)

    def test_linear_worker_push_after_fast_forward_succeeds(self):
        self.prepare()
        child = self.advance_remote()
        self.worker.write_text(self.worker.read_text() + (
            "Path('change.txt').write_text('linear worker change\\n')\n"
            "subprocess.run(['git', 'add', 'change.txt'], check=True)\n"
            "subprocess.run(['git', 'commit', '-qm', 'linear worker'], check=True)\n"
        ))
        self.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.worker_head.read_text(), child)
        head = self.git("rev-parse", "agent/7").stdout.strip()
        self.assertEqual(self.git("rev-parse", f"{head}^").stdout.strip(), child)
        self.assertEqual(self.gate_head.read_text(), head)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], head)
        self.assertEqual(self.events_named("escalate"), [])
        self.assertTrue(self.events_named("approved"))

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

    def test_diverged_refresh_escalates_without_gate(self):
        self.prepare()
        remote = self.advance_remote()
        self.git("-C", str(self.wt), "commit", "--allow-empty", "-qm", "local fork")
        local = self.git("rev-parse", "agent/7").stdout.strip()
        with chdir(self.repo), mock.patch.dict("os.environ", self.env), \
                mock.patch.object(dispatch, "run_gate") as gate:
            dispatch.configure(config.load(self.repo))
            self.assertFalse(dispatch.refresh_pr_branch(7, 70, False))
        gate.assert_not_called()
        [escalation] = self.events_named("escalate")
        self.assertEqual(escalation["code"], "refresh_head")
        self.assertIn(local, escalation["reason"])
        self.assertIn(remote, escalation["reason"])
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), local)
        self.assertEqual(self.events_named("worktree-fast-forward"), [])

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

    def test_deleted_remote_branch_ignores_stale_tracking_ref(self):
        self.prepare()
        child = self.advance_remote()
        self.git("push", "origin", "--delete", "agent/7")
        self.git("update-ref", "refs/remotes/origin/agent/7", child)
        self.assert_runs_at(self.parent)
        self.assertEqual(self.events_named("worktree-fast-forward"), [])

    def failed_command(self, command, returncode=-15):
        run = dispatch.run

        def fail(cmd, *args, **kwargs):
            if cmd == command:
                if kwargs.get("check", True):
                    raise subprocess.CalledProcessError(returncode, cmd, "", "injected command failure")
                return subprocess.CompletedProcess(cmd, returncode, "", "injected command failure")
            return run(cmd, *args, **kwargs)

        return mock.patch.object(dispatch, "run", side_effect=fail)

    def test_equal_worktree_accepts_keyword_ticket_number(self):
        self.prepare()
        with chdir(self.repo), mock.patch.dict("os.environ", self.env):
            dispatch.configure(config.load(self.repo))
            self.assertEqual(dispatch.ensure_worktree(n=7), self.wt)
        self.assertEqual(self.git("-C", str(self.wt), "rev-parse", "HEAD").stdout.strip(), self.parent)

    def test_interrupted_local_lookup_does_not_reuse_branch(self):
        self.prepare()
        self.git("worktree", "remove", str(self.wt))
        with chdir(self.repo), mock.patch.dict("os.environ", self.env), \
                self.failed_command(["git", "rev-parse", "--verify", "agent/7"]):
            dispatch.configure(config.load(self.repo))
            with self.assertRaises(subprocess.CalledProcessError):
                dispatch.ensure_worktree(7)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), self.parent)

    def test_interrupted_missing_remote_lookup_starts_from_main(self):
        with chdir(self.repo), mock.patch.dict("os.environ", self.env), \
                self.failed_command(["git", "rev-parse", "--verify", "origin/agent/7"]):
            dispatch.configure(config.load(self.repo))
            wt = dispatch.ensure_worktree(7)
        self.assertEqual(
            self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip(),
            self.git("rev-parse", "origin/main").stdout.strip(),
        )

    def test_interrupted_ancestry_check_preserves_kept_head(self):
        self.prepare()
        remote = self.advance_remote()
        with chdir(self.repo), mock.patch.dict("os.environ", self.env), \
                self.failed_command(["git", "merge-base", "--is-ancestor", self.parent, remote]):
            dispatch.configure(config.load(self.repo))
            with self.assertRaises(dispatch.WorktreeRefused) as refused:
                dispatch.ensure_worktree(7)
        self.assertIn(self.parent, str(refused.exception))
        self.assertIn(remote, str(refused.exception))
        self.assertEqual(self.git("-C", str(self.wt), "rev-parse", "HEAD").stdout.strip(), self.parent)
        self.assertEqual(self.events_named("worktree-fast-forward"), [])

    def test_diverged_refresh_still_escalates_when_unapproval_fails(self):
        self.prepare()
        remote = self.advance_remote()
        self.git("-C", str(self.wt), "commit", "--allow-empty", "-qm", "local fork")
        local = self.git("rev-parse", "agent/7").stdout.strip()
        with chdir(self.repo), mock.patch.dict("os.environ", self.env), \
                self.failed_command(["gh", "pr", "edit", "70", "--repo", "local/smoke",
                                     "--remove-label", "factory-approved"], returncode=1), \
                mock.patch.object(dispatch, "run_gate") as gate:
            dispatch.configure(config.load(self.repo))
            self.assertFalse(dispatch.refresh_pr_branch(7, 70, False))
        gate.assert_not_called()
        [escalation] = self.events_named("escalate")
        self.assertEqual(escalation["code"], "refresh_head")
        self.assertIn(local, escalation["reason"])
        self.assertIn(remote, escalation["reason"])
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), local)

