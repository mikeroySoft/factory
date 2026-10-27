from __future__ import annotations

import contextlib
import gzip
import io
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dashboard, stats


NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def at(delta: timedelta = timedelta()) -> str:
    return (NOW + delta).isoformat().replace("+00:00", "Z")


class WeeklyAggregationTest(unittest.TestCase):
    def test_exact_windowed_metrics_and_locality_fallback(self) -> None:
        rows = [
            {"at": at(timedelta(days=-8)), "event": "merged", "ticket": 99},
            {"at": at(timedelta(days=-6)), "event": "merged", "ticket": 1},
            {"at": at(timedelta(days=-5)), "event": "merged", "ticket": 1},
            {"at": at(timedelta(days=-4)), "event": "merged", "ticket": 2},
            {"at": at(timedelta(days=-4)), "event": "manage", "ticket": 1, "decision": "HUMAN"},
            {"at": at(timedelta(days=-3)), "event": "manage", "ticket": 2, "decision": "HUMAN"},
            {"at": at(timedelta(days=-2)), "event": "manage", "ticket": 2, "decision": "FIX"},
            {"at": at(timedelta(days=-2)), "event": "human-decision", "ticket": 2},
            {"at": at(timedelta(days=-3)), "event": "escalate", "ticket": 1, "code": "review_revise"},
            {"at": at(timedelta(days=-2)), "event": "escalate", "ticket": 1, "code": "review_revise"},
            {"at": at(timedelta(days=-1)), "event": "escalate", "ticket": 2, "code": "gate_failed"},
            {"at": at(timedelta(days=-3)), "event": "attempt", "ticket": 1, "attempt": 1, "gate": "PASS"},
            {"at": at(timedelta(days=-2)), "event": "attempt", "ticket": 2, "attempt": 1, "gate": "FAIL"},
            {"at": at(timedelta(days=-1)), "event": "attempt", "ticket": 2, "attempt": 2, "gate": "PASS"},
            {"at": at(timedelta(days=-3)), "event": "llm-usage", "stage": "worker", "model": "local-model",
             "prompt_tokens": 100, "completion_tokens": 20, "local": True},
            {"at": at(timedelta(days=-2)), "event": "llm-usage", "stage": "review", "model": "hosted-model",
             "prompt_tokens": 40, "completion_tokens": 10, "local": False},
            {"at": at(timedelta(days=-1)), "event": "llm-usage", "stage": "triage", "model": "local-model",
             "prompt_tokens": 10, "completion_tokens": 5},
            {"at": at(timedelta(hours=-1)), "event": "llm-usage", "stage": "worker", "model": None,
             "prompt_tokens": 999, "completion_tokens": 1, "local": True},
        ]

        result = stats.weekly_metrics(stats.window_rows(rows, NOW - timedelta(days=7), NOW),
                                      NOW - timedelta(days=7), NOW, "local-model")

        window = {"start": "2026-10-20T12:00:00Z", "end": "2026-10-27T12:00:00Z"}
        self.assertEqual(result, {
            "window": window,
            "merges": 2,
            "human_touches": 3,
            "human_touches_per_merge": 1.5,
            "escalations_7d": 3,
            "escalations_by_code": {"review_revise": 2, "gate_failed": 1},
            "review_nonconvergence_share": 2 / 3,
            "review_nonconvergence_share_numerator": 2,
            "review_nonconvergence_share_denominator": 3,
            "review_nonconvergence_share_window": window,
            "first_pass": 0.5,
            "first_pass_numerator": 1,
            "first_pass_denominator": 2,
            "first_pass_window": window,
            "tokens": {
                "worker": {"local-model": {"prompt": 100, "completion": 20, "calls": 1, "local": True}},
                "review": {"hosted-model": {"prompt": 40, "completion": 10, "calls": 1, "local": False}},
                "triage": {"local-model": {"prompt": 10, "completion": 5, "calls": 1, "local": True}},
            },
            "local_token_share": round(135 / 185, 6),
            "usage_missing": 1,
        })

    def test_dashboard_metrics_expose_rate_counts_and_weekly_escalations(self) -> None:
        dashboard.MAX_ATTEMPTS = 3
        recent = datetime.now(UTC).isoformat()
        tickets = [
            {"pr": {"number": 1}, "attempts": [{"attempt": 1}], "human_touch": {
                "escalation_count": 1, "escalation_times": [recent],
                "escalation_codes": [(recent, "gate_failed")], "resolutions": []}},
            {"pr": {"number": 2}, "attempts": [{"attempt": 1}, {"attempt": 2}], "human_touch": {
                "escalation_count": 0, "escalation_times": [], "escalation_codes": [], "resolutions": []}},
        ]
        result = dashboard.metrics(tickets)
        self.assertEqual({key: result[key] for key in (
            "first_pass_numerator", "first_pass_denominator", "first_pass_window",
            "bounce_rate_numerator", "bounce_rate_denominator", "bounce_rate_window", "escalations_7d",
        )}, {
            "first_pass_numerator": 1, "first_pass_denominator": 2, "first_pass_window": "dashboard tickets",
            "bounce_rate_numerator": 0, "bounce_rate_denominator": 2, "bounce_rate_window": "dashboard tickets",
            "escalations_7d": 1,
        })


class WeeklyCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.journal = self.cfg.factory / "events.jsonl"
        self.journal.write_text(json.dumps({"at": at(timedelta(days=-1)), "event": "merged", "ticket": 2}) + "\n")
        with gzip.open(self.cfg.factory / "events.jsonl.1.gz", "wt") as stream:
            stream.write(json.dumps({"at": at(timedelta(days=-2)), "event": "merged", "ticket": 1}) + "\n")

    def run_week(self, side_effect=None) -> tuple[int, dict, mock.Mock]:
        response = subprocess.CompletedProcess([], 0, json.dumps([
            {"number": 7, "title": "old", "url": "https://example/7", "updatedAt": at(timedelta(days=-8))},
            {"number": 8, "title": "new", "url": "https://example/8", "updatedAt": at(timedelta(days=-1))},
        ]), "")
        runner = mock.Mock(side_effect=side_effect, return_value=response)
        out = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
             mock.patch.object(stats, "_now", return_value=NOW), \
             mock.patch.object(stats.subprocess, "run", runner), \
             contextlib.redirect_stdout(out):
            code = stats.main(["--week", "--json"])
        return code, json.loads(out.getvalue()), runner

    def test_reads_rotated_journal_and_calls_gh_once(self) -> None:
        code, result, runner = self.run_week()
        self.assertEqual(code, 0)
        self.assertEqual(result["merges"], 2)
        self.assertEqual(result["stuck_over_days"], [
            {"number": 7, "title": "old", "url": "https://example/7", "updatedAt": at(timedelta(days=-8))},
        ])
        runner.assert_called_once()
        self.assertEqual(runner.call_args.kwargs["timeout"], 10)
        self.assertEqual(runner.call_args.args[0][:3], ["gh", "issue", "list"])

    def test_gh_timeout_is_unavailable_and_successful(self) -> None:
        code, result, runner = self.run_week(subprocess.TimeoutExpired(["gh"], 10))
        self.assertEqual(code, 0)
        self.assertEqual(result["stuck_over_days"], "unavailable")
        runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
