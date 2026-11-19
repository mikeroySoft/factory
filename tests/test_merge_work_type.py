"""Merge receipts retain explicit work labels rather than inferring from prose."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle


class MergeWorkTypeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets", signoff=False)
        for name, value in (("cfg", self.cfg), ("ROOT", self.root),
                            ("FACTORY", self.cfg.factory), ("REPO", self.cfg.repo),
                            ("EVENTS", self.cfg.factory / "events.jsonl"), ("UPSTREAM", None)):
            self.enterContext(mock.patch.object(dispatch, name, value, create=True))
        self.enterContext(mock.patch.dict("os.environ", {lifecycle.CONTEXT_ENV: ""}))

    def test_exact_pr_labels_then_ticket_fallback_conflicts_and_read_failure(self):
        for labels, expected in ((["speed"], "speed"), (["bug"], "bug"),
                                 (["polish"], "polish"), (["bug", "speed"], "unclassified"),
                                 (["performance", "chore"], "unclassified")):
            with self.subTest(labels=labels), mock.patch.object(
                dispatch, "gh_json", return_value={"labels": [{"name": name} for name in labels]},
            ) as gh:
                result = dispatch.merge_work_type([], 7)
                self.assertEqual(result["work_type"], expected)
                self.assertEqual(result["work_type_labels"], sorted(set(labels) & {"speed", "bug", "polish"}))
                self.assertEqual(result["work_type_source"], "ticket:7")
                gh.assert_called_once()
        with mock.patch.object(dispatch, "gh_json") as gh:
            self.assertEqual(dispatch.merge_work_type([{"name": "polish"}], 7), {
                "work_type": "polish", "work_type_source": "pr", "work_type_labels": ["polish"],
            })
            gh.assert_not_called()
            self.assertEqual(dispatch.merge_work_type([], None)["work_type"], "unclassified")
        with mock.patch.object(dispatch, "gh_json", side_effect=OSError("offline")):
            result = dispatch.merge_work_type([], 7)
            self.assertEqual(result["work_type"], "unclassified")
            self.assertEqual(result["work_type_source"], "ticket:7:unavailable")

    def test_ticket_merge_records_issue_label_when_pr_has_only_pipeline_labels(self):
        head = "a" * 40
        pr = {"number": 9, "headRefName": "agent/7", "headRefOid": head,
              "baseRefName": "main", "isDraft": False, "reviewDecision": "APPROVED",
              "state": "OPEN", "title": "No work type in this title",
              "labels": [{"name": dispatch.FACTORY_APPROVED}], "files": []}
        lifecycle.append(dispatch.EVENTS, {"event": "approved", "ticket": 7, "pr": 9,
                                          "head": head, "gate_head": head, "review_head": head})

        def gh(args):
            if args[:2] == ["pr", "list"]:
                return [pr]
            if args[:2] == ["issue", "view"]:
                return {"labels": [{"name": "bug"}]}
            if args[:2] == ["pr", "view"]:
                return {"mergeCommit": {"oid": "b" * 40}} if args[-1] == "mergeCommit" else pr
            if args[0] == "api":
                return {"behind_by": 0}
            self.fail(f"unexpected GitHub call {args}")

        with mock.patch.object(dispatch, "gh_json", side_effect=gh), \
             mock.patch.object(learn, "promote_pass"), \
             mock.patch.object(dispatch, "initiative_kind", return_value=False), \
             mock.patch.object(dispatch, "_head_evidence_matches", return_value=True), \
             mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
             mock.patch.object(dispatch, "triage_cancelled", side_effect=lambda n, checks, *a: checks), \
             mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
             mock.patch.object(dispatch, "cleanup_after_merge"), \
             mock.patch.object(dispatch, "record_merge_decision"):
            dispatch.merge_pass_locked(False)
        merged = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "merged"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["work_type"], "bug")
        self.assertEqual(merged[0]["work_type_source"], "ticket:7")

    def test_promoted_and_human_merged_prs_record_the_pr_label(self):
        head, branch = "c" * 40, "agent/lessons-2026-11-19-t7"
        pr = {"number": 9, "headRefName": branch, "headRefOid": head,
              "labels": [{"name": "speed"}]}
        events = [{"event": "review-result", "pr": 9, "head": head, "verdict": "APPROVE"},
                  {"event": "gate", "branch": branch, "head": head, "gate": "PASS"}]
        with mock.patch.object(dispatch, "gh_json", return_value={"behind_by": 0}), \
             mock.patch.object(dispatch, "pr_checks", return_value=[{"bucket": "pass"}]), \
             mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
             mock.patch.object(learn, "_open_promotion"):
            learn.promote(self.cfg, pr, events, False)
        with mock.patch.object(dispatch, "gh_json", return_value={
            **pr, "number": 10, "state": "MERGED", "mergedBy": {"login": "owner"},
            "labels": [{"name": "polish"}],
        }), mock.patch.object(learn, "_open_promotion"):
            learn._reconcile_human_merges(self.cfg, set(), [
                {"event": "review-intake", "pr": 10, "head": head, "branch": branch},
            ], False)
        merged = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "merged"]
        self.assertEqual([(row["pr"], row["work_type"]) for row in merged], [(9, "speed"), (10, "polish")])
        self.assertEqual([row["work_type_source"] for row in merged], ["pr", "pr"])
        json.dumps(merged)


if __name__ == "__main__":
    unittest.main()
