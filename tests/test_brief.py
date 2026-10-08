import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import brief


class BriefTest(unittest.TestCase):
    def test_brief_reranks_and_keeps_docs(self):
        def git(_cwd, *args):
            if args[0] == "ls-files":
                return ["README.md", "CHANGELOG.md"]
            return []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def read(self):
                return json.dumps({"answers": {"c0": {"noul": 0.1}, "c1": {"noul": 0.9}},
                                   "usage": {"input_tokens": 100}}).encode()

        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            root = Path(directory)
            (root / "a.py").write_text("first\n")
            (root / "b.py").write_text("second\n")
            with patch.object(brief, "git", git), patch.object(brief, "files_for", return_value=["a.py", "b.py"]), patch.object(brief.urllib.request, "urlopen", return_value=Response()) as urlopen, redirect_stdout(io.StringIO()) as out:
                text = brief.compose(root, {"title": "Change `a.py`", "body": ""}, "")
        self.assertIn("[brief] Jev rerank cost: $0.00000420", out.getvalue())
        self.assertLess(text.index("- b.py"), text.index("- a.py"))
        self.assertIn("- README.md", text)
        self.assertIn("- CHANGELOG.md", text)
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["state"]["candidates"][0]["summary"], "first")

    def test_brief_falls_back_without_key_or_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("first\n")
            (root / "b.py").write_text("second\n")
            with patch.object(brief, "git", return_value=["README.md", "CHANGELOG.md"]), patch.object(brief, "files_for", return_value=["a.py", "b.py"]):
                with patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}), patch.object(brief.urllib.request, "urlopen") as urlopen:
                    text = brief.compose(root, {"title": "Change `a.py`"}, "")
                    urlopen.assert_not_called()
                self.assertLess(text.index("- a.py"), text.index("- b.py"))
                self.assertIn("- README.md", text)
                self.assertIn("- CHANGELOG.md", text)
                with patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}), patch.object(
                    brief.urllib.request, "urlopen", side_effect=OSError("offline")
                ):
                    text = brief.compose(root, {"title": "Change `a.py`"}, "")
                self.assertLess(text.index("- a.py"), text.index("- b.py"))

    def test_no_matched_files_does_not_include_docs(self):
        with patch.object(brief, "files_for", return_value=[]), patch.object(
            brief, "git", return_value=["README.md", "CHANGELOG.md"]
        ):
            self.assertEqual(brief.compose(Path("."), {"title": "Change `missing_symbol`"}, ""), "")
