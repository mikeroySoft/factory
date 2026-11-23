"""#890 review: displaced branches and pre-launch escalation boundaries."""
from __future__ import annotations

import os
import subprocess
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch
from tests import test_worktree_divergence as divergence


class WorktreeRecoveryTest(unittest.TestCase):
    setUp = divergence.WorktreeDivergenceTest.setUp
    git = divergence.WorktreeDivergenceTest.git
    write_config = divergence.WorktreeDivergenceTest.write_config
    commit_config = divergence.WorktreeDivergenceTest.commit_config
    cli = divergence.WorktreeDivergenceTest.cli
    prepare = divergence.WorktreeDivergenceTest.prepare
    advance_remote = divergence.WorktreeDivergenceTest.advance_remote
    fork_history = divergence.WorktreeDivergenceTest.fork_history
    stale_notes = divergence.WorktreeDivergenceTest.stale_notes
    events_named = divergence.WorktreeDivergenceTest.events_named

    def test_set_aside_divergent_tree_frees_branch_and_starts_worker_at_pr_head(self):
        local, remote = self.fork_history()
        self.cli("dispatch", "--ticket", "7")
        kept = self.wt.with_name("wt-7-kept-" + local[:8])
        self.git("worktree", "move", str(self.wt), str(kept))
        (kept / "change.txt").write_text("uncommitted kept edits\n")
        (kept / "untracked.txt").write_text("untracked kept edits\n")
        self.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.worker_head.read_text(), remote)
        self.assertEqual(self.gate_head.read_text(), remote)
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), remote)
        self.assertEqual(self.git("-C", str(kept), "rev-parse", "HEAD").stdout.strip(), local)
        detached = self.git("-C", str(kept), "symbolic-ref", "-q", "HEAD", check=False)
        self.assertEqual(detached.returncode, 1)
        backup = f"refs/heads/backup/7-{local}"
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), local)
        self.assertEqual((kept / "change.txt").read_text(), "uncommitted kept edits\n")
        self.assertEqual((kept / "untracked.txt").read_text(), "untracked kept edits\n")
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], remote)
        [recovery] = self.events_named("worktree-recovered")
        self.assertEqual((recovery["from_head"], recovery["to_head"], recovery["backup_ref"]),
                         (local, remote, backup))
        self.assertTrue(self.events_named("approved"))

    def test_postworker_escalation_without_log_keeps_notes_and_does_not_clean_workers(self):
        self.prepare()
        notes = self.stale_notes()
        notes.write_text("CURRENT WORKER HANDOFF\n")
        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "_stop_worker_descendants") as cleanup:
            dispatch.configure(config.load(self.repo))
            dispatch.escalate(7, "CI failed after worker", None, code="ci_failed")
        self.assertIn("CURRENT WORKER HANDOFF", (self.repo / ".factory/escalations/7.md").read_text())
        self.assertIn("CURRENT WORKER HANDOFF", self.comments.read_text())
        cleanup.assert_not_called()

    def test_cleanup_failure_still_records_escalation_and_releases_claim(self):
        self.prepare()
        self.stale_notes()
        refusal = dispatch.LeaseRefused(
            subprocess.CompletedProcess(["git", "push"], 1, "", ""), "pre-launch lease refused",
        )
        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "agent_lease", side_effect=refusal), \
                mock.patch.object(dispatch, "_stop_worker_descendants",
                                  side_effect=RuntimeError("worker descendants still alive: [12345]")), \
                mock.patch.object(dispatch, "unclaim", wraps=dispatch.unclaim) as unclaim:
            dispatch.configure(config.load(self.repo))
            dispatch.process_ticket({"number": 7, "title": "local change"}, 1, False, forced=True)
        unclaim.assert_called_once_with(7, "--remove-label", dispatch.LABEL_AGENT,
                                      "--add-label", dispatch.LABEL_HUMAN)
        [escalation] = self.events_named("escalate")
        self.assertEqual(escalation["code"], "lease_refused")
        packet = (self.repo / ".factory/escalations/7.md").read_text()
        self.assertIn("worker descendants still alive: [12345]", packet)
        self.assertNotIn("STALE WORKER NOTES", packet)
        self.assertFalse(self.worker_head.exists())
        self.assertFalse(self.gate_head.exists())
        [failure] = self.events_named("worker-cleanup-failed")
        self.assertIn("12345", failure["error"])
