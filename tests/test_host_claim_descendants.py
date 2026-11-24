"""Host-claim recovery keeps genuine descendant and lock evidence."""
import fcntl
import os
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class HostClaimDescendantTest(unittest.TestCase):
    TICKET = 1000
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def setUp(self):
        test_host_claims.HostClaimRaceTest.setUp(self)
        self.github.issue["number"] = self.TICKET

    def owner_claim(
        self, module, process, *, extra_lock=None, enter_first=True, live_child=False,
    ):
        ticket_lock = module.ticket_lock(self.TICKET)
        ticket_lock.touch()
        execution = lifecycle.Execution(module.EVENTS, "ticket", ticket=self.TICKET)
        execution.process = process
        if not enter_first:
            execution.resource("acquired", ticket_lock, scope="repository")
        execution.emit("enter")
        if enter_first:
            execution.resource("acquired", ticket_lock, scope="repository")
        if extra_lock is not None:
            extra_lock.parent.mkdir(parents=True, exist_ok=True)
            extra_lock.touch()
            execution.resource("acquired", extra_lock, scope="repository")
        if live_child:
            execution.child(os.getpid())
        claim = self.github.add_claim(module.cfg.host_name, datetime.now(UTC))
        module.record(
            "host-claim-started",
            ticket=self.TICKET,
            nonce=claim["body"].split("nonce=", 1)[1].split()[0],
            execution_id=execution.execution_id,
            process=process,
        )
        return claim

    def dead_process(self, *, previous_boot=False, pid=2_000_000_000):
        process = {**lifecycle._identity(os.getpid()), "pid": pid}
        if previous_boot:
            process["boot_id"] = "previous-boot"
        return process

    def registered_child_claim(self, module, *, previous_boot=False):
        owner = self.dead_process(previous_boot=previous_boot)
        worker = self.dead_process(previous_boot=previous_boot, pid=1_999_999_999)
        child = (self.dead_process(previous_boot=True, pid=1_999_999_998)
                 if previous_boot else lifecycle._identity(os.getpid()))
        module.ticket_lock(self.TICKET).touch()
        with (
            mock.patch.object(lifecycle, "_identity", side_effect=[owner, worker, child]),
            self.assertRaises(KeyboardInterrupt),
            lifecycle.scope(module.EVENTS, "ticket", ticket=self.TICKET) as execution,
        ):
            execution.resource("acquired", module.ticket_lock(self.TICKET), scope="repository")
            claim = self.github.add_claim(module.cfg.host_name, datetime.now(UTC))
            module.record(
                "host-claim-started",
                ticket=self.TICKET,
                nonce=claim["body"].split("nonce=", 1)[1].split()[0],
                process=execution.process,
            )
            with lifecycle.scope(module.EVENTS, "worker", ticket=self.TICKET) as nested:
                nested.child(child["pid"])
                raise KeyboardInterrupt
        return claim

    def assert_recovered(self, module, old):
        [issue] = module.frontier()
        self.assertNotIn("held", issue)
        module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [module.cfg.host_name])
        self.assertNotIn(old, self.github.comments)

    def test_live_registered_nested_child_keeps_claim(self):
        ultron = self.dispatcher("ultron")
        old = self.registered_child_claim(ultron)

        [issue] = ultron.frontier()
        self.assertEqual(issue["held"], "host claim (ultron)")
        ultron.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_latest_registered_child_event_keeps_claim(self):
        ultron = self.dispatcher("ultron")
        old = self.owner_claim(ultron, self.dead_process(), live_child=True)

        [issue] = ultron.frontier()
        self.assertEqual(issue["held"], "host claim (ultron)")
        ultron.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_previous_boot_registered_child_does_not_block_recovery(self):
        ultron = self.dispatcher("ultron")
        old = self.registered_child_claim(ultron, previous_boot=True)

        self.assert_recovered(ultron, old)

    def test_non_ticket_lock_blocks_recovery_but_admission_ticket_lock_does_not(self):
        ultron = self.dispatcher("ultron")
        extra_lock = ultron.FACTORY / "locks" / "zz-worker.lock"
        old = self.owner_claim(ultron, self.dead_process(), extra_lock=extra_lock)
        self.assertEqual(ultron.ticket_lock(self.TICKET).stat().st_size, 0)
        self.assertEqual(extra_lock.stat().st_size, 0)

        with extra_lock.open("r") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            [issue] = ultron.frontier()
            self.assertEqual(issue["held"], "host claim (ultron)")
            ultron.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)
        self.assert_recovered(ultron, old)

    def test_external_ticket_lock_holds_abandoned_claim_before_admission(self):
        ultron = self.dispatcher("ultron")
        alias = self.root / "factory-alias"
        alias.symlink_to(ultron.FACTORY, target_is_directory=True)
        self.enterContext(mock.patch.object(ultron, "FACTORY", alias))
        old = self.owner_claim(ultron, self.dead_process())
        ticket_lock = ultron.ticket_lock(self.TICKET)
        self.assertEqual(ticket_lock.stat().st_size, 0)

        with ticket_lock.open("r") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            [issue] = ultron.frontier()
            self.assertEqual(issue["held"], "ticket lock held")
            ultron.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_owner_history_must_start_with_enter(self):
        ultron = self.dispatcher("ultron")
        old = self.owner_claim(ultron, self.dead_process(), enter_first=False)

        [issue] = ultron.frontier()
        self.assertEqual(issue["held"], "host claim (ultron)")
        ultron.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)


if __name__ == "__main__":
    unittest.main()
