"""#875: the configured landing host can recover an expired foreign claim."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, manage
from tests import test_host_claims as claim_fixture
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

        with mock.patch.object(tank, "gh_json", side_effect=heartbeat_after_read), \
                self.assertRaisesRegex(ValueError, "still live"):
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

    def test_foreign_journal_is_replaced_by_fresh_local_proof_before_unchanged_head_lands(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        head = self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        self.assertFalse(tank.EVENTS.exists())
        with mock.patch.object(tank, "run_gate", return_value=(True, "fresh local gate")) as gate, \
                mock.patch.object(tank, "review", wraps=tank.review) as review:
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assertEqual(self.github.head(), head)
        gate.assert_called_once()
        self.assertEqual(review.call_args.args[-1], head)
        self.assertEqual([row["head"] for row in self.events(tank, "refreshed")], [head])
        self.assertEqual([row["head"] for row in self.events(tank, "review")], [head])
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [head])
        self.assertEqual(self.events(tank, "escalate"), [])
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assert_no_deletes()

    def test_unclaimed_pr_still_refuses_missing_exact_head_approval(self):
        tank = self.dispatcher("tank")
        with mock.patch.object(tank, "escalate") as escalate:
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertEqual(escalate.call_args.kwargs["code"], "approval_missing")

    def test_expired_takeover_cannot_supply_missing_exact_head_approval(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        tank.cfg.reviewer = [
            sys.executable, "-c",
            "print('```json\\n{\"verdict\":\"REVISE\",\"findings\":[]}\\n```\\nVERDICT: REVISE')",
        ]
        with mock.patch.object(tank, "run_gate", return_value=(True, "fresh local gate")), \
                mock.patch.object(tank, "escalate") as escalate:
            tank.land_pass(False)
        self.assertFalse(self.github.merged)
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertEqual(escalate.call_args.kwargs["code"], "review_revise")
        self.assertEqual(self.events(tank, "approved"), [])
        self.assertEqual([row["verdict"] for row in self.events(tank, "review")], ["REVISE"])
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

    def test_pending_takeover_resumes_fresh_local_proof_on_next_green_pass(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        head = self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]), \
                mock.patch.object(tank, "run_gate") as gate:
            tank.land_pass(False)
        gate.assert_not_called()
        self.assertEqual(len(self.events(tank, "claim-takeover")), 1)
        self.assertEqual(tank._HOST_CLAIMS, {})
        self.assertFalse(self.github.merged)

        with mock.patch.object(tank, "run_gate", return_value=(True, "fresh local gate")) as gate, \
                mock.patch.object(tank, "review", wraps=tank.review) as review:
            tank.land_pass(False)
        gate.assert_called_once()
        self.assertEqual(review.call_args.args[-1], head)
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [head])
        self.assertEqual(self.events(tank, "escalate"), [])
        self.assertFalse(self.github.merged)
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assert_no_deletes()

    def test_pending_takeover_recovery_does_not_authorize_a_different_revision(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]):
            tank.land_pass(False)
        fixture.git(self.origin, "checkout", "-q", "agent/9")
        (self.origin / "unreviewed.txt").write_text("another revision\n")
        fixture.git(self.origin, "add", "unreviewed.txt")
        fixture.git(self.origin, "commit", "-qm", "unreviewed revision")
        fixture.git(self.origin, "checkout", "-q", "main")
        with mock.patch.object(tank, "run_gate") as gate, \
                mock.patch.object(tank, "review") as review, \
                mock.patch.object(tank, "escalate") as escalate:
            tank.land_pass(False)
        gate.assert_not_called()
        review.assert_not_called()
        self.assertFalse(self.github.merged)
        self.assertEqual(escalate.call_args.kwargs["code"], "approval_missing")
        self.assert_no_deletes()

    def test_conflict_expiry_and_worker_recovery_preserve_entire_claim_history(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        original = self.comments[0].copy()
        (self.origin / "feature.txt").write_text("main feature\n")
        fixture.git(self.origin, "add", "feature.txt")
        fixture.git(self.origin, "commit", "-qm", "conflicting main change")
        tank.land_pass(False)
        self.assertEqual(len(self.events(tank, "conflict-requeue")), 1)
        landing_ids = {row["id"] for row in self.comments
                       if row["body"].startswith("Factory claim:")}
        for row in self.comments:
            if row["body"].startswith("Factory claim: host=tank "):
                first, separator, rest = row["body"].partition("\n")
                row["body"] = (first.rsplit(" at=", 1)[0]
                               + f" at={(datetime.now(UTC) - timedelta(hours=3)).isoformat()}"
                               + separator + rest)
        resolver = self.tmp / "history-resolve.py"
        resolver.write_text(refresh_fixture.RESOLVER.replace("shared.txt", "feature.txt"))
        prompt = self.tmp / "history-worker-prompt.md"
        tank.cfg.workers = {"default": [sys.executable, str(resolver), str(prompt)]}
        with mock.patch.object(tank, "run_gate", return_value=(True, "resolved gate")) as gate, \
                mock.patch.object(tank, "review", wraps=tank.review) as review:
            [ready] = tank.frontier()
            self.assertNotIn("held", ready)
            tank.process_ticket(ready, 30, False)
        head = self.github.head()
        gate.assert_called_once()
        self.assertEqual(review.call_args.args[-1], head)
        self.assertIn("Refresh conflict", prompt.read_text())
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertIn(original, self.comments)
        self.assertTrue(landing_ids.issubset({row["id"] for row in self.comments}))
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assert_no_deletes()

    def test_foreign_claim_expires_at_exact_ttl_edge(self):
        tank = self.dispatcher("tank")
        head = self.approve(tank)
        self.claim("ultron")
        original = self.comments[0].copy()
        heartbeat = datetime.fromisoformat(original["created_at"])
        edge = heartbeat + timedelta(seconds=tank.cfg.claim_ttl_seconds)
        commands_before = self.commands["tank"].copy()
        with mock.patch.object(tank, "datetime", wraps=datetime) as clock:
            clock.now.return_value = edge - timedelta(microseconds=1)
            tank.land_pass(False)
            self.assert_untouched(tank)
            self.assertEqual(self.commands["tank"], commands_before)
            self.assertEqual(self.comments, [original])
            clock.now.return_value = edge
            tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assertIn(original, self.comments)
        self.assertTrue(any(row["body"].startswith("Factory requeue: nonce=ultron-nonce")
                            for row in self.comments))
        self.assert_no_deletes()

    def test_pending_takeover_past_ttl_renews_before_worker_admission(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        head = self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]):
            tank.land_pass(False)
            owner = tank.owned_host_claim(9)
            edge = owner["heartbeat"] + timedelta(seconds=tank.cfg.claim_ttl_seconds)
            with mock.patch.object(tank, "datetime", wraps=datetime) as clock:
                clock.now.return_value = edge + timedelta(microseconds=1)
                tank.land_pass(False)
                tank.process_ticket(self.github.issue(), 30, False)
                self.assertEqual(self.events(tank, "escalate"), [])
                self.assertEqual([row["nonce"] for row in tank.live_host_claims(tank.host_claims(9))],
                                 [owner["nonce"]])
        self.assertFalse(self.github.merged)
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        with mock.patch.object(tank, "run_gate", return_value=(True, "fresh local gate")) as gate, \
                mock.patch.object(tank, "review", wraps=tank.review) as review:
            tank.land_pass(False)
        gate.assert_called_once()
        self.assertEqual(review.call_args.args[-1], head)
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [head])
        self.assertFalse(self.github.merged)
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])
        self.assert_no_deletes()

    def test_refused_takeover_is_not_journaled_as_completed_eligibility(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("ultron", age=3 * 3600)
        self.github.assignees = ["human"]
        tank.land_pass(False)
        self.assertFalse(self.github.merged)
        exits = [row for row in self.events(tank, "lifecycle")
                 if row.get("stage") == "merge-eligibility" and row.get("kind") == "exit"]
        self.assertEqual([(row["outcome"], row["reason"]) for row in exits],
                         [("not_eligible", "claim_lost")])

    def test_pending_takeover_recovers_with_equal_nonidentical_configured_hosts(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        tank.cfg.host_name = "".join(tank.cfg.host_name)
        tank.cfg.land_host = "".join(tank.cfg.land_host)
        self.assertEqual(tank.cfg.host_name, tank.cfg.land_host)
        self.assertIsNot(tank.cfg.host_name, tank.cfg.land_host)
        head = self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]):
            tank.land_pass(False)
        self.assertEqual(len(self.events(tank, "claim-takeover")), 1)
        with mock.patch.object(tank, "run_gate", return_value=(True, "fresh local gate")) as gate:
            tank.land_pass(False)
        gate.assert_called_once()
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [head])
        self.assertFalse(self.github.merged)
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assert_no_deletes()

    def test_unnamed_host_does_not_resume_takeover_approval_recovery(self):
        tank = self.dispatcher("tank")
        for host in ("", None):
            with self.subTest(host=host):
                tank.cfg.host_name = tank.cfg.land_host = host
                with mock.patch.object(tank, "owned_host_claim", side_effect=AssertionError(
                    "unnamed host must not read takeover ownership",
                )), mock.patch.object(tank, "escalate") as escalate:
                    tank.merge_pass_locked(False)
                self.assertEqual(escalate.call_args.kwargs["code"], "approval_missing")
                self.assertFalse(self.github.merged)
                self.github.pr_labels.add(config.LABEL_APPROVED)

    def test_pending_takeover_renews_at_admission_when_earlier_pr_refreshes(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        fixture.git(self.origin, "branch", "agent/8", "agent/9")
        head = self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]):
            tank.land_pass(False)
        owner = tank.owned_host_claim(9)
        edge = owner["heartbeat"] + timedelta(seconds=tank.cfg.claim_ttl_seconds)
        original_json = tank.gh_json.side_effect

        def earlier_pr(args):
            if args[:3] == ["api", "--paginate", "--slurp"] and "/issues/8/" in args[3]:
                return [[]]
            if args[:2] == ["api", f"repos/{tank.REPO}/compare/main...{head}"]:
                return {"behind_by": 1}
            row = original_json(args)
            if args[:2] == ["pr", "list"] and "--head" not in args:
                return [{**self.github.pr(), "number": 101, "headRefName": "agent/8"}, *row]
            if args[:3] == ["pr", "view", "101"]:
                return {**row, "number": 101, "headRefName": "agent/8"}
            return row

        with mock.patch.object(tank, "datetime", wraps=datetime) as clock, \
                mock.patch.object(tank, "gh_json", side_effect=earlier_pr), \
                mock.patch.object(tank, "refresh_pr_branch", return_value=True) as refresh, \
                mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]):
            clock.now.return_value = edge + timedelta(microseconds=1)
            tank.land_pass(False)
            refresh.assert_called_once_with(8, 101, False)
            tank.process_ticket(self.github.issue(), 30, False)
            self.assertEqual(self.events(tank, "escalate"), [])
            self.assertEqual(
                [row["nonce"] for row in tank.live_host_claims(tank.host_claims(9))],
                [owner["nonce"]],
            )
        self.assertFalse(self.github.merged)
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        with mock.patch.object(tank, "run_gate", return_value=(True, "fresh local gate")) as gate:
            tank.land_pass(False)
        gate.assert_called_once()
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [head])
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assert_no_deletes()

    def test_landing_does_not_revive_dashboard_released_own_claim(self):
        tank = self.dispatcher("tank")
        self.approve(tank)
        self.claim("tank", age=3 * 3600)
        original = self.comments[0].copy()
        tank.release_expired_claim(9)
        with mock.patch.object(tank, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}]):
            tank.land_pass(False)
        self.assertEqual(self.comments[0], original)
        self.assertEqual(tank.live_host_claims(tank.host_claims(9)), [])
        self.assertIsNone(tank.host_claim_hold(9, self.github.issue(), tank.host_claims(9)))
        self.assertFalse(self.github.merged)
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assert_no_deletes()


class LandingClaimLifetimeTest(unittest.TestCase):
    setUp = claim_fixture.HostClaimRaceTest.setUp
    dispatcher = claim_fixture.HostClaimRaceTest.dispatcher

    def test_normal_owned_claim_is_released_and_does_not_block_admission(self):
        tank = self.dispatcher("tank")
        tank.cfg.land_host = "tank"
        original = self.github.add_claim("tank", datetime.now(UTC))
        with tank.landing_host_claim(7, None, dry_run=False, pr=8, head="a" * 40) as owned:
            self.assertTrue(owned)
            tank.unclaim(7, "--add-label", config.LABEL_AGENT)
        self.assertNotIn(original, self.github.comments)
        claim = tank.acquire_host_claim(7, self.github.issue, tank.host_claims(7))
        self.assertIsNotNone(claim)
        try:
            self.assertFalse(claim.preserve_history)
        finally:
            claim.release()
            tank._HOST_CLAIMS.pop(7, None)

    def test_unclaim_retains_history_from_either_local_or_published_claim(self):
        tank = self.dispatcher("tank")
        for local_retained, published_retained in ((True, False), (False, True)):
            with self.subTest(local=local_retained, published=published_retained):
                self.github = claim_fixture.FakeGitHub()
                original = self.github.add_claim("tank", datetime.now(UTC))
                if published_retained:
                    original["body"] += "\nFactory claim history: retained"
                claim = tank.HostClaim(7, tank.host_claims(7)[-1])
                claim.preserve_history = local_retained
                tank._HOST_CLAIMS[7] = claim
                try:
                    tank.unclaim(7, "--add-label", config.LABEL_AGENT)
                    self.assertIn(original, self.github.comments)
                    markers = [row["body"] for row in self.github.comments
                               if row["body"].startswith("Factory requeue:")]
                    self.assertIn("Factory claim history: retained", markers[-1])
                    self.assertFalse(any("DELETE" in cmd for _, cmd in self.github.calls))
                finally:
                    claim.stop()
                    tank._HOST_CLAIMS.pop(7, None)

    def test_resumed_heartbeat_uses_host_value_not_string_identity(self):
        tank = self.dispatcher("tank")
        tank.cfg.host_name = "".join(tank.cfg.host_name)
        tank.cfg.land_host = "".join(tank.cfg.host_name)
        self.assertIsNot(tank.cfg.host_name, tank.cfg.land_host)
        original = self.github.add_claim("tank", datetime.now(UTC))
        original["body"] += "\nFactory claim history: retained"
        with tank.landing_host_claim(7, None, dry_run=False, pr=8, head="a" * 40) as owned:
            self.assertTrue(owned)
            self.assertIn(7, tank._HOST_CLAIMS)
            self.assertTrue(tank._HOST_CLAIMS[7].thread.is_alive())
        self.assertNotIn(7, tank._HOST_CLAIMS)
        self.assertIn(original, self.github.comments)

    def test_landing_cleanup_does_not_remove_equal_replacement_claim(self):
        tank = self.dispatcher("tank")
        tank.cfg.land_host = "tank"
        original = self.github.add_claim("tank", datetime.now(UTC))
        original["body"] += "\nFactory claim history: retained"

        class EqualClaim(tank.HostClaim):
            def __eq__(self, other):
                return isinstance(other, tank.HostClaim) and self.comment == other.comment

        with tank.landing_host_claim(7, None, dry_run=False, pr=8, head="a" * 40):
            current = tank._HOST_CLAIMS[7]
            replacement = EqualClaim(7, current.comment.copy())
            self.assertEqual(replacement, current)
            self.assertIsNot(replacement, current)
            tank._HOST_CLAIMS[7] = replacement
        try:
            self.assertIs(tank._HOST_CLAIMS.get(7), replacement)
        finally:
            replacement.stop()
            tank._HOST_CLAIMS.pop(7, None)

    def test_dashboard_release_does_not_retain_later_ordinary_worker_claims(self):
        tank = self.dispatcher("tank")
        original = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3)).copy()
        tank.release_expired_claim(7)
        claim = tank.acquire_host_claim(7, self.github.issue, tank.host_claims(7))
        self.assertIsNotNone(claim)
        try:
            tank.unclaim(7, "--add-label", config.LABEL_AGENT)
        finally:
            claim.stop()
            tank._HOST_CLAIMS.pop(7, None)
        self.assertIn(original, self.github.comments)
        self.assertFalse(any(
            row["body"].startswith("Factory claim: host=tank ")
            for row in self.github.comments
        ))
        self.assertIsNone(tank.host_claim_hold(7, self.github.issue, tank.host_claims(7)))
        replacement = tank.acquire_host_claim(7, self.github.issue, tank.host_claims(7))
        self.assertIsNotNone(replacement)
        try:
            self.assertFalse(replacement.preserve_history)
        finally:
            replacement.release()
            tank._HOST_CLAIMS.pop(7, None)
        self.assertIn(original, self.github.comments)

    def test_dashboard_release_never_publishes_takeover_retention_marker(self):
        tank = self.dispatcher("tank")
        original = self.github.add_claim(
            "ultron", datetime.now(UTC) - timedelta(hours=3),
        ).copy()
        nonce = original["body"].split("nonce=", 1)[1].split()[0]

        tank.release_expired_claim(n=7, reason="dashboard release_claim")

        self.assertIn(original, self.github.comments)
        [marker] = [row["body"] for row in self.github.comments
                    if row["body"].startswith("Factory requeue:")]
        self.assertEqual(marker, f"Factory requeue: nonce={nonce}\n")
        self.assertFalse(any("DELETE" in cmd for _, cmd in self.github.calls))

    def test_takeover_publishes_exact_retention_marker_and_retained_claim_body(self):
        tank = self.dispatcher("tank")
        tank.cfg.land_host = "tank"
        original = self.github.add_claim(
            "ultron", datetime.now(UTC) - timedelta(hours=3),
        ).copy()
        foreign = tank.elected_host_claim(tank.host_claims(7))

        with tank.landing_host_claim(
            n=7, foreign=foreign, dry_run=False, pr=8, head="a" * 40,
        ) as owned:
            self.assertTrue(owned)
            local = tank.owned_host_claim(7)
            self.assertTrue(local["preserve_history"])
            self.assertEqual(
                local["body"].splitlines()[1:],
                ["Factory claim history: retained"],
            )

        self.assertIn(original, self.github.comments)
        [marker] = [row["body"] for row in self.github.comments
                    if row["body"].startswith("Factory requeue:")]
        self.assertEqual(
            marker,
            f"Factory requeue: nonce={foreign['nonce']}\nFactory claim history: retained\n",
        )
        self.assertFalse(any("DELETE" in cmd for _, cmd in self.github.calls))


if __name__ == "__main__":
    unittest.main()
