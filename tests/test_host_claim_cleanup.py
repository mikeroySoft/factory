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
        winner_nonce = None
        for module in (tank, ultron):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.return_value = now
            clocks.append(clock)
        ultron_run = ultron.run
        context = "Rebase PR #70 and resolve shared.py"

        def fail_loser_delete(cmd, *args, **kwargs):
            if "DELETE" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "temporary cleanup failure")
            return ultron_run(cmd, *args, **kwargs)

        def winner_worker(*args, **kwargs):
            nonlocal winner_nonce
            # Ultron passed its fresh admission read before Tank claimed. Its
            # later comment loses the election, but DELETE leaves it behind.
            with mock.patch.object(ultron, "run", side_effect=fail_loser_delete), \
                    self.assertRaises(subprocess.CalledProcessError):
                ultron.acquire_host_claim(7, before_claim, [])
            claims = tank.host_claims(7)
            self.assertEqual(len(claims), 2)
            winner_nonce = claims[0]["nonce"]
            self.github.open_prs = [{"number": 70}]
            if after_expiry:
                for clock in clocks:
                    clock.now.return_value = now + timedelta(hours=3)
            with mock.patch.object(ultron, "refresh_requeue_context", return_value=context):
                ultron.unclaim(7, "--add-label", "ready-for-agent")
            return self.launch("tank")

        with mock.patch.object(tank, "worker_round", side_effect=winner_worker):
            tank.process_ticket(before_claim, 1, False)
        self.assertEqual(self.launched, ["tank"])
        for clock in clocks:
            clock.now.return_value = now + timedelta(hours=3)
        requeue = next(row for row in self.github.comments
                       if row["body"].startswith("Factory requeue:"))
        self.assertEqual(requeue["body"].splitlines()[0],
                         f"Factory requeue: nonce={winner_nonce}")
        self.assertIn(context, requeue["body"])
        [blocked] = ultron.frontier()
        self.assertEqual(
            blocked.get("held"),
            "open agent PR #70 (awaiting merge or closure)",
        )
        self.github.open_prs = []  # The PR closed; its carried context is now eligible to resume.
        [issue] = ultron.frontier()
        self.assertNotIn("held", issue)
        received = []

        def replacement_worker(*args, **kwargs):
            received.append(args[4])
            return self.launch("ultron")

        with mock.patch.object(ultron, "worker_round", side_effect=replacement_worker):
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank", "ultron"])
        self.assertEqual(received, [""])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(ultron.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])
        self.github.open_prs = [{"number": 71}]
        self.assertEqual(
            tank.frontier()[0].get("held"),
            "open agent PR #71 (awaiting merge or closure)",
        )

    def test_foreign_live_requeue_survives_failed_loser_cleanup(self):
        self.requeue_with_abandoned_loser(after_expiry=False)

    def test_foreign_expired_requeue_targets_winner_not_abandoned_loser(self):
        self.requeue_with_abandoned_loser(after_expiry=True)

    def test_requeue_cannot_release_completed_replacement_at_exact_lease_boundary(self):
        now = datetime(2026, 11, 3, tzinfo=UTC)
        old = self.github.add_claim("old", now - timedelta(hours=6))
        self.github.add_claim("loser", now - timedelta(hours=6) + timedelta(minutes=1))
        replacement = self.github.add_claim("tank", now - timedelta(hours=4))
        nonce = old["body"].split("nonce=", 1)[1].split()[0]
        self.github.comments.append({
            "id": 100, "created_at": now.isoformat(), "user": {"login": "@me"},
            "body": f"Factory requeue: nonce={nonce}\nObsolete refresh",
        })
        self.github.open_prs = [{"number": 70}]
        ultron = self.dispatcher("ultron")
        with mock.patch.object(ultron, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            [issue] = ultron.frontier()
            self.assertEqual(
                issue.get("held"),
                "open agent PR #70 (awaiting merge or closure)",
            )
            ultron.process_ticket(issue, 1, False)
            self.assertEqual(self.launched, [])
            self.assertIn(replacement, self.github.comments)
            ultron.unclaim(7, "--add-label", "ready-for-agent")
            expected_nonce = replacement["body"].split("nonce=", 1)[1].split()[0]
            self.assertEqual(self.github.comments[-1]["body"].splitlines()[0],
                             f"Factory requeue: nonce={expected_nonce}")
