import json
from pathlib import Path
from unittest.mock import patch

from factory import brief


def test_brief_reranks_and_keeps_docs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
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

    (tmp_path / "a.py").write_text("first\n")
    (tmp_path / "b.py").write_text("second\n")
    with patch.object(brief, "git", git), patch.object(brief, "files_for", return_value=["a.py", "b.py"]), patch.object(brief.urllib.request, "urlopen", return_value=Response()) as urlopen:
        text = brief.compose(tmp_path, {"title": "Change `a.py`", "body": ""}, "")
    assert text.index("- b.py") < text.index("- a.py")
    assert "- README.md" in text and "- CHANGELOG.md" in text
    assert json.loads(urlopen.call_args.args[0].data)["state"]["candidates"][0]["summary"] == "first"


def test_brief_falls_back_without_key_or_on_failure(tmp_path: Path, monkeypatch):
    (tmp_path / "a.py").write_text("first\n")
    (tmp_path / "b.py").write_text("second\n")
    with patch.object(brief, "git", return_value=["README.md", "CHANGELOG.md"]), patch.object(brief, "files_for", return_value=["a.py", "b.py"]):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        with patch.object(brief.urllib.request, "urlopen") as urlopen:
            text = brief.compose(tmp_path, {"title": "Change `a.py`"}, "")
            urlopen.assert_not_called()
        assert text.index("- a.py") < text.index("- b.py")
        assert "- README.md" in text and "- CHANGELOG.md" in text
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
        with patch.object(brief.urllib.request, "urlopen", side_effect=OSError("offline")):
            text = brief.compose(tmp_path, {"title": "Change `a.py`"}, "")
        assert text.index("- a.py") < text.index("- b.py")
