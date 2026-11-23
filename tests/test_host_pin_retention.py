"""#888: explicit requeue pins, without changing escalation cleanup."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import decisions, lifecycle, manage
from tests.test_actions import ActionPathsTest as ActionHarness
from tests.test_host_claim_transitions import TimelineGitHub
from tests.test_host_claims import HostClaimRaceTest as ClaimHarness


class HostRequeueTest(unittest.TestCase):
    setUp = ClaimHarness.setUp
    dispatcher = ClaimHarness.dispatcher
    launch = ClaimHarness.launch

    def set_up_owner(self):
        self.github = TimelineGitHub()
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        return tank

    def test_explicit_requeue_blocks_foreign_dispatch(self):
        tank = self.set_up_owner()
        tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent", host="tank")
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        events = lifecycle.read_events(tank.EVENTS)
        removed = next(i for i, row in enumerate(events) if row.get("event") == "claim-label-removed")
        pinned = next(i for i, row in enumerate(events) if row.get("event") == "requeue-host-pinned")
        self.assertLess(removed, pinned)
        self.assertEqual(events[pinned]["host"], "tank")
        ultron = self.dispatcher("ultron")
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [])
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_unpinned_requeue_allows_foreign_dispatch(self):
        tank = self.set_up_owner()
        tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent")
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        ultron = self.dispatcher("ultron")
        ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, ["ultron"])

    def test_second_explicit_requeue_repins_but_escalation_cleans_up(self):
        tank = self.set_up_owner()
        for _ in range(2):
            tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent", host="tank")
            self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
            self.github.add_claim("tank", datetime.now(UTC))
        tank.escalate(7, "gate failed", None, code="gate_failed")
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
        packet = tank.FACTORY / "escalations" / "7.md"
        with mock.patch.object(manage, "dispatch", tank):
            manage.apply(7, copy.deepcopy(self.github.issue), "RETRY", "Continue", None, packet)
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])

    def test_failed_cleanup_does_not_pin(self):
        tank = self.set_up_owner()
        run = tank.run

        def fail_cleanup(cmd, *args, **kwargs):
            if "--remove-assignee" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "permission denied")
            return run(cmd, *args, **kwargs)

        with mock.patch.object(tank, "run", side_effect=fail_cleanup):
            tank.unclaim(7, "--add-label", "ready-for-agent", host="ultron")
        self.assertNotIn({"name": "host:ultron"}, self.github.issue["labels"])
        self.assertFalse(any(row.get("event") == "requeue-host-pinned"
                             for row in lifecycle.read_events(tank.EVENTS)))


class HostRequeueActionTest(unittest.TestCase):
    setUp = ActionHarness.setUp
    base_transport = ActionHarness.transport
    via_dashboard = ActionHarness.via_dashboard
    apply_dashboard = ActionHarness.apply_dashboard
    via_mcp = ActionHarness.via_mcp
    apply_mcp = ActionHarness.apply_mcp
    journal = ActionHarness.journal

    def transport(self, argv, **kwargs):
        result = self.base_transport(argv, **kwargs)
        if argv[:3] == ["gh", "issue", "edit"]:
            for index, flag in enumerate(argv[:-1]):
                if flag == "--remove-label":
                    self.issue["labels"] = [row for row in self.issue["labels"] if row["name"] != argv[index + 1]]
                elif flag == "--add-label" and {"name": argv[index + 1]} not in self.issue["labels"]:
                    self.issue["labels"].append({"name": argv[index + 1]})
                elif flag == "--remove-assignee":
                    self.issue["assignees"] = [row for row in self.issue["assignees"] if row["login"] != argv[index + 1]]
        return result

    def test_mcp_requeue_explicit_host_is_confirmed_applied_and_journaled(self):
        preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
        self.assertNotIn("error", preview, preview)
        self.assertEqual(self.mutations, [])
        self.assertIn("host:tank", str(preview["effects"]))
        receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(self.issue["labels"], [{"name": "ready-for-agent"}, {"name": "host:tank"}])
        self.assertEqual(self.issue["assignees"], [])
        self.assertEqual(self.mutations[-1][-2:], ["--add-label", "host:tank"])
        self.assertNotIn("host:tank", self.mutations[0])
        events = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        [pin] = [row for row in events if row.get("event") == "requeue-host-pinned"]
        self.assertEqual((pin["ticket"], pin["host"], pin["actor"], pin["caller"]), (7, "tank", "alice", "mcp"))
        self.assertEqual(self.apply_mcp(preview), receipt)
        self.assertEqual(len(self.mutations), 3)
        self.issue["labels"] = [{"name": "ready-for-human"}, {"name": "host:tank"}]
        second = self.via_dashboard("requeue_agent", {"number": 7})
        self.assertEqual(self.apply_dashboard(second)["status"], "success")
        self.assertIn({"name": "host:tank"}, self.issue["labels"])

    def test_invalid_hosts_refused_before_proposal(self):
        for host in ("", "host:tank", "-tank", "tank\n", "a" * 46, None, 7):
            with self.subTest(host=host), self.assertRaises(decisions.DecisionError) as error:
                self.via_dashboard("requeue_agent", {"number": 7, "host": host})
            self.assertEqual(error.exception.code, "invalid_args")
        self.assertEqual(self.mutations, [])
        self.assertEqual(self.journal(), [])


del ActionHarness, ClaimHarness
