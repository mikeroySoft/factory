from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, stats


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def tally(findings: list[dict], verdict: str) -> str:
    return (
        "```json\n"
        + json.dumps({"verdict": verdict, "findings": findings})
        + "\n```\nVERDICT: "
        + verdict
    )


class ReviewPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "remote", "add", "origin", "https://github.com/acme/widgets.git")
        (self.root / "REVIEW.md").write_text("# Review policy\n\nNit cap: 5\n\nBase policy marker.\n")
        (self.root / "app.py").write_text("value = 1\n")
        git(self.root, "add", "REVIEW.md", "app.py")
        git(self.root, "commit", "-qm", "base")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "REVIEW.md").write_text("# Review policy\n\nNit cap: 8\n\nWorker policy marker.\n")
        (self.root / "app.py").write_text("value = 2\n")
        git(self.root, "add", "REVIEW.md", "app.py")
        git(self.root, "commit", "-qm", "worker")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.prompt = self.root / "prompt.txt"

    def configure(self, output: str) -> None:
        script = (
            "from pathlib import Path; import sys; "
            f"Path({str(self.prompt)!r}).write_text(sys.argv[1]); print({output!r})"
        )
        dispatch.configure(config.Config(
            self.root,
            "acme/widgets",
            reviewer=[sys.executable, "-c", script, "{prompt}"],
            review_policy="REVIEW.md",
        ))

    @staticmethod
    def finding(severity: str, line: int) -> dict:
        return {
            "severity": severity,
            "pass": "bug",
            "path": "app.py",
            "line": line,
            "summary": f"Finding {line}",
        }

    def publish(self) -> dict:
        posted = subprocess.CompletedProcess([], 0, "https://example.invalid/review/1\n", "")
        with mock.patch.object(dispatch, "run", return_value=posted):
            dispatch.pr_comment(7, dispatch._PENDING_REVIEWS[7]["findings"])
        return next(
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "review-result"
        )

    def test_policy_comes_from_base_and_caps_published_nits_but_counts_all(self) -> None:
        self.configure(tally([self.finding("nit", line) for line in range(1, 9)], "APPROVE"))

        verdict, findings = dispatch.review(self.root, 7, "PASS", self.head)
        result = self.publish()

        self.assertEqual(verdict, "APPROVE")
        self.assertIn("Base policy marker.", self.prompt.read_text())
        self.assertNotIn("Worker policy marker.", self.prompt.read_text())
        self.assertEqual(findings.count('"severity": "nit"'), 5)
        self.assertIn("3 additional nit findings omitted by policy cap", findings)
        self.assertEqual(result["counts"], {"important": 0, "nit": 8, "pre_existing": 0})
        self.assertEqual(result["verdict"], "APPROVE")

    def test_important_finding_with_approve_verdict_fails_closed_and_records_reason(self) -> None:
        finding = {
            **self.finding("important", 1),
            "class": "missing-value",
            "instances": [{
                "path": "app.py", "line": 1, "summary": "Finding 1",
            }],
        }
        self.configure(tally([finding], "APPROVE"))

        verdict, _ = dispatch.review(self.root, 7, "PASS", self.head)
        result = self.publish()

        self.assertEqual(verdict, "REVISE")
        self.assertEqual(result["verdict"], "REQUEST_CHANGES")
        self.assertEqual(result["reason"], "verdict_counts_mismatch")
        self.assertEqual(result["counts"], {"important": 1, "nit": 0, "pre_existing": 0})

    def test_ticket_review_result_does_not_enter_external_lane(self) -> None:
        self.configure(tally([], "APPROVE"))

        dispatch.review(self.root, 7, "PASS", self.head)

        self.assertNotIn("pr", self.publish())

    def test_verdict_counts_mismatch_returns_revise(self) -> None:
        _findings, verdict, parsed, counts, reason = dispatch._parse_review_output(
            tally([self.finding("nit", 1)], "REVISE"), 5
        )

        self.assertEqual(verdict, "REVISE")
        self.assertTrue(parsed)
        self.assertEqual(counts, {"important": 0, "nit": 1, "pre_existing": 0})
        self.assertEqual(reason, "verdict_counts_mismatch")

    def test_missing_tally_without_policy_fails_closed(self) -> None:
        git(self.root, "rm", "REVIEW.md")
        git(self.root, "commit", "-qm", "base without policy")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "app.py").write_text("value = 3\n")
        git(self.root, "add", "app.py")
        git(self.root, "commit", "-qm", "worker without policy")
        self.configure("VERDICT: APPROVE")

        verdict, findings = dispatch.review(
            self.root, 7, "PASS", git(self.root, "rev-parse", "HEAD")
        )

        self.assertEqual(verdict, "REVISE")
        self.assertIn("unparsed_verdict", findings)

    def test_missing_or_invalid_tally_fails_closed(self) -> None:
        for output in ("VERDICT: APPROVE", "```json\nnot json\n```\nVERDICT: APPROVE"):
            with self.subTest(output=output):
                self.configure(output)
                verdict, findings = dispatch.review(self.root, 7, "PASS", self.head)
                self.assertEqual(verdict, "REVISE")
                self.assertIn("unparsed_verdict", findings)

    def test_policy_path_is_configurable(self) -> None:
        (self.root / config.CONFIG_NAME).write_text('[review]\npolicy = "docs/review-policy.md"\n')
        loaded = config.load(self.root)
        self.assertEqual(loaded.review_policy, "docs/review-policy.md")


class ReviewStatsTest(unittest.TestCase):
    def test_review_counts_are_exposed_per_round(self) -> None:
        rounds = stats.review_findings([
            {"event": "review-result", "counts": {"important": 0, "nit": 8, "pre_existing": 0}},
            {"event": "review-result", "counts": {"important": 1, "nit": 0, "pre_existing": 2}},
        ])
        row = {
            "ticket": 7,
            "title": "t",
            "state": "merged",
            "attempts": 1,
            "review_rounds": 2,
            "review_findings": rounds,
            "merge_hours": None,
            "escalation_count": 0,
            "manager_failures": 0,
            "resolutions": [],
            "ready_for_human_minutes": 0.0,
            "requeue_count": 0,
            "llm_usage": {},
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stats.print_table([row])
        self.assertEqual(rounds, [
            {"important": 0, "nit": 8, "pre_existing": 0},
            {"important": 1, "nit": 0, "pre_existing": 2},
        ])
        self.assertIn("review findings I/N/P", out.getvalue())
        self.assertIn("0/8/0; 1/0/2", out.getvalue())


if __name__ == "__main__":
    unittest.main()
