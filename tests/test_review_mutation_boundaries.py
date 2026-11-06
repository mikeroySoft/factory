"""Citation metrics and risk decisions retain their boundary guarantees."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory import dispatch, risk


class ReviewMutationBoundariesTest(unittest.TestCase):
    def test_citations_use_exact_hunk_ends_and_pure_deletion_anchor(self):
        for hunk, inside, outside in (
            ("@@ -3 +3 @@\n-old\n+new\n", 3, 4),
            ("@@ -3 +2,0 @@\n-deleted\n", 2, 3),
        ):
            diff = "diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n" + hunk
            for line, expected in ((inside, 0), (outside, 1)):
                with self.subTest(hunk=hunk, line=line):
                    findings = "```json\n" + json.dumps({"findings": [{
                        "path": "code.py", "line": line,
                    }]}) + "\n```"
                    self.assertEqual(dispatch._outside_diff_findings(diff, findings), expected)

    def test_unknown_risk_base_fails_closed_instead_of_classifying_empty_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            for args in (
                ("init", "-q", "-b", "main"),
                ("-c", "user.name=Risk fixture", "-c", "user.email=risk@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "initial", "--allow-empty"),
            ):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            with self.assertRaises(subprocess.CalledProcessError):
                risk.classify("missing-risk-base", "HEAD", cwd=repo)


if __name__ == "__main__":
    unittest.main()
