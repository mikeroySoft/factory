from __future__ import annotations

import copy
import json
import os
import subprocess
import unittest
from unittest import mock

from factory import certcheck, config, dispatch

try:
    import test_certcheck_regression as fixtures
except ModuleNotFoundError:
    from tests import test_certcheck_regression as fixtures


class CertificateEvidenceRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.MergeCertificateRegressionTest(
            "test_approval_head_tamper_fails_and_unjournaled_main_commit_is_uncertified",
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        patcher = mock.patch.dict(os.environ, self.fixture.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        dispatch.configure(config.Config(self.fixture.root, "acme/widgets", signoff=False))

    def complete_events(self) -> list[dict]:
        rows = copy.deepcopy(self.fixture.events[:-1])
        gate = next(row for row in rows if row.get("event") == "attempt")
        decision = next(row for row in rows if row.get("stage") == "gate")
        decision["head"] = self.fixture.head
        gate.update(
            gate_decision_id=decision["decision_id"], report_path=decision["report_path"],
            report_sha256=decision["report_sha256"],
        )
        return rows

    def test_missing_certificate_metadata_requires_new_evidence(self) -> None:
        rows = self.complete_events()
        self.assertFalse(dispatch._certificate_evidence_needs_refresh(rows, 7, self.fixture.head))
        for stage, field in (
            ("gate", "head"), ("gate", "report_sha256"), ("review", "head"),
        ):
            with self.subTest(stage=stage, field=field):
                incomplete = copy.deepcopy(rows)
                next(row for row in incomplete if row.get("stage") == stage).pop(field)
                self.assertTrue(
                    dispatch._certificate_evidence_needs_refresh(incomplete, 7, self.fixture.head),
                )
        no_decisions = [row for row in rows if row.get("event") != "decision"]
        self.assertTrue(dispatch._certificate_evidence_needs_refresh(no_decisions, 7, self.fixture.head))

    def test_merge_refreshes_predeployment_evidence_instead_of_fabricating_decisions(self) -> None:
        pr = {
            "number": 70, "headRefName": "agent/7", "headRefOid": self.fixture.head,
            "baseRefName": "main", "isDraft": False, "state": "OPEN",
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
        }

        def gh_json(args):
            if args[:2] == ["pr", "list"]:
                return [pr]
            if args[:2] == ["pr", "view"]:
                return pr
            if args[:2] == ["issue", "view"]:
                return {"labels": []}
            if args[0] == "api":
                return {"behind_by": 0}
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=gh_json), \
                mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "test", "bucket": "pass"}]), \
                mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(dispatch, "refresh_pr_branch", return_value=True) as refresh, \
                mock.patch.object(
                    dispatch, "write_merge_certificate",
                    side_effect=AssertionError("incomplete evidence reached certificate writer"),
                ) as write, \
                mock.patch.object(dispatch, "cleanup_after_merge"):
            dispatch.merge_pass_locked(False)
        refresh.assert_called_once_with(7, 70, False)
        write.assert_not_called()

    def test_missing_report_decision_does_not_hash_display_text_as_evidence(self) -> None:
        report = self.fixture.root / ".factory" / "missing.md"
        dispatch._record_gate_decision(7, False, "- test: FAIL\n", self.fixture.head, str(report), 0)
        rows = json.loads(dispatch.EVENTS.read_text().splitlines()[-1])
        self.assertIsNone(rows["report_sha256"])
        self.assertFalse(report.exists())

    def test_programming_errors_in_github_client_are_not_swallowed(self) -> None:
        with mock.patch.object(dispatch, "gh_json", side_effect=AssertionError("unexpected API")), \
                self.assertRaisesRegex(AssertionError, "unexpected API"):
            dispatch._certificate_checks(self.fixture.head, [{"name": "test", "bucket": "pass"}])

    def test_legacy_approval_earns_fresh_gate_review_and_approval_that_verify_after_cleanup(self) -> None:
        self.fixture.git("update-ref", "refs/remotes/origin/main", self.fixture.base)
        self.fixture.git("update-ref", "refs/remotes/origin/agent/7", self.fixture.head)
        old_rows = certcheck.read_journal(dispatch.EVENTS)
        self.assertTrue(dispatch._certificate_evidence_needs_refresh(old_rows, 7, self.fixture.head))
        pr = {
            "number": 70, "headRefName": "agent/7", "headRefOid": self.fixture.head,
            "baseRefName": "main", "state": "OPEN", "reviewDecision": "APPROVED",
        }
        real_run = dispatch.run
        gates = []

        def run(cmd, **kwargs):
            if cmd[:3] == ["git", "fetch", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[:6] == [dispatch.sys.executable, "-P", "-E", "-m", "factory", "gate"]:
                report = kwargs["cwd"] / cmd[cmd.index("--report") + 1]
                report.write_bytes(b"# Gate\r\n- test: PASS\r\n")
                gates.append(report)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "gh":
                return subprocess.CompletedProcess(
                    cmd, 0, "https://github.com/acme/widgets/pull/70#issuecomment-2\n", "",
                )
            return real_run(cmd, **kwargs)

        reviewer = {
            "verdict": "APPROVE", "findings": "VERDICT: APPROVE", "accepted": True,
            "parsed": True, "actual_head": self.fixture.head, "usage": None,
            "counts": None, "reason": "approved", "rejection_reason": None,
            "returncode": 0,
        }
        with mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "push_agent", return_value=self.fixture.head), \
                mock.patch.object(dispatch, "review_prompt", return_value="Review #7"), \
                mock.patch.object(dispatch, "run_reviewer", return_value=reviewer) as review, \
                mock.patch.object(dispatch, "retain_handoff", return_value=True):
            self.assertTrue(dispatch.refresh_pr_branch(7, 70, False))
        self.assertEqual(len(gates), 1)
        review.assert_called_once()
        rows = certcheck.read_journal(dispatch.EVENTS)
        self.assertFalse(dispatch._certificate_evidence_needs_refresh(rows, 7, self.fixture.head))
        self.assertEqual(rows[:len(old_rows)], old_rows)
        document = json.loads(self.fixture.certificate.read_bytes())
        with mock.patch.object(dispatch, "_certificate_checks", return_value=document["premises"]["ci"]["checks"]):
            path, document = dispatch.write_merge_certificate(
                7, 70, self.fixture.head, pr, "squash", [], rows,
            )
        digest = dispatch.finalize_merge_certificate(path, document, self.fixture.merge_sha)
        dispatch.record(
            "merged", ticket=7, pr=70, head=self.fixture.head, method="squash",
            merge_sha=self.fixture.merge_sha, certificate_sha256=digest,
            certificate=str(path.relative_to(self.fixture.root)),
        )
        with mock.patch.object(dispatch, "retain_handoff", return_value=True):
            dispatch.cleanup_after_merge(7, self.fixture.head)
        self.assertFalse(gates[0].exists())
        result = self.fixture.factory("verify-merge", "--certificate", str(path), "--json")
        self.assertEqual(json.loads(result.stdout)["status"], "certified")

    def merge_with_complete_evidence(self, mode: str, checker_code: int) -> tuple[list, mock.Mock]:
        dispatch.cfg.merge_certify = mode
        self.fixture.events[:] = self.complete_events()
        self.fixture.write_events()
        self.fixture.git("update-ref", "refs/remotes/origin/main", self.fixture.base)
        worktree_stub = self.fixture.root / ".factory" / "wt-7" / "factory" / "certcheck.py"
        worktree_stub.parent.mkdir(parents=True)
        worktree_stub.write_text("raise SystemExit(0)\n")
        pr = {
            "number": 70, "headRefName": "agent/7", "headRefOid": self.fixture.head,
            "baseRefName": "main", "isDraft": False, "state": "OPEN",
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
            "mergeCommit": {"oid": self.fixture.merge_sha},
            "title": "feature",
        }

        def gh_json(args):
            if args[:2] == ["pr", "list"]:
                return [pr]
            if args[:2] == ["pr", "view"]:
                return pr
            if args[:2] == ["issue", "view"]:
                return {"labels": []}
            if args[0] == "api" and "/compare/" in args[1]:
                return {"behind_by": 0}
            if args[0] == "api" and "/check-runs" in args[1]:
                return {"check_runs": [
                    {"id": 101, "name": "test", "status": "completed", "conclusion": "success"},
                ]}
            raise AssertionError(args)

        calls = []
        real_run = dispatch.run

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
            if "verify-merge" in cmd:
                return subprocess.CompletedProcess(cmd, checker_code, "failed:gate\n", "")
            if cmd[0] == "gh" or cmd[:3] == ["git", "fetch", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, **kwargs)

        with mock.patch.object(dispatch, "gh_json", side_effect=gh_json), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "test", "bucket": "pass"}]), \
                mock.patch.object(dispatch, "cleanup_after_merge"), \
                mock.patch.object(dispatch, "escalate") as escalate:
            dispatch.merge_pass_locked(False)
        return calls, escalate

    def test_enforce_stops_merge_with_complete_evidence_using_installed_checker(self) -> None:
        calls, escalate = self.merge_with_complete_evidence("enforce", 1)
        checker, cwd = next((cmd, cwd) for cmd, cwd in calls if "verify-merge" in cmd)
        self.assertEqual(checker[:6], [dispatch.sys.executable, "-P", "-E", "-m", "factory", "verify-merge"])
        self.assertEqual(cwd, self.fixture.root)
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd, _ in calls))
        self.assertTrue(any("--remove-label" in cmd for cmd, _ in calls))
        self.assertEqual(escalate.call_args.kwargs["code"], "certificate_failed")

    def test_audit_merges_complete_evidence_without_running_checker(self) -> None:
        calls, escalate = self.merge_with_complete_evidence("audit", 1)
        self.assertFalse(any("verify-merge" in cmd for cmd, _ in calls))
        self.assertTrue(any(cmd[:3] == ["gh", "pr", "merge"] for cmd, _ in calls))
        escalate.assert_not_called()
        verdict = self.fixture.factory(
            "verify-merge", "--certificate", str(self.fixture.certificate), "--json",
        )
        self.assertEqual(json.loads(verdict.stdout)["status"], "certified")

    def test_incomplete_metadata_waits_for_final_ci_and_human_veto_before_refresh(self) -> None:
        pr = {
            "number": 70, "headRefName": "agent/7", "headRefOid": self.fixture.head,
            "baseRefName": "main", "isDraft": False, "state": "OPEN",
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
        }
        passing = [{"name": "test", "bucket": "pass"}]
        for condition in ("pending", "human-veto"):
            with self.subTest(condition=condition):
                views = [pr, {**pr, "reviewDecision": "CHANGES_REQUESTED"}]

                def gh_json(args):
                    if args[:2] == ["pr", "list"]:
                        return [pr]
                    if args[:2] == ["pr", "view"]:
                        return views.pop(0)
                    if args[:2] == ["issue", "view"]:
                        return {"labels": []}
                    if args[0] == "api" and "/compare/" in args[1]:
                        return {"behind_by": 0}
                    raise AssertionError(args)

                final_checks = [{"name": "test", "bucket": "pending"}] if condition == "pending" else passing
                with mock.patch.object(dispatch, "gh_json", side_effect=gh_json), \
                        mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                        mock.patch.object(dispatch, "pr_checks", side_effect=[passing, final_checks]), \
                        mock.patch.object(dispatch, "refresh_pr_branch") as refresh, \
                        mock.patch.object(dispatch, "write_merge_certificate") as write:
                    dispatch.merge_pass_locked(False)
                refresh.assert_not_called()
                write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
