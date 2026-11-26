"""Requeue provider input and exact named-action scope."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import actions, config, decisions

REPO = "acme/widgets"
ACTOR = ["gh", "api", "--hostname", "github.com", "--method", "GET", "user", "--jq", ".login"]
ISSUE = [
    "gh", "issue", "view", "7", "--repo", REPO, "--json",
    "number,title,body,state,labels,assignees,updatedAt,url",
]
PR_LIST = [
    "gh", "pr", "list", "--repo", REPO, "--state", "open", "--head", "agent/7", "--json", "number",
]


class RequeueInputFollowupTest(unittest.TestCase):
    @staticmethod
    def issue(labels: list[str]) -> dict:
        return {
            "number": 7,
            "title": "Fix parser",
            "body": "Body",
            "state": "OPEN",
            "labels": [{"name": label} for label in labels],
            "assignees": [{"login": "alice"}],
            "updatedAt": "2026-11-25T00:00:01Z",
            "url": f"https://github.com/{REPO}/issues/7",
        }

    def test_malformed_requeue_pr_lists_are_provider_failures(self) -> None:
        malformed = (
            {},
            [None],
            [{"number": None}],
            [{"number": True}],
            [{"number": "70"}],
            [{"number": 0}],
            [{"number": -1}],
        )
        for value in malformed:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                cfg = config.Config(root=Path(directory), repo=REPO)
                issue = self.issue([config.LABEL_HUMAN])
                expected = [
                    (ACTOR, "alice\n"),
                    (ISSUE, json.dumps(issue)),
                    (PR_LIST, json.dumps(value)),
                ]

                def transport(argv: list[str], expected=expected, **kwargs) -> subprocess.CompletedProcess[str]:
                    wanted, output = expected.pop(0)
                    self.assertEqual(argv, wanted)
                    return subprocess.CompletedProcess(argv, 0, output, "")

                with mock.patch.object(decisions, "_run", side_effect=transport), \
                        self.assertRaises(decisions.DecisionError) as caught:
                    actions.prepare(
                        cfg, json.loads('"requeue_agent"'), {"number": 7}, caller="dashboard",
                    )
                self.assertEqual(caught.exception.code, "provider_unavailable")
                self.assertEqual(expected, [])

    def test_requeue_accepts_the_first_valid_pr_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo=REPO)
            issue = self.issue([config.LABEL_HUMAN])
            expected = [
                (ACTOR, "alice\n"),
                (ISSUE, json.dumps(issue)),
                (PR_LIST, '[{"number": 1}]'),
                (ACTOR, "alice\n"),
                (ISSUE, json.dumps(issue)),
                (["gh", "pr", "close", "1", "--repo", REPO], "ok"),
                ([
                    "gh", "issue", "edit", "7", "--repo", REPO,
                    "--add-label", config.LABEL_AGENT,
                    "--remove-label", config.LABEL_HUMAN,
                    "--remove-assignee", "alice",
                ], "ok"),
            ]

            def transport(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
                wanted, output = expected.pop(0)
                self.assertEqual(argv, wanted)
                return subprocess.CompletedProcess(argv, 0, output, "")

            with mock.patch.object(decisions, "_run", side_effect=transport):
                preview = actions.prepare(
                    cfg, json.loads('"requeue_agent"'), {"number": 7}, caller="dashboard",
                )
                receipt = decisions.apply(cfg, preview["proposal_id"], preview["confirmation"])
            self.assertEqual(receipt["status"], "success")
            self.assertEqual(expected, [])

    def test_other_named_issue_actions_never_close_agent_prs(self) -> None:
        cases = (
            (
                "add_needs_triage",
                {"number": 7},
                [],
                ["gh", "issue", "edit", "7", "--repo", REPO, "--add-label", config.LABEL_TRIAGE],
            ),
            (
                "resolve_and_close",
                {"number": 7, "reason": "not planned"},
                [config.LABEL_HUMAN],
                ["gh", "issue", "close", "7", "--repo", REPO, "--reason", "not planned"],
            ),
        )
        for name, args, labels, mutation in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                cfg = config.Config(root=Path(directory), repo=REPO)
                issue = self.issue(labels)
                expected = [
                    (ACTOR, "alice\n"),
                    (ISSUE, json.dumps(issue)),
                    (ACTOR, "alice\n"),
                    (ISSUE, json.dumps(issue)),
                    (mutation, "ok"),
                ]

                def transport(argv: list[str], expected=expected, **kwargs) -> subprocess.CompletedProcess[str]:
                    wanted, output = expected.pop(0)
                    self.assertEqual(argv, wanted)
                    return subprocess.CompletedProcess(argv, 0, output, "")

                with mock.patch.object(decisions, "_run", side_effect=transport):
                    preview = actions.prepare(cfg, name, args, caller="dashboard")
                    self.assertFalse(any(
                        "open agent PR" in change
                        for effect in preview["effects"]
                        for change in effect["changes"]
                    ))
                    receipt = decisions.apply(cfg, preview["proposal_id"], preview["confirmation"])
                self.assertEqual(receipt["status"], "success")
                self.assertEqual(expected, [])


if __name__ == "__main__":
    unittest.main()
