"""Behavioral coverage for the remaining host-claim mutation gap."""
import copy
import unittest

from tests import test_host_claims


class HostClaimMutantGapTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher

    def test_default_claim_rejects_issue_leaving_agent_queue_during_election(self):
        tank = self.dispatcher("tank")
        github = tank.gh_json

        def close_after_claim(args):
            result = github(args)
            if args[0] == "api" and "POST" in args:
                self.github.issue["state"] = "CLOSED"
                self.github.issue["labels"] = [{"name": "ready-for-human"}]
            return result

        tank.gh_json = close_after_claim
        claim = tank.acquire_host_claim(7, copy.deepcopy(self.github.issue), [])
        if claim is not None:
            self.addCleanup(claim.stop)

        self.assertIsNone(claim)
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
        self.assertEqual(self.github.issue["assignees"], [])
