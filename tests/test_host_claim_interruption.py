"""Expired own-host claims must not strand a PR after an interrupted attempt."""
import copy
import subprocess
import unittest
from contextlib import chdir
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as smoke
from tests import test_host_claims
from tests import test_worktree_resume as resume


class HostClaimInterruptionTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def interrupt_pr_gate(self, module):
        def killed(*args):
            self.github.open_prs = [{"number": 70}]
            raise KeyboardInterrupt("host killed during PR gate")

        with mock.patch.object(module, "pr_gate_and_review", side_effect=killed), \
                self.assertRaises(KeyboardInterrupt):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        return module.host_claims(7)[0]

    def expire(self, module):
        clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
        clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)

    def post_escalations(self, module):
        run = module.run

        def post(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "comment"]:
                self.github.json(module.cfg.host_name, [
                    "api", "repos/local/claims/issues/7/comments", "--method", "POST",
                    "-f", f"body={cmd[cmd.index('--body') + 1]}",
                ])
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return run(cmd, *args, **kwargs)

        return mock.patch.object(module, "run", side_effect=post)

    def test_expired_own_claim_interrupted_during_pr_gate_escalates(self):
        tank = self.dispatcher("tank")
        claim = self.interrupt_pr_gate(tank)
        self.expire(tank)
        [issue] = tank.frontier()
        self.assertNotIn("held", issue)

        # Reconfiguration discards pass-local state but retains the host journal.
        tank.configure(tank.cfg)
        with self.post_escalations(tank):
            tank.process_ticket(issue, 1, False)

        self.assertIn({"name": "ready-for-human"}, self.github.issue["labels"])
        self.assertNotIn({"name": "ready-for-agent"}, self.github.issue["labels"])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.open_prs, [{"number": 70}])
        self.assertEqual(self.launched, ["tank"])
        [comment] = [row["body"] for row in self.github.comments
                     if row["body"].startswith("Factory dispatcher escalating:")]
        self.assertEqual(comment.splitlines()[1], f"Factory requeue: nonce={claim['nonce']}")
        self.assertIn("expired", comment)
        self.assertTrue(any(row.get("event") == "escalate"
                            for row in lifecycle.read_events(tank.EVENTS)))

    def test_live_interrupted_claim_and_foreign_expired_claim_remain_held(self):
        tank = self.dispatcher("tank")
        self.interrupt_pr_gate(tank)
        self.assertEqual(tank.frontier()[0].get("held"), "host claim (tank)")
        ultron = self.dispatcher("ultron")
        self.expire(ultron)
        self.assertEqual(ultron.frontier()[0].get("held"), "host label (tank)")

    def test_completed_own_attempt_remains_held_after_expiry(self):
        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.open_prs = [{"number": 70}]
        self.expire(tank)
        self.assertEqual(tank.frontier()[0].get("held"), "open PR")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(tank.frontier()[0], 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, ["tank"])

    def test_escalated_interrupted_claim_keeps_existing_requeue_admission(self):
        tank = self.dispatcher("tank")
        self.interrupt_pr_gate(tank)
        ultron = self.dispatcher("ultron")
        with self.post_escalations(ultron):
            ultron.escalate(7, "CI failed", None, code="ci_failed")
        self.github.run("human", [
            "gh", "issue", "edit", "7", "--remove-label", "ready-for-human",
            "--add-label", "ready-for-agent",
        ])
        self.expire(tank)
        with mock.patch.object(tank, "escalate") as escalate:
            tank.process_ticket(tank.frontier()[0], 1, False)
        escalate.assert_not_called()
        self.assertEqual(self.launched, ["tank", "tank"])

    def test_oom_without_terminal_row_recovers_even_when_already_unassigned(self):
        tank = self.dispatcher("tank")
        claim = self.interrupt_pr_gate(tank)
        self.github.issue["assignees"] = []
        self.expire(tank)
        read_events = lifecycle.read_events

        def without_exit(path):
            return [row for row in read_events(path)
                    if not (row.get("event") == "lifecycle" and row.get("stage") == "ticket"
                            and row.get("kind") == "exit")]

        with mock.patch.object(lifecycle, "read_events", side_effect=without_exit), \
                self.post_escalations(tank):
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            tank.process_ticket(issue, 1, False)

        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
        self.assertEqual(self.github.open_prs, [{"number": 70}])
        self.assertEqual(self.launched, ["tank"])
        self.assertTrue(any(f"Factory requeue: nonce={claim['nonce']}" in row["body"]
                            for row in self.github.comments))

    def test_heartbeat_after_frontier_prevents_recovery(self):
        tank = self.dispatcher("tank")
        claim = self.interrupt_pr_gate(tank)
        self.expire(tank)
        [issue] = tank.frontier()
        self.assertNotIn("held", issue)
        comment = next(row for row in self.github.comments if row["id"] == claim["id"])
        comment["body"] = (f"Factory claim: host=tank nonce={claim['nonce']} "
                           f"at={tank.datetime.now(UTC).isoformat()}")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        with mock.patch.object(tank, "escalate") as escalate:
            tank.process_ticket(issue, 1, False)
        escalate.assert_not_called()
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, ["tank"])

    def test_expired_claim_without_claimed_row_escalates(self):
        tank = self.dispatcher("tank")
        self.github.open_prs = [{"number": 70}]
        claim = self.github.add_claim("tank", datetime.now(UTC))
        self.expire(tank)
        with self.post_escalations(tank):
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            tank.process_ticket(issue, 1, False)
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
        self.assertTrue(any(claim["body"].split("nonce=")[1].split()[0] in row["body"]
                            and "Factory requeue:" in row["body"]
                            for row in self.github.comments))
        self.assertEqual(self.launched, [])

    def test_github_clock_ahead_of_local_journal_does_not_hold(self):
        tank = self.dispatcher("tank")
        claim = self.interrupt_pr_gate(tank)
        comment = next(row for row in self.github.comments if row["id"] == claim["id"])
        comment["created_at"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        self.expire(tank)
        with self.post_escalations(tank):
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            tank.process_ticket(issue, 1, False)
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])

    def test_new_unjournaled_claim_cannot_inherit_previous_completion(self):
        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.comments.clear()
        self.github.add_claim("tank", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        self.expire(tank)
        with self.post_escalations(tank):
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            tank.process_ticket(issue, 1, False)
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])

    def test_completed_noninterned_ticket_remains_held(self):
        self.github.issue["number"] = int("1000")
        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.open_prs = [{"number": 70}]
        self.expire(tank)
        self.assertEqual(tank.frontier()[0].get("held"), "open PR")

    def test_other_ticket_completion_cannot_complete_interrupted_claim(self):
        tank = self.dispatcher("tank")
        self.interrupt_pr_gate(tank)
        tank.record("claimed", ticket=8, execution_id="other")
        tank.record("approved", ticket=8, execution_id="other")
        self.expire(tank)
        self.assertNotIn("held", tank.frontier()[0])


class HostClaimWorktreeInterruptionTest(unittest.TestCase):
    setUp = smoke.LocalCLI.setUp
    git = smoke.LocalCLI.git
    write_config = smoke.LocalCLI.write_config
    commit_config = smoke.LocalCLI.commit_config
    prepare = resume.WorktreeResumeTest.prepare
    post_escalations = HostClaimInterruptionTest.post_escalations

    def test_expired_claim_reports_and_preserves_unpushed_commits_through_resume(self):
        self.prepare()
        self.github = test_host_claims.FakeGitHub()
        self.github.open_prs = [{"number": 70}]
        cfg = config.load(self.repo)
        cfg.host_name = "tank"
        worker_heads = []

        def worker(*args, **kwargs):
            dispatch.run(["git", "merge", "--no-edit", "origin/main"], cwd=self.wt)
            head = self.git("-C", str(self.wt), "rev-parse", "HEAD").stdout.strip()
            worker_heads.append(head)
            return True, "gate passed", None, head

        def publish(wt, branch, title, body, *, ticket, lease):
            dispatch.push_agent(wt, ticket, lease)
            return True

        run = dispatch.run

        def command(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "comment"]:
                self.github.json("tank", [
                    "api", "repos/local/smoke/issues/7/comments", "--method", "POST",
                    "-f", f"body={cmd[cmd.index('--body') + 1]}",
                ])
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "gh":
                return self.github.run("tank", cmd, *args, **kwargs)
            return run(cmd, *args, **kwargs)

        with chdir(self.repo), mock.patch.dict("os.environ", self.env):
            dispatch.configure(cfg)
            self.enterContext(mock.patch.object(dispatch, "run", side_effect=command))
            self.enterContext(mock.patch.object(dispatch, "gh_json",
                                               side_effect=lambda args: self.github.json("tank", args)))
            for name, value in {
                "admit_plan": lambda *a: None,
                "open_blockers": lambda *a: [],
                "worker_round": worker,
                "push_and_pr": publish,
                "record_risk": lambda *a: None,
                "pr_comment": lambda *a: None,
                "approve_pr": lambda *a: True,
            }.items():
                self.enterContext(mock.patch.object(dispatch, name, value))
            with mock.patch.object(dispatch, "pr_gate_and_review", side_effect=KeyboardInterrupt), \
                    self.assertRaises(KeyboardInterrupt):
                dispatch.process_ticket(copy.deepcopy(self.github.issue), 1, False)

            commits = []
            for message in ("unpublished one", "unpublished two"):
                self.git("-C", str(self.wt), "commit", "--allow-empty", "-qm", message)
                commits.append(self.git("rev-parse", "agent/7").stdout.strip())
            (self.repo / "main-change.txt").write_text("main advanced\n")
            self.git("add", "main-change.txt")
            self.git("commit", "-qm", "advance main")
            self.git("push", "origin", "main")
            main = self.git("rev-parse", "main").stdout.strip()
            clock = self.enterContext(mock.patch.object(dispatch, "datetime", wraps=datetime))
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            dispatch.process_ticket(dispatch.frontier()[0], 1, False)

            self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
            self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), commits[-1])
            self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0],
                             self.parent)
            [comment] = [row["body"] for row in self.github.comments
                         if row["body"].startswith("Factory dispatcher escalating:")]
            for head in commits:
                self.assertIn(head, comment)

            self.github.run("human", [
                "gh", "issue", "edit", "7", "--remove-label", "ready-for-human",
                "--add-label", "ready-for-agent",
            ])
            with mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "", False)):
                dispatch.process_ticket(dispatch.frontier()[0], 1, False)
            pushed = self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0]
            self.assertEqual(pushed, worker_heads[-1])
            for head in [*commits, main]:
                self.git("merge-base", "--is-ancestor", head, pushed)


class LegacyHostClaimInterruptionTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch
    interrupt_pr_gate = HostClaimInterruptionTest.interrupt_pr_gate
    expire = HostClaimInterruptionTest.expire

    def test_legacy_completion_uses_own_host_clock_not_github_clock(self):
        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.open_prs = [{"number": 70}]
        self.github.comments[0]["created_at"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        rows = lifecycle.read_events(tank.EVENTS)
        for row in rows:
            row.pop("claim_nonce", None)
        self.expire(tank)
        with mock.patch.object(lifecycle, "read_events", return_value=rows):
            self.assertEqual(tank.frontier()[0].get("held"), "open PR")

    def test_legacy_completion_before_latest_heartbeat_does_not_hold(self):
        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.open_prs = [{"number": 70}]
        comment = self.github.comments[0]
        comment["body"] = comment["body"].rsplit("at=", 1)[0] + (
            f"at={(datetime.now(UTC) + timedelta(minutes=5)).isoformat()}"
        )
        rows = lifecycle.read_events(tank.EVENTS)
        for row in rows:
            row.pop("claim_nonce", None)
        self.expire(tank)
        with mock.patch.object(lifecycle, "read_events", return_value=rows):
            self.assertNotIn("held", tank.frontier()[0])

    def test_legacy_claim_interrupted_with_skewed_github_clock_is_admitted(self):
        tank = self.dispatcher("tank")
        self.interrupt_pr_gate(tank)
        self.github.comments[0]["created_at"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        rows = lifecycle.read_events(tank.EVENTS)
        for row in rows:
            row.pop("claim_nonce", None)
        self.expire(tank)
        with mock.patch.object(lifecycle, "read_events", return_value=rows):
            self.assertNotIn("held", tank.frontier()[0])


class InterruptedClaimRequeueTrustTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch
    post_escalations = HostClaimInterruptionTest.post_escalations

    def test_untrusted_or_obsolete_requeue_does_not_silently_hold_incomplete_claim(self):
        root = self.root
        for kind in ("untrusted", "obsolete"):
            with self.subTest(kind=kind):
                self.root = root / kind
                self.github = test_host_claims.FakeGitHub()
                tank = self.dispatcher("tank")
                now = datetime.now(UTC)
                prior = self.github.add_claim("ultron", now - timedelta(hours=6)) if kind == "obsolete" else None
                if prior:
                    self.github.run("human", [
                        "gh", "issue", "edit", "7", "--remove-label", "host:ultron",
                    ])
                current = self.github.add_claim("tank", now - timedelta(hours=3))
                nonce = (prior or current)["body"].split("nonce=", 1)[1].split()[0]
                self.github.comments.append({
                    "id": 100, "body": f"Factory requeue: nonce={nonce}\nContinue work",
                    "created_at": now.isoformat(),
                    "user": {"login": "@me" if prior else "outside-commenter"},
                })
                self.github.open_prs = [{"number": 70}]
                with self.post_escalations(tank):
                    [issue] = tank.frontier()
                    self.assertNotIn("held", issue)
                    tank.process_ticket(issue, 1, False)
                self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
                self.assertEqual(self.launched, [])
                current_nonce = current["body"].split("nonce=", 1)[1].split()[0]
                self.assertTrue(any(f"Factory requeue: nonce={current_nonce}" in row["body"]
                                    and row["body"].startswith("Factory dispatcher escalating:")
                                    for row in self.github.comments))
