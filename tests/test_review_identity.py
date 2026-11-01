"""Finding identity must survive partial fixes and conflicting duplicate severities."""
from __future__ import annotations

import json
import unittest

from factory import dispatch
from tests import test_review_convergence as convergence
from tests.test_review_convergence import finding, tally


class ReviewIdentityTest(unittest.TestCase):
    setUp = convergence.ReviewConvergenceTest.setUp
    configure = convergence.ReviewConvergenceTest.configure

    def test_changed_instances_keep_one_still_open_finding_without_id(self) -> None:
        original = finding(finding_id=None)
        original["instances"].append({"path": "other.py", "line": 2, "summary": "Other occurrence."})
        reduced = finding(finding_id=None)
        expanded = finding(finding_id=None)
        expanded["instances"].append({"path": "new.py", "line": 3, "summary": "New occurrence."})
        statuses = [{"id": "F1", "status": "still-open"}]
        self.configure([
            tally("REVISE", [original]),
            tally("REVISE", [reduced], statuses),
            tally("REVISE", [expanded], statuses),
        ])

        first_verdict, first = dispatch.review(self.root, 7, "PASS", self.head)
        self.assertEqual(first_verdict, "REVISE")
        for expected_instance in ("app.py", "new.py"):
            verdict, rendered = dispatch.review(self.root, 7, "PASS", self.head)
            self.assertEqual(verdict, "REVISE")
            [row] = dispatch._review_tally(rendered)["findings"]
            self.assertEqual((row.get("id"), row.get("status")), ("F1", "still-open"))
            bounce = dispatch._worker_review_findings(first, rendered)
            self.assertEqual(bounce.count("[F1 still-open]"), 1)
            self.assertEqual(bounce.count("Value is wrong."), 1)
            self.assertIn(expected_instance, bounce)
        ledger = json.loads((dispatch.FACTORY / "review-ledger-7.json").read_text())
        self.assertEqual([row["id"] for row in ledger["findings"]], ["F1"])

    def test_conflicting_duplicate_severities_cannot_suppress_important(self) -> None:
        important = finding(finding_id=None)
        for optional in ("nit", "pre-existing"):
            for important_first in (False, True):
                with self.subTest(optional=optional, important_first=important_first):
                    rows = [finding(severity=optional, finding_id=None), important]
                    if important_first:
                        rows.reverse()
                    self.configure([tally("REVISE", rows)])
                    (dispatch.FACTORY / "review-ledger-7.json").unlink(missing_ok=True)

                    verdict, rendered = dispatch.review(self.root, 7, "PASS", self.head)

                    self.assertEqual(verdict, "REVISE")
                    [row] = dispatch._review_tally(rendered)["findings"]
                    self.assertEqual(row["severity"], "important")
                    bounce = dispatch._worker_review_findings(rendered)
                    self.assertEqual(bounce.count("Value is wrong."), 1)
                    self.assertEqual(dispatch._required_fixes(rendered, ""), [{
                        "kind": "finding", "ref": "app.py:1", "summary": "Value is wrong.",
                    }])


if __name__ == "__main__":
    unittest.main()
