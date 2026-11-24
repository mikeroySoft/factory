"""Explicit host requeues win against admissions already in flight."""
import copy
import unittest
from unittest import mock

from factory import config, lifecycle
from tests.test_host_claims import FakeGitHub
from tests.test_host_claims import HostClaimRaceTest as ClaimHarness
from tests.test_host_pin_retention import HostRequeueActionTest as ActionHarness


class RequeueInflightAdmissionTest(unittest.TestCase):
    base_set_up = ActionHarness.setUp
    transport = ActionHarness.transport
    base_transport = ActionHarness.base_transport
    via_mcp = ActionHarness.via_mcp
    apply_mcp = ActionHarness.apply_mcp
    dispatcher = ClaimHarness.dispatcher
    launch = ClaimHarness.launch

    def setUp(self):
        self.base_set_up()
        self.issue["labels"].append({"name": config.LABEL_AGENT})
        self.issue["assignees"] = []
        self.github = FakeGitHub()
        self.github.issue = self.issue
        self.launched = []

    def assert_requeue_beats_inflight_admission(self, requeue):
        ultron = self.dispatcher("ultron")
        acquire_host_claim = ultron.acquire_host_claim
        interleavings = 0

        def acquire(n, stale_issue, stale_claims, **kwargs):
            nonlocal interleavings
            interleavings += 1
            self.assertEqual(stale_claims, [])
            self.assertIn(config.LABEL_AGENT, {row["name"] for row in stale_issue["labels"]})
            self.assertFalse(any(row["name"].startswith("host:") for row in stale_issue["labels"]))

            requeue()

            labels = {row["name"] for row in self.issue["labels"]}
            self.assertIn(config.LABEL_AGENT, labels)
            self.assertIn("host:tank", labels)
            return acquire_host_claim(n, stale_issue, stale_claims, **kwargs)

        with mock.patch.object(ultron, "acquire_host_claim", side_effect=acquire):
            ultron.process_ticket(copy.deepcopy(self.issue), 1, False)

        self.assertEqual(interleavings, 1)
        self.assertEqual(self.launched, [])
        self.assertEqual(
            [row["name"] for row in self.issue["labels"] if row["name"].startswith("host:")],
            ["host:tank"],
        )
        self.assertFalse(any("Factory claim: host=ultron " in row["body"] for row in self.github.comments))

        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.issue), 1, False)
        self.assertEqual(self.launched, ["tank"])
        self.assertTrue(any("Factory claim: host=tank " in row["body"] for row in self.github.comments))

    def test_dispatcher_explicit_requeue_blocks_inflight_foreign_acquire(self):
        tank = self.dispatcher("tank")

        def requeue():
            tank.unclaim(
                7,
                "--remove-label", "ready-for-human",
                "--add-label", config.LABEL_AGENT,
                host="tank",
            )
            pins = [row for row in lifecycle.read_events(tank.EVENTS)
                    if row.get("event") == "requeue-host-pinned"]
            self.assertEqual([(row["ticket"], row["host"]) for row in pins], [(7, "tank")])

        self.assert_requeue_beats_inflight_admission(requeue)

    def test_mcp_explicit_requeue_blocks_inflight_foreign_acquire(self):
        preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
        self.assertNotIn("error", preview, preview)

        def requeue():
            receipt = self.apply_mcp(preview)
            self.assertEqual(receipt["status"], "success")
            pins = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                    if row.get("event") == "requeue-host-pinned"]
            self.assertEqual(
                [(row["ticket"], row["host"], row["caller"]) for row in pins],
                [(7, "tank", "mcp")],
            )

        self.assert_requeue_beats_inflight_admission(requeue)


del ActionHarness, ClaimHarness
