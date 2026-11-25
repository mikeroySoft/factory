"""Merge admission with required/advisory checks and unavailable self-hosted runners."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, runtime_events


class MergeChecksTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        dispatch.configure(config.Config(root=Path(temp.name), repo="acme/widgets", signoff=False))
        self.pr = {"number": 70, "headRefName": "agent/7", "headRefOid": "head",
                   "baseRefName": "main", "isDraft": False, "state": "OPEN", "title": "feature",
                   "labels": [{"name": "factory-approved"}], "reviewDecision": "APPROVED"}
        self.passing = {"name": "unit", "bucket": "pass", "link": ""}
        self.gpu = {"name": "E2E tests (mi300x)", "bucket": "pending",
                    "link": "https://github.com/acme/widgets/actions/runs/123/job/456"}
        self.required = []
        self.all_checks = [self.passing, self.gpu]
        self.job = {"status": "queued", "conclusion": None, "steps": [],
                    "runner_id": None, "runner_name": None,
                    "labels": ["self-hosted", "linux", "mi300x"],
                    "created_at": "2026-01-01T00:00:00Z"}
        self.runners = [{"runners": []}]
        self.errors = {}
        self.required_error = None
        self.check_reads = 0
        self.second_checks = None
        self.commands = []
        self.policy = []
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.run_gh))
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.query))
        self.enterContext(mock.patch.object(dispatch, "_required_ci", side_effect=lambda *a, **k: self.policy))
        self.enterContext(mock.patch.object(dispatch, "_head_evidence_matches", return_value=True))
        self.enterContext(mock.patch.object(dispatch, "cleanup_after_merge"))
        self.escalate = self.enterContext(mock.patch.object(dispatch, "escalate"))
        dispatch.record("approved", ticket=7, pr=70, head="head", gate_head="head", review_head="head")

    def query(self, args):
        if args[:2] == ["pr", "list"]:
            return [self.pr]
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[:2] == ["issue", "view"]:
            return {"labels": []}
        if args[0] == "api":
            return {"behind_by": 0}
        self.fail(args)

    def run_gh(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd[:3] == ["gh", "pr", "checks"]:
            if "--required" in cmd:
                if self.required_error:
                    return subprocess.CompletedProcess(cmd, *self.required_error)
                self.check_reads += 1
                rows = self.required
            else:
                rows = self.second_checks if self.check_reads > 1 and self.second_checks is not None else self.all_checks
            code = 1 if any(row["bucket"] == "fail" for row in rows) else 8 if any(
                row["bucket"] == "pending" for row in rows) else 0
            return subprocess.CompletedProcess(cmd, code, json.dumps(rows), "")
        if cmd[:2] == ["gh", "api"]:
            endpoint = cmd[2]
            if endpoint in self.errors:
                code, text = self.errors[endpoint]
                return subprocess.CompletedProcess(cmd, code, text, "")
            if "/actions/jobs/" in endpoint:
                value = self.job
            elif "/actions/runners?" in endpoint:
                self.assertEqual(cmd[-2:], ["--paginate", "--slurp"])
                value = self.runners
            elif "/actions/runs/" in endpoint:
                value = {"status": "completed"}
            else:
                self.fail(cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(value), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def merged(self):
        return any(cmd[:3] == ["gh", "pr", "merge"] for cmd in self.commands)

    def decision(self):
        return next(row for row in reversed(lifecycle.read_events(dispatch.EVENTS))
                    if row.get("event") == "decision" and row.get("stage") == "merge")

    def test_advisory_pending_and_failed_merge_without_withdrawing_approval(self):
        dispatch.cfg.advisory_checks = ["E2E tests (*"]
        for bucket in ("pending", "fail", "cancel"):
            with self.subTest(bucket=bucket):
                self.commands.clear()
                self.gpu["bucket"] = bucket
                dispatch.merge_pass_locked(False)
                self.assertTrue(self.merged())
                self.assertFalse(any("--remove-label" in cmd for cmd in self.commands))
                self.escalate.assert_not_called()
                row = self.decision()["checks"][1]
                self.assertEqual((row["disposition"], row["original_bucket"]), ("advisory", bucket))
                self.assertFalse(any("/actions/jobs/" in str(cmd) for cmd in self.commands))

    def test_required_pending_and_failure_override_advisory_and_runner_skip(self):
        dispatch.cfg.advisory_checks = ["*"]
        for bucket in ("pending", "fail"):
            with self.subTest(bucket=bucket):
                self.commands.clear()
                self.required = [{**self.passing, "bucket": bucket}]
                dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
                self.assertEqual(any("--remove-label" in cmd for cmd in self.commands), bucket == "fail")
        self.assertEqual(self.escalate.call_args.kwargs["code"], "ci_failed")

    def test_stale_unassigned_job_without_matching_runner_merges_without_rerun(self):
        for bucket in ("pending", "cancel"):
            with self.subTest(bucket=bucket):
                self.commands.clear()
                self.gpu["bucket"] = bucket
                self.job.update(status="queued" if bucket == "pending" else "completed",
                                conclusion=None if bucket == "pending" else "cancelled")
                self.runners = [{"runners": [{"labels": [{"name": label} for label in
                                                        ("self-hosted", "linux", "w7900")]}]}]
                dispatch.merge_pass_locked(False)
                self.assertTrue(self.merged())
                row = self.decision()["checks"][1]
                self.assertEqual(row["bucket"], "skipped-infra")
                self.assertEqual(row["labels"], ["linux", "mi300x", "self-hosted"])
                self.assertEqual(row["registered_runners"], 1)
                self.assertEqual((row["run"], row["job"]), (123, 456))
                self.assertGreater(row["queued_seconds"], 21600)
                self.assertFalse(any(cmd[:3] == ["gh", "run", "rerun"] for cmd in self.commands))
                self.escalate.assert_not_called()

    def test_zero_runners_skip_but_policy_provenance_still_blocks(self):
        self.policy = [{"name": "required CI evidence unavailable", "bucket": "pending"}]
        dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.policy = []
        dispatch.merge_pass_locked(False)
        self.assertTrue(self.merged())
        self.assertEqual(self.decision()["checks"][1]["bucket"], "skipped-infra")

    def test_available_runner_on_later_page_and_casefolded_labels_blocks(self):
        self.runners += [{"runners": [{"labels": [{"name": label} for label in
                                                  ("SELF-HOSTED", "Linux", "MI300X", "extra")]}]}]
        dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.escalate.assert_not_called()

    def test_missing_runner_evidence_never_skips(self):
        for endpoint in ("actions/jobs/456", "actions/runners?per_page=100"):
            for code, text in ((1, "unavailable"), (0, "not-json"), (0, "{}"), (0, "null")):
                with self.subTest(endpoint=endpoint, code=code, text=text):
                    self.errors = {f"repos/acme/widgets/{endpoint}": (code, text)}
                    dispatch.merge_pass_locked(False)
                    self.assertFalse(self.merged())
        self.errors = {}
        for value in ([], [{"runners": [{"labels": None}]}]):
            self.runners = value
            dispatch.merge_pass_locked(False)
            self.assertFalse(self.merged())

    def test_recent_assigned_hosted_or_started_jobs_do_not_skip(self):
        original = self.job.copy()
        for change in ({"created_at": datetime.now(UTC).isoformat()}, {"runner_id": 1},
                       {"runner_name": "worker"}, {"labels": ["ubuntu-latest"]},
                       {"steps": [{"conclusion": "success"}]},
                       {"steps": [{"started_at": "2026-01-01T01:00:00Z"}]},
                       {"status": "in_progress"}, {"status": "completed", "conclusion": "failure"},
                       {"created_at": "unknown"}, {"created_at": "2026-01-01"}):
            with self.subTest(change=change):
                self.job = {**original, **change}
                dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
        self.escalate.assert_not_called()

    def test_timeout_boundary_and_configured_limit(self):
        self.job["created_at"] = "2026-01-01T00:00:00Z"
        with mock.patch.object(dispatch, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 1, 1, 6, tzinfo=UTC)
            rows = dispatch.pr_checks(70, "head")
            self.assertEqual(rows[1]["bucket"], "skipped-infra")
            self.assertEqual(rows[1]["queued_seconds"], 21600)
            dispatch.cfg.queued_check_timeout = "6h1s"
            rows = dispatch.pr_checks(70, "head")
            self.assertEqual(rows[1]["bucket"], "pending")

    def test_required_starvation_escalates_explicit_block_and_journals_wait(self):
        self.required = [self.passing, self.gpu]
        self.escalate.side_effect = lambda n, reason, *_args, **kw: dispatch.record(
            "escalate", ticket=n, reason=reason, code=kw["code"])
        dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.assertIn("blocks merge", self.escalate.call_args.args[1])
        self.assertEqual(self.escalate.call_args.kwargs["code"], "ci_runner")
        self.assertFalse(any(cmd[:3] == ["gh", "run", "rerun"] for cmd in self.commands))
        self.assertEqual(self.decision()["checks"][1]["bucket"], "infra")
        exits = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("kind") == "exit"]
        self.assertEqual(exits[-1]["outcome"], "project_escalation")
        dispatch.merge_pass_locked(False)
        self.escalate.assert_called_once()
        waits = [row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_runner")
        self.assertEqual(waits[-1]["mode"], "eligibility")
        projected = runtime_events.project(dispatch.EVENTS)
        waits = [row["wait"] for row in projected["events"] if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_runner")

    def test_queued_job_with_optional_steps_absent_still_skips(self):
        del self.job["steps"]
        dispatch.merge_pass_locked(False)
        self.assertTrue(self.merged())
        self.assertEqual(self.decision()["checks"][1]["bucket"], "skipped-infra")

    def test_final_recheck_rejects_new_pending_or_failed_required_check(self):
        for bucket in ("pending", "fail"):
            with self.subTest(bucket=bucket):
                self.commands.clear()
                self.check_reads = 0
                def required(cmd, bucket=bucket, **kwargs):
                    if cmd[:3] == ["gh", "pr", "checks"] and "--required" in cmd:
                        self.required = [self.passing if self.check_reads == 0 else
                                         {**self.passing, "bucket": bucket}]
                    return self.run_gh(cmd, **kwargs)
                with mock.patch.object(dispatch, "run", side_effect=required):
                    dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())

    def test_required_query_error_fails_closed_and_known_empty_falls_back(self):
        for error in ((1, "", "authentication failed"), (1, "[]", "forbidden"),
                      (0, "null", ""), (0, "{}", ""), (0, "not-json", ""),
                      (0, '[{"name":"unit","bucket":"unknown"}]', "")):
            with self.subTest(error=error):
                self.required_error = error
                dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
        self.required_error = (1, "", "no required checks reported on the 'main' branch")
        dispatch.merge_pass_locked(False)
        self.assertTrue(self.merged())

    def test_decision_retains_advisory_checks_beyond_bounded_trace_inputs(self):
        dispatch.cfg.advisory_checks = ["E2E tests (*"]
        self.all_checks = [self.passing] * 20 + [self.gpu]
        dispatch.merge_pass_locked(False)
        self.assertTrue(self.merged())
        self.assertEqual(self.decision()["checks"][-1]["disposition"], "advisory")

    def test_merge_decision_redacts_credentials_in_all_check_evidence(self):
        dispatch.cfg.advisory_checks = ["E2E tests (*"]
        secret = "ghp_" + "x" * 20
        self.gpu["link"] += "?token=" + secret
        dispatch.merge_pass_locked(False)
        self.assertTrue(self.merged())
        self.assertNotIn(secret, json.dumps(self.decision()))
        self.assertIn("[credential withheld]", self.decision()["checks"][-1]["link"])


    def test_required_nonzero_results_drive_pending_and_failure_outcomes(self):
        def checked_run(cmd, **kwargs):
            proc = self.run_gh(cmd, **kwargs)
            if kwargs.get("check", True) and proc.returncode:
                raise subprocess.CalledProcessError(
                    proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr,
                )
            return proc

        for bucket in ("pending", "fail"):
            with self.subTest(bucket=bucket):
                self.commands.clear()
                self.escalate.reset_mock()
                self.required = [{**self.passing, "bucket": bucket}]
                with mock.patch.object(dispatch, "run", side_effect=checked_run):
                    dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
                removed = any("--remove-label" in cmd for cmd in self.commands)
                self.assertEqual(removed, bucket == "fail")
                if bucket == "fail":
                    self.assertEqual(self.escalate.call_args.kwargs["code"], "ci_failed")
                else:
                    self.escalate.assert_not_called()

    def test_all_check_fallback_nonzero_results_drive_pending_and_failure_outcomes(self):
        def checked_run(cmd, **kwargs):
            proc = self.run_gh(cmd, **kwargs)
            if kwargs.get("check", True) and proc.returncode:
                raise subprocess.CalledProcessError(
                    proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr,
                )
            return proc

        self.required_error = (1, "", "no required checks reported on the 'main' branch")
        self.job["created_at"] = datetime.now(UTC).isoformat()
        for bucket in ("pending", "fail"):
            with self.subTest(bucket=bucket):
                self.commands.clear()
                self.escalate.reset_mock()
                self.gpu["bucket"] = bucket
                with mock.patch.object(dispatch, "run", side_effect=checked_run):
                    dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
                removed = any("--remove-label" in cmd for cmd in self.commands)
                self.assertEqual(removed, bucket == "fail")
                if bucket == "fail":
                    self.assertEqual(self.escalate.call_args.kwargs["code"], "ci_failed")
                else:
                    self.escalate.assert_not_called()

    def test_no_required_diagnostic_is_only_valid_for_exit_one(self):
        self.all_checks = [self.passing]
        for code in (0, 8):
            with self.subTest(code=code):
                self.commands.clear()

                def checked_run(cmd, code=code, **kwargs):
                    if cmd[:3] == ["gh", "pr", "checks"] and "--required" in cmd:
                        proc = subprocess.CompletedProcess(
                            cmd, code, "", "no required checks reported on the 'main' branch",
                        )
                    else:
                        proc = self.run_gh(cmd, **kwargs)
                    if kwargs.get("check", True) and proc.returncode:
                        raise subprocess.CalledProcessError(
                            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr,
                        )
                    return proc

                with mock.patch.object(dispatch, "run", side_effect=checked_run):
                    dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
                self.assertFalse(any("--remove-label" in cmd for cmd in self.commands))

    def test_empty_required_rows_with_accepted_exit_codes_use_all_checks(self):
        self.all_checks = [self.passing]
        for code in (0, 8):
            with self.subTest(code=code):
                self.commands.clear()

                def checked_run(cmd, code=code, **kwargs):
                    if cmd[:3] == ["gh", "pr", "checks"] and "--required" in cmd:
                        proc = subprocess.CompletedProcess(cmd, code, "[]", "")
                    else:
                        proc = self.run_gh(cmd, **kwargs)
                    if kwargs.get("check", True) and proc.returncode:
                        raise subprocess.CalledProcessError(
                            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr,
                        )
                    return proc

                with mock.patch.object(dispatch, "run", side_effect=checked_run):
                    dispatch.merge_pass_locked(False)
                self.assertTrue(self.merged())
                self.assertEqual(self.decision()["checks"][0]["bucket"], "pass")

    def test_malformed_check_rows_fail_closed_before_merge(self):
        variants = (
            ("non-dict", [self.passing, "unit"]),
            ("missing-name", [self.passing, {"bucket": "pass"}]),
            ("non-string-name", [self.passing, {"name": 7, "bucket": "pass"}]),
            ("unknown-bucket", [self.passing, {"name": "odd", "bucket": "unknown"}]),
        )
        for label, rows in variants:
            with self.subTest(label=label):
                self.commands.clear()

                def malformed_run(cmd, rows=rows, **kwargs):
                    if cmd[:3] == ["gh", "pr", "checks"] and "--required" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")
                    return self.run_gh(cmd, **kwargs)

                with mock.patch.object(dispatch, "run", side_effect=malformed_run):
                    dispatch.merge_pass_locked(False)
                self.assertFalse(self.merged())
                self.assertFalse(any("--remove-label" in cmd for cmd in self.commands))

    def test_failed_job_lookup_remains_pending_without_subprocess_failure(self):
        def checked_run(cmd, **kwargs):
            proc = self.run_gh(cmd, **kwargs)
            if kwargs.get("check", True) and proc.returncode:
                raise subprocess.CalledProcessError(
                    proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr,
                )
            return proc

        self.errors["repos/acme/widgets/actions/jobs/456"] = (1, "unavailable")
        with mock.patch.object(dispatch, "run", side_effect=checked_run):
            dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.assertFalse(any("--remove-label" in cmd for cmd in self.commands))
        waits = [row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_pending")

    def test_completed_non_cancelled_job_remains_pending(self):
        self.job.update(status="completed", conclusion="action_required")
        dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        waits = [row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_pending")

    def test_runner_with_exact_required_labels_keeps_check_pending(self):
        self.runners = [{"runners": [{"labels": [
            {"name": label} for label in self.job["labels"]
        ]}]}]
        dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        waits = [row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_pending")

    def test_large_merge_decision_preserves_complete_evidence_within_reader_limit(self):
        from factory import dashboard

        dispatch.cfg.advisory_checks = ["E2E tests (*"]
        secret = "ghp_" + "x" * 20
        self.all_checks = [self.passing] + [
            {**self.gpu, "name": f"E2E tests ({index} {'界' * 100})",
             "link": self.gpu["link"] + "?token=" + secret}
            for index in range(200)
        ]
        dispatch.merge_pass_locked(False)
        self.assertTrue(self.merged())
        with mock.patch.object(dashboard, "FACTORY", dispatch.FACTORY, create=True):
            rows, coverage = dashboard.bounded_journal_rows()
        self.assertNotIn("row_limit", coverage["gaps"])
        [decision] = [row for row in rows
                      if row.get("event") == "decision" and row.get("stage") == "merge"]
        self.assertEqual(decision["chosen"], "merge now")
        evidence = decision["checks_ref"]
        data = (dispatch.ROOT / evidence["path"]).read_bytes()
        self.assertEqual(evidence["sha256"], hashlib.sha256(data).hexdigest())
        checks = json.loads(data)
        self.assertEqual(evidence["count"], len(self.all_checks))
        self.assertEqual(checks, [{**self.passing, "required": False}] + [
            {**check, "required": False, "link": check["link"].replace(secret, "[credential withheld]"),
             "bucket": "skipping", "disposition": "advisory", "original_bucket": "pending"}
            for check in self.all_checks[1:]
        ])
        self.assertNotIn(secret, data.decode())
        self.assertIn(evidence["path"], {value["ref"] for value in decision["inputs"]})

    def test_merge_decision_at_reader_byte_limit_stays_inline_with_utf8_and_execution_ids(self):
        from factory import dashboard

        check = {"name": "unicode-boundary", "bucket": "pass", "detail": ""}
        with lifecycle.scope(
            dispatch.EVENTS, "merge-eligibility", ticket=7, dispatcher=True,
        ) as execution:
            with mock.patch.object(dispatch, "record") as captured:
                dispatch.record_merge_decision(
                    7, 70, "merge now", "eligible", "head", "head", [check],
                )
            baseline = {
                "event": "decision",
                **captured.call_args.kwargs,
                "execution_id": execution.execution_id,
                "dispatcher_run_id": execution.dispatcher_run_id,
            }
            baseline_bytes = json.dumps(
                baseline, ensure_ascii=False, separators=(",", ":"),
            ).encode()
            padding_bytes = runtime_events.ROW_LIMIT - 1 - len(baseline_bytes)
            self.assertGreater(padding_bytes, 3)
            detail = "界" * (padding_bytes // 3) + "x" * (padding_bytes % 3)
            check["detail"] = detail
            decision_id = dispatch.record_merge_decision(
                7, 70, "merge now", "eligible", "head", "head", [check],
            )
            oversized_decision_id = dispatch.record_merge_decision(
                7, 70, "merge now", "eligible", "head", "head",
                [{**check, "detail": detail + "x"}],
            )

        decision_line = next(
            line for line in dispatch.EVENTS.read_bytes().splitlines(keepends=True)
            if json.loads(line).get("decision_id") == decision_id
        )
        self.assertTrue(decision_line.endswith(b"\n"))
        self.assertEqual(len(decision_line), runtime_events.ROW_LIMIT)
        with mock.patch.object(dashboard, "FACTORY", dispatch.FACTORY, create=True):
            rows, coverage = dashboard.bounded_journal_rows()
        self.assertNotIn("row_limit", coverage["gaps"])
        [decision] = [row for row in rows if row.get("decision_id") == decision_id]
        [oversized_decision] = [
            row for row in rows if row.get("decision_id") == oversized_decision_id
        ]
        self.assertIn("checks_ref", oversized_decision)
        self.assertNotIn("checks", oversized_decision)
        self.assertEqual(decision["checks"], [{**check}])
        self.assertNotIn("checks_ref", decision)
        self.assertEqual(decision["execution_id"], execution.execution_id)
        self.assertEqual(decision["dispatcher_run_id"], execution.dispatcher_run_id)

    def test_oversized_merge_decision_artifact_is_utf8_and_preserves_non_ci_inputs(self):
        from factory import dashboard

        head, approval_head, merge_sha = "h" * 40, "a" * 40, "m" * 40
        checks = [{
            "name": "界" * 6000,
            "bucket": "pass",
            "link": "https://github.com/acme/widgets/actions/runs/123",
        }]
        decision_id = dispatch.record_merge_decision(
            7, 70, "merge now", "eligible", head, approval_head, checks,
            merge_sha=merge_sha,
        )

        with mock.patch.object(dashboard, "FACTORY", dispatch.FACTORY, create=True):
            rows, coverage = dashboard.bounded_journal_rows()
        self.assertNotIn("row_limit", coverage["gaps"])
        [decision] = [row for row in rows if row.get("decision_id") == decision_id]
        evidence = decision["checks_ref"]
        data = (dispatch.ROOT / evidence["path"]).read_bytes()
        self.assertEqual(
            data,
            json.dumps(checks, ensure_ascii=False, separators=(",", ":")).encode(),
        )
        self.assertIn("界".encode(), data)
        self.assertNotIn(b"\\u754c", data)
        self.assertEqual(evidence["count"], len(checks))
        self.assertEqual(evidence["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(decision["inputs"][:3], [
            {
                "kind": "head",
                "ref": f"https://github.com/acme/widgets/commit/{head}",
                "summary": f"PR head {head}",
            },
            {
                "kind": "approval_head",
                "ref": ".factory/events.jsonl",
                "summary": f"approval head {approval_head}",
            },
            {
                "kind": "merge_sha",
                "ref": f"https://github.com/acme/widgets/commit/{merge_sha}",
                "summary": f"merge SHA {merge_sha}",
            },
        ])
        self.assertEqual(len(decision["inputs"]), 4)
        self.assertEqual(decision["inputs"][3], {
            "kind": "ci_checks",
            "ref": evidence["path"],
            "summary": f"All {len(checks)} checks; SHA-256 {evidence['sha256']}",
        })

    def test_oversized_decisions_create_missing_parents_and_reuse_artifact_directory(self):
        factory = dispatch.ROOT / "missing" / ".factory"
        self.assertFalse(factory.parent.exists())
        expected = {}
        with mock.patch.object(dispatch, "FACTORY", factory):
            for name in ("first", "second"):
                checks = [{"name": name, "bucket": "pass", "detail": "界" * 6000}]
                decision_id = dispatch.record_merge_decision(
                    7, 70, "merge now", "eligible", "head", "head", checks,
                )
                expected[decision_id] = checks
                self.assertTrue((factory / "merge-checks").is_dir())

        decisions = [
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("decision_id") in expected
        ]
        self.assertEqual({row["decision_id"] for row in decisions}, set(expected))
        paths = set()
        for decision in decisions:
            evidence = decision["checks_ref"]
            paths.add(evidence["path"])
            data = (dispatch.ROOT / evidence["path"]).read_bytes()
            self.assertEqual(json.loads(data), expected[decision["decision_id"]])
            self.assertEqual(evidence["count"], 1)
            self.assertEqual(evidence["sha256"], hashlib.sha256(data).hexdigest())
            self.assertIn(
                evidence["path"],
                {value["ref"] for value in decision["inputs"] if value["kind"] == "ci_checks"},
            )
        self.assertEqual(len(paths), 2)

    def test_only_queued_or_completed_cancelled_job_states_can_skip_ci(self):
        for status in ("queued", "completed", "in_progress", "waiting", "pending",
                       "requested", "", None):
            for conclusion in (None, "cancelled", "success", "failure",
                               "action_required", "timed_out"):
                with self.subTest(status=status, conclusion=conclusion):
                    self.commands.clear()
                    self.job.update(status=status, conclusion=conclusion)
                    dispatch.merge_pass_locked(False)
                    skippable = status == "queued" or (
                        status == "completed" and conclusion == "cancelled"
                    )
                    self.assertEqual(self.merged(), skippable)
                    if skippable:
                        self.assertEqual(self.decision()["checks"][1]["bucket"], "skipped-infra")
                    else:
                        waits = [row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                                 if row.get("kind") == "wait"]
                        self.assertEqual(waits[-1]["reason"], "ci_pending")
                    self.escalate.assert_not_called()
                    self.assertFalse(any("--remove-label" in cmd for cmd in self.commands))


if __name__ == "__main__":
    unittest.main()
