"""Focused token-usage extraction checks."""

import json
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, stats, triage


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

    def test_omp_usage_carries_reported_model_for_pricing(self) -> None:
        row = {"type": "message_end", "message": {
            "role": "assistant", "model": "m1", "content": [{"type": "text", "text": "VERDICT: APPROVE"}],
            "usage": {"input": 3, "output": 1},
        }}

        self.assertEqual(dispatch._omp_output(json.dumps(row))[1]["model"], "m1")

    def test_stats_totals_worker_usage_and_prices_only_from_table(self) -> None:
        def event(stage, **fields):
            return {"event": "llm-usage", "ticket": 7, "stage": stage, **fields}

        nulls = dict.fromkeys(("model", "prompt_tokens", "completion_tokens", "cached_tokens", "cost"))
        events = [
            event("triage", model="m1", prompt_tokens=1_000_000, completion_tokens=0, prefix_cache_hit_rate=0.5),
            event("review", prompt_tokens=10, completion_tokens=2),
            event("worker", attempt=1, **{**nulls, "cost": 0.5}),
            event("worker", attempt=2, **{**nulls, "model": "m1", "prompt_tokens": 2_000_000,
                                         "completion_tokens": 1_000_000, "cached_tokens": 1_000_000}),
            event("worker", attempt=3, **nulls),
        ]
        prices = {"m1": {"prompt": 3, "completion": 15, "cached": 0.3}}

        priced = stats.llm_usage(events, prices)
        unpriced = stats.llm_usage(events)

        self.assertEqual(priced["worker"], {"prompt_tokens": 2_000_000, "completion_tokens": 1_000_000,
                                            "cached_tokens": 1_000_000, "cost": 18.8})
        self.assertEqual(priced["triage"]["cost"], 1.65)  # half the prompt billed at the cached rate
        self.assertIsNone(priced["review"]["cost"])  # no model reported: never guessed
        self.assertEqual(priced["total"], {"prompt_tokens": 3_000_010, "completion_tokens": 1_000_002,
                                           "cached_tokens": 1_000_000, "cost": 20.45})
        self.assertEqual((unpriced["worker"]["cost"], unpriced["triage"]["cost"], unpriced["total"]["cost"]),
                         (0.5, None, 0.5))
        self.assertEqual(stats.llm_usage([event("worker", attempt=1, **nulls)])["worker"],
                         dict.fromkeys(("prompt_tokens", "completion_tokens", "cached_tokens", "cost")))

        row = {"ticket": 7, "title": "t", "state": "merged", "attempts": 3, "review_rounds": 1,
               "merge_hours": None, "escalation_count": 0, "manager_failures": 0, "resolutions": [],
               "ready_for_human_minutes": 0.0, "requeue_count": 0, "llm_usage": priced}
        with mock.patch("builtins.print") as printed:
            stats.print_table([row])
        header, _, line = (call.args[0] for call in printed.call_args_list)
        self.assertIn("$18.8000", line)
        self.assertIn("$20.4500", line)
        self.assertIn("worker $", header)


if __name__ == "__main__":
    unittest.main()
