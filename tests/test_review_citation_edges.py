"""Citation accounting follows actual Git headers and zero-length hunks."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory import dispatch


class ReviewCitationEdgesTest(unittest.TestCase):
    def test_git_headers_content_and_empty_file_citations(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                ).stdout

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Citation fixture")
            git("config", "user.email", "citation@example.invalid")
            git("config", "commit.gpgsign", "false")
            original = [f"line {n}\n" for n in range(30)]
            for name, content in (
                ("space name.py", "old\n"),
                ("content.py", "".join(original)),
                ("emptied.py", "old\nsecond\nthird\n"),
            ):
                (repo / name).write_text(content)
            git("add", ".")
            git("commit", "-qm", "base")
            (repo / "space name.py").write_text("new\n")
            changed = original.copy()
            changed[0] = "++ b/not-a-path.py\n"
            changed[-1] = "new final line\n"
            (repo / "content.py").write_text("".join(changed))
            (repo / "emptied.py").write_text("")
            diff = git("diff", "--src-prefix=a/", "--dst-prefix=b/")

            for path, line, expected in (
                ("space name.py", 1, 0),
                ("space name.py", 2, 1),
                ("content.py", 30, 0),
                ("not-a-path.py", 30, 1),
                ("emptied.py", 1, 0),
                ("emptied.py", 2, 1),
            ):
                with self.subTest(path=path, line=line):
                    findings = "```json\n" + json.dumps({"findings": [{
                        "path": path, "line": line,
                    }]}) + "\n```"
                    self.assertEqual(dispatch._outside_diff_findings(diff, findings), expected)


if __name__ == "__main__":
    unittest.main()
