"""#947: same-pass decisions and partial claim cleanup must release the worker."""
from __future__ import annotations

import subprocess
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import lifecycle, manage
from tests import test_947_manager_liveness as liveness
from tests.test_host_claim_transitions import TimelineGitHub
from tests.test_host_claims import HostClaimRaceTest as HostHarness


class CleanupTimelineGitHub(TimelineGitHub):
    def run(self, host, command, *args, **kwargs):
        before = len(self.timeline)
        result = super().run(host, command, *args, **kwargs)
        for item in self.timeline[before:]:
            item.setdefault("actor", {"login": "@me"})
        return result


class SamePassReleaseTest(unittest.TestCase):
    setUp = HostHarness.setUp
    dispatcher = HostHarness.dispatcher
    launch = HostHarness.launch

    def prepare(self):
        self.github = CleanupTimelineGitHub()
        tank = self.dispatcher("tank")
        tank.cfg.manager = ["fake-manager", "{prompt}"]
        packet = tank.cfg.factory / "request.md"
        packet.write_text("authorization evidence")
        tank.record(
            "manager-request", ticket=7, code="add_only_authorization",
            packet=str(packet), round=1, paths=["tests/policy.py"], exhausted=False,
        )
        self.github.add_claim("tank", datetime.now(UTC))
        return tank, next(row for row in lifecycle.read_events(tank.EVENTS)
                          if row.get("event") == "manager-request")

    def test_same_pass_retry_releases_actual_claim_and_next_worker_is_eligible(self):
        tank, request = self.prepare()
        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(manage.handoff, "dispatch", tank), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}):
            manage._handle_add_only_request(7, self.github.issue, request)
            self.assertFalse(manage.pending_add_only_request(7))
        self.assertEqual(self.github.manager_runs, 1)
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertFalse(any(row["body"].startswith("Factory claim:")
                             for row in self.github.comments))
        [issue] = tank.frontier()
        self.assertNotIn("held", issue)

    def test_partial_host_cleanup_recovers_without_masking_a_human_edit(self):
        tank, request = self.prepare()
        tank.cfg.manager = None
        real_run = tank.run
        failed = False

        def fail_delete_once(command, *args, **kwargs):
            nonlocal failed
            if command[:2] == ["gh", "api"] and "DELETE" in command and not failed:
                failed = True
                return subprocess.CompletedProcess(command, 1, "", "temporary delete failure")
            return real_run(command, *args, **kwargs)

        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(manage.handoff, "dispatch", tank), \
                mock.patch.object(tank, "run", side_effect=fail_delete_once), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}):
            manage._handle_add_only_request(7, self.github.issue, request)
            self.assertEqual(self.github.issue["assignees"], [])
            events = lifecycle.read_events(tank.EVENTS)
            self.assertFalse(manage.human_activity(7, request, events))
            self.github.timeline.append({
                "event": "edited", "created_at": "2099-01-01T00:00:00Z",
                "actor": {"login": "human-owner"},
            })
            self.assertTrue(manage.human_activity(7, request, events))
            self.github.timeline.pop()
            tank.cfg.manager = ["fake-manager", "{prompt}"]
            manage._handle_add_only_request(7, self.github.issue, request)
            self.assertFalse(manage.pending_add_only_request(7))
        self.assertEqual(self.github.manager_runs, 1)
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertFalse(any(row["body"].startswith("Factory claim:")
                             for row in self.github.comments))


class PromptFailureRecoveryTest(unittest.TestCase):
    setUp = liveness.QueuedAuthorizationLivenessTest.setUp
    github = liveness.QueuedAuthorizationLivenessTest.github
    command_result = liveness.QueuedAuthorizationLivenessTest.command_result

    def test_unwritable_prompt_keeps_request_pending_without_application_failure(self):
        from factory import dispatch

        self.cfg.manager = ["manager", "{prompt}"]
        (self.cfg.factory / "manager-prompt-7.md").mkdir()
        request = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                       if row.get("event") == "manager-request")
        with mock.patch.object(dispatch, "gh_json", side_effect=self.github), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}):
            manage.request_add_only_authorization(
                7, self.issue, ["tests/policy.py"], "gate failed", None,
            )
        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual(self.manager_calls, 0)
        self.assertTrue(manage.pending_add_only_request(7))
        self.assertFalse(any(row.get("event") in {"manage", "manager-request-failed", "escalate"}
                             for row in events))
        self.assertTrue(any(row.get("event") == "manager-request-release"
                            and row.get("packet") == request["packet"] for row in events))


del HostHarness


if __name__ == "__main__":
    unittest.main()
