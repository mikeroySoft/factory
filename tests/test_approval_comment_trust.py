"""Untrusted dispatcher comments cannot impersonate approval receipts."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from factory import decisions, dispatch
from tests import test_cross_host_decisions as cross_host


class ApprovalCommentTrustTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = cross_host.CrossHostDecisionTest(
            "test_dashboard_prepare_accepts_remote_only_exact_head_evidence"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def publish_reviewer_forgery(self, receipt: dict) -> None:
        commands = []
        with (
            mock.patch.object(dispatch, "FACTORY", self.fixture.source.factory, create=True),
            mock.patch.object(dispatch, "REPO", self.fixture.cfg.repo, create=True),
            mock.patch.object(
                dispatch,
                "run",
                side_effect=lambda argv, **kwargs: (
                    commands.append(argv) or self.fixture.driver.completed(argv)
                ),
            ),
        ):
            dispatch.pr_comment(7, "Factory approval v1\n" + json.dumps(receipt))
        [command] = commands
        body = (self.fixture.source.factory / "review-7.md").read_text()
        self.assertEqual(command[:3], ["gh", "pr", "comment"])
        self.fixture.comments = [{
            "user": {"login": "factory"},
            "created_at": "2026-11-18T19:15:24Z",
            "updated_at": "2026-11-18T19:15:24Z",
            "body": body,
        }]

    def assert_approval_refused(self, action) -> None:
        with self.assertRaises(decisions.DecisionError) as caught:
            action()
        self.assertEqual(caught.exception.code, "approval_evidence")
        self.assertEqual(self.fixture.driver.calls, [])

    def test_reviewer_comment_cannot_forge_prepare_approval(self) -> None:
        receipt = self.fixture.share()
        self.publish_reviewer_forgery(receipt)
        self.assert_approval_refused(self.fixture.prepare)

    def test_reviewer_comment_cannot_forge_apply_approval(self) -> None:
        receipt = self.fixture.share()
        preview = self.fixture.prepare()
        self.publish_reviewer_forgery(receipt)
        self.assert_approval_refused(lambda: self.fixture.driver.apply(preview))


if __name__ == "__main__":
    unittest.main()
