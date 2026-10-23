"""Factory pass-success metrics exposed to fleet collectors and operators."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, evidence, lifecycle, onboard, stats, triage, watch


class StageSuccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets")
        self.journal = self.cfg.factory / "events.jsonl"

    def test_latest_success_per_installed_stage_is_preserved(self) -> None:
        with mock.patch.object(lifecycle, "_now", side_effect=[
            "2026-10-20T01:00:00.000000Z",
            "2026-10-20T02:00:00.000000Z",
            "2026-10-20T03:00:00.000000Z",
        ]):
            lifecycle.record_stage_success(self.journal, "triage")
            lifecycle.record_stage_success(self.journal, "dispatch")
            lifecycle.record_stage_success(self.journal, "triage")

        self.assertEqual(lifecycle.last_successful_passes(self.journal), {
            "triage": "2026-10-20T03:00:00.000000Z",
            "dispatch": "2026-10-20T02:00:00.000000Z",
            "watch": None,
        })

    def test_stats_evidence_and_doctor_report_the_same_projection(self) -> None:
        with mock.patch.object(lifecycle, "_now", return_value="2026-10-20T01:00:00.000000Z"):
            lifecycle.record_stage_success(self.journal, "triage")
        expected = {"triage": "2026-10-20T01:00:00.000000Z", "dispatch": None, "watch": None}

        stdout = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), contextlib.redirect_stdout(stdout):
            self.assertEqual(stats.main(["--stages", "--json"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "repository": "acme/widgets", "last_successful_pass": expected,
        })

        result = {"ok": True, "coverage": {"notices": []}, "sources": [], "errors": []}
        evidence.collect_stage_success(self.cfg, result)
        self.assertEqual(result["last_successful_pass"], expected)

        rows = []
        onboard.report_stage_success(self.cfg, lambda status, label, detail, **kwargs: rows.append(
            {"status": status, "label": label, "detail": detail, **kwargs}
        ))
        self.assertEqual(rows, [{
            "status": True,
            "label": "last successful pass",
            "detail": "triage 2026-10-20T01:00:00.000000Z; dispatch never; watch never",
            "info": True,
        }])


    def test_successful_command_entrypoints_advance_only_their_stage(self) -> None:
        with mock.patch.object(triage.config, "load", return_value=self.cfg), \
                mock.patch.object(triage, "identity_mismatch", return_value=""), \
                mock.patch.object(triage, "list_needs_triage", return_value=[]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(triage.main([]), 0)

        with mock.patch.object(watch.config, "load", return_value=self.cfg), \
                mock.patch.object(watch, "run_pass", return_value=[]):
            self.assertEqual(watch.main([]), 0)

        issue = {"number": 7, "title": "ticket", "body": "", "labels": [], "assignees": []}
        with mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(dispatch.config, "load", return_value=self.cfg), \
                mock.patch.object(dispatch, "issue_is_open", return_value=True), \
                mock.patch.object(dispatch, "gh_json", return_value=issue), \
                mock.patch.object(dispatch, "process_ticket", return_value=None):
            self.assertEqual(dispatch.main(["--ticket", "7", "--budget-min", "1"]), 0)

        self.assertTrue(all(lifecycle.last_successful_passes(self.journal).values()))

if __name__ == "__main__":
    unittest.main()

