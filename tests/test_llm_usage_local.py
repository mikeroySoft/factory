from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, lifecycle, triage


class EndpointLocalityTest(unittest.TestCase):
    def test_loopback_and_private_addresses_are_local(self) -> None:
        for url in ("http://127.0.0.1:11434/v1", "http://[::1]:8000", "http://10.1.2.3/v1",
                    "https://172.20.0.2", "http://192.168.1.9", "http://localhost:9000"):
            with self.subTest(url=url):
                self.assertTrue(config.local_endpoint(url))
        for url in ("https://api.openai.com/v1", "http://8.8.8.8", "not a url"):
            with self.subTest(url=url):
                self.assertFalse(config.local_endpoint(url))

    def test_worker_usage_marks_the_configured_local_model(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dispatch.cfg = config.Config(root, "acme/widgets")
        dispatch.cfg.llm_model = "local-model"
        log = root / "worker.log"
        log.write_text("")
        transcript = root / "session.jsonl"
        transcript.write_text(json.dumps({
            "type": "message",
            "message": {"role": "assistant", "model": "local-model", "usage": {
                "input": 4, "output": 2, "cacheRead": 0, "cacheWrite": 0,
            }},
        }) + "\n")

        usage = dispatch.worker_usage(log, None, str(transcript))

        self.assertEqual(usage["local"], True)

    def test_triage_usage_records_endpoint_locality(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "events.jsonl"
        execution = SimpleNamespace(ticket=7, path=path, execution_id="e", root_execution_id="r",
                                    commit=mock.Mock())
        with mock.patch.object(triage, "LLM_URL", "http://192.168.1.2/v1/chat/completions"):
            triage._record_usage(execution, {"prompt_tokens": 4, "completion_tokens": 2})

        [row] = lifecycle.read_events(path)
        self.assertIs(row["local"], True)
        execution.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
