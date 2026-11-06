"""Risk policy and blocking mutation selection must use branch-owned changes."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import binding, config, dispatch, lifecycle, risk
from scripts import mutation

REVIEWER = """
import json
from pathlib import Path
reverted = '-guard = "fixed"' in Path('review.diff').read_text()
findings = [{"severity": "important", "pass": "bug", "path": "factory/manage.py",
             "line": 1, "summary": "Reverts the merged guard fix."}] if reverted else []
verdict = 'REVISE' if findings else 'APPROVE'
print('```json\\n' + json.dumps({"verdict": verdict, "findings": findings})
      + '\\n```\\nVERDICT: ' + verdict)
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


class ReviewRiskMergeBaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(lifecycle.CONTEXT_ENV, None)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Risk fixture")
        git(self.repo, "config", "user.email", "risk@example.invalid")
        (self.repo / "factory").mkdir()
        self.commit(self.repo, {
            "factory/manage.py": 'guard = "old"\n',
            "factory/watch.py": 'mode = "old"\n\nmain_only = "old"\n',
        })
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.wt = self.repo.parent / "ticket"
        git(self.repo, "worktree", "add", "-qb", "agent/533", str(self.wt))
        self.head = self.commit(self.wt, {
            "factory/watch.py": 'mode = "branch"\n\nmain_only = "old"\n',
        })
        dispatch.configure(config.Config(
            self.repo, "acme/widgets", reviewer=[sys.executable, "-c", REVIEWER],
        ))
        self.enterContext(mock.patch.object(binding, "accepted", return_value={"issue": {
            "title": "Watch change", "body": "Change watch without reverting main.", "comments": [],
        }}))

    def commit(self, repo: Path, files: dict[str, str]) -> str:
        for name, source in files.items():
            (repo / name).write_text(source)
        git(repo, "add", *files)
        git(repo, "commit", "-qm", "fixture change")
        return git(repo, "rev-parse", "HEAD")

    def advance_main(self) -> str:
        tip = self.commit(self.repo, {
            "factory/manage.py": 'guard = "fixed"\n',
            "factory/watch.py": 'mode = "old"\n\nmain_only = "fixed"\n',
        })
        git(self.repo, "update-ref", "refs/remotes/origin/main", tip)
        return tip

    def test_main_only_high_risk_change_does_not_block_branch_review(self) -> None:
        self.advance_main()
        tier = dispatch.record_risk(self.wt, 533, self.head)
        verdict, findings, failed = dispatch.pr_gate_and_review(
            self.wt, 533, "PASS", self.head, {},
        )
        self.assertEqual(tier, ("Medium", ["medium:other path"]))
        self.assertEqual(verdict, "APPROVE", findings)
        self.assertFalse(failed)
        self.assertNotIn(533, dispatch._PENDING_SECOND_REVIEWS)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.head)

    def test_mutation_selection_excludes_main_only_files_and_lines(self) -> None:
        self.advance_main()
        with contextlib.chdir(self.wt):
            self.assertEqual(mutation.changed_lines(), {"factory/watch.py": {1}})

    def test_genuine_revert_stays_high_risk_and_selects_reverted_lines(self) -> None:
        fixed = self.advance_main()
        git(self.wt, "merge", "-q", "--no-edit", "origin/main")
        git(self.wt, "revert", "--no-edit", fixed)
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(dispatch.record_risk(self.wt, 533, head), (
            "High", ["high:approval, gate, and dispatch control"],
        ))
        with contextlib.chdir(self.wt):
            self.assertEqual(mutation.changed_lines(), {
                "factory/manage.py": {1}, "factory/watch.py": {1, 3},
            })
        verdict, findings = dispatch.review(self.wt, 533, "PASS", head)
        self.assertEqual(verdict, "REVISE")
        self.assertIn("Reverts the merged guard fix", findings)

    def test_default_off_analysis_reads_merge_base_source_not_newer_main(self) -> None:
        self.head = self.commit(self.wt, {
            "factory/watch.py": (
                'mode = "old"\n\nmain_only = "old"\n\n'
                'ENABLED = False\nif ENABLED:\n    publish()\n'
            ),
        })
        tip = self.commit(self.repo, {
            "factory/watch.py": 'mode = "old"\n\nmain_only = "old"\nENABLED = True\n',
        })
        git(self.repo, "update-ref", "refs/remotes/origin/main", tip)
        self.assertEqual(risk.classify("origin/main", self.head, cwd=self.wt), (
            "Low", ["low:default-off code"],
        ))


if __name__ == "__main__":
    unittest.main()
