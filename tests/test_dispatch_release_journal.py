"""Assignment-release receipts identify the requeue path, not just its helper."""
from __future__ import annotations

import unittest

from factory import lifecycle
from tests import test_host_claims


class RequeueReleaseJournalTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def test_deferred_releases_journal_the_actual_exit_and_close_the_pr(self):
        for host in ("", "tank"):
            for path in ("mutation-proof-wait", "review-rework"):
                with self.subTest(host=host, path=path):
                    self.github = test_host_claims.FakeGitHub()
                    module = self.dispatcher(host)
                    # Model an active dispatch after publication, with real claim cleanup.
                    if host:
                        claim = module.acquire_host_claim(7, self.github.issue, [])
                        self.assertIsNotNone(claim)
                        self.addCleanup(claim.stop)
                    module._ASSIGNMENTS[7] = "@me"
                    self.github.issue["assignees"] = [{"login": "@me"}]
                    self.github.open_prs = [{"number": 70}]
                    before = len(lifecycle.read_events(module.EVENTS))

                    if path == "mutation-proof-wait":
                        module.defer_mutation_proof(7, "a" * 40, "ticket review")
                    else:
                        module._record_rework(7, "a" * 40, "repair missing branch", deferred=True)

                    self.assertEqual(self.github.issue["assignees"], [])
                    self.assertEqual(self.github.open_prs, [])
                    self.assertEqual(
                        [comment["body"] for comment in self.github.comments],
                        [f"Factory requeue: nonce={claim.comment['nonce']}\n"] if host else [],
                    )
                    self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
                    events = lifecycle.read_events(module.EVENTS)[before:]
                    [released] = [row for row in events if row.get("event") == "assignee-cleared"]
                    self.assertEqual(
                        (released["ticket"], released["login"], released["exit_path"]),
                        (7, "@me", path),
                    )
                    [closed] = [row for row in events if row.get("event") == "pr-requeued"]
                    self.assertEqual((closed["ticket"], closed["pr"]), (7, 70))
                    (module.FACTORY / "wt-7").rmdir()


if __name__ == "__main__":
    unittest.main()
