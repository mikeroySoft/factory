from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch
from factory.config import Config


class HoldMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for name, value in (("cfg", Config(root=Path(temporary.name), repo="acme/widgets")),
                            ("REPO", "acme/widgets")):
            patcher = mock.patch.object(dispatch, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def pr(*, approved: bool) -> dict:
        return {
            "number": 42,
            "headRefName": "agent/7",
            "headRefOid": "head",
            "baseRefName": "main",
            "isDraft": False,
            "labels": ([{"name": dispatch.FACTORY_APPROVED}] if approved else []),
            "reviewDecision": "APPROVED",
            "state": "OPEN",
        }

    def test_approved_pr_enters_candidate_handling(self) -> None:
        pr = self.pr(approved=True)
        with mock.patch.object(dispatch, "gh_json", return_value=[pr]), \
                mock.patch("factory.learn.promote_pass"), \
                mock.patch.object(dispatch, "initiative_kind", return_value="initiative") as candidate:
            dispatch.merge_pass_locked(dry_run=True)

        candidate.assert_called_once_with(7)

    def test_unheld_unapproved_pr_logs_why_it_was_skipped(self) -> None:
        pr = self.pr(approved=False)
        with mock.patch.object(dispatch, "gh_json", return_value=[pr]), \
                mock.patch("factory.learn.promote_pass"), \
                mock.patch.object(dispatch, "initiative_kind") as candidate, \
                mock.patch.object(dispatch, "log") as log:
            dispatch.merge_pass_locked(dry_run=True)

        candidate.assert_not_called()
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any(
            "#42" in message
            and dispatch.FACTORY_APPROVED in message
            and "missing" in message.lower()
            for message in messages
        ))


if __name__ == "__main__":
    unittest.main()
