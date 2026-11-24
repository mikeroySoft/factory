from __future__ import annotations

import contextlib
import gzip
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, stats

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
START = NOW - timedelta(days=7)


def at(delta: timedelta = timedelta()) -> str:
    return (NOW + delta).isoformat().replace("+00:00", "Z")


class PostFixMetricsTest(unittest.TestCase):
    def test_cohorts_use_first_worker_evidence_and_follow_outcomes_past_window(self) -> None:
        rows = [
            {"at": at(timedelta(days=-8)), "event": "manage", "ticket": 60, "decision": "FIX"},
            {"at": at(timedelta(days=-7)), "event": "manage", "ticket": 1, "decision": "FIX"},
            {"at": at(timedelta(days=-6, hours=-23)), "event": "llm-usage", "ticket": 1,
             "stage": "worker", "worker_model": "model/a", "model": "runtime/other"},
            {"at": at(timedelta(days=-6, hours=-22)), "event": "attempt", "ticket": 1,
             "worker_model": "model/a"},
            {"at": at(timedelta(days=-6, hours=-21)), "event": "claimed", "ticket": 1,
             "worker_model": "model/unrelated-later"},
            {"at": at(timedelta(days=-6)), "event": "manage", "ticket": 2, "decision": "FIX"},
            {"at": at(timedelta(days=-5, hours=-23)), "event": "claimed", "ticket": 2,
             "worker_model": "model/b"},
            {"at": at(timedelta(days=-5, hours=-22)), "event": "llm-usage", "ticket": 2,
             "stage": "worker", "worker_model": "model/b-fallback"},
            {"at": at(timedelta(days=-5, hours=-21)), "event": "attempt", "ticket": 2,
             "worker_model": "model/later-retry"},
            {"at": at(timedelta(days=-5)), "event": "manage", "ticket": 3, "decision": "FIX"},
            {"at": at(timedelta(days=-4, hours=-23)), "event": "claimed", "ticket": 3},
            {"at": at(timedelta(days=-4, hours=-22)), "event": "attempt", "ticket": 3,
             "worker_model": "model/actual"},
            {"at": at(timedelta(days=-4)), "event": "manage", "ticket": 4, "decision": "FIX"},
            {"at": at(timedelta(days=-3)), "event": "manage", "ticket": 5, "decision": "FIX"},
            {"at": at(timedelta(days=-3, hours=1)), "event": "claimed", "ticket": 5,
             "worker_model": "model/old"},
            {"at": at(timedelta(days=-2)), "event": "manage", "ticket": 5, "decision": "FIX"},
            {"at": at(timedelta(days=-2, hours=1)), "event": "attempt", "ticket": 5,
             "worker_model": "model/new"},
            {"at": at(timedelta(days=-2, hours=2)), "event": "llm-usage", "ticket": 5,
             "stage": "worker", "worker_model": "model/new"},
            {"at": at(timedelta(days=-1)), "event": "manage", "ticket": 9, "decision": "FIX"},
            {"at": at(timedelta(hours=-23)), "event": "claimed", "ticket": 9,
             "worker_model": "model/pending"},
            {"at": at(timedelta(days=-4, hours=1)), "event": "merged", "ticket": 60},
            {"at": at(timedelta(days=-3, hours=1)), "event": "merged", "ticket": 1},
            {"at": at(timedelta(days=-3, hours=2)), "event": "escalate", "ticket": 2},
            {"at": at(timedelta(days=-3, hours=-12)), "event": "manage", "ticket": 6, "decision": "FIX"},
            {"at": at(timedelta(days=-3, hours=-11)), "event": "claimed", "ticket": 6},
            {"at": at(timedelta(days=-3, hours=-10)), "event": "merged", "ticket": 6},
            {"at": at(timedelta(days=-3, hours=3)), "event": "merged", "ticket": 3},
            {"at": at(timedelta(days=-3, hours=4)), "event": "merged", "ticket": 4},
            {"at": at(timedelta(days=-1, hours=1)), "event": "merged", "ticket": 5},
            {"at": at(timedelta(hours=1)), "event": "manage", "ticket": 80, "decision": "FIX"},
            {"at": at(timedelta(hours=2)), "event": "claimed", "ticket": 80,
             "worker_model": "model/outside"},
            {"at": at(timedelta(hours=3)), "event": "merged", "ticket": 80},
            {"at": at(timedelta(hours=4)), "event": "merged", "ticket": 9},
        ]

        result = stats.post_fix_metrics(rows, START, NOW)

        self.assertEqual(result, {
            "window": {"start": "2026-11-17T12:00:00Z", "end": "2026-11-24T12:00:00Z"},
            "cohorts": 8,
            "merged": 6,
            "re_escalated": 1,
            "pending": 1,
            "resolved": 7,
            "merge_rate": 6 / 7,
            "by_worker_model": {
                stats.MISSING_WORKER_MODEL: {
                    "merged": 1, "re_escalated": 0, "pending": 0,
                    "resolved": 1, "merge_rate": 1.0,
                },
                stats.NO_WORKER_CLAIM: {
                    "merged": 1, "re_escalated": 0, "pending": 0,
                    "resolved": 1, "merge_rate": 1.0,
                },
                "model/a": {
                    "merged": 1, "re_escalated": 0, "pending": 0,
                    "resolved": 1, "merge_rate": 1.0,
                },
                "model/b-fallback": {
                    "merged": 0, "re_escalated": 1, "pending": 0,
                    "resolved": 1, "merge_rate": 0.0,
                },
                "model/new": {
                    "merged": 1, "re_escalated": 0, "pending": 0,
                    "resolved": 1, "merge_rate": 1.0,
                },
                "model/actual": {
                    "merged": 1, "re_escalated": 0, "pending": 0,
                    "resolved": 1, "merge_rate": 1.0,
                },
                "model/old": {
                    "merged": 0, "re_escalated": 0, "pending": 1,
                    "resolved": 0, "merge_rate": None,
                },
                "model/pending": {
                    "merged": 1, "re_escalated": 0, "pending": 0,
                    "resolved": 1, "merge_rate": 1.0,
                },
            },
            "baseline": {"merged": 6, "resolved": 35, "merge_rate": 6 / 35},
            "caveat": stats.POST_FIX_CAVEAT,
        })

    def test_missing_first_execution_model_is_not_borrowed_from_a_later_retry(self) -> None:
        rows = [
            {"at": at(timedelta(hours=-6)), "event": "manage", "ticket": 1, "decision": "FIX"},
            {"at": at(timedelta(hours=-5)), "event": "llm-usage", "ticket": 1, "stage": "worker"},
            {"at": at(timedelta(hours=-4)), "event": "attempt", "ticket": 1},
            {"at": at(timedelta(hours=-3)), "event": "claimed", "ticket": 1, "worker_model": "later"},
            {"at": at(timedelta(hours=-2)), "event": "attempt", "ticket": 1, "worker_model": "later"},
            {"at": at(timedelta(hours=-1)), "event": "merged", "ticket": 1},
        ]
        result = stats.post_fix_metrics(rows, START, NOW)
        self.assertEqual(result["by_worker_model"], {
            stats.MISSING_WORKER_MODEL: {
                "merged": 1, "re_escalated": 0, "pending": 0, "resolved": 1, "merge_rate": 1.0,
            },
        })

    def test_resolved_rate_ignores_other_stages_and_keeps_empty_models_unknown(self) -> None:
        rows = [
            {"at": at(timedelta(hours=-6)), "event": "manage", "ticket": 1, "decision": "FIX"},
            {"at": at(timedelta(hours=-5)), "event": "llm-usage", "ticket": 1,
             "stage": "triage", "worker_model": "not-a-worker"},
            {"at": at(timedelta(hours=-4)), "event": "claimed", "ticket": 1, "worker_model": "shared"},
            {"at": at(timedelta(hours=-3)), "event": "attempt", "ticket": 1, "worker_model": "shared"},
            {"at": at(timedelta(hours=-2)), "event": "merged", "ticket": 1},
            {"at": at(timedelta(hours=-6)), "event": "manage", "ticket": 2, "decision": "FIX"},
            {"at": at(timedelta(hours=-4)), "event": "claimed", "ticket": 2, "worker_model": "shared"},
            {"at": at(timedelta(hours=-1)), "event": "escalate", "ticket": 2},
            {"at": at(timedelta(hours=-6)), "event": "manage", "ticket": 3, "decision": "RETRY"},
            {"at": at(timedelta(hours=-5)), "event": "claimed", "ticket": 3, "worker_model": "unrelated"},
            {"at": at(timedelta(hours=-1)), "event": "merged", "ticket": 3},
            {"at": at(), "event": "manage", "ticket": 4, "decision": "FIX"},
            {"at": at(timedelta(hours=1)), "event": "claimed", "ticket": 4, "worker_model": ""},
            {"at": at(timedelta(hours=2)), "event": "merged", "ticket": 4},
            {"at": at(timedelta(hours=-6)), "event": "manage", "ticket": 5, "decision": "FIX"},
            {"at": at(timedelta(hours=-5)), "event": "llm-usage", "ticket": 5,
             "stage": "worker", "worker_model": ""},
            {"at": at(timedelta(hours=-1)), "event": "escalate", "ticket": 5},
        ]
        result = stats.post_fix_metrics(rows, START, NOW)
        self.assertEqual((result["cohorts"], result["merge_rate"]), (4, 0.5))
        self.assertEqual(result["by_worker_model"], {
            model: {"merged": 1, "re_escalated": 1, "pending": 0, "resolved": 2, "merge_rate": 0.5}
            for model in ("shared", stats.MISSING_WORKER_MODEL)
        })


class PostFixCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.journal = self.cfg.factory / "events.jsonl"
        self.journal.write_text("\n".join((
            json.dumps({"at": at(timedelta(days=-1)), "event": "claimed", "ticket": 7,
                        "worker_model": "openai-codex/gpt-6.1-sol"}),
            json.dumps({"at": at(timedelta(hours=-1)), "event": "merged", "ticket": 7}),
        )) + "\n")
        with gzip.open(self.cfg.factory / "events.jsonl.1.gz", "wt") as stream:
            stream.write(json.dumps({
                "at": at(timedelta(days=-2)), "event": "manage", "ticket": 7, "decision": "FIX",
            }) + "\n")

    def run_week(self, *args: str) -> str:
        out = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
             mock.patch.object(stats, "_now", return_value=NOW), \
             mock.patch.object(stats, "stuck_over_days", return_value=[]), \
             contextlib.redirect_stdout(out):
            self.assertEqual(stats.main(["--week", *args]), 0)
        return out.getvalue()

    def test_week_json_and_text_include_rotated_post_fix_cohorts(self) -> None:
        result = json.loads(self.run_week("--json"))
        self.assertEqual(result["post_fix"]["by_worker_model"]["openai-codex/gpt-6.1-sol"]["merged"], 1)
        self.assertEqual(result["post_fix"]["merge_rate"], 1.0)

        text = self.run_week()
        self.assertIn("Post-FIX outcomes by worker model", text)
        self.assertIn("openai-codex/gpt-6.1-sol", text)
        self.assertIn("resolved merge rate 1.0 (1/1)", text)
        self.assertIn("Baseline: 6/35 merged (17%)", text)
        self.assertIn("not controlled", text)


if __name__ == "__main__":
    unittest.main()
