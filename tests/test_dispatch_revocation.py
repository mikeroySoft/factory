"""Revoking a live ticket claim ends work, publication and review until a new pass."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import test_refresh_conflict as fixtures
from factory import config, dispatch, lifecycle, manage


class DispatchRevocationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.root, self.origin = fixtures.build_fork(self.tmp)
        fixtures.git(self.origin, "branch", "agent/9")
        self.cfg = config.Config(root=self.root, repo="local/revocation", idle_timeout=0, exit_grace=0,
                                 workers={"default": [sys.executable, "-c", "pass"]})
        dispatch.configure(self.cfg)
        self.wt = dispatch.ensure_worktree(9)
        dispatch.LOGS.mkdir()
        self.github = fixtures.FakeGitHub(self.origin, dispatch.run)
        self.marker = self.tmp / "revoked"
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.github.run))
        self.enterContext(mock.patch.object(dispatch, "WATCH_POLL", 0.02))
        self.packet = self.tmp / "packet.md"
        self.packet.write_text("manager guidance")
        self.lease = self.github.head()

    def gh_json(self, args):
        if args == ["api", "user"]:
            return {"login": "Factory"}
        if self.marker.exists():
            self.github.assignees = []
        return self.github.gh_json(args)

    def events(self, kind):
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == kind]

    def local_commit(self):
        fixtures.git(self.wt, "commit", "--allow-empty", "-qm", "unpublished worker work")
        return fixtures.git(self.wt, "rev-parse", "HEAD")

    def assert_push_refused(self, head, reason):
        try:
            dispatch.push_agent(self.wt, 9, self.lease)
        except Exception as exc:
            self.assertIsInstance(exc, dispatch.TicketRunStopped)
        self.assertEqual(self.github.head(), self.lease)
        self.assertEqual(fixtures.git(self.wt, "rev-parse", "HEAD"), head)
        [row] = self.events("push-refused")[-1:]
        self.assertEqual((row["ticket"], row["head"], row["reason"]), (9, head, reason))

    def test_escalate_mid_pass_blocks_manager_fix_push_and_review(self):
        stale = self.github.issue()
        head = self.local_commit()
        dispatch.record("attempt", ticket=9, head=head)
        dispatch.escalate(9, "gate failed", None, code="gate_failed")
        with mock.patch.object(dispatch, "worker_round", return_value=(True, "PASS", None, head)) as worker, \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=(None, "", False)) as review:
            manage.apply(9, stale, "FIX", "", {"worker": "default", "guidance": "fix"}, self.packet)
        worker.assert_not_called()
        review.assert_not_called()
        self.assert_push_refused(head, "ready-for-human")
        [row] = self.events("worker-stopped")
        self.assertEqual((row["ticket"], row["head"], row["reason"]), (9, head, "ready-for-human"))

    def test_unassign_running_worker_stops_group_and_refuses_push(self):
        child_pid = self.tmp / "child.pid"
        script = (
            "import subprocess, sys, time\nfrom pathlib import Path\n"
            "subprocess.run(['git', 'commit', '--allow-empty', '-qm', 'worker work'], check=True)\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"Path({str(child_pid)!r}).write_text(str(child.pid))\n"
            f"Path({str(self.marker)!r}).touch()\n"
            "time.sleep(2)\n"
        )
        self.cfg.workers = {"default": [sys.executable, "-c", script]}
        with mock.patch.object(dispatch, "build_prompt", return_value="work"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
            try:
                dispatch.worker_round(9, self.wt, {config.LABEL_AGENT}, "change", "", 1, time.monotonic() + 5)
            except Exception as exc:
                self.assertIsInstance(exc, dispatch.TicketRunStopped)
        gate.assert_not_called()
        self.assertTrue(self.marker.exists())
        head = fixtures.git(self.wt, "rev-parse", "HEAD")
        self.assertNotEqual(head, self.lease)
        self.assert_push_refused(head, "unassigned")
        [stopped] = self.events("worker-stopped")
        self.assertEqual((stopped["ticket"], stopped["reason"], stopped["head"]), (9, "unassigned", head))
        # A killed descendant may briefly remain as a zombie until its parent is reaped.
        stat = Path(f"/proc/{child_pid.read_text()}/stat")
        if stat.exists():
            self.assertEqual(stat.read_text().split()[2], "Z")

    def test_human_requeue_next_pass_claims_works_and_pushes(self):
        dispatch.escalate(9, "gate failed", None, code="gate_failed")
        self.github.issue_labels = {config.LABEL_AGENT}
        self.github.assignees = []
        dispatch.configure(self.cfg)  # the next dispatcher pass, not a resumed revoked run
        with mock.patch.object(dispatch, "build_prompt", return_value="work"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")), \
                mock.patch.object(dispatch, "record_risk"), \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "", False)) as review, \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True), \
                mock.patch.object(dispatch, "open_blockers", return_value=[]):
            self.cfg.workers = {"default": [sys.executable, "-c",
                "import subprocess; subprocess.run(['git','commit','--allow-empty','-qm','requeued work'], check=True)"]}
            dispatch.process_ticket(self.github.issue(), 1, False)
        head = fixtures.git(self.wt, "rev-parse", "HEAD")
        self.assertNotEqual(head, self.lease)
        self.assertEqual(self.github.head(), head)
        self.assertEqual(self.github.assignees, ["factory"])
        review.assert_called_once()
        self.assertEqual(self.events("push-refused"), [])


if __name__ == "__main__":
    unittest.main()
