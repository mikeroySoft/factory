from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn


class ReadinessTest(unittest.TestCase):
    HEAD = "7a05cbf46ff7c00f66a36c1d01a951bc92c2e464"
    APP = 15368

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        dispatch.configure(config.Config(root=Path(temp.name), repo="acme/widgets"))
        self.pr = {"head": {"sha": self.HEAD}, "base": {"ref": "main"},
                   "mergeable_state": "clean"}
        self.rules = [{"type": "required_status_checks", "parameters": {
            "required_status_checks": [{"context": "gate", "integration_id": self.APP}]}}]
        self.checks = [{"id": 10, "name": "gate", "head_sha": self.HEAD,
                        "app": {"id": self.APP}, "status": "completed",
                        "conclusion": "success"}]
        self.statuses = []
        self.rows = [{"name": "gate", "bucket": "pass", "link": ""}]
        self.error = None
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.run_gh))

    def run_gh(self, cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "checks"]:
            value = self.rows
        elif cmd[:3] == ["gh", "pr", "view"]:
            value = {"headRefOid": self.pr["head"]["sha"]}
        elif cmd[:2] == ["gh", "api"]:
            endpoint = cmd[2]
            if self.error and self.error in endpoint:
                return subprocess.CompletedProcess(cmd, 1, "unavailable", "")
            if "/pulls/" in endpoint:
                value = self.pr
            elif "/rules/branches/" in endpoint:
                value = [self.rules] if "--slurp" in cmd else self.rules
            elif "/check-runs" in endpoint:
                value = {"check_runs": self.checks, "total_count": len(self.checks)}
                if "--slurp" in cmd:
                    value = [value]
            elif "/statuses" in endpoint:
                value = [self.statuses] if "--slurp" in cmd else self.statuses
            elif "/compare/" in endpoint:
                value = {"behind_by": 0, "files": []}
            else:
                self.fail(f"Unexpected endpoint: {endpoint}")
        else:
            self.fail(f"Unexpected command: {cmd}")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(value), "")

    def assert_pending(self):
        self.assertIn("pending", {row["bucket"] for row in dispatch.pr_checks(573)})

    def test_wrong_app_or_sha_is_not_mergeable(self):
        for field, value in (("app", {"id": 99}), ("head_sha", "old-head"),
                             ("name", "Gate")):
            with self.subTest(field=field):
                original = self.checks[0][field]
                self.checks[0][field] = value
                self.assert_pending()
                self.checks[0][field] = original

    def test_exact_required_app_and_head_pass(self):
        self.assertEqual({row["bucket"] for row in dispatch.pr_checks(573)}, {"pass"})

    def test_missing_required_context_is_pending_despite_other_green_ci(self):
        self.rules[0]["parameters"]["required_status_checks"].append(
            {"context": "security", "integration_id": self.APP})
        self.assert_pending()

    def test_unreadable_provenance_fails_closed(self):
        for endpoint in ("/pulls/", "/rules/branches/", "/check-runs"):
            with self.subTest(endpoint=endpoint):
                self.error = endpoint
                self.assert_pending()

    def test_latest_required_run_cannot_inherit_old_success(self):
        self.checks.insert(0, {**self.checks[0], "id": 11, "status": "queued",
                               "conclusion": None})
        self.assert_pending()

    def test_github_policy_veto_blocks_even_matching_green_gate(self):
        for state in ("blocked", "unknown", "dirty", None):
            with self.subTest(state=state):
                self.pr["mergeable_state"] = state
                self.assert_pending()

    def test_behind_head_preserves_existing_refresh_path(self):
        self.pr["mergeable_state"] = "behind"
        self.assertEqual({row["bucket"] for row in dispatch.pr_checks(573)}, {"pass"})

    def test_unspecified_app_accepts_exact_head_status_context(self):
        self.rules[0]["parameters"]["required_status_checks"][0].pop("integration_id")
        self.checks = []
        self.statuses = [{"id": 20, "context": "gate", "state": "success",
                          "sha": self.HEAD}]
        self.assertEqual({row["bucket"] for row in dispatch.pr_checks(573)}, {"pass"})
        self.rules[0]["parameters"]["required_status_checks"][0]["integration_id"] = self.APP
        self.assert_pending()

    def test_promotion_does_not_report_wrong_app_as_mergeable(self):
        self.checks[0]["app"] = {"id": 99}
        branch = "agent/lessons-2026-11-02-t517"
        pr = {"number": 573, "headRefName": branch, "headRefOid": self.HEAD}
        events = [{"event": "review-result", "pr": 573, "head": self.HEAD,
                   "verdict": "APPROVE"},
                  {"event": "gate", "branch": branch, "head": self.HEAD, "gate": "PASS"}]
        output = io.StringIO()
        with redirect_stdout(output):
            learn.promote(dispatch.cfg, pr, events, dry_run=True)
        self.assertNotIn("would merge", output.getvalue())
        self.assertIn("not merging", output.getvalue())

    def test_external_readiness_rejects_wrong_app(self):
        self.checks[0]["app"] = {"id": 99}
        history = [{"event": "review-result", "pr": 573, "head": self.HEAD,
                    "verdict": "APPROVE"}]
        with mock.patch.object(dispatch, "record") as record:
            dispatch.review_readiness(573, self.HEAD, history)
        self.assertEqual(record.call_args.kwargs["state"], "ci_pending")

    def test_matching_gate_can_report_ready(self):
        history = [{"event": "review-result", "pr": 573, "head": self.HEAD,
                    "verdict": "APPROVE"}]
        with mock.patch.object(dispatch, "record") as record:
            dispatch.review_readiness(573, self.HEAD, history)
        self.assertEqual(record.call_args.kwargs["state"], "ready")

    def test_push_during_provenance_read_invalidates_readiness(self):
        original_run = self.run_gh
        pulls = 0

        def racing_run(cmd, **kwargs):
            nonlocal pulls
            if cmd[:2] == ["gh", "api"] and "/pulls/" in cmd[2]:
                pulls += 1
                if pulls == 2:
                    self.pr["head"]["sha"] = "new-head"
            return original_run(cmd, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=racing_run):
            self.assert_pending()

    def test_required_checks_on_later_api_pages_are_observed(self):
        original_run = self.run_gh

        def paginated_run(cmd, **kwargs):
            if cmd[:2] == ["gh", "api"] and "/check-runs" in cmd[2]:
                pages = [{"check_runs": [], "total_count": 101},
                         {"check_runs": self.checks, "total_count": 101}]
                return subprocess.CompletedProcess(cmd, 0, json.dumps(pages), "")
            return original_run(cmd, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=paginated_run):
            self.assertEqual({row["bucket"] for row in dispatch.pr_checks(573)}, {"pass"})

    def test_unpinned_legacy_context_must_also_match_head(self):
        self.rules[0]["parameters"]["required_status_checks"][0].pop("integration_id")
        self.checks = []
        self.statuses = [{"id": 20, "context": "gate", "state": "success", "sha": "old-head"}]
        self.assert_pending()

    def test_unpinned_status_cannot_satisfy_a_pinned_gate(self):
        self.rules[0]["parameters"]["required_status_checks"].append({"context": "lint"})
        self.checks[0]["app"] = {"id": 99}
        self.statuses = [
            {"id": 20, "context": "gate", "state": "success", "sha": self.HEAD},
            {"id": 21, "context": "lint", "state": "success", "sha": self.HEAD},
        ]
        self.assert_pending()

    def test_strict_base_refusal_still_refreshes_lessons_head(self):
        self.pr["mergeable_state"] = "behind"
        branch = "agent/lessons-2026-11-02-t517"
        pr = {"number": 573, "headRefName": branch, "headRefOid": self.HEAD}
        events = [{"event": "review-result", "pr": 573, "head": self.HEAD,
                   "verdict": "APPROVE"},
                  {"event": "gate", "branch": branch, "head": self.HEAD, "gate": "PASS"}]
        original_run = self.run_gh

        def behind_run(cmd, **kwargs):
            if cmd[:2] == ["gh", "api"] and "/compare/" in cmd[2]:
                return subprocess.CompletedProcess(cmd, 0, '{"behind_by":4}', "")
            return original_run(cmd, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=behind_run), \
                mock.patch.object(learn, "refresh") as refresh:
            learn.promote(dispatch.cfg, pr, events, dry_run=False)
        refresh.assert_called_once_with(dispatch.cfg, 573, branch, self.HEAD)
