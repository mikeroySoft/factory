"""Focused token-usage extraction checks."""

import json
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, triage


class UsageExtractionTest(unittest.TestCase):
    def test_triage_uses_only_reported_cache_metadata(self) -> None:
        plain = triage._usage_fields({
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        })
        cached = triage._usage_fields({
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 5},
            },
        })

        self.assertEqual(plain, {"prompt_tokens": 10, "completion_tokens": 2})
        self.assertEqual(cached, {
            "prompt_tokens": 20,
            "completion_tokens": 3,
            "prefix_cache_hit_rate": 0.25,
        })

    def test_review_command_only_changes_omp_output_mode(self) -> None:
        with mock.patch.object(
            dispatch,
            "cfg",
            config.Config(root=Path("/tmp"), repo="example/repo", reviewer=["codex", "exec", "{prompt}"]),
            create=True,
        ):
            command, structured = dispatch._review_command("review me")
        self.assertEqual(command, ["codex", "exec", "review me"])
        self.assertFalse(structured)

        with mock.patch.object(
            dispatch,
            "cfg",
            config.Config(root=Path("/tmp"), repo="example/repo", reviewer=["omp", "-p", "--mode", "text", "{prompt}"]),
            create=True,
        ):
            command, structured = dispatch._review_command("review me")
        self.assertEqual(command, ["omp", "-p", "--mode", "json", "review me"])
        self.assertTrue(structured)

    def test_omp_jsonl_sums_turns_and_recovers_final_text(self) -> None:
        rows = [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "intermediate"}],
                    "usage": {
                        "input": 10,
                        "output": 2,
                        "cacheRead": 4,
                        "cacheWrite": 0,
                    },
                },
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "VERDICT: APPROVE"}],
                    "usage": {
                        "input": 3,
                        "output": 1,
                        "cacheRead": 0,
                        "cacheWrite": 5,
                    },
                },
            },
        ]

        text, usage = dispatch._omp_output("\n".join(map(json.dumps, rows)))

        self.assertEqual(text, "VERDICT: APPROVE")
        self.assertEqual(usage, {
            "prompt_tokens": 22,
            "completion_tokens": 3,
            "prefix_cache_hit_rate": 4 / 22,
        })


if __name__ == "__main__":
    unittest.main()
