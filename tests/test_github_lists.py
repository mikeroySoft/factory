"""Complete GitHub listings are required before pipeline decisions."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch


class GithubLists(unittest.TestCase):
    def test_approved_pr_after_default_window_is_selected_for_refresh(self):
        approved = {
            "number": 652, "headRefName": "agent/626", "headRefOid": "head",
            "baseRefName": "main", "isDraft": False,
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
            "state": "OPEN",
        }
        prs = [
            {**approved, "number": 1000 + n, "headRefName": f"feature/{n}", "labels": []}
            for n in range(41)
        ] + [approved]

        def transport(command, **kwargs):
            if command[:3] == ["gh", "pr", "list"]:
                limit = int(command[command.index("--limit") + 1]) if "--limit" in command else 30
                result = prs[:limit]
            elif command[:3] == ["gh", "pr", "view"]:
                result = approved
            elif command[:2] == ["gh", "api"]:
                result = {"behind_by": 1}
            else:
                raise AssertionError(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with tempfile.TemporaryDirectory() as directory:
            dispatch.configure(config.Config(root=Path(directory), repo="acme/widgets"))
            with mock.patch.object(dispatch, "run", side_effect=transport), \
                    mock.patch.object(dispatch, "initiative_kind", return_value=False), \
                    mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                    mock.patch("factory.learn.promote_pass"), \
                    mock.patch.object(dispatch, "log") as log:
                dispatch.merge_pass_locked(True)

        log.assert_any_call("PR #652: would refresh (behind main)")

    def test_saturated_dispatch_and_stats_lists_fail_instead_of_returning_partial_data(self):
        from factory import stats

        for kind in ("pr", "issue"):
            for count in (1, 2, 3):
                with self.subTest(kind=kind, count=count):
                    args = [kind, "list", "--limit", "2", "--json", "number"]
                    rows = [{"number": n} for n in range(count)]
                    result = subprocess.CompletedProcess([], 0, json.dumps(rows), "")
                    with mock.patch.object(dispatch, "run", return_value=result), \
                            mock.patch.object(stats.subprocess, "run", return_value=result):
                        for read in (lambda: dispatch.gh_json(args), lambda: stats.gh(*args)):
                            if count < 2:
                                self.assertEqual(read(), rows)
                            else:
                                with self.assertRaisesRegex(RuntimeError, "possible truncation"):
                                    read()

    def test_triage_reaches_older_tickets_and_rejects_saturation(self):
        from factory import triage

        for count in (42, 1000):
            with self.subTest(count=count):
                def transport(command, **kwargs):
                    limit = int(command[command.index("--limit") + 1]) if "--limit" in command else 30
                    rows = [{"number": n} for n in range(count)][:limit]
                    return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")

                with tempfile.TemporaryDirectory() as directory:
                    triage.configure(config.Config(root=Path(directory), repo="acme/widgets"))
                    with mock.patch.object(triage.subprocess, "run", side_effect=transport):
                        if count == 42:
                            self.assertEqual(triage.list_needs_triage(), list(range(42)))
                        else:
                            with self.assertRaisesRegex(RuntimeError, "possible truncation"):
                                triage.list_needs_triage()

    def test_weekly_stale_metric_rejects_partial_issue_list(self):
        from datetime import UTC, datetime

        from factory import stats

        rows = [{"number": n, "updatedAt": "2026-01-01T00:00:00Z"} for n in range(1000)]
        result = subprocess.CompletedProcess([], 0, json.dumps(rows), "")
        with mock.patch.object(stats.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "possible truncation"):
                stats.stuck_over_days(datetime(2026, 11, 10, tzinfo=UTC))

    def test_labels_and_searches_reject_saturation(self):
        from factory import github

        for command in (["label", "list"], ["search", "prs"], ["search", "issues"]):
            with self.subTest(command=command):
                with self.assertRaisesRegex(RuntimeError, "possible truncation"):
                    github.json_output([*command, "--limit", "2"], '[{"number": 1}, {"number": 2}]')

    def test_unbounded_or_projected_lists_cannot_claim_completeness(self):
        from factory import github

        with self.assertRaisesRegex(RuntimeError, "explicit --limit"):
            github.json_output(["pr", "list"], "[]")
        with self.assertRaisesRegex(RuntimeError, "JSON array"):
            github.json_output(["issue", "list", "--limit", "1000"], '{"count": 1000}')
