"""#875: claim release is durable before refresh requeue opens admission."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests import test_land_claim_takeover as takeover_fixture
from tests import test_land_host as land_fixture

git = land_fixture.git


class ClaimRefreshProtocolTest(unittest.TestCase):
    setUp = land_fixture.LandHostTest.setUp
    dispatcher = takeover_fixture.LandClaimTakeoverTest.dispatcher
    claim = takeover_fixture.LandClaimTakeoverTest.claim
    approve = takeover_fixture.LandClaimTakeoverTest.approve
    events = takeover_fixture.LandClaimTakeoverTest.events

    def takeover_claim(self, tank):
        self.claim("ultron", age=3 * 3600)
        foreign = tank.elected_host_claim(tank.host_claims(9))
        with tank.landing_host_claim(
            9, foreign, dry_run=False, pr=102, head=self.old_head,
        ) as owned:
            self.assertTrue(owned)
        [local] = [row for row in tank.host_claims(9) if row["host"] == "tank"]
        self.assertTrue(local["preserve_history"])
        return foreign, local

    @staticmethod
    def expire(comment):
        first, separator, rest = comment["body"].partition("\n")
        comment["body"] = (
            first.rsplit(" at=", 1)[0]
            + f" at={(datetime.now(UTC) - timedelta(hours=3)).isoformat()}"
            + separator + rest
        )

    def assert_originals_not_deleted(self, originals):
        for original in originals:
            self.assertIn(original, self.comments)
            self.assertFalse(any(
                "DELETE" in cmd and cmd[2].endswith(f"/{original['id']}")
                for calls in self.commands.values() for cmd in calls
            ))

    def test_conflict_refresh_releases_takeover_before_cross_host_admission_cleanup(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(tank)
        foreign, local = self.takeover_claim(tank)
        local_comment = next(row for row in self.comments if row["id"] == local["id"])
        self.expire(local_comment)
        originals = [row.copy() for row in self.comments]

        (self.origin / "feature.txt").write_text("main feature\n")
        git(self.origin, "add", "feature.txt")
        git(self.origin, "commit", "-qm", "conflicting main change")

        tank_json = tank.gh_json.side_effect
        admitted = []
        receipt_visible = []

        def admit_after_requeue(args):
            row = tank_json(args)
            body = next((arg.removeprefix("body=") for arg in args if arg.startswith("body=")), "")
            if (not admitted and args[:1] == ["api"] and "--method" in args
                    and args[args.index("--method") + 1] == "POST"
                    and body.startswith(f"Factory requeue: nonce={local['nonce']}")):
                receipt_visible.append(any(
                    comment["body"] == f"Factory claim released: nonce={local['nonce']}"
                    for comment in self.comments
                ))
                claims = ultron.host_claims(9)
                issue = self.github.issue()
                self.assertIsNone(ultron.host_claim_hold(9, issue, claims))
                replacement = ultron.acquire_host_claim(9, issue, claims)
                self.assertIsNotNone(replacement)
                ultron.run([
                    "gh", "issue", "edit", "9", "--repo", ultron.REPO,
                    "--add-assignee", "@me",
                ])
                ultron.release_stale_claims(
                    9, issue, claims, replacement=replacement.comment["id"],
                )
                replacement.stop()
                ultron._HOST_CLAIMS.pop(9, None)
                admitted.append(replacement.comment.copy())
            return row

        with mock.patch.object(tank, "gh_json", side_effect=admit_after_requeue):
            self.assertFalse(tank.refresh_pr_branch(9, 102, False))

        self.assertEqual(receipt_visible, [True])
        self.assertEqual(len(admitted), 1)
        bodies = [row["body"] for row in self.comments]
        self.assertLess(
            bodies.index(f"Factory claim released: nonce={local['nonce']}"),
            next(i for i, body in enumerate(bodies)
                 if body.startswith(f"Factory requeue: nonce={local['nonce']}")),
        )
        self.assert_originals_not_deleted(originals)
        self.assertIn(foreign["id"], {row["id"] for row in self.comments})
        self.assertEqual(self.github.head(), self.old_head)
        self.assertFalse(self.github.merged)
        self.assertEqual(len(self.events(tank, "conflict-requeue")), 1)

    def test_dashboard_release_of_takeover_is_not_resumed_by_landing(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        _foreign, local = self.takeover_claim(tank)
        local_comment = next(row for row in self.comments if row["id"] == local["id"])
        self.expire(local_comment)
        originals = [row.copy() for row in self.comments]
        before_ids = {row["id"] for row in self.comments}

        tank.release_expired_claim(9, reason="dashboard release_claim")

        dashboard_rows = [row for row in self.comments if row["id"] not in before_ids]
        self.assertFalse(any("Factory claim history: retained" in row["body"] for row in dashboard_rows))
        bodies = [row["body"] for row in self.comments]
        self.assertLess(
            bodies.index(f"Factory claim released: nonce={local['nonce']}"),
            next(i for i, body in enumerate(bodies)
                 if body.startswith(f"Factory requeue: nonce={local['nonce']}")),
        )
        released_body = local_comment["body"]

        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]):
            tank.land_pass(False)

        self.assertEqual(local_comment["body"], released_body)
        self.assertEqual(tank.live_host_claims(tank.host_claims(9)), [])
        self.assertNotIn(9, tank._HOST_CLAIMS)
        self.assertEqual(len(self.events(tank, "claim-takeover")), 1)
        self.assert_originals_not_deleted(originals)
        self.assertFalse(self.github.merged)


if __name__ == "__main__":
    unittest.main()
