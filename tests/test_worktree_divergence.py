"""#890: divergent resumes retain evidence, not stale notes or owned daemons."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as smoke
from tests import test_worktree_resume as resume


class WorktreeDivergenceTest(unittest.TestCase):
    setUp = smoke.LocalCLI.setUp
    git = smoke.LocalCLI.git
    write_config = smoke.LocalCLI.write_config
    commit_config = smoke.LocalCLI.commit_config
    cli = smoke.LocalCLI.cli
    prepare = resume.WorktreeResumeTest.prepare
    advance_remote = resume.WorktreeResumeTest.advance_remote
    events_named = resume.WorktreeResumeTest.events_named

    def fork_history(self):
        self.prepare()
        remote = self.advance_remote()
        self.git("-C", str(self.wt), "commit", "--allow-empty", "-qm", "local fork")
        return self.git("rev-parse", "agent/7").stdout.strip(), remote

    def stale_notes(self):
        handoff = self.wt / ".factory" / "handoff-7.md"
        handoff.parent.mkdir(exist_ok=True)
        handoff.write_text("STALE WORKER NOTES FROM AN EARLIER ATTEMPT\n")
        self.comments = self.base / "comments.jsonl"
        gh = self.bin / "gh"
        gh.write_text(smoke.GH.replace(
            'a = sys.argv[1:]',
            'a = sys.argv[1:]\n'
            'if a[:2] == ["issue", "comment"]:\n'
            f'    with Path({str(self.comments)!r}).open("a") as out:\n'
            '        out.write(json.dumps(a) + "\\n")',
        ))
        return handoff

    def assert_fresh_escalation(self):
        packet = self.repo / ".factory" / "escalations" / "7.md"
        self.assertNotIn("STALE WORKER NOTES", packet.read_text())
        self.assertNotIn("STALE WORKER NOTES", self.comments.read_text())

    def test_diverged_resume_preserves_backup_and_escalates_only_once(self):
        local, remote = self.fork_history()
        handoff = self.stale_notes()
        first = self.cli("dispatch", "--ticket", "7")
        self.cli("dispatch", "--ticket", "7")  # owner requeue / RETRY of the same pair
        escalations = self.events_named("escalate")
        self.assertEqual(len(escalations), 1)
        reason = escalations[0]["reason"]
        self.assertIn("neither-is-ancestor", reason)
        self.assertIn(local, reason)
        self.assertIn(remote, reason)
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/heads/backup/").stdout.split()
        self.assertEqual(len(refs), 1)
        backup = refs[0]
        self.assertEqual(self.git("rev-parse", backup).stdout.strip(), local)
        self.assertIn(backup, reason)
        self.assertIn(backup, first.stdout + first.stderr)
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), local)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], remote)
        self.assertEqual(self.events_named("attempt"), [])
        self.assertFalse(self.worker_head.exists())
        self.assertFalse(self.gate_head.exists())
        self.assert_fresh_escalation()
        self.assertIn("STALE WORKER NOTES", handoff.read_text())  # retained, not erased

    def test_prelaunch_escalation_omits_previous_attempt_handoff(self):
        self.fork_history()
        self.stale_notes()
        self.cli("dispatch", "--ticket", "7")
        self.assert_fresh_escalation()

    def start_orphan(self, ticket=7):
        pidfile = self.base / f"daemon-{ticket}-{time.monotonic_ns()}.pid"
        child = f"from pathlib import Path; import os,time; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(120)"
        parent = (
            "import subprocess,sys\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True, "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        )
        with lifecycle.scope(self.events, "ticket", ticket=ticket), \
                lifecycle.scope(self.events, "worker") as execution:
            proc = subprocess.Popen([sys.executable, "-c", parent], cwd=self.wt,
                                    env=execution.env(), start_new_session=True)
            execution.child(proc.pid)
            self.assertEqual(proc.wait(timeout=10), 0)
            execution.child_done(proc.pid)
        deadline = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = int(pidfile.read_text())
        identity = lifecycle._identity(pid)
        self.addCleanup(self.kill_if_alive, identity)
        self.assertEqual(lifecycle._process_state(identity), "alive")
        return identity

    @staticmethod
    def kill_if_alive(identity):
        if lifecycle._process_state(identity) == "alive":
            os.kill(identity["pid"], signal.SIGKILL)

    def assert_dead(self, identity):
        deadline = time.monotonic() + 5
        while lifecycle._process_state(identity) == "alive" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(lifecycle._process_state(identity), "dead")

    def test_prelaunch_refusals_stop_only_this_tickets_owned_daemons(self):
        local, remote = self.fork_history()
        self.stale_notes()
        for refusal in ("lease", "divergence"):
            with self.subTest(refusal=refusal):
                owned = self.start_orphan()
                foreign = self.start_orphan(ticket=8)
                unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                             cwd=self.wt, start_new_session=True)
                self.addCleanup(unrelated.wait)
                self.addCleanup(unrelated.kill)
                with chdir(self.repo), mock.patch.dict(os.environ, self.env):
                    dispatch.configure(config.load(self.repo))
                    if refusal == "lease":
                        error = dispatch.LeaseRefused(
                            subprocess.CompletedProcess(["git", "push"], 1, "", ""),
                            "injected pre-launch lease refusal",
                        )
                        patch = mock.patch.object(dispatch, "agent_lease", side_effect=error)
                        # An equal worktree reaches lease acquisition without launching a worker.
                        worktree = mock.patch.object(dispatch, "ensure_worktree", return_value=self.wt)
                    else:
                        patch, worktree = mock.patch.object(dispatch, "run_worker"), mock.patch.object(dispatch, "run_gate")
                    with patch, worktree:
                        dispatch.process_ticket({"number": 7, "title": "local change"}, 1, False, forced=True)
                self.assert_dead(owned)
                self.assertEqual(lifecycle._process_state(foreign), "alive")
                self.assertIsNone(unrelated.poll())
                self.assert_fresh_escalation()
                self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), local)
                self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], remote)
                self.kill_if_alive(foreign)
                unrelated.kill()
                unrelated.wait()

    def test_normal_worker_exit_reaps_background_process_group(self):
        self.prepare()
        pidfile = self.base / "background.pid"
        child = (
            "import os,time,json; from pathlib import Path; from factory import lifecycle; "
            f"Path({str(pidfile)!r}).write_text(json.dumps(lifecycle._identity(os.getpid()))); time.sleep(120)"
        )
        parent = (
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            f"while not Path({str(pidfile)!r}).exists(): time.sleep(0.01)\n"
        )
        with mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            code, timeout = dispatch.run_worker([sys.executable, "-c", parent], self.wt,
                                                self.base / "worker.log", self.base / "handoff")
        identity = json.loads(pidfile.read_text())
        self.addCleanup(self.kill_if_alive, identity)
        self.assertEqual((code, timeout), (0, None))
        self.assert_dead(identity)
