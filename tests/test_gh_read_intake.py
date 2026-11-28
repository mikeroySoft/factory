"""Caller regressions for review intake GitHub-read isolation."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, gh_reads, lifecycle

APPROVAL = (
    '```json\n{"verdict":"APPROVE","findings":[]}\n```\n'
    "VERDICT: APPROVE"
)


class GhReadIntakeTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(mock.patch.dict(
            os.environ,
            {"GH_CONFIG_DIR": "/machine/gh", lifecycle.CONTEXT_ENV: ""},
            clear=True,
        ))
        self.cfg = config.Config(
            root=Path(temporary.name), repo="acme/widgets", reviewer=["reviewer"],
        )
        self.cfg.install = config.merge(
            config.DEFAULT_INSTALL,
            {"dispatch_env": {"GH_CONFIG_DIR": "/machine/gh"}},
        )
        dispatch.configure(self.cfg)
        self.prs: list[dict] = []
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self.identity_reads: list[str] = []
        self.dashboard_failures = 0
        self.checks = subprocess.CompletedProcess([], 0, "[]", "")
        self.enterContext(mock.patch.object(dispatch, "_run", side_effect=self._run))
        self.enterContext(mock.patch.object(dispatch, "_review_policy", return_value=(None, 5)))
        self.rate_limit_probe = self.enterContext(mock.patch(
            "factory.gh_reads.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps({"resources": {
                name: {"remaining": 5000, "reset": 4102444800}
                for name in ("core", "graphql")
            }}), ""),
        ))

    @staticmethod
    def pr(number: int, *, requested: str | None = None) -> dict:
        return {
            "number": number,
            "state": "OPEN",
            "isDraft": False,
            "isCrossRepository": True,
            "headRefName": f"contributor/change-{number}",
            "headRefOid": f"head-{number}",
            "baseRefOid": "base",
            "labels": [] if requested else [{"name": config.LABEL_REVIEW}],
            "reviewRequests": [] if requested is None else [{"login": requested}],
        }

    def _run(self, cmd, cwd, check, stdout, stderr, env, timeout):
        self.calls.append((cmd, env))
        if cmd == ["reviewer"]:
            return subprocess.CompletedProcess(cmd, 0, APPROVAL, "")
        if cmd[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.prs), "")
        if cmd == ["gh", "api", "user"]:
            identity = "dispatcher" if env is None else "dashboard"
            self.identity_reads.append(identity)
            if identity == "dashboard" and self.dashboard_failures:
                self.dashboard_failures -= 1
                return subprocess.CompletedProcess(cmd, 1, "", "connection refused")
            login = "factory-bot" if identity == "dispatcher" else "human"
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"login": login}), "")
        if cmd[:3] == ["gh", "pr", "checks"]:
            return subprocess.CompletedProcess(cmd, self.checks.returncode,
                                               self.checks.stdout, self.checks.stderr)
        if cmd[:3] == ["gh", "pr", "view"]:
            number = int(cmd[3])
            pr = next(row for row in self.prs if row["number"] == number)
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"headRefOid": pr["headRefOid"]}), "",
            )
        if cmd[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"labels": []}), "")
        if cmd[:3] == ["gh", "issue", "create"]:
            return subprocess.CompletedProcess(
                cmd, 0, "https://github.com/acme/widgets/issues/99\n", "",
            )
        if cmd[:2] == ["gh", "api"] and "--method" not in cmd:
            return subprocess.CompletedProcess(cmd, 0, "diff", "")
        if cmd[:4] == ["gh", "api", "--method", "POST"]:
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    def events(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS)
                if row.get("event") == event]

    def test_dashboard_identity_failure_retries_complete_identity_set_for_later_pr(self) -> None:
        self.prs = [self.pr(1, requested="human"), self.pr(2, requested="human")]
        self.dashboard_failures = 1

        dispatch.review_intake_pass(False)

        self.assertEqual(
            self.identity_reads,
            ["dispatcher", "dashboard", "dispatcher", "dashboard"],
        )
        self.assertEqual(
            [(row["pr"], row["verdict"]) for row in self.events("review-result")],
            [(2, "APPROVE")],
        )
        [skip] = self.events("gh-read-skipped")
        self.assertEqual(
            (skip["pr"], skip["ticket"], skip["code"]),
            (1, 1, "gh_read_failed"),
        )
        self.rate_limit_probe.assert_called_once()

    def test_review_continues_when_github_reports_no_required_checks(self) -> None:
        self.prs = [self.pr(17)]
        self.checks = subprocess.CompletedProcess(
            [], 1, "", "no required checks reported on the 'main' branch\n",
        )

        dispatch.review_intake_pass(False)

        self.assertEqual(
            [(row["pr"], row["verdict"]) for row in self.events("review-result")],
            [(17, "APPROVE")],
        )
        self.assertEqual(
            [cmd[:4] for cmd, _env in self.calls if cmd[:3] == ["gh", "pr", "checks"]],
            [["gh", "pr", "checks", "17"], ["gh", "pr", "checks", "17"]],
        )
        self.assertTrue(all(
            "--required" in cmd
            for cmd, _env in self.calls if cmd[:3] == ["gh", "pr", "checks"]
        ))
        self.assertEqual(self.events("gh-read-skipped"), [])
        self.rate_limit_probe.assert_not_called()

    def test_merge_records_no_passing_ci_when_github_reports_no_checks(self) -> None:
        self.prs = [{
            "number": 70,
            "headRefName": "agent/7",
            "headRefOid": "head-7",
            "baseRefName": "main",
            "isDraft": False,
            "labels": [{"name": config.LABEL_APPROVED}],
            "reviewDecision": "APPROVED",
        }]
        self.checks = subprocess.CompletedProcess(
            [], 1, "", "no checks reported on the 'agent/7' branch\n",
        )

        dispatch.merge_pass_locked(False)

        rows = lifecycle.read_events(dispatch.EVENTS)
        [exit_row] = [row for row in rows
                      if row.get("stage") == "merge-eligibility" and row.get("kind") == "exit"]
        [wait] = [row["wait"] for row in rows
                  if row.get("stage") == "merge-eligibility" and row.get("kind") == "wait"]
        self.assertEqual(
            (exit_row["ticket"], exit_row["outcome"], exit_row["reason"]),
            (7, "not_eligible", "no_passing_ci"),
        )
        self.assertEqual(
            (wait["reason"], wait["mode"], wait["details"]),
            ("no_passing_ci", "eligibility", {"pr": 70}),
        )
        self.assertEqual(self.events("gh-read-skipped"), [])
        self.assertFalse(any(
            cmd[0] == "gh" and not gh_reads.is_read(cmd) for cmd, _env in self.calls
        ))
        self.rate_limit_probe.assert_not_called()

    def test_escalated_pr_does_not_stop_later_review_intake(self) -> None:
        self.cfg.review_rounds = 0
        self.prs = [self.pr(17), self.pr(18)]
        dispatch.record("review-intake", pr=17, head="head-17")

        dispatch.review_intake_pass(False)

        self.assertEqual(
            [(row["pr"], row["ticket"]) for row in self.events("escalate")],
            [(17, 99)],
        )
        self.assertEqual(
            [(row["pr"], row["verdict"]) for row in self.events("review-result")],
            [(18, "APPROVE")],
        )
        created = [cmd for cmd, _env in self.calls if cmd[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(created), 1)
        self.assertIn("PR #17: unresolved after 1 automated review attempt(s)", created[0])


if __name__ == "__main__":
    unittest.main()
