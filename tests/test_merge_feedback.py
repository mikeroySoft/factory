"""Frontier feedback follows the same required/advisory CI policy as merge admission."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests.test_feedback import H, Provider
from tests.test_frontier import pr_row


class MergeFeedbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".factory").mkdir()
        self.cfg = config.Config(
            self.root, "example/project", manager=["manager-stub"],
            manager_review="all", manager_stale_days=7,
        )
        dispatch.configure(self.cfg)
        self.provider = Provider()
        self.pr = pr_row()
        self.mutations: list[list[str]] = []
        self.manager_output = "DECISION: APPROVE\nLooks right"
        self.checks = self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[]))
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.fake_run))
        from factory import dashboard
        self.enterContext(mock.patch.object(dashboard, "github", side_effect=self.read))

    def read(self, **kwargs):
        if not self.provider.heads:
            self.provider.heads = [self.pr["headRefOid"]] * 2
        return self.provider(**kwargs)

    def gh_json(self, args):
        if args[:2] == ["pr", "list"]:
            return [self.pr]
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[:2] == ["api", "repos/example/project"]:
            return {"id": "R_1"}
        if args[:2] == ["issue", "view"]:
            return {
                "id": "I_79", "number": 79,
                "url": "https://github.com/example/project/issues/79",
                "title": "Ticket", "body": "Scope", "state": "OPEN",
                "labels": [{"name": "ready-for-agent"}],
            }
        if args[:2] == ["api", "repos/example/project/issues/79/timeline"]:
            return [[]]
        if args[:2] == ["api", "user"]:
            return {"login": "factory-bot"}
        raise AssertionError(args)

    def fake_run(self, cmd, *args, **kwargs):
        if cmd[0] == "manager-stub":
            return subprocess.CompletedProcess(cmd, 0, self.manager_output, "")
        if cmd[0] == "gh":
            self.mutations.append(cmd)
            stdout = (
                "https://github.com/example/project/pull/80#issuecomment-8001"
                if cmd[1:3] == ["pr", "comment"] else
                "https://github.com/example/project/issues/79#issuecomment-7901"
                if cmd[1:3] == ["issue", "comment"] else ""
            )
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
        raise AssertionError(cmd)

    def events(self, kind: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == kind]

    def approved_head(self) -> None:
        dispatch.record("claimed", ticket=79)
        dispatch.record("attempt", ticket=79, gate="PASS", head=H, actual_head=H)
        dispatch.record("review", ticket=79, verdict="APPROVE", accepted=True, head=H, actual_head=H)

    def quiet_humans(self) -> None:
        self.provider.reviews = []
        self.provider.thread["isResolved"] = True

    def selected_checks(self, advisory: str) -> list[dict]:
        return [
            {"name": "unit", "bucket": "pass", "required": False},
            {"name": advisory, "bucket": "skipping", "required": False,
             "disposition": "advisory", "original_bucket": "fail"},
        ]

    def assert_manager_approved(self) -> None:
        self.assertEqual(
            [(row["pr"], row["head"], row["decision"]) for row in self.events("manage")],
            [(80, H, "APPROVE")],
        )
        self.assertEqual(self.events("escalate"), [])
        self.assertTrue(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))

    def test_advisory_check_run_does_not_preempt_manager_approval(self) -> None:
        self.quiet_humans()
        self.provider.checks = [
            {**self.provider.checks[0], "node_id": "CR_unit", "id": 2, "name": "unit", "conclusion": "success"},
            {**self.provider.checks[0], "name": "GPU advisory"},
        ]
        self.checks.return_value = self.selected_checks("GPU advisory")
        self.approved_head()

        manage.frontier_pass()

        self.assert_manager_approved()

    def test_advisory_commit_status_does_not_preempt_manager_approval(self) -> None:
        self.quiet_humans()
        self.provider.checks = []
        self.provider.statuses = [
            {"node_id": "CS_unit", "context": "unit", "state": "success"},
            {"node_id": "CS_gpu", "context": "GPU advisory", "state": "failure"},
        ]
        self.checks.return_value = self.selected_checks("GPU advisory")
        self.approved_head()

        manage.frontier_pass()

        self.assert_manager_approved()

    def test_required_ci_failure_blocks_manager_approval(self) -> None:
        self.quiet_humans()
        self.provider.checks[0]["name"] = "required gate"
        self.checks.return_value = [
            {"name": "unit", "bucket": "pass", "required": True},
            {"name": "required gate", "bucket": "fail", "required": True},
        ]
        self.approved_head()

        manage.frontier_pass()

        self.assertEqual([row["code"] for row in self.events("escalate")], ["ci_failed"])
        self.assertEqual(self.events("manage"), [])

    def test_human_review_and_thread_feedback_remain_blocking(self) -> None:
        self.provider.checks[0]["name"] = "GPU advisory"
        self.checks.return_value = self.selected_checks("GPU advisory")
        self.approved_head()

        manage.frontier_pass()

        self.assertEqual([row["code"] for row in self.events("escalate")], ["pr_feedback"])
        delivered = self.events("feedback-delivered")
        self.assertEqual(
            {pair[0].split(":")[4] for pair in delivered[0]["pairs"]},
            {"review", "review_comment"},
        )
        self.assertEqual(self.events("manage"), [])

    def test_unknown_check_policy_keeps_ci_feedback_blocking(self) -> None:
        self.quiet_humans()
        self.provider.checks = []
        self.provider.statuses = [
            {"node_id": "CS_unknown", "context": "unknown gate", "state": "failure"},
        ]
        self.checks.return_value = []
        self.approved_head()

        manage.frontier_pass()

        self.assertEqual([row["code"] for row in self.events("escalate")], ["pr_feedback"])
        self.assertEqual(self.events("manage"), [])

    def test_non_ci_feedback_with_summary_outside_selected_failures_blocks_approval(self) -> None:
        self.provider.thread["isResolved"] = True
        self.provider.checks[0]["name"] = "GPU advisory"
        self.checks.return_value = self.selected_checks("GPU advisory")
        self.approved_head()
        observe_pr = manage.observe_pr

        def summarized_observation(*args, **kwargs):
            observation = observe_pr(*args, **kwargs)
            for item in observation["items"]:
                if item["kind"] == "review":
                    item["summary"] = "Human requested changes"
            return observation

        with mock.patch.object(manage, "observe_pr", side_effect=summarized_observation):
            manage.frontier_pass()

        self.assertEqual([row["code"] for row in self.events("escalate")], ["pr_feedback"])
        self.assertEqual(
            {pair[0].split(":")[4] for pair in self.events("feedback-delivered")[0]["pairs"]},
            {"review"},
        )
        self.assertEqual(self.events("manage"), [])
        self.assertFalse(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))

    def test_ci_feedback_without_summary_blocks_approval_with_selected_passing_checks(self) -> None:
        self.quiet_humans()
        self.provider.checks[0]["name"] = ""
        self.checks.return_value = self.selected_checks("GPU advisory")
        self.approved_head()

        manage.frontier_pass()

        self.assertEqual([row["code"] for row in self.events("escalate")], ["pr_feedback"])
        self.assertEqual(
            {pair[0].split(":")[4] for pair in self.events("feedback-delivered")[0]["pairs"]},
            {"check_run"},
        )
        self.assertEqual(self.events("manage"), [])
        self.assertFalse(any("--add-label" in cmd and "factory-approved" in cmd for cmd in self.mutations))


if __name__ == "__main__":
    unittest.main()
