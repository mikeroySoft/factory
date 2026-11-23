"""#890: backup refs, ancestry failures, and current-attempt handoff boundaries."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch
from tests import test_worktree_divergence as divergence
from tests import test_worktree_resume as resume


class WorktreeRefEdgesTest(unittest.TestCase):
    setUp = divergence.WorktreeDivergenceTest.setUp
    git = divergence.WorktreeDivergenceTest.git
    write_config = divergence.WorktreeDivergenceTest.write_config
    commit_config = divergence.WorktreeDivergenceTest.commit_config
    prepare = divergence.WorktreeDivergenceTest.prepare
    advance_remote = divergence.WorktreeDivergenceTest.advance_remote
    fork_history = divergence.WorktreeDivergenceTest.fork_history
    stale_notes = divergence.WorktreeDivergenceTest.stale_notes
    events_named = divergence.WorktreeDivergenceTest.events_named
    failed_command = resume.WorktreeResumeTest.failed_command

    def backup_refs(self):
        return self.git("for-each-ref", "--format=%(refname)", "refs/heads/backup/").stdout.split()

    def latest_comment(self):
        argv = json.loads(self.comments.read_text().splitlines()[-1])
        return argv[argv.index("--body") + 1]

    def assert_heads(self, local, remote, worktree=None):
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), local)
        self.assertEqual(
            self.git("-C", str(worktree or self.wt), "rev-parse", "HEAD").stdout.strip(), local,
        )
        self.assertEqual(
            self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], remote,
        )

    def test_new_backup_is_idempotent_and_keeps_displaced_commit_reachable(self):
        local, remote = self.fork_history()
        backup = f"refs/heads/backup/7-{local}"
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            self.assertEqual(dispatch._backup_worktree(7, local, remote), backup)
            self.assertEqual(dispatch._backup_worktree(7, local, remote), backup)
        [receipt] = self.events_named("worktree-backup")
        self.assertEqual(
            (receipt["ticket"], receipt["head"], receipt["remote"], receipt["backup_ref"]),
            (7, local, remote, backup),
        )
        self.assertEqual(self.backup_refs(), [backup])
        self.git("worktree", "remove", str(self.wt))
        self.git("branch", "-D", "agent/7")
        self.git("reflog", "expire", "--expire=now", "--all")
        self.git("gc", "--prune=now")
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), local)
        self.assertEqual(self.git("show", "-s", "--format=%s", backup).stdout.strip(), "local fork")
        self.assertEqual(self.git("cat-file", "-t", local).stdout.strip(), "commit")

    def test_existing_correct_backup_is_reused_without_a_new_receipt(self):
        local, remote = self.fork_history()
        backup = f"refs/heads/backup/7-{local}"
        self.git("update-ref", backup, local)
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            for _ in range(2):
                with self.assertRaises(dispatch.WorktreeDiverged) as refused:
                    dispatch.ensure_worktree(7)
                self.assertIn(backup, str(refused.exception))
                self.assertIn(local, str(refused.exception))
                self.assertIn(remote, str(refused.exception))
        self.assertEqual(self.backup_refs(), [backup])
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), local)
        self.assertEqual(self.events_named("worktree-backup"), [])
        self.assert_heads(local, remote)

    def test_conflicting_backup_is_not_overwritten_or_reported_as_preserved_divergence(self):
        local, remote = self.fork_history()
        backup = f"refs/heads/backup/7-{local}"
        self.git("update-ref", backup, remote)
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            with self.assertRaises(dispatch.WorktreeRefused) as refused:
                dispatch.ensure_worktree(7)
        self.assertNotIsInstance(refused.exception, dispatch.WorktreeDiverged)
        self.assertIn(backup, str(refused.exception))
        self.assertIn(local, str(refused.exception))
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), remote)
        self.assertEqual(self.backup_refs(), [backup])
        self.assertEqual(self.events_named("worktree-backup"), [])
        self.assertEqual(self.events_named("worktree-fast-forward"), [])
        self.assert_heads(local, remote)

    def test_set_aside_tree_with_conflicting_backup_stays_attached_and_untouched(self):
        local, remote = self.fork_history()
        kept = self.wt.with_name("wt-7-kept")
        self.git("worktree", "move", str(self.wt), str(kept))
        (kept / "change.txt").write_text("uncommitted kept edits\n")
        (kept / "untracked.txt").write_text("untracked kept edits\n")
        before = self.git("-C", str(kept), "status", "--porcelain").stdout
        backup = f"refs/heads/backup/7-{local}"
        self.git("update-ref", backup, remote)
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            with self.assertRaises(dispatch.WorktreeRefused):
                dispatch.ensure_worktree(7)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.git("-C", str(kept), "symbolic-ref", "HEAD").stdout.strip(), "refs/heads/agent/7")
        self.assertEqual(self.git("-C", str(kept), "status", "--porcelain").stdout, before)
        self.assertEqual((kept / "change.txt").read_text(), "uncommitted kept edits\n")
        self.assertEqual((kept / "untracked.txt").read_text(), "untracked kept edits\n")
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), remote)
        self.assertEqual(self.events_named("worktree-recovered"), [])
        self.assert_heads(local, remote, kept)

    def test_set_aside_tree_reuses_existing_backup_before_releasing_branch(self):
        local, remote = self.fork_history()
        backup = f"refs/heads/backup/7-{local}"
        self.git("update-ref", backup, local)
        kept = self.wt.with_name("wt-7-kept")
        self.git("worktree", "move", str(self.wt), str(kept))
        (kept / "change.txt").write_text("uncommitted kept edits\n")
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            self.assertEqual(dispatch.ensure_worktree(7), self.wt)
        self.assert_heads(remote, remote)
        self.assertEqual(self.git("-C", str(kept), "rev-parse", "HEAD").stdout.strip(), local)
        detached = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"], cwd=kept, env=self.env,
            capture_output=True, check=False,
        )
        self.assertEqual(detached.returncode, 1)
        self.assertEqual((kept / "change.txt").read_text(), "uncommitted kept edits\n")
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), local)
        self.assertEqual(self.events_named("worktree-backup"), [])
        [receipt] = self.events_named("worktree-recovered")
        self.assertEqual(
            (receipt["worktree"], receipt["from_head"], receipt["to_head"], receipt["backup_ref"]),
            (str(kept), local, remote, backup),
        )

    def test_backup_lookup_errors_do_not_create_a_ref_or_claim_preservation(self):
        local, remote = self.fork_history()
        backup = f"refs/heads/backup/7-{local}"
        command = ["git", "rev-parse", "--verify", "--quiet", backup]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            for returncode in (128, -15):
                with self.subTest(returncode=returncode), self.failed_command(command, returncode):
                    with self.assertRaises(dispatch.WorktreeRefused) as refused:
                        dispatch._backup_worktree(7, local, remote)
                    self.assertIn(backup, str(refused.exception))
                    self.assertIn(local, str(refused.exception))
        self.assertEqual(self.backup_refs(), [])
        self.assertEqual(self.events_named("worktree-backup"), [])
        self.assert_heads(local, remote)

    def test_ancestry_errors_in_either_direction_refuse_without_backing_up_or_moving_heads(self):
        local, remote = self.fork_history()
        before = self.git("-C", str(self.wt), "status", "--porcelain").stdout
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            for first, second in ((local, remote), (remote, local)):
                for returncode in (128, -15):
                    command = ["git", "merge-base", "--is-ancestor", first, second]
                    with self.subTest(first=first, returncode=returncode), self.failed_command(command, returncode):
                        with self.assertRaises(dispatch.WorktreeRefused) as refused:
                            dispatch.ensure_worktree(7)
                        self.assertNotIsInstance(refused.exception, dispatch.WorktreeDiverged)
                        for detail in ("ancestry check failed", local, remote, str(self.wt)):
                            self.assertIn(detail, str(refused.exception))
                        self.assert_heads(local, remote)
                        self.assertEqual(self.git("-C", str(self.wt), "status", "--porcelain").stdout, before)
        self.assertEqual(self.backup_refs(), [])
        self.assertEqual(self.events_named("worktree-backup"), [])
        self.assertEqual(self.events_named("worktree-fast-forward"), [])

    def test_handoff_selection_depends_on_launch_state_not_log_presence(self):
        self.prepare()
        notes = self.stale_notes()
        content = "OUTSIDE_HANDOFF_WINDOW\n" + "x" * 4100 + "\nCURRENT HANDOFF TAIL\n  "
        notes.write_text(content)
        log_path = self.base / "worker.log"
        log_path.write_text("worker output\n")
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            for pre_launch in (True, False):
                for worker_log in (None, log_path):
                    with self.subTest(pre_launch=pre_launch, worker_log=worker_log), \
                            mock.patch.object(dispatch, "_stop_worker_descendants") as cleanup:
                        dispatch.escalate(
                            7, "handoff boundary", worker_log, code="lease_refused", pre_launch=pre_launch,
                        )
                        packet = (self.repo / ".factory/escalations/7.md").read_text()
                        comment = self.latest_comment()
                        if pre_launch:
                            cleanup.assert_called_once_with(7)
                            self.assertIn("no current worker handoff", packet)
                            self.assertNotIn("CURRENT HANDOFF TAIL", packet)
                            self.assertNotIn("CURRENT HANDOFF TAIL", comment)
                        else:
                            cleanup.assert_not_called()
                            self.assertIn(content.strip()[-4000:], packet)
                            self.assertIn(content.strip()[-4000:], comment)
                        self.assertNotIn("OUTSIDE_HANDOFF_WINDOW", packet)
                        self.assertNotIn("OUTSIDE_HANDOFF_WINDOW", comment)
                        self.assertEqual(notes.read_text(), content)
                        self.assertEqual("Worker logs:" in comment, worker_log is not None)

    def test_prelaunch_backup_creation_failure_omits_old_handoff_and_cleans_descendants(self):
        local, remote = self.fork_history()
        notes = self.stale_notes()
        backup = f"refs/heads/backup/7-{local}"
        command = ["git", "update-ref", backup, local, ""]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                self.failed_command(command, 128), \
                mock.patch.object(dispatch, "_stop_worker_descendants") as cleanup:
            dispatch.configure(config.load(self.repo))
            with self.assertRaises(subprocess.CalledProcessError) as failure:
                dispatch.process_ticket({"number": 7, "title": "local change"}, 1, False, forced=True)
        self.assertEqual(failure.exception.cmd, command)
        self.assertFalse(self.worker_head.exists())
        self.assertFalse(self.gate_head.exists())
        self.assertEqual(self.events_named("attempt"), [])
        self.assertEqual(self.backup_refs(), [])
        self.assert_heads(local, remote)
        [escalation] = self.events_named("escalate")
        self.assertEqual(escalation["code"], "dispatcher_error")
        self.assertNotIn("STALE WORKER NOTES", (self.repo / ".factory/escalations/7.md").read_text())
        self.assertNotIn("STALE WORKER NOTES", self.latest_comment())
        self.assertIn("STALE WORKER NOTES", notes.read_text())
        cleanup.assert_called_once_with(7)

    def test_postworker_push_refusal_keeps_current_handoff_without_prelaunch_cleanup(self):
        self.prepare()
        notes = self.stale_notes()
        self.worker.write_text(self.worker.read_text() + (
            "notes = Path('.factory/handoff-7.md')\n"
            "notes.parent.mkdir(exist_ok=True)\n"
            "notes.write_text('CURRENT WORKER HANDOFF\\n')\n"
        ))
        command = [
            "git", "push", f"--force-with-lease=agent/7:{self.parent}",
            "origin", f"{self.parent}:refs/heads/agent/7",
        ]
        real_run = dispatch.run

        def refuse_push(cmd, *args, **kwargs):
            if cmd == command:
                return subprocess.CompletedProcess(cmd, 1, "", "[rejected] stale info")
            return real_run(cmd, *args, **kwargs)

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "run", side_effect=refuse_push), \
                mock.patch.object(dispatch, "_stop_worker_descendants") as cleanup:
            dispatch.configure(config.load(self.repo))
            dispatch.process_ticket({"number": 7, "title": "local change"}, 1, False, forced=True)
        self.assertEqual(self.worker_head.read_text(), self.parent)
        self.assertEqual(self.gate_head.read_text(), self.parent)
        self.assertTrue(self.events_named("attempt"))
        [escalation] = self.events_named("escalate")
        self.assertEqual(escalation["code"], "lease_refused")
        self.assertIn("CURRENT WORKER HANDOFF", (self.repo / ".factory/escalations/7.md").read_text())
        self.assertIn("CURRENT WORKER HANDOFF", self.latest_comment())
        self.assertNotIn("STALE WORKER NOTES", self.latest_comment())
        self.assertEqual(notes.read_text(), "CURRENT WORKER HANDOFF\n")
        self.assert_heads(self.parent, self.parent)
        cleanup.assert_not_called()
