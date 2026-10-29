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
            "merge_rate": 1.0,
            "merge_rate_numerator": 2,
            "merge_rate_denominator": 2,
            "merge_rate_window": window,
            "closed_unmerged": 0,
            "closed_unmerged_by": {},
            "closed_unmerged_numerator": 0,
            "closed_unmerged_denominator": 2,
            "closed_unmerged_window": window,
            "median_open_to_merge_hours": None,
            "median_open_to_merge_hours_numerator": 0,
            "median_open_to_merge_hours_denominator": 2,
            "median_open_to_merge_hours_window": window,
            "cost_per_merged_pr": None,
            "cost_per_merged_pr_numerator": None,
            "cost_per_merged_pr_denominator": 2,
            "cost_per_merged_pr_window": window,
            "cost_unpriced": [],
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
            "local_cost_share": round(135 / 185, 6),
            "local_cost_share_numerator": 135,
            "local_cost_share_denominator": 185,
            "local_cost_share_window": window,
            "usage_missing": 1,
        })

    def test_merge_rate_closed_count_and_median_open_to_merge(self) -> None:
        rows = [
            {"at": at(timedelta(days=-6)), "event": "pr-opened", "ticket": 1, "pr": 11},
            {"at": at(timedelta(days=-5)), "event": "pr-opened", "ticket": 2, "pr": 12},
            {"at": at(timedelta(days=-4)), "event": "pr-opened", "ticket": 3, "pr": 13},
            {"at": at(timedelta(days=-3)), "event": "pr-opened", "ticket": 4, "pr": 14},
            {"at": at(timedelta(days=-5)), "event": "merged", "ticket": 1, "pr": 11},
            {"at": at(timedelta(days=-3)), "event": "merged", "ticket": 2, "pr": 12},
            {"at": at(timedelta(days=-1)), "event": "merged", "ticket": 3, "pr": 13},
            {"at": at(timedelta(hours=-1)), "event": "pr-closed", "ticket": 4, "pr": 14,
             "by": "manager", "reason": "close"},
        ]

        result = stats.weekly_metrics(rows, NOW - timedelta(days=7), NOW, None)

        self.assertEqual(result["merge_rate"], 0.75)
        self.assertEqual(result["closed_unmerged"], 1)
        self.assertEqual(result["closed_unmerged_by"], {"manager": 1})
        self.assertEqual(result["median_open_to_merge_hours"], 48.0)

    def test_no_merges_returns_none_for_merge_rate_median_and_cost(self) -> None:
        rows = [{"at": at(timedelta(days=-1)), "event": "pr-closed", "ticket": 4, "pr": 14,
                 "by": "external", "reason": "closed outside factory"}]

        result = stats.weekly_metrics(rows, NOW - timedelta(days=7), NOW, None)

        self.assertIsNone(result["merge_rate"])
        self.assertIsNone(result["median_open_to_merge_hours"])
        self.assertIsNone(result["cost_per_merged_pr"])

    def test_cost_per_merged_pr_requires_prices_for_every_model(self) -> None:
        rows = [
            {"at": at(timedelta(days=-2)), "event": "merged", "ticket": 1, "pr": 11},
            {"at": at(timedelta(days=-1)), "event": "merged", "ticket": 2, "pr": 12},
            {"at": at(timedelta(days=-3)), "event": "llm-usage", "ticket": 1, "stage": "worker",
             "model": "m1", "prompt_tokens": 1_000_000, "completion_tokens": 0},
            {"at": at(timedelta(days=-3)), "event": "llm-usage", "ticket": 2, "stage": "worker",
             "model": "m2", "prompt_tokens": 0, "completion_tokens": 500_000},
        ]
        prices = {
            "m1": {"prompt": 2.0, "completion": 4.0},
            "m2": {"prompt": 3.0, "completion": 6.0},
        }

        priced = stats.weekly_metrics(rows, NOW - timedelta(days=7), NOW, None, prices)
        unpriced = stats.weekly_metrics(rows, NOW - timedelta(days=7), NOW, None, {"m1": prices["m1"]})

        self.assertEqual(priced["cost_per_merged_pr"], 2.5)
        self.assertEqual(priced["cost_per_merged_pr_numerator"], 5.0)
        self.assertEqual(priced["cost_unpriced"], [])
        self.assertIsNone(unpriced["cost_per_merged_pr"])
        self.assertEqual(unpriced["cost_unpriced"], ["m2"])

    def test_ticketless_merges_do_not_change_pr_outcomes_or_cost(self) -> None:
        rows = [
            {"at": at(timedelta(days=-2)), "event": "merged", "ticket": 1, "pr": 11},
            {"at": at(timedelta(days=-1)), "event": "merged", "pr": 99},
            {"at": at(timedelta(days=-3)), "event": "llm-usage", "ticket": 1,
             "stage": "worker", "model": "m1", "cost": 10.0},
        ]

        result = stats.weekly_metrics(rows, NOW - timedelta(days=7), NOW, None)

        self.assertEqual(result["merges"], 1)
        self.assertEqual(result["merge_rate"], 1.0)
        self.assertEqual(result["merge_rate_denominator"], 1)
        self.assertEqual(result["cost_per_merged_pr"], 10.0)

    def test_inferred_worker_model_is_not_missing_usage(self) -> None:
        row = {"at": at(), "event": "llm-usage", "stage": "worker", "model": "configured",
               "model_inferred": True, "prompt_tokens": 1, "completion_tokens": 1}

        result = stats.weekly_metrics([row], NOW - timedelta(days=7), NOW, None)

        self.assertEqual(result["usage_missing"], 0)

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

    def run_week(self, side_effect=None, argv=None) -> tuple[int, dict | str, mock.Mock]:
        issues = [
            {"number": 7, "title": "old", "url": "https://example/7", "updatedAt": at(timedelta(days=-8))},
            {"number": 8, "title": "new", "url": "https://example/8", "updatedAt": at(timedelta(days=-1))},
        ]

        def response(command, **_kwargs):
            payload = [] if command[1:3] == ["pr", "list"] else issues
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        runner = mock.Mock(side_effect=side_effect or response)
        out = io.StringIO()
        args = argv or ["--week", "--json"]
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
             mock.patch.object(stats, "_now", return_value=NOW), \
             mock.patch.object(stats.subprocess, "run", runner), \
             contextlib.redirect_stdout(out):
            code = stats.main(args)
        output = out.getvalue()
        return code, json.loads(output) if "--json" in args else output, runner

    def test_reads_rotated_journal_and_reconciles_then_checks_stuck_tickets(self) -> None:
        code, result, runner = self.run_week()
        self.assertEqual(code, 0)
        self.assertEqual(result["merges"], 2)
        self.assertEqual(result["stuck_over_days"], [
            {"number": 7, "title": "old", "url": "https://example/7", "updatedAt": at(timedelta(days=-8))},
        ])
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 10 for call in runner.call_args_list))
        self.assertEqual(runner.call_args_list[0].args[0][:3], ["gh", "pr", "list"])
        self.assertEqual(runner.call_args_list[1].args[0][:3], ["gh", "issue", "list"])

    def test_gh_timeout_is_unavailable_and_successful(self) -> None:
        code, result, runner = self.run_week(subprocess.TimeoutExpired(["gh"], 10))
        self.assertEqual(code, 0)
        self.assertEqual(result["stuck_over_days"], "unavailable")
        self.assertEqual(runner.call_count, 2)

    def test_hosted_triage_model_is_not_legacy_local_fallback(self) -> None:
        self.cfg.llm_url = "https://api.openai.com/v1"
        self.cfg.llm_model = "shared-model"
        with self.journal.open("a") as stream:
            stream.write(json.dumps({
                "at": at(timedelta(hours=-1)), "event": "llm-usage", "stage": "worker",
                "model": "shared-model", "prompt_tokens": 4, "completion_tokens": 2,
            }) + "\n")

        code, result, _ = self.run_week()

        self.assertEqual(code, 0)
        self.assertEqual(result["local_token_share"], 0)
        self.assertIs(result["tokens"]["worker"]["shared-model"]["local"], False)

    def test_reconciliation_records_external_close_once(self) -> None:
        rows = [{"at": at(timedelta(days=-6)), "event": "pr-opened", "ticket": 7, "pr": 17}]
        response = subprocess.CompletedProcess([], 0, json.dumps([
            {"number": 17, "closedAt": at(timedelta(days=-1)), "mergedAt": None},
        ]), "")
        with mock.patch.object(stats.subprocess, "run", return_value=response) as runner:
            stats.reconcile_closed_prs(self.journal, rows, NOW - timedelta(days=7))
            stats.reconcile_closed_prs(self.journal, rows, NOW - timedelta(days=7))

        closes = [row for row in rows if row.get("event") == "pr-closed"]
        self.assertEqual(closes, [{
            "at": at(timedelta(days=-1)), "event": "pr-closed", "ticket": 7, "pr": 17,
            "by": "external", "reason": "closed outside factory",
        }])
        self.assertEqual(runner.call_count, 2)

    def test_text_output_names_new_weekly_metrics(self) -> None:
        code, output, _ = self.run_week(argv=["--week"])

        self.assertEqual(code, 0)
        for label in ("Merge rate:", "Closed unmerged:", "Median open→merge:", "Cost per merged PR:"):
            self.assertIn(label, output)


if __name__ == "__main__":
    unittest.main()
