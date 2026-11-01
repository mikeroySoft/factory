from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, stats


def review_output(findings: list[dict], verdict: str = "REVISE") -> str:
    return (
        "Reviewer prose.\n\n"
        "## Required fixes\n"
        "- **Rules broken:** prose must not become a fix.\n"
        "- `other.py:9`: prose must also be ignored.\n\n"
        "```json\n"
        + json.dumps({"verdict": verdict, "findings": findings})
        + f"\n```\nVERDICT: {verdict}"
    )


def row(severity: str, path: str, line: int, summary: str) -> dict:
    return {
        "severity": severity,
        "pass": "bug",
        "class": "review-class",
        "instances": [{"path": path, "line": line, "summary": summary}],
        "path": path,
        "line": line,
        "summary": summary,
    }


class FindingSelectionTest(unittest.TestCase):
    def test_required_fixes_use_only_important_json_tally(self) -> None:
        findings = review_output([
            row("important", "factory/a.py", 4, "Fix the defect."),
            row("nit", "factory/a.py", 5, "Rename this."),
            row("pre-existing", "legacy.py", 3, "Old defect."),
        ])

        self.assertEqual(dispatch._required_fixes(findings, "https://review.example/1"), [{
            "kind": "finding",
            "ref": "factory/a.py:4",
            "summary": "Fix the defect.",
        }])
        self.assertEqual(
            dispatch._required_fixes(
                "## Required fixes\n- `other.py:9`: prose must be ignored.\n"
                "VERDICT: REVISE",
                "https://review.example/1",
            ),
            [],
        )


    def test_worker_bounce_contains_only_important_findings(self) -> None:
        findings = review_output([
            row("important", "factory/a.py", 4, "Fix the defect."),
            row("nit", "factory/a.py", 5, "Rename this."),
            row("pre-existing", "legacy.py", 3, "Old defect."),
        ])

        bounce = dispatch._worker_review_findings(findings)

        self.assertIn("Fix the defect.", bounce)
        self.assertNotIn("Rename this.", bounce)
        self.assertNotIn("Old defect.", bounce)

    def test_pre_existing_only_approve_is_nonblocking(self) -> None:
        findings = review_output([
            row("pre-existing", "legacy.py", 3, "Old defect."),
        ], verdict="APPROVE")

        _, verdict, parsed, counts, reason = dispatch._parse_review_output(findings, 5)

        self.assertEqual((verdict, parsed, reason), ("APPROVE", True, None))
        self.assertEqual(counts, {"important": 0, "nit": 0, "pre_existing": 1})
        self.assertEqual(dispatch._required_fixes(findings, "https://review.example/1"), [])
        self.assertNotIn("Old defect.", dispatch._worker_review_findings(findings))


    def test_learn_counts_only_structured_important_findings(self) -> None:
        self.assertEqual(learn._finding_count({"important": 2, "nit": 9, "pre_existing": 4}), 2)
        self.assertEqual(learn._finding_count(None), 1)
        self.assertEqual(
            learn._finding_count(
                "## Required fixes\n- first prose finding\n- second prose finding\n"
                "VERDICT: REVISE"
            ),
            1,
        )


class ReviewFollowUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dispatch.configure(config.Config(
            self.root,
            "acme/widgets",
            review_follow_up=True,
        ))
        dispatch.FACTORY.mkdir()
        (dispatch.FACTORY / "review-ledger-7.json").write_text(json.dumps({
            "version": 1,
            "ticket": 7,
            "base": "b" * 40,
            "last_reviewed_head": "h" * 40,
            "repeated_dropped": 0,
            "findings": [{
                "id": "F1",
                "head": "h" * 40,
                "class": "legacy-defect",
                "instances": [{"path": "legacy.py", "line": 4, "summary": "Old defect."}],
                "severity": "pre-existing",
                "pass": "bug",
                "path": "legacy.py",
                "line": 4,
                "summary": "Old defect.",
                "status": "open",
            }],
        }))

    def test_pre_existing_findings_file_one_linked_needs_triage_issue(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "https://github.com/acme/widgets/issues/8\n", "")
        with mock.patch.object(dispatch, "run", return_value=completed) as run:
            dispatch._review_follow_up(7, 70)
            dispatch._review_follow_up(7, 70)

        [call] = run.call_args_list
        command = call.args[0]
        self.assertEqual(command[:3], ["gh", "issue", "create"])
        self.assertEqual(command[command.index("--label") + 1], "needs-triage")
        body = command[command.index("--body") + 1]
        self.assertIn("https://github.com/acme/widgets/pull/70", body)
        self.assertIn("legacy.py:4", body)
        follow_ups = [
            event for event in lifecycle.read_events(dispatch.EVENTS)
            if event.get("event") == "review-follow-up"
        ]
        self.assertEqual(len(follow_ups), 1)


class ReviewConfigurationTest(unittest.TestCase):
    def test_review_flags_are_off_by_default_and_load_from_config(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        (root / ".factory.toml").write_text("[repo]\nslug = 'acme/widgets'\n[review]\nverify = true\nfollow_up = true\n")

        loaded = config.load(root)

        self.assertTrue(loaded.review_verify)
        self.assertTrue(loaded.review_follow_up)
        defaults = config.Config(root, "acme/widgets")
        self.assertFalse(defaults.review_verify)
        self.assertFalse(defaults.review_follow_up)


class ReviewStatsOutputTest(unittest.TestCase):
    def test_ticket_table_prints_convergence_metrics(self) -> None:
        row_data = {
            "ticket": 7,
            "title": "t",
            "state": "merged",
            "attempts": 1,
            "review_rounds": 3,
            "rounds_to_pass": 3,
            "repeated_findings_dropped": 2,
            "important_findings_per_round": [2, 1, 0],
            "review_findings": [],
            "merge_hours": None,
            "escalation_count": 0,
            "manager_failures": 0,
            "resolutions": [],
            "ready_for_human_minutes": 0.0,
            "requeue_count": 0,
            "llm_usage": {},
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stats.print_table([row_data])
        rendered = output.getvalue()
        self.assertIn("rounds to pass", rendered)
        self.assertIn("repeats dropped", rendered)
        self.assertIn("important/round", rendered)
        self.assertIn("2,1,0", rendered)


if __name__ == "__main__":
    unittest.main()
