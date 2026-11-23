"""Explicit-host action readiness is published only after the pin is audited."""
import copy
import json
import subprocess
import unittest
from unittest import mock

from factory import config, decisions, lifecycle
from tests.test_host_claims import FakeGitHub
from tests.test_host_claims import HostClaimRaceTest as ClaimHarness
from tests.test_host_pin_retention import HostRequeueActionTest as ActionHarness


class RequeueDecisionOrderingTest(unittest.TestCase):
    base_setUp = ActionHarness.setUp
    transport = ActionHarness.transport
    base_transport = ActionHarness.base_transport
    via_mcp = ActionHarness.via_mcp
    apply_mcp = ActionHarness.apply_mcp
    dispatcher = ClaimHarness.dispatcher
    launch = ClaimHarness.launch

    def setUp(self):
        self.base_setUp()
        self.issue["labels"].append({"name": config.LABEL_AGENT})
        self.github = FakeGitHub()
        self.github.issue = self.issue
        self.launched = []

    def proposal(self, preview):
        path = self.cfg.factory / "decisions" / f"proposal-{preview['proposal_id']}.json"
        return json.loads(path.read_text())

    def test_mcp_cleanup_pin_and_audit_never_admit_foreign_stale_frontier(self):
        ultron = self.dispatcher("ultron")
        preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
        stale = copy.deepcopy(self.issue)
        boundaries = []
        transport, append = self.transport, lifecycle.append

        def probe(boundary):
            boundaries.append(boundary)
            ultron.process_ticket(copy.deepcopy(stale), 1, False)
            self.assertEqual(self.launched, [], boundary)
            self.assertNotIn({"name": config.LABEL_AGENT}, self.issue["labels"], boundary)

        def observe(argv, **kwargs):
            result = transport(argv, **kwargs)
            if argv[:3] == ["gh", "issue", "edit"]:
                if "--remove-assignee" in argv:
                    probe("cleanup")
                elif "--add-label" in argv and "host:tank" in argv:
                    probe("pin")
            return result

        def audit(path, row):
            if row.get("event") == "requeue-host-pinned":
                probe("audit")
            return append(path, row)

        with mock.patch.object(decisions, "_run", side_effect=observe), \
                mock.patch.object(lifecycle, "append", side_effect=audit):
            receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(boundaries, ["cleanup", "pin", "audit"])
        self.assertEqual({row["name"] for row in self.issue["labels"]},
                         {config.LABEL_AGENT, "host:tank"})
        self.assertEqual(self.issue["assignees"], [])
        ultron.process_ticket(stale, 1, False)
        self.assertEqual(self.launched, [])
        self.assertEqual(self.apply_mcp(preview), receipt)

    def test_cleanup_failure_keeps_readiness_revoked(self):
        self.assert_failure_nonready("cleanup")

    def test_label_creation_failure_keeps_readiness_revoked(self):
        self.assert_failure_nonready("label")

    def test_pin_failure_keeps_readiness_revoked(self):
        self.assert_failure_nonready("pin")

    def test_audit_failure_keeps_readiness_revoked(self):
        self.assert_failure_nonready("audit")

    def assert_failure_nonready(self, failure):
        preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
        transport, append = self.transport, lifecycle.append

        def fail(argv, **kwargs):
            cleanup = argv[:3] == ["gh", "issue", "edit"] and "--remove-assignee" in argv
            label = argv[:3] == ["gh", "label", "create"]
            pin = argv[:3] == ["gh", "issue", "edit"] and "--add-label" in argv and "host:tank" in argv
            if {"cleanup": cleanup, "label": label, "pin": pin}.get(failure, False):
                return subprocess.CompletedProcess(argv, 1, "", "permission denied")
            return transport(argv, **kwargs)

        def audit(path, row):
            if failure == "audit" and row.get("event") == "requeue-host-pinned":
                raise OSError("disk full")
            return append(path, row)

        with mock.patch.object(decisions, "_run", side_effect=fail), \
                mock.patch.object(lifecycle, "append", side_effect=audit):
            receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["status"], "uncertain")
        self.assertNotIn({"name": config.LABEL_AGENT}, self.issue["labels"])
        self.assertFalse(any(row.get("event") == "requeue-host-pinned"
                             for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")))
        self.assertEqual(self.apply_mcp(preview), receipt)

    def test_exact_requeue_agent_action_and_request_zero_host_binding(self):
        for name in ("requeue_agent", "requeue_agent_extra", "prefix_requeue_agent", "requeue_agen"):
            with self.subTest(name=name):
                preview = decisions.prepare(
                    self.cfg, [{"op": "issue", "number": 7, "add": [config.LABEL_AGENT],
                                "remove": [config.LABEL_HUMAN], "unassign": True}],
                    caller="mcp", action={"name": name, "args": {"number": 7, "host": "tank"}},
                )
                document = self.proposal(preview)
                pins = [step for step in document["steps"] if step["action"] == "requeue_host_pin"]
                if name == "requeue_agent":
                    [pin] = pins
                    self.assertEqual((pin["request"], pin["number"], pin["host"]), (0, 7, "tank"))
                    self.assertEqual(pin["argv"], ["gh", "issue", "edit", "7", "--repo", self.cfg.repo,
                                                   "--add-label", "host:tank"])
                    self.assertTrue(all(step["request"] == 0 for step in document["steps"]))
                else:
                    self.assertEqual(pins, [])
                    self.assertNotIn("host:tank", str(preview["effects"]))

    def test_exact_requeue_host_pin_action_controls_audit(self):
        for name in ("requeue_host_pin", "requeue_host_pin_extra", "prefix_requeue_host_pin", "requeue_host_pi"):
            with self.subTest(name=name):
                step = {
                    "kind": "command", "action": name, "number": 7, "host": "tank",
                    "argv": ["gh", "issue", "edit", "7", "--repo", self.cfg.repo, "--add-label", "host:tank"],
                    "cwd": None,
                }
                with mock.patch.object(decisions, "_plan_request", return_value=[step]):
                    preview = decisions.prepare(
                        self.cfg, [{"op": "issue", "number": 7, "comment": "pin"}], caller="mcp",
                    )
                before = len([row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                              if row.get("event") == "requeue-host-pinned"])
                receipt = self.apply_mcp(preview)
                self.assertEqual(receipt["status"], "success")
                pins = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                        if row.get("event") == "requeue-host-pinned"]
                self.assertEqual(len(pins) - before, int(name == "requeue_host_pin"))
                if name == "requeue_host_pin":
                    self.assertEqual((pins[-1]["ticket"], pins[-1]["host"], pins[-1]["caller"]), (7, "tank", "mcp"))

    def test_hostless_requeue_retains_existing_host_without_pin_audit(self):
        self.issue["labels"] = [{"name": config.LABEL_HUMAN}, {"name": "host:tank"}]
        preview = self.via_mcp("requeue_agent", {"number": 7})
        receipt = self.apply_mcp(preview)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual({row["name"] for row in self.issue["labels"]}, {config.LABEL_AGENT, "host:tank"})
        self.assertFalse(any(row.get("event") == "requeue-host-pinned"
                             for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")))


del ActionHarness, ClaimHarness
