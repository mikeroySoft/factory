"""#836: older approved PRs must not disappear behind gh's default list limit."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config


class MergeListingTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.events = Path(temp.name) / ".factory" / "events.jsonl"
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.multiple(
            dispatch, create=True,
            cfg=Config(root=Path(temp.name), repo="acme/widgets"),
            REPO="acme/widgets", FACTORY=self.events.parent, EVENTS=self.events,
        ))
        self.enterContext(mock.patch("factory.learn.promote_pass"))
        self.enterContext(mock.patch.object(dispatch, "initiative_kind", return_value=None))
        self.log = self.enterContext(mock.patch.object(dispatch, "log"))
        self.checks = self.enterContext(mock.patch.object(
            dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pending"}],
        ))
        self.prs = []
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.gh_json))

    def gh_json(self, args):
        self.assertEqual(args[:2], ["pr", "list"])
        limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 30
        return self.prs[:limit]

    def open_prs(self, count):
        return [{"number": n, "headRefName": f"agent/{n}", "headRefOid": f"head-{n}",
                 "baseRefName": "main", "isDraft": False, "labels": [], "reviewDecision": ""}
                for n in range(count, 0, -1)]

    def test_considers_approved_pr_older_than_newest_thirty(self):
        self.prs = self.open_prs(64)
        self.prs[-1]["labels"] = [{"name": dispatch.FACTORY_APPROVED}]

        dispatch.merge_pass_locked(False)

        self.checks.assert_called_once_with(1, "head-1")
        self.assertFalse(any(row.get("event") == "merge-list-limit"
                             for row in lifecycle.read_events(self.events)))

    def test_listing_at_limit_journals_and_logs_warning(self):
        self.prs = self.open_prs(1001)

        dispatch.merge_pass_locked(False)

        warnings = [row for row in lifecycle.read_events(self.events)
                    if row.get("event") == "merge-list-limit"]
        self.assertEqual([(row["limit"], row["count"]) for row in warnings], [(1000, 1001)])
        self.assertTrue(any("warning" in call.args[0].lower()
                            and "1000" in call.args[0]
                            and "truncat" in call.args[0].lower()
                            for call in self.log.call_args_list))

    def test_exactly_list_limit_does_not_warn(self):
        self.prs = self.open_prs(1000)

        dispatch.merge_pass_locked(False)

        self.assertFalse(any(row.get("event") == "merge-list-limit"
                             for row in lifecycle.read_events(self.events)))
        self.assertFalse(any("warning" in call.args[0].lower()
                             for call in self.log.call_args_list))

    def test_listing_above_limit_dry_run_warns_without_journaling(self):
        self.prs = self.open_prs(1001)
        self.prs[-1]["labels"] = [{"name": dispatch.FACTORY_APPROVED}]

        dispatch.merge_pass_locked(True)

        self.checks.assert_called_once_with(1, "head-1")
        self.assertFalse(self.events.exists())
        self.assertTrue(any("warning" in call.args[0].lower()
                            and "1000" in call.args[0]
                            and "truncat" in call.args[0].lower()
                            for call in self.log.call_args_list))
