"""#553: classified read failures stop observation-driven actions and read-only writes."""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, feedback, lifecycle, manage, reads, triage
from tests.test_feedback import H, Provider


class ManagerObservationBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / ".factory").mkdir()
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        self.cfg = config.Config(
            self.root,
            "example/project",
            manager=["manager-stub"],
            manager_stale_days=7,
        )
        self.cfg.manager_review = "all"
        dispatch.configure(self.cfg)
        self.pr = {
            "id": "PR_80",
            "number": 80,
            "title": "feature",
            "url": "https://github.com/example/project/pull/80",
            "state": "OPEN",
            "headRefName": "agent/79",
            "headRefOid": H,
            "baseRefName": "main",
            "isCrossRepository": False,
            "isDraft": False,
            "labels": [],
            "reviewDecision": "",
            "updatedAt": (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.provider = Provider()
        self.provider.reviews = []
        self.provider.checks = []
        self.provider.thread["isResolved"] = True
        self.manager_calls = 0
        self.mutations: list[list[str]] = []
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.fake_run))
        from factory import dashboard
        self.enterContext(mock.patch.object(dashboard, "github", side_effect=self.read))

    def gh_json(self, args):
        if args[:2] == ["pr", "list"]:
            return [self.pr]
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[:2] == ["api", "repos/example/project"]:
            return {"id": "R_1"}
        if args[:2] == ["issue", "view"]:
            return {
                "id": "I_79",
                "number": 79,
                "url": "https://github.com/example/project/issues/79",
                "title": "Ticket",
                "body": "Scope",
                "state": "OPEN",
                "labels": [{"name": "ready-for-agent"}],
            }
        raise AssertionError(args)

    def read(self, **kwargs):
        if kwargs.get("query") == feedback.REVIEW_QUERY:
            command = ["gh", "api", "graphql"]
            raise reads.ReadFailure(
                "gh_read_failed",
                subprocess.CompletedProcess(command, 1, "", "HTTP 500: provider unavailable"),
            )
        return self.provider(**kwargs)

    def fake_run(self, command, *args, **kwargs):
        if command[0] == "manager-stub":
            self.manager_calls += 1
            return subprocess.CompletedProcess(command, 0, "DECISION: APPROVE\nLooks right", "")
        if command[0] == "gh":
            self.mutations.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    def test_classified_feedback_failure_skips_manager_item_without_action(self) -> None:
        dispatch.record("claimed", ticket=79)
        dispatch.record("attempt", ticket=79, gate="PASS", head=H, actual_head=H)
        dispatch.record("review", ticket=79, verdict="APPROVE", accepted=True, head=H, actual_head=H)

        with reads.pass_scope(dispatch.EVENTS, "dispatch"):
            manage.frontier_pass()

        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual(self.manager_calls, 0)
        self.assertEqual(self.mutations, [])
        self.assertEqual(
            [row for row in rows if row.get("event") in {
                "approved", "escalate", "feedback-delivered", "manage",
            }],
            [],
        )
        [skip] = [row for row in rows if row.get("event") == "read-skip"]
        self.assertEqual((skip["ticket"], skip["pr"], skip["code"]), (79, 80, "gh_read_failed"))


class TriageReadOnlyPolicyBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "example/project")
        self.cfg.factory.mkdir()
        triage.configure(self.cfg)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.object(triage, "fetch_issue", side_effect=self.failed_read))
        self.enterContext(mock.patch.object(reads, "_quota", return_value={"available": False}))

    @staticmethod
    def failed_read(*args, **kwargs):
        command = ["gh", "issue", "view", "79"]
        result = reads.run(
            command,
            lambda: subprocess.CompletedProcess(command, 1, "", "HTTP 429: rate_limited"),
        )
        result.check_returncode()

    def args(self, *, dry_run=False, replay=None):
        return argparse.Namespace(issue=None if replay else 79, replay=replay, dry_run=dry_run)

    def test_dry_run_read_failure_writes_no_policy_or_lifecycle_events(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            triage.execute(self.args(dry_run=True), None)
        self.assertFalse(self.events.exists())

    def test_replay_read_failure_writes_no_policy_or_lifecycle_events(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            triage.execute(self.args(replay="79"), None)
        self.assertFalse(self.events.exists())

    def test_real_run_records_classified_failure_and_backoff(self) -> None:
        with lifecycle.scope(self.events, "triage", lazy=True) as execution:
            result = triage.execute(self.args(), execution)

        self.assertEqual(result, 1)
        rows = lifecycle.read_events(self.events)
        self.assertEqual(
            [row["event"] for row in rows if row.get("event", "").startswith("read-")],
            ["read-backoff", "read-skip", "read-summary"],
        )
        self.assertEqual(
            [(row.get("ticket"), row.get("code")) for row in rows if row.get("event") == "read-skip"],
            [(79, "rate_limited")],
        )


if __name__ == "__main__":
    unittest.main()
