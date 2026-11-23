"""An explicit re-pin cannot hide an unretired target-host claim, even after TTL."""
import copy
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, lifecycle
from tests.test_host_claim_transitions import TimelineGitHub
from tests.test_host_claims import HostClaimRaceTest as ClaimHarness
from tests.test_requeue_decision_ordering import RequeueDecisionOrderingTest as ActionHarness


class RetainedClaimDispatchTest(unittest.TestCase):
    setUp = ClaimHarness.setUp
    dispatcher = ClaimHarness.dispatcher
    launch = ClaimHarness.launch

    def test_target_claim_refuses_explicit_requeue_before_any_mutation(self):
        dispatchers = {host: self.dispatcher(host) for host in ("tank", "ultron", None)}
        for age in (timedelta(hours=3), timedelta()):
            for configured_host in ("tank", "ultron", None):
                with self.subTest(age=age, configured_host=configured_host):
                    self.github = TimelineGitHub()
                    dispatcher = dispatchers[configured_host]
                    self.github.add_claim("tank", datetime.now(UTC) - age)
                    self.github.issue["labels"] = [{"name": config.LABEL_HUMAN}, {"name": "host:tank"}]
                    before = copy.deepcopy((self.github.issue, self.github.comments, self.github.timeline))
                    with mock.patch.object(dispatcher, "run", wraps=dispatcher.run) as run:
                        with self.assertRaisesRegex(ValueError, "unresolved host claim"):
                            dispatcher.unclaim(
                                7, "--remove-label", config.LABEL_HUMAN,
                                "--add-label", config.LABEL_AGENT, host="tank",
                            )
                        run.assert_not_called()
                    self.assertEqual((self.github.issue, self.github.comments, self.github.timeline), before)
                    events = lifecycle.read_events(dispatcher.EVENTS)
                    self.assertEqual(events[-1]["event"], "requeue-host-refused")
                    self.assertEqual((events[-1]["ticket"], events[-1]["host"]), (7, "tank"))

    def test_nonelected_target_claim_also_refuses(self):
        self.github = TimelineGitHub()
        dispatcher = self.dispatcher("owner")
        self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        self.github.add_claim("owner", datetime.now(UTC))
        before = copy.deepcopy((self.github.issue, self.github.comments))
        with self.assertRaisesRegex(ValueError, "unresolved host claim"):
            dispatcher.unclaim(7, "--add-label", config.LABEL_AGENT, host="tank")
        self.assertEqual((self.github.issue, self.github.comments), before)

    def test_hostless_owned_requeue_still_retires_expired_claim(self):
        dispatcher = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        dispatcher.unclaim(7, "--remove-label", config.LABEL_HUMAN, "--add-label", config.LABEL_AGENT)
        self.assertEqual(self.github.issue["labels"], [{"name": config.LABEL_AGENT}])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertFalse(any(row["body"].startswith("Factory claim:") for row in self.github.comments))


class RetainedClaimActionTest(unittest.TestCase):
    setUp = ActionHarness.setUp
    base_setUp = ActionHarness.base_setUp
    transport = ActionHarness.transport
    base_transport = ActionHarness.base_transport
    via_mcp = ActionHarness.via_mcp
    apply_mcp = ActionHarness.apply_mcp
    claim_transport = ActionHarness.claim_transport

    def test_target_claim_refuses_prepare_and_apply_without_mutations(self):
        initial = copy.deepcopy(self.issue)
        for at in ("2020-01-01T00:00:00Z", datetime.now(UTC).isoformat()):
            for stage in ("prepare", "apply"):
                with self.subTest(at=at, stage=stage):
                    self.issue = copy.deepcopy(initial)
                    self.mutations.clear()
                    with self.claim_transport([[]]):
                        preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank", "comment": "resume"})
                    self.assertNotIn("error", preview)
                    before = copy.deepcopy(self.issue)
                    pages = [[], [{
                        "body": f"Factory claim: host=tank nonce=kept at={at}",
                        "user": {"login": "alice"}, "created_at": "2020-01-01T00:00:00Z",
                    }]]
                    with self.claim_transport(pages):
                        result = (self.apply_mcp(preview) if stage == "apply" else
                                  self.via_mcp("requeue_agent", {"number": 7, "host": "tank"}))
                    self.assertEqual(result.get("error", {}).get("code"), "target_busy", result)
                    self.assertEqual(self.issue, before)
                    self.assertEqual(self.mutations, [])
                    events = lifecycle.read_events(self.cfg.factory / "events.jsonl")
                    self.assertEqual(events[-1]["event"], "requeue-host-refused")
                    self.assertEqual((events[-1]["ticket"], events[-1]["host"], events[-1]["caller"]),
                                     (7, "tank", "mcp"))

    def test_counterfeit_and_invalid_claims_do_not_block_explicit_requeue(self):
        initial = copy.deepcopy(self.issue)
        valid = {
            "body": "Factory claim: host=tank nonce=kept at=2020-01-01T00:00:00Z",
            "user": {"login": "alice"}, "created_at": "2020-01-01T00:00:00Z",
        }
        ignored = [
            {**valid, "user": {"login": "outsider"}, "author_association": "COLLABORATOR"},
            {**valid, "user": None},
            {**valid, "body": "Factory claim: host=tank nonce=kept at=invalid"},
            {**valid, "body": "Factory claim: host=tank nonce=kept at=2020-01-01T00:00:00"},
            {**valid, "created_at": "invalid"},
            {**valid, "created_at": "2020-01-01T00:00:00"},
            {**valid, "created_at": None},
            {**valid, "body": "quoted\n" + valid["body"]},
        ]
        for comment in ignored:
            with self.subTest(comment=comment):
                self.issue = copy.deepcopy(initial)
                self.mutations.clear()
                with self.claim_transport([[], [comment]]):
                    preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
                    self.assertNotIn("error", preview, preview)
                    receipt = self.apply_mcp(preview)
                self.assertEqual(receipt["status"], "success", receipt)
                self.assertEqual({row["name"] for row in self.issue["labels"]},
                                 {config.LABEL_AGENT, "host:tank"})

    def test_authenticated_multiline_claim_refuses_at_prepare_and_apply(self):
        comment = {
            "body": "Factory claim: host=tank nonce=kept at=2020-01-01T00:00:00Z\nheartbeat detail",
            "user": {"login": "ALIce"}, "created_at": "2020-01-01T00:00:00Z",
        }
        with self.claim_transport([[]]):
            preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
        before = copy.deepcopy(self.issue)
        with self.claim_transport([[], [comment]]):
            prepared = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
            applied = self.apply_mcp(preview)
        for result in (prepared, applied):
            self.assertEqual(result.get("error", {}).get("code"), "target_busy", result)
        self.assertEqual(self.issue, before)
        self.assertEqual(self.mutations, [])

    def test_hostless_action_does_not_read_retained_claims(self):
        with self.claim_transport([[]], failed=True):
            preview = self.via_mcp("requeue_agent", {"number": 7})
            receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["status"], "success")
        self.assertIn({"name": config.LABEL_AGENT}, self.issue["labels"])


del ActionHarness, ClaimHarness
