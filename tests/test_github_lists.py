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
