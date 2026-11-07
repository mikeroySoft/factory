"""Review packets must not turn main-only changes into branch reverts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import binding, config, dispatch, lifecycle

REVIEWER = """
import json, sys
from pathlib import Path
diff = Path('review.diff').read_text()
reverted = '-guard = "fixed"' in diff and '+guard = "old"' in diff
findings = [{"severity": "important", "pass": "bug", "path": "factory/manager.py",
             "line": 1, "summary": "Reverts the merged guard fix."}] if reverted else []
Path(sys.argv[1]).write_text(json.dumps({
    "diff": diff, "prompt": sys.argv[2],
    "snapshot": Path('snapshot/factory/manager.py').read_text(),
}))
verdict = 'REVISE' if findings else 'APPROVE'
print('```json\\n' + json.dumps({"verdict": verdict, "findings": findings})
      + '\\n```\\nVERDICT: ' + verdict)
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


class ReviewMergeBaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(lifecycle.CONTEXT_ENV, None)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Review fixture")
        git(self.repo, "config", "user.email", "review@example.invalid")
        (self.repo / "factory").mkdir()
        (self.repo / "factory/manager.py").write_text('guard = "old"\n')
        git(self.repo, "add", "factory/manager.py")
        git(self.repo, "commit", "-qm", "initial")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.wt = self.tmp / "ticket"
        git(self.repo, "worktree", "add", "-qb", "agent/533", str(self.wt))
        (self.wt / "note.md").write_text("branch work\n")
        git(self.wt, "add", "note.md")
        git(self.wt, "commit", "-qm", "branch work")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.captured = self.tmp / "packet.json"
        self.cfg = config.Config(
            self.repo, "acme/widgets",
            reviewer=[sys.executable, "-c", REVIEWER, str(self.captured), "{prompt}",
                      "--model", "primary/model"],
            second_reviewer=[sys.executable, "-c", REVIEWER, str(self.captured), "{prompt}",
                             "--model", "second/model"],
        )
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir(exist_ok=True)
        self.enterContext(mock.patch.object(binding, "accepted", return_value={"issue": {
            "title": "Branch work", "body": "Add a note without reverting fixes.", "comments": [],
        }}))
        self.enterContext(mock.patch.object(dispatch, "_ticket_claim_state", return_value={
            "state": "OPEN", "labels": [{"name": config.LABEL_AGENT}],
            "assignees": [{"login": "factory"}],
        }))
        self.enterContext(mock.patch.object(dispatch, "host_claim_login", return_value="factory"))

    def advance_main(self) -> str:
        (self.repo / "factory/manager.py").write_text('guard = "fixed"\n')
        git(self.repo, "add", "factory/manager.py")
        git(self.repo, "commit", "-qm", "unrelated guard fix on main")
        tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", tip)
        return tip

    def test_main_advance_during_review_does_not_produce_revert_or_revise(self) -> None:
        command = dispatch._review_command

        def advance_before_diff(*args, **kwargs):
            self.advance_main()
            return command(*args, **kwargs)

        with mock.patch.object(dispatch, "_review_command", side_effect=advance_before_diff):
            verdict, findings = dispatch.review(self.wt, 533, "PASS", self.head)

        packet = json.loads(self.captured.read_text())
        self.assertIn("+branch work", packet["diff"])
        self.assertNotIn("factory/manager.py", packet["diff"])
        self.assertEqual(packet["snapshot"], 'guard = "old"\n')
        self.assertEqual(verdict, "APPROVE", findings)
        self.assertNotIn("Reverts the merged guard fix", findings)
        event = next(e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "review")
        self.assertEqual(event["counts"]["important"], 0)
        self.assertEqual(event["actual_head"], self.head)

    def test_branch_commit_that_reverts_merged_fix_still_blocks_review(self) -> None:
        fixed = self.advance_main()
        git(self.wt, "merge", "-q", "--no-edit", "origin/main")
        git(self.wt, "revert", "--no-edit", fixed)
        head = git(self.wt, "rev-parse", "HEAD")
        verdict, findings = dispatch.review(self.wt, 533, "PASS", head)

        packet = json.loads(self.captured.read_text())
        self.assertIn('-guard = "fixed"', packet["diff"])
        self.assertIn('+guard = "old"', packet["diff"])
        self.assertEqual(verdict, "REVISE")
        self.assertIn("Reverts the merged guard fix", findings)
        self.assertEqual(dispatch._PENDING_REVIEWS[533]["counts"]["important"], 1)
        event = next(e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "review")
        self.assertEqual(event["outside_diff_findings"], 0)

    def test_high_risk_both_reviewers_and_changed_files_exclude_main_only_code(self) -> None:
        self.advance_main()
        packets = []
        launch = dispatch._run_reviewer

        def capture(*args, **kwargs):
            result = launch(*args, **kwargs)
            packets.append(json.loads(self.captured.read_text()))
            return result

        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:fixture"])), \
                mock.patch.object(dispatch, "_worker_model_for_head", return_value="worker/model"), \
                mock.patch.object(dispatch, "_run_reviewer", side_effect=capture):
            verdict, findings, failed = dispatch.pr_gate_and_review(
                self.wt, 533, "PASS", self.head, {},
            )

        self.assertEqual(verdict, "APPROVE", findings)
        self.assertFalse(failed)
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0]["diff"], packets[1]["diff"])
        self.assertNotIn("factory/manager.py", packets[1]["diff"])
        risk = dispatch._PENDING_RISK[533]
        self.assertEqual(risk["mutation"], "SKIP")
        self.assertIn("note.md", risk["packet"])
        self.assertNotIn("factory/manager.py", risk["packet"])

    def test_explicit_rereview_boundary_shows_only_new_changes(self) -> None:
        self.advance_main()
        (self.wt / "note.md").write_text("revised branch work\n")
        git(self.wt, "add", "note.md")
        git(self.wt, "commit", "-qm", "revision")
        head = git(self.wt, "rev-parse", "HEAD")
        result = dispatch.run_reviewer(self.wt, "Review revision only.", head, base_ref=self.head)

        packet = json.loads(self.captured.read_text())
        self.assertIn("-branch work\n+revised branch work", packet["diff"])
        self.assertNotIn("factory/manager.py", packet["diff"])
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertTrue(result["accepted"])

    def test_outside_diff_citations_are_counted_without_suppressing_findings(self) -> None:
        self.advance_main()
        rows = [
            {"severity": "important", "pass": "bug", "path": "factory/manager.py",
             "line": 1, "summary": "Outside branch diff."},
            {"severity": "nit", "pass": "spec", "path": "note.md",
             "line": 1, "summary": "Inside branch diff."},
            {"severity": "nit", "pass": "spec", "path": "note.md",
             "line": 50, "summary": "Outside displayed lines."},
        ]
        output = f'```json\n{json.dumps({"verdict": "REVISE", "findings": rows})}\n```\nVERDICT: REVISE'
        self.cfg.reviewer = [sys.executable, "-c", f"print({output!r})"]
        verdict, findings = dispatch.review(self.wt, 533, "PASS", self.head)

        self.assertEqual(verdict, "REVISE")
        self.assertIn("Outside branch diff.", findings)
        self.assertIn("Outside displayed lines.", findings)
        event = next(e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "review")
        self.assertEqual(event["outside_diff_findings"], 2)

    def test_citation_count_handles_deleted_files_and_quoted_paths(self) -> None:
        name = "r\u00e9sum\u00e9\tname.py"
        (self.wt / name).write_text("-- source, not a diff header\n++ another source line\n")
        (self.wt / "factory/manager.py").unlink()
        git(self.wt, "add", name, "factory/manager.py")
        git(self.wt, "commit", "-qm", "quoted path and deletion")
        head = git(self.wt, "rev-parse", "HEAD")
        rows = [
            {"severity": "nit", "pass": "spec", "path": path, "line": line, "summary": "Citation"}
            for path, line in ((name, 1), (name, 2), ("factory/manager.py", 1), ("note.md", 2))
        ]
        output = f'```json\n{json.dumps({"verdict": "APPROVE", "findings": rows})}\n```\nVERDICT: APPROVE'
        self.cfg.reviewer = [sys.executable, "-c", f"print({output!r})"]
        verdict, _ = dispatch.review(self.wt, 533, "PASS", head)

        self.assertEqual(verdict, "APPROVE")
        event = next(e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "review")
        self.assertEqual(event["outside_diff_findings"], 1)


if __name__ == "__main__":
    unittest.main()
