"""Expired own-host claims must not strand a PR after an interrupted attempt."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


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
        self.assertEqual(ultron.frontier()[0].get("held"), "open PR")

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
