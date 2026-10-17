"""The escalation-reason classifier recovers legacy codes, folds re-fires and tags by evidence."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "escalation-reasons" / "classify.py"
spec = importlib.util.spec_from_file_location("escalation_classify", SCRIPT)
classify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classify)


class ClassifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "escalations").mkdir()
        (self.root / "logs").mkdir()

    def journal(self, rows):
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps({"event": "escalate", **r}) + "\n" for r in rows))
        return path

    def test_legacy_reason_recovers_code_and_refires_fold(self) -> None:
        conflict = "PR #32: rebase onto moved main conflicts; worktree /x"
        rows = classify.escalations(self.journal([
            {"ticket": 13, "at": "2026-09-06T02:10:21Z", "reason": conflict},
            {"ticket": 13, "at": "2026-09-06T02:21:00Z", "reason": conflict},
            {"ticket": 13, "at": "2026-09-06T05:00:00Z", "reason": conflict},
            {"ticket": 7, "at": "2026-09-06T03:00:00Z", "reason": "worker edited protected paths"},
            {"ticket": 9, "at": "2026-09-06T04:00:00Z", "reason": "x", "code": "budget"},
        ]))
        self.assertEqual([(r["ticket"], r["code"], r["repeats"]) for r in rows],
                         [(13, "refresh_conflict", 2), (7, "protected_paths", 1),
                          (9, "budget", 1), (13, "refresh_conflict", 1)])

    def test_tags_follow_code_log_and_review_evidence(self) -> None:
        owner = "# Escalation\n\n## Latest review findings\n\n## Required fixes\n\n1. This is an owner decision, not a code change.\n\n## Handoff\n\nnone\n"
        defect = owner.replace("This is an owner decision, not a code change.", "`foo.py:3` returns the wrong key.")
        gate = "## Last gate report\n\n- test: FAIL\n\nCould not find platform independent libraries\n\n## Latest review findings\n\nnone\n\n## Handoff\n\nnone\n"
        self.assertEqual(classify.classify("protected_paths", "", ""), "requirement")
        self.assertEqual(classify.classify("ci_failed", "", ""), "eval")
        self.assertEqual(classify.classify("refresh_conflict", "", ""), "environment")
        self.assertEqual(classify.classify("review_revise", owner, ""), "requirement")
        self.assertEqual(classify.classify("review_revise", defect, ""), "model")
        self.assertEqual(classify.classify("gate_failed", "", "Error: Authentication failed"), "environment")
        self.assertEqual(classify.classify("gate_failed", gate, ""), "environment")
        self.assertEqual(classify.classify("gate_failed", "## Last gate report\n\n- protected-paths: FAIL\n\n## Latest review findings\n", ""), "requirement")
        self.assertEqual(classify.classify("gate_failed", "## Last gate report\n\n- test: FAIL\n\n## Latest review findings\n", ""), "model")
        self.assertEqual(classify.classify("pr_unpublished", "", "May I use `gh api` instead?"), "requirement")


if __name__ == "__main__":
    unittest.main()
