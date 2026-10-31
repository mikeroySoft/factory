from __future__ import annotations

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


def finding(*, severity: str = "important", finding_id: str | None = "F1") -> dict:
    row = {
        "severity": severity,
        "pass": "bug",
        "class": "wrong-value",
        "instances": [{"path": "app.py", "line": 1, "summary": "Value is wrong."}],
        "path": "app.py",
        "line": 1,
        "summary": "Value is wrong.",
    }
    if finding_id is not None:
        row["id"] = finding_id
    return row


def tally(verdict: str, findings: list[dict], ledger: list[dict] | None = None) -> str:
    body = {"verdict": verdict, "findings": findings}
    if ledger is not None:
        body["ledger"] = ledger
    return f"```json\n{json.dumps(body)}\n```\nVERDICT: {verdict}"


class ReviewConvergenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "app.py").write_text("value = 0\n")
        git(self.root, "add", "app.py")
        git(self.root, "commit", "-qm", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        git(self.root, "update-ref", "refs/remotes/origin/main", self.base)
        (self.root / "app.py").write_text("value = 1\n")
        git(self.root, "commit", "-qam", "first head")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.state = self.root / "reviewer-state.json"

    def configure(self, outputs: list[str], *, verify: bool = False) -> None:
        self.state.write_text(json.dumps({"outputs": outputs, "seen": []}))
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"state = Path({str(self.state)!r})\n"
            "data = json.loads(state.read_text())\n"
            "data['seen'].append({"
            "'prompt': sys.argv[1], "
            "'diff': Path('review.diff').read_text(), "
            "'full': Path('review-full.diff').read_text() if Path('review-full.diff').exists() else None})\n"
            "index = len(data['seen']) - 1\n"
            "state.write_text(json.dumps(data))\n"
            "print(data['outputs'][index])\n"
        )
        dispatch.configure(config.Config(
            self.root,
            "acme/widgets",
            reviewer=[sys.executable, "-c", script, "{prompt}"],
            review_verify=verify,
        ))
        dispatch.FACTORY.mkdir(exist_ok=True)

    def test_rereview_uses_incremental_diff_and_base_move_falls_back_to_full(self) -> None:
        repeated = finding(finding_id=None)
        self.configure([
            tally("REVISE", [finding()]),
            tally("REVISE", [finding(), repeated], [{"id": "F1", "status": "still-open"}]),
            tally("APPROVE", [], [{"id": "F1", "status": "fixed"}]),
        ])

        verdict, _ = dispatch.review(self.root, 7, "PASS", self.head)
        self.assertEqual(verdict, "REVISE")

        (self.root / "app.py").write_text("value = 2\n")
        git(self.root, "commit", "-qam", "second head")
        second = git(self.root, "rev-parse", "HEAD")
        verdict, findings = dispatch.review(self.root, 7, "PASS", second)
        self.assertEqual(verdict, "REVISE")
        seen = json.loads(self.state.read_text())["seen"]
        self.assertIn("-value = 1", seen[1]["diff"])
        self.assertNotIn("-value = 0", seen[1]["diff"])
        self.assertIn("-value = 0", seen[1]["full"])
        self.assertEqual(findings.count('"class": "wrong-value"'), 1)
        self.assertIn('"status": "still-open"', findings)
        ledger = json.loads((dispatch.FACTORY / "review-ledger-7.json").read_text())
        self.assertEqual(len(ledger["findings"]), 1)
        self.assertEqual(ledger["repeated_dropped"], 1)

        (self.root / "base.txt").write_text("moved\n")
        git(self.root, "add", "base.txt")
        git(self.root, "commit", "-qm", "moved base")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "app.py").write_text("value = 3\n")
        git(self.root, "commit", "-qam", "third head")
        third = git(self.root, "rev-parse", "HEAD")

        verdict, _ = dispatch.review(self.root, 7, "PASS", third)
        self.assertEqual(verdict, "APPROVE")
        seen = json.loads(self.state.read_text())["seen"]
        self.assertEqual(seen[2]["diff"], seen[2]["full"])
        self.assertIn("base moved", seen[2]["prompt"].lower())

    def test_missing_class_on_important_finding_fails_closed(self) -> None:
        row = finding(finding_id=None)
        del row["class"]
        del row["instances"]
        normalized, verdict, parsed, counts, reason = dispatch._parse_review_output(
            tally("REVISE", [row]), 5
        )
        self.assertEqual((verdict, parsed, counts, reason), ("REVISE", False, None, "unparsed_verdict"))
        self.assertNotIn('"class"', normalized)

    def test_missing_class_fails_closed_without_review_policy(self) -> None:
        row = finding(finding_id=None)
        del row["class"]
        del row["instances"]
        self.configure([tally("REVISE", [row])])

        verdict, _ = dispatch.review(self.root, 7, "PASS", self.head)

        self.assertEqual(verdict, "REVISE")
        result = dispatch._PENDING_REVIEWS[7]
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "unparsed_verdict")
        self.assertFalse((dispatch.FACTORY / "review-ledger-7.json").exists())


    def test_unconfirmed_important_is_downgraded_and_does_not_block(self) -> None:
        self.configure([
            tally("REVISE", [finding()]),
            json.dumps({"confirmed": False, "reason": "The cited line is correct."}),
        ], verify=True)

        verdict, findings = dispatch.review(self.root, 7, "PASS", self.head)

        self.assertEqual(verdict, "APPROVE")
        self.assertIn('"severity": "nit"', findings)
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual([row["confirmed"] for row in rows if row.get("event") == "review-verification"], [False])

    def test_verifier_timeout_leaves_important_blocking(self) -> None:
        self.configure([tally("REVISE", [finding()])], verify=True)
        main = subprocess.CompletedProcess([], 0, tally("REVISE", [finding()]), "")
        with mock.patch.object(
            dispatch,
            "_run_reviewer",
            side_effect=[main, subprocess.TimeoutExpired(["reviewer"], 1)],
        ):
            result = dispatch.run_reviewer(
                self.root,
                dispatch.review_prompt(7, "PASS"),
                self.head,
                subject="#7",
            )
        self.assertEqual(result["verdict"], "REVISE")
        self.assertEqual(result["counts"]["important"], 1)


class ReviewConvergenceStatsTest(unittest.TestCase):
    def test_reports_rounds_repeats_and_important_findings(self) -> None:
        metrics = stats.review_convergence([
            {"event": "review-result", "verdict": "REQUEST_CHANGES", "counts": {"important": 2}, "repeated_dropped": 0},
            {"event": "review-result", "verdict": "REQUEST_CHANGES", "counts": {"important": 1}, "repeated_dropped": 1},
            {"event": "review-result", "verdict": "APPROVE", "counts": {"important": 0}, "repeated_dropped": 0},
        ])
        self.assertEqual(metrics, {
            "rounds_to_pass": 3,
            "repeated_findings_dropped": 1,
            "important_findings_per_round": [2, 1, 0],
        })


if __name__ == "__main__":
    unittest.main()
