"""Foreign-fork PRs never own or mutate Factory ticket delivery."""
from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime, timedelta

from tests import test_host_claims


class DispatchPROwnershipTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    launch = test_host_claims.HostClaimRaceTest.launch

    def dispatcher(self):
        root = self.root
        self.root = root / str(len(list(root.iterdir())))
        self.root.mkdir()
        try:
            module = test_host_claims.HostClaimRaceTest.dispatcher(self, None)
        finally:
            self.root = root

        original = module.gh_json
        self.pr_reads: list[list[str]] = []

        def github(args):
            if args[:2] == ["pr", "list"]:
                self.pr_reads.append(list(args))
                return copy.deepcopy(self.github.open_prs)
            if args[:2] == ["pr", "view"]:
                self.pr_reads.append(list(args))
                number = int(args[2])
                return copy.deepcopy(next(row for row in self.github.open_prs if row["number"] == number))
            return original(args)

        module.gh_json = github
        return module

    @staticmethod
    def pr(number: int, *, foreign: bool) -> dict:
        return {
            "number": number,
            "headRefName": "agent/7",
            "isCrossRepository": foreign,
            "labels": [],
        }

    def assert_cross_repository_requested(self) -> None:
        self.assertTrue(self.pr_reads)
        for args in self.pr_reads:
            fields = args[args.index("--json") + 1].split(",")
            self.assertIn("isCrossRepository", fields)

    def test_frontier_only_local_agent_pr_holds_ticket(self):
        module = self.dispatcher()
        self.github.open_prs = [self.pr(70, foreign=True)]
        self.assertNotIn("held", module.frontier()[0])

        self.github.open_prs = [self.pr(71, foreign=False)]
        self.assertEqual(
            module.frontier()[0].get("held"),
            "open agent PR #71 (awaiting merge or closure)",
        )
        self.assert_cross_repository_requested()

    def test_process_ticket_only_local_agent_pr_blocks_admission(self):
        self.github.open_prs = [self.pr(70, foreign=True)]
        foreign = self.dispatcher()
        foreign.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
        self.assertEqual(self.launched, [None])

        self.github.open_prs = [self.pr(71, foreign=False)]
        local = self.dispatcher()
        local.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
        self.assertEqual(self.launched, [None])
        self.assert_cross_repository_requested()

    def test_expired_host_claim_does_not_let_a_fork_pr_hold_the_ticket(self):
        module = self.dispatcher()
        module.cfg.host_name = "tank"
        self.github.add_claim("old-host", datetime.now(UTC) - timedelta(hours=3))
        self.github.open_prs = [self.pr(70, foreign=True)]

        self.assertNotIn("held", module.frontier()[0])

        self.github.open_prs.append(self.pr(71, foreign=False))
        self.assertEqual(module.frontier()[0].get("held"),
                         "open agent PR #71 (awaiting merge or closure)")

    def test_discovered_requeue_closes_only_local_pr(self):
        module = self.dispatcher()
        self.github.open_prs = [self.pr(70, foreign=True), self.pr(71, foreign=False)]

        module.close_requeue_pr(7)

        self.assertEqual([row["number"] for row in self.github.open_prs], [70])
        mutated = [cmd for _, cmd in self.github.calls if cmd[1:3] in (["pr", "edit"], ["pr", "close"])]
        self.assertTrue(mutated)
        self.assertEqual({cmd[3] for cmd in mutated}, {"71"})
        self.assert_cross_repository_requested()

    def test_explicit_requeue_freshly_checks_pr_ownership(self):
        module = self.dispatcher()
        self.github.open_prs = [self.pr(70, foreign=True), self.pr(71, foreign=False)]

        module.close_requeue_pr(7, 70)
        self.assertEqual([row["number"] for row in self.github.open_prs], [70, 71])
        self.assertFalse(any(cmd[3] == "70" for _, cmd in self.github.calls if cmd[1:3] in (["pr", "edit"], ["pr", "close"])))

        module.close_requeue_pr(7, 71)
        self.assertEqual([row["number"] for row in self.github.open_prs], [70])
        self.assert_cross_repository_requested()


if __name__ == "__main__":
    unittest.main()
