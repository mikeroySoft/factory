"""Reviewer failures are mechanism errors, not product change requests."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle
from factory.domain import ReviewVerdict


class ReviewerEnumsTest(unittest.TestCase):
    def test_reviewer_crash_produces_error_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dispatch.configure(config.Config(root=root, repo="acme/widgets", reviewer=["reviewer"]))
            failed = subprocess.CompletedProcess(["reviewer"], 1, "", "provider crashed")
            def git_result(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, "head\n" if command[1] == "rev-parse" else "", "")
            with patch.object(dispatch, "run", side_effect=git_result), \
                    patch.object(dispatch, "_review_policy", return_value=(None, 5)), \
                    patch.object(dispatch, "_run_reviewer", return_value=failed), \
                    patch.object(dispatch.time, "sleep"), \
                    patch.object(dispatch, "gh_json", return_value={"title": "Fix defect", "body": ""}), \
                    patch("factory.binding.accepted", return_value=None):
                verdict, findings = dispatch.review(root, 7, "PASS", "head")
            self.assertIs(verdict, ReviewVerdict.ERROR)
            self.assertIn("provider crashed", findings)
            rows = lifecycle.read_events(dispatch.EVENTS)
            review = next(row for row in rows if row["event"] == "review")
            self.assertIs(review["verdict"], ReviewVerdict.ERROR)
            self.assertFalse(review["accepted"])
            dispatch._record_review_decision(7, verdict, findings, "head", False, False, None, 0)
            result = lifecycle.read_events(dispatch.EVENTS)[-1]
            self.assertEqual(result["event"], "review-result")
            self.assertIs(result["verdict"], ReviewVerdict.ERROR)

    def test_unparseable_review_is_error_not_revise(self):
        _, verdict, parsed, _, reason = dispatch._parse_review_output("provider unavailable", 5)
        self.assertIs(verdict, ReviewVerdict.ERROR)
        self.assertFalse(parsed)
        self.assertEqual(reason, "unparsed_verdict")

    def test_inconsistent_review_is_error_not_revise(self):
        text = '```json\n{"verdict":"REVISE","findings":[]}\n```\nVERDICT: REVISE'
        _, verdict, _, counts, reason = dispatch._parse_review_output(text, 5)
        self.assertIs(verdict, ReviewVerdict.ERROR)
        self.assertEqual(counts["important"], 0)
        self.assertEqual(reason, "verdict_counts_mismatch")

    def test_valid_product_feedback_remains_revise(self):
        text = ('```json\n{"verdict":"REVISE","findings":[{"severity":"important",'
                '"pass":"bug","path":"factory/dispatch.py","line":1,"summary":"Defect"}]}\n```\n'
                'VERDICT: REVISE')
        _, verdict, parsed, counts, reason = dispatch._parse_review_output(text, 5)
        self.assertIs(verdict, ReviewVerdict.REVISE)
        self.assertTrue(parsed)
        self.assertEqual(counts["important"], 1)
        self.assertIsNone(reason)
