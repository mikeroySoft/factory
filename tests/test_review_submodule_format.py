"""Submodule log records must not abort review citation accounting."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle


class ReviewSubmoduleFormatTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(lifecycle.CONTEXT_ENV, None)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo = self.tmp / "repo"
        source = self.tmp / "source"
        for repo in (self.repo, source):
            repo.mkdir()
            self.git(repo, "init", "-q", "-b", "main")
            self.git(repo, "config", "user.name", "Submodule fixture")
            self.git(repo, "config", "user.email", "submodule@example.invalid")
            self.git(repo, "config", "commit.gpgsign", "false")
        (source / "module.py").write_text("value = 'old'\n")
        self.git(source, "add", "module.py")
        self.git(source, "commit", "-qm", "original module")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(source), "a-submodule")
        (self.repo / "z-code.py").write_text("value = 'old'\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "base")
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.git(self.repo, "checkout", "-qb", "agent/533")
        (source / "module.py").write_text("value = 'new'\n")
        self.git(source, "add", "module.py")
        self.git(source, "commit", "-qm", "updated module")
        submodule = self.repo / "a-submodule"
        self.git(submodule, "fetch", "-q", "origin")
        self.git(submodule, "checkout", "-q", "origin/main")
        self.git(self.repo, "config", "diff.submodule", "log")
        dispatch.configure(config.Config(self.repo, "acme/widgets"))

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        ).stdout.strip()

    def review(self, rows, outside):
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "branch change")
        head = self.git(self.repo, "rev-parse", "HEAD")
        diff = self.git(self.repo, "diff", f"origin/main...{head}")
        self.assertTrue(diff.startswith("Submodule a-submodule "), diff)
        captured = self.tmp / "review.diff"
        output = "```json\n" + json.dumps({"verdict": "APPROVE", "findings": rows})
        output += "\n```\nVERDICT: APPROVE"
        reviewer = (
            "import sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(Path('review.diff').read_text()); print(sys.argv[2])"
        )
        result = dispatch.run_reviewer(
            self.repo, "Review the branch diff.", head,
            reviewer=[sys.executable, "-c", reviewer, str(captured), output],
        )
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(result["actual_head"], head)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(result["outside_diff_findings"], outside)
        published = json.loads(result["findings"].split("```json\n")[1].split("\n```")[0])
        self.assertEqual(published["findings"], rows)
        return captured.read_text()

    def test_submodule_only_log_accepts_empty_approve(self):
        diff = self.review([], outside=0)
        self.assertIn("> updated module", diff)
        self.assertNotIn("diff --git ", diff)

    def test_leading_submodule_log_preserves_later_patch_citations(self):
        (self.repo / "z-code.py").write_text("value = 'new'\n")
        rows = [
            {"severity": "nit", "pass": "bug", "path": "z-code.py", "line": 1,
             "summary": "Citation inside the displayed patch."},
            {"severity": "nit", "pass": "bug", "path": "a-submodule/module.py", "line": 1,
             "summary": "Citation outside the summary's displayed hunks."},
        ]
        diff = self.review(rows, outside=1)
        self.assertIn("diff --git a/z-code.py b/z-code.py", diff)
        self.assertIn("-value = 'old'\n+value = 'new'", diff)


if __name__ == "__main__":
    unittest.main()
