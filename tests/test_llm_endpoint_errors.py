"""Local-model errors name the real failure: a timeout vs. an unreachable endpoint."""
from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from factory import triage
from factory.config import Config


class EndpointErrorMessage(unittest.TestCase):
    """A slow model reads as a timeout; only a real connection failure reads as unreachable."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(triage.__dict__))
        cfg = Config(root=Path(temp.name), repo="local/triage")
        cfg.triage_timeout = 300
        triage.configure(cfg)
        triage.LLM_URL = "http://127.0.0.1:11435/v1/chat/completions"

    def message(self, failure: BaseException) -> tuple[str, SystemExit]:
        err = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=failure), redirect_stderr(err), \
                self.assertRaises(SystemExit) as ended:
            triage.call_llm([{"role": "user", "content": "hi"}])
        return err.getvalue(), ended.exception

    def test_read_timeout_says_timed_out_with_seconds_and_url(self):
        text, ended = self.message(TimeoutError("timed out"))
        self.assertIn("timed out after 300s at http://127.0.0.1:11435/v1/chat/completions", text)
        self.assertNotIn("cannot reach", text)
        self.assertIsInstance(ended, triage.EndpointTimeout)

    def test_connect_timeout_says_timed_out(self):
        text, ended = self.message(urllib.error.URLError(TimeoutError("timed out")))
        self.assertIn("timed out after 300s connecting to http://127.0.0.1:11435", text)
        self.assertNotIn("cannot reach", text)
        self.assertNotIsInstance(ended, triage.EndpointTimeout)

    def test_connection_refused_says_cannot_reach(self):
        text, ended = self.message(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        self.assertIn("cannot reach local model at http://127.0.0.1:11435", text)
        self.assertNotIn("timed out", text)
        self.assertEqual(ended.code, 2)



if __name__ == "__main__":
    unittest.main()
