"""Human add-only authorization releases queued work without replaying a manager."""
from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_authorization_request as request_tests
from tests import test_unchanged_rounds as round_tests


class HumanAuthorizationTest(unittest.TestCase):
    setUp = request_tests.ManagerAuthorizationRequestTest.setUp
    command_result = request_tests.ManagerAuthorizationRequestTest.command_result
    events = request_tests.ManagerAuthorizationRequestTest.events
    request = request_tests.ManagerAuthorizationRequestTest.request

    def queue(self):
        self.cfg.manager = None
        self.request()
        return manage._pending_add_only_row(self.events(), 7)

    def test_manager_pass_accepts_human_override_without_replaying_a_failed_decision(self):
        request = self.queue()
        dispatch.record("manager-request-failed", ticket=7, packet=request["packet"],
                        round=request["round"], reason="partial authorization application")
        self.cfg.manager = ["manager", "{prompt}"]
        issue = {**self.issue, "state": "OPEN", "labels": [
            {"name": config.LABEL_AGENT}, {"name": config.LABEL_ADD_ONLY_OVERRIDE},
        ]}

        def github(args):
            return [] if args[:2] == ["issue", "list"] else issue

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(manage, "human_activity", return_value=True), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result):
            manage.escalation_pass()
            manage.escalation_pass()

        self.assertFalse(manage.pending_add_only_request(7))
        self.assertEqual(self.manager_calls, [])
        self.assertFalse(any(row.get("event") in {"manage", "escalate"} for row in self.events()))
        completed = [row for row in self.events() if row.get("event") == "manager-request-complete"]
        self.assertEqual([(row["packet"], row["round"]) for row in completed],
                         [(request["packet"], request["round"])])

    def test_unrelated_human_activity_does_not_grant_authorization(self):
        request = self.queue()
        self.cfg.manager = ["manager", "{prompt}"]
        with mock.patch.object(manage, "human_activity", return_value=True), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result):
            manage._handle_add_only_request(7, self.issue, request)
        self.assertTrue(manage.pending_add_only_request(7))
        self.assertEqual(self.manager_calls, [])
        self.assertFalse(any(row.get("event") == "escalate" for row in self.events()))


class HumanAuthorizationCLITest(unittest.TestCase):
    setUp = round_tests.UnchangedRoundsTest.setUp
    git = round_tests.UnchangedRoundsTest.git
    write_config = round_tests.UnchangedRoundsTest.write_config
    commit_config = round_tests.UnchangedRoundsTest.commit_config
    cli = round_tests.UnchangedRoundsTest.cli
    failed_gate = round_tests.UnchangedRoundsTest.failed_gate

    def test_direct_human_override_resumes_worker_without_a_configured_manager(self):
        self.failed_gate(needs=True)
        allowed = self.base / "allowed"
        self.write_config(check=[sys.executable, "-c", (
            "from pathlib import Path; "
            "print('FAIL: test_contract (test_contract.ContractTest.test_contract)'); "
            f"raise SystemExit(0 if Path({str(allowed)!r}).exists() else 1)"
        )])
        self.commit_config()
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run"])
        state = json.loads(self.state.read_text())
        state["labels"].append(config.LABEL_ADD_ONLY_OVERRIDE)
        self.state.write_text(json.dumps(state))
        allowed.touch()

        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")

        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run", "run"])
        rows = lifecycle.read_events(self.events)
        self.assertEqual(sum(row["event"] == "manager-request" for row in rows), 1)
        self.assertEqual(sum(row["event"] == "manager-request-complete" for row in rows), 1)
        self.assertFalse(any(row["event"] == "manage" for row in rows))
        attempts = [row for row in rows if row["event"] == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["FAIL", "PASS"])


if __name__ == "__main__":
    unittest.main()
