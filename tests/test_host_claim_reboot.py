"""Same-host crash recovery must not wait for the remote claim lease."""
import fcntl
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class HostClaimRebootTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def abandoned_claim(self, module, *, age=60, reboot=True, completed=False):
        at = datetime.now(UTC) - timedelta(seconds=age)
        process = lifecycle._identity(os.getpid())
        if reboot:
            process["boot_id"] = "previous-boot"
        process["pid"] = 2_000_000_000
        with mock.patch.object(lifecycle, "_now", return_value=(at - timedelta(seconds=1)).isoformat()):
            execution = lifecycle.Execution(module.EVENTS, "ticket", ticket=7)
            execution.process = process
            execution.emit("enter")
            execution.resource("acquired", module.ticket_lock(7), scope="repository")
        claim = self.github.add_claim(module.cfg.host_name, at)
        if completed:
            execution.emit("exit")
        module.ticket_lock(7).touch()
        self.assertEqual(module.ticket_lock(7).stat().st_size, 0)
        self.assertFalse(module.lock_held(module.ticket_lock(7)))
        return claim

    def assert_resumed(self, module, old):
        [issue] = module.frontier()
        self.assertNotIn("held", issue)
        module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [module.cfg.host_name])
        self.assertNotIn(old, self.github.comments)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual([row["host"] for row in module.host_claims(7)], [module.cfg.host_name])

    def test_first_pass_reclaims_previous_boot_claim_before_ttl(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron)
        self.assert_resumed(ultron, old)

    def test_first_pass_reclaims_dead_pid_on_current_boot(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron, reboot=False)
        self.assert_resumed(ultron, old)

    def test_first_pass_resumes_open_pr_after_reboot_and_owner_requeue(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron)
        self.github.open_prs = [{"number": 70}]
        ultron.record("generation", ticket=7, generation=1, intent="resume", source="requeue_agent")
        self.assert_resumed(ultron, old)

    def test_expired_reboot_claim_cannot_hold_resume_with_open_pr(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron, age=3 * 3600)
        self.github.open_prs = [{"number": 70}]
        ultron.record("generation", ticket=7, generation=1, intent="resume", source="requeue_agent")
        self.assert_resumed(ultron, old)

    def test_live_current_boot_claim_is_honored_even_with_free_lock(self):
        ultron = self.dispatcher("ultron")
        with lifecycle.scope(ultron.EVENTS, "ticket", ticket=7):
            self.github.add_claim("ultron", datetime.now(UTC))
            [issue] = ultron.frontier()
            self.assertEqual(issue["held"], "host claim (ultron)")
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [])
        self.assertEqual(len(self.github.comments), 1)

    def test_held_ticket_lock_prevents_recovery(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron)
        with ultron.ticket_lock(7).open("r") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            [issue] = ultron.frontier()
            self.assertIn("held", issue)
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_completed_owner_retains_open_pr_hold_after_reboot(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron, age=3 * 3600, completed=True)
        self.github.open_prs = [{"number": 70}]
        self.assertEqual(ultron.frontier()[0]["held"], "open PR")
        self.assertIn(old, self.github.comments)

    def test_foreign_claim_is_not_reclaimed_using_local_process_evidence(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron)
        tank = self.dispatcher("tank")
        self.assertEqual(tank.frontier()[0]["held"], "host claim (ultron)")
        tank.process_ticket(tank.frontier()[0], 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_unknown_process_identity_does_not_shorten_lease(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron)
        with mock.patch.object(lifecycle, "_process_state", return_value="unknown"):
            self.assertEqual(ultron.frontier()[0]["held"], "host claim (ultron)")
            ultron.process_ticket(ultron.frontier()[0], 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_abandoned_owner_does_not_supersede_later_requeue_election(self):
        ultron = self.dispatcher("ultron")
        old = self.abandoned_claim(ultron, age=3 * 3600)
        later = self.github.add_claim(
            "tank", datetime.fromisoformat(old["created_at"]) + timedelta(seconds=60),
        )
        nonce = later["body"].split("nonce=", 1)[1].split()[0]
        self.github.comments.append({
            "id": 100, "body": f"Factory requeue: nonce={nonce}\nresume current work",
            "user": {"login": "@me"},
        })
        self.github.open_prs = [{"number": 70}]
        claims, context = ultron.host_claim_state(7)
        self.assertEqual(ultron.elected_host_claim(claims)["host"], "tank")
        self.assertEqual(context, "resume current work")
        self.assert_resumed(ultron, old)
