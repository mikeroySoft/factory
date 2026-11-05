"""Retained dispatch claims recover without weakening assignment ownership."""
from __future__ import annotations

import copy
import subprocess
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import lifecycle, reads
from tests import test_host_claims


class DispatchReadResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        test_host_claims.HostClaimRaceTest.setUp(self)

    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    @staticmethod
    def failure(stderr: str = "HTTP 500: provider unavailable") -> reads.ReadFailure:
        command = ["gh", "issue", "view", "7"]
        return reads.ReadFailure(
            "gh_read_failed",
            subprocess.CompletedProcess(command, 1, "", stderr),
        )

    def fail_claimed_worker_once(self, module) -> list[tuple]:
        attempts = []

        def worker(*args, **kwargs):
            attempts.append((args, kwargs))
            if len(attempts) == 1:
                self.remote_at_failure = copy.deepcopy((self.github.issue, self.github.comments))
                raise self.failure()
            return self.launch(module.cfg.host_name)

        self.enterContext(mock.patch.object(module, "worker_round", side_effect=worker))
        with reads.pass_scope(module.EVENTS, "dispatch"):
            module.process_ticket(module.frontier()[0], 1, False)
        return attempts

    def test_default_host_retained_assignment_resumes_on_next_pass(self) -> None:
        single = self.dispatcher(None)
        attempts = self.fail_claimed_worker_once(single)

        self.assertEqual((self.github.issue, self.github.comments), self.remote_at_failure)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])

        with reads.pass_scope(single.EVENTS, "dispatch"):
            single.ticket_pass(1, False)

        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.launched, [None])

    def test_retained_journal_does_not_resume_a_foreign_assignment(self) -> None:
        single = self.dispatcher(None)
        attempts = self.fail_claimed_worker_once(single)
        self.github.issue["assignees"] = [{"login": "other-owner"}]
        before = copy.deepcopy((self.github.issue, self.github.comments))

        with reads.pass_scope(single.EVENTS, "dispatch"):
            single.ticket_pass(1, False)

        self.assertEqual(len(attempts), 1)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_heartbeat_retries_read_failures_and_open_circuit_intervals(self) -> None:
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        [owned] = tank.host_claims(7)
        heartbeat = tank.HostClaim(7, owned)
        circuit = self.failure("read circuit open")

        with mock.patch.object(heartbeat.stopped, "wait", side_effect=[False, False, False, True]), \
                mock.patch.object(tank, "owned_host_claim", side_effect=[circuit, circuit, owned]) as ownership, \
                reads.pass_scope(tank.EVENTS, "dispatch"):
            heartbeat.heartbeat()

        self.assertEqual(ownership.call_count, 3)
        self.assertEqual(self.github.patch_count, 1)
        skips = [row for row in lifecycle.read_events(tank.EVENTS)
                 if row.get("event") == "read-skip" and row.get("operation") == "claim-heartbeat"]
        self.assertEqual(len(skips), 2)


if __name__ == "__main__":
    unittest.main()
