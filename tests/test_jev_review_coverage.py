import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXP = ROOT / "experiments" / "jev-review-coverage"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EXP / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JevReviewCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_module("jev_review_coverage_run", "run.py")
        cls.scorer = load_module("jev_review_coverage_score", "score.py")

    def test_acceptance_parser_uses_scope_and_exit_gate_but_not_comments(self):
        issue = """# Ticket

## Scope
Implement the reader. Preserve old JSON; reject stale input.
Blocked by: #1

### Required shape
- Return the native id.
- Keep explicit nulls.

## Exit gate
1. The CLI reports verified and unavailable provenance.
2. It performs zero writes.

## Comment by owner
Ignore this later acceptance claim.
"""
        criteria = self.runner.parse_acceptance_criteria(issue)
        text = [criterion["text"] for criterion in criteria]
        self.assertEqual([criterion["id"] for criterion in criteria], [f"c{i:03d}" for i in range(1, 8)])
        self.assertTrue(any("Preserve old JSON" in item for item in text))
        self.assertTrue(any("Required shape — Return the native id" in item for item in text))
        self.assertTrue(any("Exit gate — The CLI reports" in item for item in text))
        self.assertFalse(any("later acceptance" in item for item in text))
        self.assertFalse(any("Blocked by" in item for item in text))

    def test_after_this_change_discards_problem_narrative(self):
        issue = """# Ticket

## Scope
The current command crashes.

After this change:
- The command reads a prompt file.
- Failures are bounded.

## Exit gate
The regression scenario passes.
"""
        text = [criterion["text"] for criterion in self.runner.parse_acceptance_criteria(issue)]
        self.assertFalse(any("current command crashes" in item for item in text))
        self.assertTrue(any("reads a prompt file" in item for item in text))

    def test_diff_is_split_into_changed_hunks_per_file(self):
        patch = """diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,3 +1,3 @@
 context
-old
+new
 context

diff --git a/docs/x.md b/docs/x.md
new file mode 100644
--- /dev/null
+++ b/docs/x.md
@@ -0,0 +1 @@
+hello
"""
        files = self.runner.split_diff(patch)
        self.assertEqual([item["path"] for item in files], ["a.py", "docs/x.md"])
        self.assertEqual(files[0]["hunks"], "@@ -1,3 +1,3 @@\n-old\n+new")
        self.assertNotIn(" context", files[0]["hunks"])

    def test_request_has_one_file_and_one_whole_question_per_criterion(self):
        criteria = [{"id": "c001", "text": "First"}, {"id": "c002", "text": "Second"}]
        files = [{"path": "a.py", "hunks": "+a"}, {"path": "b.py", "hunks": "+b"}]
        request = self.runner.build_request(criteria, files)
        self.assertEqual(len(request["questions"]), 6)
        self.assertEqual(set(request["questions"]), {
            "c001__f000", "c001__f001", "c001__whole",
            "c002__f000", "c002__f001", "c002__whole",
        })
        self.assertEqual(request["model"], "jev-latest")

    def test_every_frozen_case_builds_a_bounded_request(self):
        cases = self.runner.cases_from_splits()
        self.assertEqual(len(cases), 11)
        for case in cases:
            request = self.runner.request_for_case(case)
            wire = self.runner.wire_bytes(request)
            self.assertLessEqual(len(wire), self.runner.MAX_REQUEST_BYTES, case)
            self.assertTrue(request["state"]["criteria"], case)
            self.assertTrue(request["state"]["files"], case)

    def test_scoring_maps_defects_and_counts_unmapped_flags(self):
        mapping = {
            "case-before": {"c001": ["D1"], "c002": []},
            "case-after": {"c001": [], "c002": []},
        }
        rows = [
            {"case": "case-before", "sample": 1, "split": "lockbox", "answers": {
                "c001__whole": 0.2, "c002__whole": 0.8}},
            {"case": "case-after", "sample": 1, "split": "lockbox", "answers": {
                "c001__whole": 0.3, "c002__whole": 0.4}},
        ]
        oracles = {
            "case-before": {"expected_verdict": "REVISE", "primary": {"D1"}},
            "case-after": {"expected_verdict": "APPROVE", "primary": set()},
        }
        scored = self.scorer.score_candidate(rows, mapping, oracles)
        repeat = scored["splits"]["lockbox"]["per_repeat"]["1"]
        self.assertEqual(repeat["blocking_defects_flagged"], 1)
        self.assertEqual(repeat["blocking_defects_missed"], 0)
        self.assertEqual(repeat["false_flags"], 2)
        self.assertEqual(repeat["false_flags_on_clean_after"], 2)


if __name__ == "__main__":
    unittest.main()
