"""Revoking a live ticket claim ends work, publication and review until a new pass."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_refresh_conflict as fixtures


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

    def assert_child_stopped(self, pid):
        stat = Path(f"/proc/{pid}/stat")
        deadline = time.monotonic() + 2
        while stat.exists():
            try:
                state = stat.read_text().split()[2]
            except FileNotFoundError:
                return
            if state == "Z":
                return
            if time.monotonic() >= deadline:
                self.fail(f"worker descendant {pid} still running ({state})")
            time.sleep(0.01)

    def local_commit(self):
        fixtures.git(self.wt, "commit", "--allow-empty", "-qm", "unpublished worker work")
        return fixtures.git(self.wt, "rev-parse", "HEAD")

    def assert_push_refused(self, head, reason):
        with self.assertRaises(dispatch.TicketRunStopped):
            dispatch.push_agent(self.wt, 9, self.lease)
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
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate, \
                self.assertRaises(dispatch.TicketRunStopped):
            dispatch.worker_round(9, self.wt, {config.LABEL_AGENT}, "change", "", 1, time.monotonic() + 5)
        gate.assert_not_called()
        self.assertTrue(self.marker.exists())
        head = fixtures.git(self.wt, "rev-parse", "HEAD")
        self.assertNotEqual(head, self.lease)
        self.assert_push_refused(head, "unassigned")
        [stopped] = self.events("worker-stopped")
        self.assertEqual((stopped["ticket"], stopped["reason"], stopped["head"]), (9, "unassigned", head))
        # A killed descendant may briefly remain as a zombie until its parent is reaped.
        self.assert_child_stopped(child_pid.read_text())

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

    def test_fresh_manager_read_blocks_human_or_unassigned_ticket(self):
        stale = self.github.issue()
        for reason in ("ready-for-human", "unassigned"):
            with self.subTest(reason=reason):
                dispatch.configure(self.cfg)
                self.github.issue_labels = {config.LABEL_HUMAN} if reason == "ready-for-human" else {config.LABEL_AGENT}
                self.github.assignees = ["factory"] if reason == "ready-for-human" else []
                with mock.patch.object(dispatch, "worker_round") as worker, \
                        mock.patch.object(dispatch, "push_agent") as push, \
                        mock.patch.object(dispatch, "pr_gate_and_review") as review:
                    manage.apply(9, stale, "FIX", "", {"worker": "default", "guidance": "fix"}, self.packet)
                worker.assert_not_called()
                push.assert_not_called()
                review.assert_not_called()
                self.assertEqual(self.events("worker-stopped")[-1]["reason"], reason)

    def test_push_rechecks_claim_after_waiting_for_remote_lock(self):
        head = self.local_commit()
        original = self.gh_json
        reads = 0

        def read(args):
            nonlocal reads
            if args[:2] == ["issue", "view"]:
                reads += 1
                if reads == 2:
                    self.github.assignees = []
            return original(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
                self.assertRaises(dispatch.TicketRunStopped):
            dispatch.push_agent(self.wt, 9, self.lease, check=False)
        self.assertEqual(self.github.head(), self.lease)
        self.assertEqual(fixtures.git(self.wt, "rev-parse", "HEAD"), head)
        self.assertEqual(self.events("push-refused")[-1]["reason"], "unassigned")

    def test_post_escalation_head_never_launches_ticket_review(self):
        self.github.issue_labels = {config.LABEL_HUMAN}
        head = self.local_commit()
        with mock.patch.object(dispatch, "run_reviewer") as reviewer, \
                self.assertRaises(dispatch.TicketRunStopped):
            dispatch.review(self.wt, 9, "PASS", head)
        reviewer.assert_not_called()
        self.assertEqual(self.events("review"), [])
        self.assertEqual(self.events("review-refused")[-1]["head"], head)

    def test_unreadable_claim_refuses_push_without_changing_remote(self):
        head = self.local_commit()
        with mock.patch.object(dispatch, "gh_json", side_effect=OSError("offline")):
            self.assert_push_refused(head, "claim-unavailable")

    def test_human_label_stops_a_running_worker_even_with_assignment_retained(self):
        def read(args):
            if args == ["api", "user"]:
                return {"login": "factory"}
            if self.marker.exists():
                self.github.issue_labels = {config.LABEL_HUMAN}
            return self.github.gh_json(args)

        script = f"from pathlib import Path; import time; Path({str(self.marker)!r}).touch(); time.sleep(5)"
        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
                self.assertRaises(dispatch.TicketRunStopped) as stopped:
            dispatch.run_worker(
                [sys.executable, "-c", script], self.wt, self.tmp / "worker.log",
                self.tmp / "handoff.md", ticket=9,
            )
        self.assertEqual(stopped.exception.reason, "ready-for-human")
        self.assertEqual(self.github.assignees, ["factory"])
        self.assertEqual(self.events("worker-stopped")[-1]["reason"], "ready-for-human")

    def test_revoked_fast_exit_stops_surviving_worker_descendants(self):
        pid = self.tmp / "child.pid"
        script = (
            "import subprocess, sys\nfrom pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
            f"Path({str(pid)!r}).write_text(str(child.pid))\n"
            f"Path({str(self.marker)!r}).touch()\n"
        )
        with self.assertRaises(dispatch.TicketRunStopped):
            dispatch.run_worker(
                [sys.executable, "-c", script], self.wt, self.tmp / "worker.log",
                self.tmp / "handoff.md", ticket=9,
            )
        self.assert_child_stopped(pid.read_text())

    def test_unassignment_cannot_be_reclaimed_next_pass_without_human_requeue(self):
        from datetime import UTC, datetime

        head = self.local_commit()
        self.github.assignees = []
        self.assert_push_refused(head, "unassigned")
        dispatch.configure(self.cfg)
        timeline = [{"event": "unassigned", "assignee": {"login": "factory"},
                     "created_at": datetime.now(UTC).isoformat()}]
        with mock.patch("factory.handoff.timeline", return_value=timeline), \
                mock.patch.object(dispatch, "worker_round", side_effect=AssertionError("revoked ticket launched a worker")) as worker:
            dispatch.process_ticket(self.github.issue(), 1, False)
        worker.assert_not_called()
        self.assertEqual(self.github.assignees, [])
        self.assertEqual(self.github.head(), self.lease)
        self.github.assignees = ["factory"]  # Reassignment alone is not a human requeue.
        with mock.patch("factory.handoff.timeline", return_value=timeline), \
                mock.patch.object(dispatch, "worker_round") as worker:
            manage.apply(9, self.github.issue(), "FIX", "", {"worker": "default", "guidance": "fix"}, self.packet)
        worker.assert_not_called()
        self.github.assignees = []

        timeline.append({"event": "labeled", "label": {"name": config.LABEL_AGENT},
                         "actor": {"login": "owner", "type": "User"},
                         "created_at": datetime.now(UTC).isoformat()})
        dispatch.configure(self.cfg)
        with mock.patch("factory.handoff.timeline", return_value=timeline), \
                mock.patch.object(dispatch, "build_prompt", return_value="work"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")), \
                mock.patch.object(dispatch, "record_risk"), \
                mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "", False)) as review, \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True):
            dispatch.process_ticket(self.github.issue(), 1, False)
        self.assertEqual(self.github.assignees, ["factory"])
        self.assertEqual(self.github.head(), head)
        review.assert_called_once()


if __name__ == "__main__":
    unittest.main()
