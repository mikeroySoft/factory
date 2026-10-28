from __future__ import annotations

import copy
import json
import os
import subprocess
import unittest
from unittest import mock

from factory import config, dispatch

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
        with mock.patch.object(dispatch, "gh_json", side_effect=AssertionError("unexpected API")):
            with self.assertRaisesRegex(AssertionError, "unexpected API"):
                dispatch._certificate_checks(self.fixture.head, [{"name": "test", "bucket": "pass"}])


if __name__ == "__main__":
    unittest.main()
