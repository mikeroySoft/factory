"""#875: the configured landing host can recover an expired foreign claim."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, manage
from tests import test_land_host as fixture
from tests import test_refresh_conflict as refresh_fixture


class LandClaimTakeoverTest(unittest.TestCase):
    setUp = fixture.LandHostTest.setUp
    dispatcher = fixture.LandHostTest.dispatcher
    claim = fixture.LandHostTest.claim
    approve = fixture.LandHostTest.approve
    events = fixture.LandHostTest.events
    move_main = fixture.LandHostTest.move_main
    assert_untouched = fixture.LandHostTest.assert_untouched

    def assert_no_deletes(self):
        self.assertFalse(any("DELETE" in cmd for calls in self.commands.values() for cmd in calls))

    def test_manager_frontier_only_inspects_expired_claims_on_land_host(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        read_json = tank.gh_json.side_effect

        def frontier_json(args):
            rows = read_json(args)
            if args[:2] == ["pr", "list"]:
                return [{**row, "isCrossRepository": False} for row in rows]
            return rows

        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(tank, "gh_json", side_effect=frontier_json), \
                mock.patch.object(manage, "observe_pr", return_value={"owner": {"relation": "other"}}) as observe:
            manage.frontier_pass(False)
            observe.assert_called_once()
            observe.reset_mock()
            self.comments[0]["body"] = ("Factory claim: host=ultron nonce=ultron-nonce "
                                         f"at={datetime.now(UTC).isoformat()}")
            manage.frontier_pass(False)
            observe.assert_not_called()

    def test_release_revalidates_heartbeat_at_unclaim_boundary(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        read_json = tank.gh_json.side_effect

        def heartbeat_after_read(args):
            row = read_json(args)
            if args[:2] == ["issue", "view"]:
                self.comments[0]["body"] = ("Factory claim: host=ultron nonce=ultron-nonce "
                                             f"at={datetime.now(UTC).isoformat()}")
            return row

        with mock.patch.object(tank, "gh_json", side_effect=heartbeat_after_read):
            with self.assertRaisesRegex(ValueError, "still live"):
                tank.release_expired_claim(9)
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(self.commands["tank"], [])

    def test_lost_takeover_election_never_deletes_claims(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        read_json = tank.gh_json.side_effect

        def renew_before_election(args):
            row = read_json(args)
            if args[:2] == ["api", "repos/acme/widgets/issues/9/comments"] \
                    and any(arg.startswith("body=Factory claim:") for arg in args):
                self.comments[0]["body"] = ("Factory claim: host=ultron nonce=ultron-nonce "
                                             f"at={datetime.now(UTC).isoformat()}")
            return row

        with mock.patch.object(tank, "gh_json", side_effect=renew_before_election):
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assert_no_deletes()

    def test_live_foreign_claim_refuses_takeover_even_when_requeued(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron")
        self.comments.append({"id": 2, "created_at": datetime.now(UTC).isoformat(),
                              "user": {"login": "factory"},
                              "body": "Factory requeue: nonce=ultron-nonce\nContinue"})
        self.commands["tank"].clear()
        tank.land_pass(False)
        self.assert_untouched(tank)
        self.assertEqual(self.commands["tank"], [])

    def test_expired_claim_does_not_authorize_non_land_host_or_unconfigured_takeover(self):
        for host, landing in (("ultron", "tank"), ("tank", None)):
            with self.subTest(host=host, landing=landing):
                module = self.dispatcher(host)
                module.cfg.land_host = landing
                self.approve(module)
                self.comments.clear()
                self.claim("foreign", age=3 * 3600)
                self.commands[host].clear()
                module.merge_pass_locked(False)
                self.assert_untouched(module)
                self.assertEqual(self.commands[host], [])

    def test_released_expired_foreign_claim_can_land(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        self.comments.append({"id": 2, "created_at": datetime.now(UTC).isoformat(),
                              "user": {"login": "factory"},
                              "body": "Factory requeue: nonce=ultron-nonce\nContinue"})
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assert_no_deletes()

    def test_expired_takeover_cannot_supply_missing_exact_head_approval(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        with mock.patch.object(tank, "escalate") as escalate:
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertEqual(escalate.call_args.kwargs["code"], "approval_missing")
        self.assert_no_deletes()

    def test_changed_remote_head_cannot_use_old_approval(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        json_call = tank.gh_json.side_effect

        def changed_head(args):
            row = json_call(args)
            if args[:2] == ["pr", "view"] and "headRefName" in args[-1]:
                return {**row, "headRefOid": "f" * 40}
            return row

        with mock.patch.object(tank, "gh_json", side_effect=changed_head):
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assertEqual(self.events(tank, "escalate"), [])
        self.assert_no_deletes()

    def test_expired_takeover_refreshes_gates_reviews_and_lands_only_new_head(self):
        tank = self.dispatcher("tank")
        old_head = self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        self.move_main()
        with mock.patch.object(tank, "run_gate", return_value=(True, "gate passed")) as gate, \
                mock.patch.object(tank, "review", wraps=tank.review) as review:
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        head = self.github.head()
        self.assertNotEqual(head, old_head)
        gate.assert_called_once()
        self.assertEqual(review.call_args.args[-1], head)
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [old_head, head])
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assert_no_deletes()

    def test_expired_takeover_conflict_requeues_without_deleting_claims(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        original = self.comments[0].copy()
        (self.origin / "feature.txt").write_text("main feature\n")
        fixture.git(self.origin, "add", "feature.txt")
        fixture.git(self.origin, "commit", "-qm", "conflicting main change")
        with mock.patch.object(tank, "run_gate") as gate, mock.patch.object(tank, "review") as review:
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assertEqual(len(self.events(tank, "conflict-requeue")), 1)
        self.assertEqual(self.events(tank, "escalate"), [])
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertIn(config.LABEL_AGENT, self.github.issue_labels)
        self.assertIn(original, self.comments)
        gate.assert_not_called()
        review.assert_not_called()
        self.assert_no_deletes()

        # The retained landing claim keeps the existing TTL hold; after expiry,
        # the ordinary worker must still gate/review its resolved exact head.
        self.assertIsNotNone(tank.host_claim_hold(9, self.github.issue(), tank.host_claims(9)))
        for row in self.comments:
            if row["body"].startswith("Factory claim: host=tank "):
                row["body"] = (row["body"].rsplit(" at=", 1)[0]
                               + f" at={(datetime.now(UTC) - timedelta(hours=3)).isoformat()}")
        resolver = self.tmp / "resolve.py"
        resolver.write_text(refresh_fixture.RESOLVER.replace("shared.txt", "feature.txt"))
        prompt = self.tmp / "worker-prompt.md"
        tank.cfg.workers = {"default": [sys.executable, str(resolver), str(prompt)]}
        with mock.patch.object(tank, "run_gate", return_value=(True, "resolved gate")) as gate, \
                mock.patch.object(tank, "review", wraps=tank.review) as review:
            [ready] = tank.frontier()
            tank.process_ticket(ready, 30, False)
        head = self.github.head()
        self.assertNotEqual(head, self.old_head)
        gate.assert_called_once()
        self.assertEqual(review.call_args.args[-1], head)
        self.assertIn("Refresh conflict", prompt.read_text())
        self.commands["tank"].clear()
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assert_no_deletes()

    def test_dry_run_checks_expiry_without_mutations(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        before = self.comments.copy()
        self.commands["tank"].clear()
        tank.land_pass(True)
        self.assertFalse(self.github.merged)
        self.assertEqual(self.comments, before)
        self.assertEqual(self.commands["tank"], [cmd for cmd in self.commands["tank"]
                                                if cmd[:3] == ["gh", "pr", "checks"]
                                                or cmd[:2] == ["gh", "api"]])

    def test_release_rejects_human_assignee_and_new_heartbeat_without_mutations(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        self.github.assignees = ["human"]
        with self.assertRaisesRegex(ValueError, "human assignee"):
            tank.release_expired_claim(9)
        self.github.assignees = ["factory"]
        self.comments[0]["body"] = ("Factory claim: host=ultron nonce=ultron-nonce "
                                     f"at={datetime.now(UTC).isoformat()}")
        with self.assertRaisesRegex(ValueError, "still live"):
            tank.release_expired_claim(9)
        self.assertEqual(self.commands["tank"], [])

    def test_release_rejects_untrusted_shared_login_and_changed_election(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        with self.assertRaisesRegex(ValueError, "changed"):
            tank.release_expired_claim(9, expected_nonce="obsolete")
        self.comments[0]["user"] = {"login": "other-bot"}
        self.comments[0]["author_association"] = "MEMBER"
        with self.assertRaisesRegex(ValueError, "does not match"):
            tank.release_expired_claim(9)
        self.assertEqual(self.commands["tank"], [])

    def test_release_owned_expired_claim_preserves_comment(self):
        tank = self.dispatcher("tank")
        self.claim("tank", age=3 * 3600)
        original = self.comments[0].copy()
        claim = tank.release_expired_claim(9, expected_nonce="tank-nonce")
        self.assertEqual(claim["nonce"], "tank-nonce")
        self.assertIn(original, self.comments)
        self.assertIn(config.LABEL_AGENT, self.github.issue_labels)
        self.assert_no_deletes()

    def test_expired_foreign_claim_lands_without_deleting_claim_history(self):
        tank = self.dispatcher("tank")
        head = self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        original = self.comments[0].copy()
        self.github.issue_labels.add("host:ultron")

        tank.land_pass(False)

        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assertIn(original, self.comments)
        self.assertFalse(any("DELETE" in cmd for cmd in self.commands["tank"]))
        requeues = [row for row in self.comments if row["body"].startswith("Factory requeue:")]
        self.assertEqual(requeues[0]["body"].splitlines()[0], "Factory requeue: nonce=ultron-nonce")
        takeover = self.events(tank, "claim-takeover")
        self.assertEqual([(row["foreign_host"], row["foreign_nonce"]) for row in takeover],
                         [("ultron", "ultron-nonce")])


if __name__ == "__main__":
    unittest.main()
