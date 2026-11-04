"""Foreign requeues must survive an election loser's failed comment cleanup."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests import test_host_claims


class HostClaimCleanupTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def requeue_with_abandoned_loser(self, *, after_expiry):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        before_claim = copy.deepcopy(self.github.issue)
        now = datetime.now(UTC)
        clocks = []
        for module in (tank, ultron):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.return_value = now
            clocks.append(clock)
        ultron_run = ultron.run
        context = "Rebase PR #70 and resolve shared.py"
        self.enterContext(mock.patch.object(ultron, "refresh_requeue_context", return_value=context))

        def fail_loser_delete(cmd, *args, **kwargs):
            if "DELETE" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "temporary cleanup failure")
            return ultron_run(cmd, *args, **kwargs)

        def winner_worker(*args, **kwargs):
            # Ultron passed its fresh admission read before Tank claimed. Its
            # later comment loses the election, but DELETE leaves it behind.
            with mock.patch.object(ultron, "run", side_effect=fail_loser_delete), \
                    self.assertRaises(subprocess.CalledProcessError):
                ultron.acquire_host_claim(7, before_claim, [])
            self.assertEqual(len(ultron.host_claims(7)), 2)
            self.github.open_prs = [{"number": 70}]
            if not after_expiry:
                ultron.unclaim(7, "--add-label", "ready-for-agent")
            return self.launch("tank")

        with mock.patch.object(tank, "worker_round", side_effect=winner_worker):
            tank.process_ticket(before_claim, 1, False)
        self.assertEqual(self.launched, ["tank"])
        for clock in clocks:
            clock.now.return_value = now + timedelta(hours=3)
        if after_expiry:
            ultron.unclaim(7, "--add-label", "ready-for-agent")
        winner = tank.host_claims(7)[0]
        requeue = next(row for row in self.github.comments
                       if row["body"].startswith("Factory requeue:"))
        self.assertEqual(requeue["body"].splitlines()[0],
                         f"Factory requeue: nonce={winner['nonce']}")
        [issue] = ultron.frontier()
        self.assertNotIn("held", issue)
        received = []

        def replacement_worker(*args, **kwargs):
            received.append(args[4])
            return self.launch("ultron")

        with mock.patch.object(ultron, "worker_round", side_effect=replacement_worker):
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank", "ultron"])
        self.assertEqual(received, [context])
        self.assertEqual([row["name"] for row in self.github.issue["labels"]],
                         ["ready-for-agent", "host:ultron"])
        self.assertEqual(len(ultron.host_claims(7)), 1)
        for clock in clocks:
            clock.now.return_value += timedelta(hours=3)
        self.assertEqual(tank.frontier()[0].get("held"), "open PR")

    def test_foreign_live_requeue_survives_failed_loser_cleanup(self):
        self.requeue_with_abandoned_loser(after_expiry=False)

    def test_foreign_expired_requeue_targets_winner_not_abandoned_loser(self):
        self.requeue_with_abandoned_loser(after_expiry=True)
