"""Consumer boundaries for host-aware dispatch, triage, and management."""
import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import lifecycle, manage, triage
from factory.config import Config
from tests import test_host_claim_transitions as transitions_test
from tests import test_host_claims as claims_test


class HostClaimFrontierBoundaryTest(unittest.TestCase):
    setUp = claims_test.HostClaimRaceTest.setUp
    dispatcher = claims_test.HostClaimRaceTest.dispatcher
    launch = claims_test.HostClaimRaceTest.launch

    def test_assigned_ticket_without_claim_is_held_and_never_launched(self):
        tank = self.dispatcher("tank")
        self.github.issue["assignees"] = [{"login": "@me"}]
        before = copy.deepcopy((self.github.issue, self.github.comments))

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "assigned")
        tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, [])
        self.assertEqual((self.github.issue, self.github.comments), before)


class TriageHostBoundaryTest(unittest.TestCase):
    def test_equal_dynamic_host_names_run_triage_while_unequal_names_skip(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host_name = bytearray(b"tank-dynamic-host").decode()
        triage_host = bytearray(b"tank-dynamic-host").decode()
        cfg = Config(root=root, repo="local/claims")
        cfg.host_name = host_name
        cfg.triage_host = triage_host

        with mock.patch.object(triage.config, "load", return_value=cfg), \
                mock.patch.object(triage, "list_needs_triage", return_value=[]) as issue_list, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(triage.main(["--dry-run"]), 0)
        issue_list.assert_called_once()

        cfg.triage_host = bytearray(b"ultron-dynamic-host").decode()
        output = io.StringIO()
        with mock.patch.object(triage.config, "load", return_value=cfg), \
                mock.patch.object(triage, "list_needs_triage") as issue_list, \
                redirect_stdout(output):
            self.assertEqual(triage.main(["--dry-run"]), 0)
        issue_list.assert_not_called()
        self.assertEqual(output.getvalue(), "triage: skipped, triage host is ultron-dynamic-host\n")


class ManagerHostRemovalBoundaryTest(unittest.TestCase):
    setUp = claims_test.HostClaimRaceTest.setUp
    dispatcher = claims_test.HostClaimRaceTest.dispatcher
    launch = claims_test.HostClaimRaceTest.launch

    def test_only_host_removal_receipts_after_current_escalation_are_ignored(self):
        self.github = transitions_test.TimelineGitHub()
        tank = self.dispatcher("tank")
        tank.record("claim-label-removed", ticket=7, label="host:retired")
        self.github.add_claim("tank", datetime.now(UTC))
        tank.escalate(7, "gate failed", None, code="gate_failed")
        events = lifecycle.read_events(tank.EVENTS)
        escalation = next(row for row in events if row.get("event") == "escalate")

        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(manage.handoff, "dispatch", tank):
            self.assertFalse(manage.human_activity(7, escalation, events))
            self.github.timeline.insert(-1, {
                "event": "unlabeled",
                "label": {"name": "host:retired"},
                "created_at": escalation["at"],
                "actor": {"login": "@me"},
            })
            self.assertTrue(manage.human_activity(7, escalation, events))
