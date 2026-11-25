"""Recovery only reclaims claims with one corroborated journal owner."""
import copy
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class HostClaimOwnerTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    ticket = 1000
    base = datetime(2026, 11, 24, 12, tzinfo=UTC)

    def module(self, host="ultron", *, now=None):
        self.github.issue["number"] = self.ticket
        module = self.dispatcher(host)
        clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
        clock.now.return_value = now or self.base + timedelta(minutes=1)
        return module

    def acquisition(self, module, at, *, completed=False):
        process = {
            **lifecycle._identity(os.getpid()),
            "boot_id": "previous-boot",
            "pid": 2_000_000_000,
        }
        with mock.patch.object(lifecycle, "_now", return_value=at.isoformat()):
            execution = lifecycle.Execution(module.EVENTS, "ticket", ticket=self.ticket)
            execution.process = process
            execution.emit("enter")
            acquired = execution.resource(
                "acquired", module.ticket_lock(self.ticket), scope="repository",
            )
            if completed:
                execution.emit("exit")
        return execution, acquired

    def claim(self, host, at, *, created=None):
        claim = self.github.add_claim(host, at)
        if created is not None:
            claim["created_at"] = created.isoformat()
        return claim

    @staticmethod
    def nonce(claim):
        return claim["body"].split("nonce=", 1)[1].split()[0]

    def assert_reclaimed(self, module, old):
        [issue] = module.frontier()
        self.assertNotIn("held", issue)
        module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [module.cfg.host_name])
        self.assertNotIn(old, self.github.comments)

    def assert_held(self, module, old, reason="host claim (ultron)"):
        [issue] = module.frontier()
        self.assertEqual(issue.get("held"), reason)
        module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [])
        self.assertIn(old, self.github.comments)

    def test_foreign_claim_cannot_borrow_local_legacy_owner(self):
        module = self.module()
        self.acquisition(module, self.base)
        foreign = self.claim("tank", self.base + timedelta(seconds=1))
        self.claim("ultron", self.base + timedelta(seconds=2))

        self.assert_held(module, foreign, "host claim (tank)")

    def test_nonce_owner_ignores_wrong_event_ticket_and_nonce_rows(self):
        module = self.module()
        execution, _ = self.acquisition(module, self.base)
        old = self.claim("ultron", self.base + timedelta(seconds=1))
        nonce = self.nonce(old)
        module.record(
            "host-claim-started", ticket=self.ticket, nonce=nonce,
            execution_id=execution.execution_id,
        )
        module.record("not-a-start", ticket=self.ticket, nonce=nonce, execution_id="wrong-event")
        module.record(
            "host-claim-started", ticket=self.ticket + 1, nonce=nonce,
            execution_id="wrong-ticket",
        )
        module.record(
            "host-claim-started", ticket=self.ticket, nonce="different-nonce",
            execution_id="wrong-nonce",
        )

        self.assert_reclaimed(module, old)

    def test_legacy_owner_ignores_nonacquisition_rows(self):
        module = self.module()
        _, acquired = self.acquisition(module, self.base + timedelta(microseconds=100_000))
        old = self.claim("ultron", self.base + timedelta(microseconds=200_000))
        changes = (
            {"event": "not-lifecycle"},
            {"kind": "lock_released"},
            {"stage": "worker"},
            {"ticket": self.ticket + 1},
            {"lock": str(module.ticket_lock(self.ticket).resolve()) + ".other"},
        )
        for index, change in enumerate(changes, 1):
            row = copy.deepcopy(acquired)
            row.update(
                change,
                at=(self.base + timedelta(microseconds=100_000 + index)).isoformat(),
                execution_id=f"decoy-{index}",
                root_execution_id=f"decoy-{index}",
                event_id=f"decoy-event-{index}",
            )
            lifecycle.append(module.EVENTS, row)

        self.assert_reclaimed(module, old)

    def test_legacy_owner_accepts_last_microsecond_of_creation_second(self):
        module = self.module()
        self.acquisition(module, self.base + timedelta(microseconds=999_999))
        old = self.claim("ultron", self.base, created=self.base)

        self.assert_reclaimed(module, old)

    def test_invalid_missing_and_naive_acquisition_times_are_ignored(self):
        module = self.module()
        _, acquired = self.acquisition(module, self.base + timedelta(microseconds=100_000))
        old = self.claim("ultron", self.base + timedelta(microseconds=200_000))
        for index, value in enumerate(("invalid", None, "2026-11-24T12:00:00.150000"), 1):
            row = copy.deepcopy(acquired)
            row.update(
                execution_id=f"bad-time-{index}",
                root_execution_id=f"bad-time-{index}",
                event_id=f"bad-time-event-{index}",
            )
            if value is None:
                row.pop("at")
            else:
                row["at"] = value
            lifecycle.append(module.EVENTS, row)

        self.assert_reclaimed(module, old)

    def test_multiple_possible_legacy_owners_preserve_claim(self):
        module = self.module()
        self.acquisition(module, self.base + timedelta(microseconds=100_000))
        self.acquisition(module, self.base + timedelta(microseconds=700_000))
        old = self.claim("ultron", self.base, created=self.base)

        self.assert_held(module, old)

    def test_legacy_owner_with_different_nonce_preserves_claim(self):
        module = self.module()
        execution, _ = self.acquisition(module, self.base)
        old = self.claim("ultron", self.base + timedelta(seconds=1))
        module.record(
            "host-claim-started", ticket=self.ticket, nonce="another-attempt",
            execution_id=execution.execution_id,
        )

        self.assert_held(module, old)

    def test_completed_legacy_owner_preserves_expired_open_pr(self):
        module = self.module(now=self.base + timedelta(hours=3))
        self.acquisition(module, self.base, completed=True)
        old = self.claim("ultron", self.base + timedelta(seconds=1))
        self.github.open_prs = [{"number": 70}]

        self.assert_held(module, old, "open PR")

    def test_unrelated_receipts_do_not_replace_proven_legacy_owner(self):
        module = self.module()
        execution, _ = self.acquisition(module, self.base + timedelta(microseconds=100_000))
        old = self.claim("ultron", self.base + timedelta(microseconds=200_000))
        after = (self.base + timedelta(seconds=1)).isoformat()
        module.record("approved", ticket=self.ticket, execution_id="wrong-event", at=after)
        module.record("claimed", ticket=self.ticket + 1, execution_id="wrong-ticket", at=after)
        module.record(
            "claimed", ticket=self.ticket, execution_id=execution.execution_id, at=after,
        )
        module.record(
            "claimed", ticket=self.ticket, execution_id="older-owner",
            at=(self.base - timedelta(seconds=1)).isoformat(),
        )

        self.assert_reclaimed(module, old)

    def test_later_conflicting_receipt_preserves_claim(self):
        module = self.module()
        self.acquisition(module, self.base + timedelta(microseconds=100_000))
        old = self.claim("ultron", self.base + timedelta(microseconds=200_000))
        module.record(
            "claimed", ticket=self.ticket, execution_id="later-owner",
            at=(self.base + timedelta(seconds=1)).isoformat(),
        )

        self.assert_held(module, old)

    def test_invalid_conflicting_receipt_time_preserves_claim(self):
        module = self.module()
        self.acquisition(module, self.base + timedelta(microseconds=100_000))
        old = self.claim("ultron", self.base + timedelta(microseconds=200_000))
        module.record(
            "claimed", ticket=self.ticket, execution_id="unknown-owner", at="invalid",
        )

        self.assert_held(module, old)

    def test_missing_conflicting_receipt_time_preserves_claim(self):
        module = self.module()
        self.acquisition(module, self.base + timedelta(microseconds=100_000))
        old = self.claim("ultron", self.base + timedelta(microseconds=200_000))
        lifecycle.append(module.EVENTS, {
            "event": "claimed", "ticket": self.ticket, "execution_id": "unknown-owner",
        })

        self.assert_held(module, old)


if __name__ == "__main__":
    unittest.main()
