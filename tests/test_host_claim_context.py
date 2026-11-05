"""Shared requeue instructions supersede a returning host's journal."""
import unittest
from datetime import UTC, datetime
from types import FunctionType
from unittest import mock

from factory import dispatch
from tests.test_host_claims import HostClaimRaceTest as Harness


class HostClaimContextTest(unittest.TestCase):
    setUp = Harness.setUp
    launch = Harness.launch
    make_dispatcher = Harness.dispatcher

    def dispatcher(self, host):
        module = self.make_dispatcher(host)
        for name in ("refresh_requeue_context", "pending_risk_requeue_head"):
            original = getattr(dispatch, name)
            self.enterContext(mock.patch.object(
                module, name, FunctionType(original.__code__, module.__dict__),
            ))
        return module

    def requeue(self, module, event, **fields):
        self.github.add_claim(module.cfg.host_name, datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        module.record(event, ticket=7, pr=70, head="c" * 40, round=1, **fields)
        module.unclaim(7, "--add-label", "ready-for-agent", assignee_last=True)

    def test_foreign_gate_requeue_supersedes_local_conflict_instructions(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.requeue(tank, "conflict-requeue", verb="rebase", files=["already-resolved.py"])
        self.assertEqual(
            ultron.frontier()[0].get("held"),
            "open agent PR #70 (awaiting merge or closure)",
        )
        self.github.open_prs = []  # The old PR closed before the carried work resumes.
        ultron.process_ticket(ultron.frontier()[0], 1, False)
        self.github.add_claim("ultron", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        ultron.record("gate-requeue", ticket=7, pr=70, head="a" * 40, round=1,
                      reports=["new gate failure", "new gate failure again"])
        ultron.unclaim(7, "--add-label", "ready-for-agent", assignee_last=True)
        self.assertEqual(
            tank.frontier()[0].get("held"),
            "open agent PR #70 (awaiting merge or closure)",
        )
        self.github.open_prs = []  # That PR also closed before the next host resumes.
        contexts = []

        def worker(*args, **kwargs):
            contexts.append(args[4])
            return self.launch("tank")

        with mock.patch.object(tank, "worker_round", side_effect=worker):
            tank.process_ticket(tank.frontier()[0], 1, False)

        self.assertEqual(self.launched, ["ultron", "tank"])
        self.assertIn("new gate failure again", contexts[0])
        self.assertNotIn("already-resolved.py", contexts[0])

    def test_foreign_gate_requeue_clears_obsolete_local_risk_disagreement(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        tank.record("risk-requeue", ticket=7, pr=70, head="c" * 40,
                    round=1, packet="obsolete disagreement")
        self.requeue(ultron, "gate-requeue", reports=["current gate failure"])
        self.assertEqual(
            tank.frontier()[0].get("held"),
            "open agent PR #70 (awaiting merge or closure)",
        )
        self.github.open_prs = []  # Closure releases the unconditional PR hold.
        heads = iter(("a" * 40, "b" * 40))

        def worker(*args, **kwargs):
            self.launch("tank")
            return True, "gate passed", None, next(heads)

        def review(wt, n, report, head, runs):
            disagreement = head == "a" * 40
            tank._PENDING_RISK[n] = {
                "head": head, "disagreement": disagreement, "packet": "current disagreement",
            }
            return ("REVISE" if disagreement else "APPROVE"), "current findings", False

        with mock.patch.object(tank, "worker_round", side_effect=worker), \
                mock.patch.object(tank, "pr_gate_and_review", side_effect=review), \
                mock.patch.object(tank, "push_agent", return_value="b" * 40), \
                mock.patch.object(tank, "escalate") as escalate, \
                mock.patch.object(tank, "approve_pr", return_value=True) as approve:
            tank.process_ticket(tank.frontier()[0], 1, False)

        self.assertEqual(self.launched, ["tank", "tank"])
        escalate.assert_not_called()
        approve.assert_called_once_with(7, "b" * 40)

    def test_matching_shared_risk_requeue_preserves_successive_head_escalation(self):
        tank = self.dispatcher("tank")
        self.requeue(tank, "risk-requeue", packet="pending disagreement")
        self.assertEqual(
            tank.frontier()[0].get("held"),
            "open agent PR #70 (awaiting merge or closure)",
        )
        self.github.open_prs = []  # Closure releases the unconditional PR hold.
        tank._PENDING_RISK[7] = {
            "head": "a" * 40, "disagreement": True, "packet": "second disagreement",
        }
        with mock.patch.object(tank, "pr_gate_and_review", return_value=("REVISE", "findings", False)), \
                mock.patch.object(tank, "escalate") as escalate:
            tank.process_ticket(tank.frontier()[0], 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertEqual(escalate.call_args.kwargs["code"], "risk_disagreement")


# Keep discovery from running the imported harness a second time.
del Harness
