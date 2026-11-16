"""#771: self-review COMMENT fallback and review-post failure after intake."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, learn, lifecycle
from factory.config import Config

APPROVE_OUT = '```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE'


class SelfReviewCommentTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(root=Path(temp.name), repo="acme/widgets", manager=["true"])
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(
            mock.patch.multiple(
                dispatch,
                create=True,
                cfg=self.cfg,
                ROOT=self.cfg.root,
                REPO=self.cfg.repo,
                UPSTREAM=None,
                UPSTREAM_REPO=None,
                FACTORY=self.cfg.factory,
                LOGS=self.cfg.factory / "logs",
                SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
                EVENTS=self.events,
                MAX_ACTIVE=1,
                MAX_ATTEMPTS=self.cfg.max_attempts,
            )
        )
        self.enterContext(mock.patch.object(dispatch, "log"))
        self.author = "mikeroysoft-agent"
        self.open_prs: list[dict] = []
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self._gh_json))
        self.run = self.enterContext(mock.patch.object(dispatch, "run", side_effect=self._run))
        self.post_rc = 0
        self.post_stderr = ""
        self.reviewer_out = APPROVE_OUT
        self.cfg.reviewer = ["reviewer", "{prompt}"]

    def _gh_json(self, args: list[str], env=None):
        if args[:2] == ["api", "user"]:
            return {"login": self.author}
        if args[:2] == ["issue", "view"]:
            return {"labels": []}
        if args[:2] == ["pr", "view"]:
            return self.open_prs[0]
        if args and args[0] == "api" and "compare" in args[1]:
            return {"behind_by": 0}
        return self.open_prs

    def _run(
        self, cmd: list[str], cwd=None, check=True, stdout=None, stderr=None, env=None, timeout=None
    ):
        if cmd[:4] == ["gh", "api", "--method", "POST"] and cmd[4].endswith("/reviews"):
            proc = subprocess.CompletedProcess(cmd, self.post_rc, "{}", self.post_stderr)
            if check:
                proc.check_returncode()
            return proc
        if cmd and cmd[0] == "reviewer":
            return subprocess.CompletedProcess(cmd, 0, self.reviewer_out, "")
        if cmd[:3] == ["gh", "pr", "checks"]:
            return subprocess.CompletedProcess(cmd, 0, "[]", "")
        if cmd[:3] == ["gh", "pr", "view"]:
            head = self.open_prs[0]["headRefOid"] if self.open_prs else "h"
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefOid": head}), "")
        if cmd[:2] == ["gh", "api"] and any("compare" in str(part) for part in cmd):
            return subprocess.CompletedProcess(cmd, 0, "diff --git a/x b/x\n", "")
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    def _pr(
        self,
        *,
        number: int = 9,
        branch: str = "agent/8",
        head: str | None = None,
        labels: list | None = None,
        review_decision: str = "",
    ) -> dict:
        if head is None:
            head = "h" * 40
        return {
            "number": number,
            "state": "OPEN",
            "isDraft": False,
            "isCrossRepository": False,
            "headRefName": branch,
            "headRefOid": head,
            "baseRefOid": "b" * 40,
            "baseRefName": "main",
            "labels": labels if labels is not None else [{"name": "needs-review"}],
            "reviewRequests": [],
            "author": {"login": self.author},
            "reviewDecision": review_decision,
            "title": "agent/8: fixture",
            "files": [],
        }

    def rows(self, *events: str) -> list[dict]:
        return [e for e in lifecycle.read_events(self.events) if e.get("event") in events]

    def test_review_post_failure_after_intake_does_not_crash_dispatch(self) -> None:
        self.open_prs = [self._pr()]
        self.post_rc = 1
        self.post_stderr = "gh: Unprocessable Entity (HTTP 422)"
        dispatch.review_intake_pass(dry_run=False)
        intakes = self.rows("review-intake")
        self.assertEqual(len(intakes), 1)
        self.assertEqual(intakes[0]["head"], "h" * 40)
        results = self.rows("review-result")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["head"], "h" * 40)
        self.assertEqual(results[0]["verdict"], "APPROVE")
        # A later pass must not crash, and must not re-run the reviewer for this head
        # once the verdict is journaled.
        self.run.reset_mock()
        dispatch.review_intake_pass(dry_run=False)
        reviewer_calls = [
            c.args[0] for c in self.run.call_args_list if c.args[0][:1] == ["reviewer"]
        ]
        self.assertEqual(reviewer_calls, [])
        self.assertEqual(len(self.rows("review-result")), 1)

    def test_author_equals_reviewer_posts_comment_event(self) -> None:
        self.open_prs = [self._pr()]
        dispatch.review_intake_pass(dry_run=False)
        posts = [
            c.args[0]
            for c in self.run.call_args_list
            if c.args[0][:4] == ["gh", "api", "--method", "POST"]
        ]
        self.assertEqual(len(posts), 1)
        self.assertIn("event=COMMENT", posts[0])
        self.assertNotIn("event=APPROVE", posts[0])
        self.assertNotIn("event=REQUEST_CHANGES", posts[0])
        [result] = self.rows("review-result")
        self.assertEqual(result["pr"], 9)
        self.assertEqual(result["head"], "h" * 40)
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(result["branch"], "agent/8")

    def test_comment_posted_revise_blocks_merge_and_routes_to_revise(self) -> None:
        head = "c" * 40
        self.open_prs = [
            self._pr(
                labels=[{"name": "factory-approved"}],
                review_decision="",
                head=head,
            )
        ]
        with self.events.open("a") as fh:
            for row in (
                {
                    "event": "attempt",
                    "ticket": 8,
                    "gate": "PASS",
                    "head": head,
                    "actual_head": head,
                },
                {
                    "event": "review",
                    "ticket": 8,
                    "verdict": "APPROVE",
                    "accepted": True,
                    "head": head,
                    "actual_head": head,
                },
                {
                    "event": "approved",
                    "ticket": 8,
                    "pr": 9,
                    "head": head,
                    "gate_head": head,
                    "review_head": head,
                },
                {
                    "event": "review-result",
                    "pr": 9,
                    "head": head,
                    "verdict": "REQUEST_CHANGES",
                    "findings": "VERDICT: REVISE",
                },
            ):
                fh.write(json.dumps({"at": "2000-01-01T00:00:00Z", **row}) + "\n")
        approved_before = len(self.rows("approved"))
        with (
            mock.patch.object(learn, "promote_pass"),
            mock.patch.object(dispatch, "escalate") as escalate,
            mock.patch.object(
                dispatch,
                "pr_checks",
                return_value=[{"name": "ci", "bucket": "pass"}],
            ),
        ):
            dispatch.merge_pass_locked(False)
        gh_cmds = [c.args[0] for c in self.run.call_args_list if c.args[0][:1] == ["gh"]]
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in gh_cmds))
        self.assertIn(
            [
                "gh",
                "pr",
                "edit",
                "9",
                "--repo",
                "acme/widgets",
                "--remove-label",
                "factory-approved",
            ],
            gh_cmds,
        )
        escalate.assert_called()
        self.assertEqual(escalate.call_args.kwargs.get("code"), "review_revise")
        reason = escalate.call_args.args[1] if escalate.call_args.args else ""
        self.assertIn("REQUEST_CHANGES", reason)
        self.assertEqual(len(self.rows("approved")), approved_before)
        self.assertFalse(
            any(
                cmd[:3] == ["gh", "pr", "edit"]
                and "--add-label" in cmd
                and "factory-approved" in cmd
                for cmd in gh_cmds
            )
        )

    def test_intake_without_result_retries_review_without_a_new_intake(self) -> None:
        self.open_prs = [self._pr()]
        dispatch.record("review-intake", pr=9, head="h" * 40)
        dispatch.review_intake_pass(dry_run=False)
        self.assertEqual(len(self.rows("review-intake")), 1)
        [result] = self.rows("review-result")
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(result["head"], "h" * 40)
        reviewer_calls = [
            c.args[0] for c in self.run.call_args_list if c.args[0][:1] == ["reviewer"]
        ]
        self.assertEqual(len(reviewer_calls), 1)


if __name__ == "__main__":
    unittest.main()
