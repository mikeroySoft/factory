"""Operator Git configuration must not change machine-parsed review packets."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle

REVIEWER = """
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "diff": Path("review.diff").read_text(),
    "snapshot": Path("snapshot/unchanged.py").read_text(),
}))
print(sys.argv[2])
"""


class ReviewDiffFormatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop(lifecycle.CONTEXT_ENV, None)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Review fixture")
        self.git("config", "user.email", "review@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.original = [f"value_{n} = {n}\n" for n in range(1, 31)]
        (self.repo / "changed.py").write_text("".join(self.original))
        (self.repo / "unchanged.py").write_text("main_only = 'old'\n")
        self.git("add", "changed.py", "unchanged.py")
        self.git("commit", "-qm", "base")
        self.git("checkout", "-qb", "agent/533")
        (self.repo / "changed.py").write_text("value_1 = 'branch'\n" + "".join(self.original[1:]))
        self.git("add", "changed.py")
        self.git("commit", "-qm", "branch change")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        (self.repo / "unchanged.py").write_text("main_only = 'fixed'\n")
        self.git("add", "unchanged.py")
        self.git("commit", "-qm", "unrelated main fix")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-q", "agent/533")
        self.captured = self.tmp / "packet.json"
        dispatch.configure(config.Config(self.repo, "acme/widgets"))

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def review(
        self, rows: list[dict], *, outside: int = 0, base_ref: str | None = None,
    ) -> dict:
        verdict = "REVISE" if any(row["severity"] == "important" for row in rows) else "APPROVE"
        output = (
            "```json\n" + json.dumps({"verdict": verdict, "findings": rows})
            + f"\n```\nVERDICT: {verdict}"
        )
        result = dispatch.run_reviewer(
            self.repo, "Review branch changes only.", self.head, base_ref=base_ref,
            reviewer=[sys.executable, "-c", REVIEWER, str(self.captured), output],
        )
        self.assertTrue(result["accepted"], result)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["verdict"], verdict)
        self.assertEqual(result["actual_head"], self.head)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(result["outside_diff_findings"], outside)
        published = json.loads(result["findings"].split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(published["findings"], rows)
        packet = json.loads(self.captured.read_text())
        self.assertNotIn("\x1b[", packet["diff"])
        self.assertIn("diff --git a/changed.py b/changed.py", packet["diff"])
        self.assertNotIn("unchanged.py", packet["diff"])
        self.assertEqual(packet["snapshot"], "main_only = 'old'\n")
        return result

    def assert_citations(self) -> None:
        inside = [{
            "severity": "nit", "pass": "bug", "path": "changed.py",
            "line": 1, "summary": "Inside the branch diff.",
        }]
        outside = [
            {"severity": "important", "pass": "bug", "path": "unchanged.py",
             "line": 1, "summary": "Outside the branch diff, still blocking."},
            {"severity": "nit", "pass": "spec", "path": "changed.py",
             "line": 30, "summary": "Outside the displayed hunk."},
        ]
        for rows, expected in (([], 0), (inside, 0), (outside, 2)):
            with self.subTest(findings=rows):
                self.review(rows, outside=expected)

    def test_color_always_accepts_empty_approve_and_counts_citations(self) -> None:
        self.git("config", "color.diff", "always")
        self.assert_citations()

    def test_noprefix_preserves_citation_paths(self) -> None:
        self.git("config", "diff.noprefix", "true")
        self.assert_citations()

    def test_custom_prefixes_preserve_citation_paths(self) -> None:
        self.git("config", "diff.srcPrefix", "before/")
        self.git("config", "diff.dstPrefix", "after/")
        self.assert_citations()

    def test_external_diff_cannot_replace_review_packet(self) -> None:
        sentinel = self.tmp / "external-ran"
        external = self.tmp / "external.py"
        external.write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
            "print('operator external diff')\n"
        )
        self.git("config", "diff.external", shlex.join([sys.executable, str(external)]))

        self.assert_citations()

        self.assertFalse(sentinel.exists())

    def test_operator_format_preserves_explicit_rereview_boundary(self) -> None:
        self.git("config", "color.diff", "always")
        self.git("config", "diff.noprefix", "true")
        self.git("config", "diff.srcPrefix", "before/")
        self.git("config", "diff.dstPrefix", "after/")
        previous = self.head
        (self.repo / "changed.py").write_text("value_1 = 'revision'\n" + "".join(self.original[1:]))
        self.git("add", "changed.py")
        self.git("commit", "-qm", "revision")
        self.head = self.git("rev-parse", "HEAD")

        result = self.review([], base_ref=previous)

        diff = json.loads(self.captured.read_text())["diff"]
        self.assertIn("-value_1 = 'branch'\n+value_1 = 'revision'", diff)
        self.assertNotIn("-value_1 = 1", diff)
        self.assertEqual(result["outside_diff_findings"], 0)


if __name__ == "__main__":
    unittest.main()
