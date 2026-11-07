"""Weekly ready-to-merge, cost and all-repeat success evidence, without network."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, promotion, stats

START = datetime(2026, 10, 26, tzinfo=UTC)
END = START + timedelta(days=7)


def event(kind: str, ticket: int, hours: float, **fields) -> dict:
    return {"event": kind, "ticket": ticket, "at": (START + timedelta(hours=hours)).isoformat(), **fields}


def ready(ticket: int, hours: float) -> dict:
    return event("labeled", ticket, hours, label={"name": config.LABEL_AGENT})


class WeeklyCycleTest(unittest.TestCase):
    def test_latest_premerge_ready_exact_median_and_stable_percentile_interval(self) -> None:
        rows = [
            ready(1, -24), ready(1, 1), event("merged", 1, 3), ready(1, 4),
            ready(2, 1), event("merged", 2, 5),
            ready(3, 1), event("merged", 3, 9),
            ready(4, 1), event("merged", 4, 17),
            ready(5, 1), event("merged", 5, 33),
            ready(6, -2), event("merged", 6, 200),
            event("merged", 7, 10),
            ready(8, -10), event("merged", 8, -1),
        ]
        result = stats.weekly_cycle_metrics(rows, START, END, 6.6)
        self.assertEqual(result["cycle_median_hours"], 8)
        self.assertEqual(result["cycle_ci95"], [2, 32])
        self.assertEqual(result["cycle_n"], 5)
        self.assertEqual(result["cycle_open"], 1)
        self.assertEqual(result["cycle_target_hours"], 6.6)
        self.assertIsNone(result["cycle_reason"])
        self.assertEqual(result, stats.weekly_cycle_metrics(list(reversed(rows)), START, END, 6.6))

    def test_readiness_before_window_duplicates_and_half_open_boundaries(self) -> None:
        rows = [ready(1, -12), event("merged", 1, 0), event("merged", 1, 0),
                ready(2, 5), event("merged", 2, 168), ready(3, 168),
                ready(4, 3), event("merged", 4, 3),
                event("labeled", 5, 1, label={"name": "needs-triage"})]
        result = stats.weekly_cycle_metrics(rows, START, END, 12)
        self.assertEqual(result["cycle_median_hours"], 12)
        self.assertEqual(result["cycle_ci95"], [12, 12])
        self.assertEqual(result["cycle_n"], 1)
        self.assertEqual(result["cycle_open"], 1)
        self.assertEqual(result["cycle_target_hours"], 12)

    def test_empty_and_missing_readiness_are_null_with_reason(self) -> None:
        for rows in ([], [event("merged", 1, 5)], [ready(1, 5)]):
            with self.subTest(rows=rows):
                result = stats.weekly_cycle_metrics(rows, START, END, 6.6)
                self.assertIsNone(result["cycle_median_hours"])
                self.assertIsNone(result["cycle_ci95"])
                self.assertEqual(result["cycle_n"], 0)
                self.assertTrue(result["cycle_reason"])

    def test_bootstrap_is_percentile_resampling_of_ticket_durations(self) -> None:
        # Uneven sample: pins percentile interpolation, not a normal-theory interval.
        rows = [row for ticket, duration in enumerate([1, 2, 7, 30], 1)
                for row in (ready(ticket, 0), event("merged", ticket, duration))]
        result = stats.weekly_cycle_metrics(rows, START, END, 6.6)
        self.assertEqual(result["cycle_median_hours"], 4.5)
        self.assertEqual(result["cycle_ci95"], [1, 30])


class WeeklyOutcomeTest(unittest.TestCase):
    def metrics(self, rows):
        return stats.weekly_outcome_metrics(rows, START, END, 6.6)

    def test_cost_all_weekly_spend_per_distinct_merge_without_usage_copies(self) -> None:
        rows = [event("merged", 1, 4), event("merged", 1, 4), event("merged", 2, 10),
                event("llm-usage", 1, 1, stage="worker", attempt=1, cost=3),
                event("attempt", 1, 2, attempt=1, cost=3),
                event("llm-usage", 1, 2, stage="review", cost=1),
                event("attempt", 3, 3, attempt=1, cost=2),
                event("llm-usage", 4, 4, stage="triage", cost=0),
                event("attempt", 5, -1, cost=100),
                event("decision", 1, 1, cost={"seconds": 3})]
        result = self.metrics(rows)
        self.assertEqual(result["cost_per_merged_ticket"], 3)
        self.assertEqual(result["cost_n"], 4)
        self.assertIsNone(result["cost_reason"])
        self.assertEqual(self.metrics([event("merged", 1, 1), event("attempt", 1, 1, cost=0)])[
            "cost_per_merged_ticket"], 0)

    def test_missing_cost_or_zero_merges_is_null_with_reason(self) -> None:
        for rows, count in (([], 0), ([event("merged", 1, 1)], 0),
                            ([event("attempt", 1, 1, cost=5)], 1)):
            with self.subTest(rows=rows):
                result = self.metrics(rows)
                self.assertIsNone(result["cost_per_merged_ticket"])
                self.assertEqual(result["cost_n"], count)
                self.assertTrue(result["cost_reason"])

    def test_latest_eval_run_all_k_success_short_cases_excluded(self) -> None:
        k = promotion.MIN_REPEATS
        rows = []
        def repeat(case, run, index, hours, approved=True, status="completed"):
            rows.append(event("eval", 1, hours, case=case, profile="default", run=run,
                              repeat=index, status=status, approved=approved))
        for i in range(1, k + 1):
            repeat("pass", "old", i, 1, False)
            repeat("pass", "new", i, 2)
            repeat("mixed", "new", i, 2, i != k)
            repeat("short", "old", i, 1)
            repeat("unavailable", "new", i, 2, status="not_tested")
        for i in range(1, k):
            repeat("short", "new", i, 3)
        # Duplicate repeat cannot complete a short run; a later row supersedes one repeat.
        repeat("short", "new", 1, 4)
        repeat("mixed", "new", 1, 4, False)
        result = self.metrics(list(reversed(rows)))
        self.assertEqual(result["pass_k"], 1 / 3)
        self.assertEqual(result["k"], k)
        self.assertEqual(result["cases"], 3)
        self.assertEqual(result["pass_k_excluded"], 1)
        self.assertIsNone(result["pass_k_reason"])
        empty = self.metrics([])
        self.assertIsNone(empty["pass_k"])
        self.assertEqual(empty["cases"], 0)
        self.assertTrue(empty["pass_k_reason"])


class WeeklyOutcomeCommandTest(unittest.TestCase):
    def test_text_and_json_use_history_target_and_explicit_null_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", stats_cycle_target_hours=10)
            cfg.factory.mkdir()
            rows = [ready(1, -2), event("merged", 1, 6), event("attempt", 1, 4, cost=2)]
            (cfg.factory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            with mock.patch.object(stats, "resolve", return_value=cfg), \
                    mock.patch.object(stats, "_now", return_value=END), \
                    mock.patch.object(stats, "stuck_over_days", return_value=[]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(stats.main(["--week", "--json"]), 0)
                result = json.loads(out.getvalue())
                self.assertEqual(result["cycle_median_hours"], 8)
                self.assertEqual(result["cycle_ci95"], [8, 8])
                self.assertEqual(result["cycle_target_hours"], 10)
                self.assertEqual(result["cost_per_merged_ticket"], 2)
                self.assertIsNone(result["pass_k"])
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    stats.main(["--week"])
                for text in ("cycle_median_hours: 8", "cycle_ci95: [8", "cycle_n: 1",
                             "cycle_target_hours: 10", "cost_per_merged_ticket: 2", "cost_n: 1",
                             "pass_k: null", "no complete eval cases"):
                    self.assertIn(text, out.getvalue())

    def test_github_events_use_latest_label_and_actual_pr_merge_time(self) -> None:
        cfg = config.Config(Path("/unused"), "acme/widgets")
        rows = [event("pr-opened", 1, -50, pr=11), event("merged", 1, 10, pr=11),
                event("pr-opened", 2, 1, pr=12)]
        def gh(*args):
            if args[0] == "api":
                ticket = int(args[1].split("/")[-2])
                return [[{"event": "labeled", "created_at": (START - timedelta(hours=1)).isoformat(),
                          "label": {"name": config.LABEL_AGENT}},
                         {"event": "labeled", "created_at": (START + timedelta(hours=ticket)).isoformat(),
                          "label": {"name": config.LABEL_AGENT}}]]
            self.assertEqual(args[0:2], ("pr", "view"))
            return {"mergedAt": (START + timedelta(hours=7)).isoformat() if args[2] == "11" else None}
        with mock.patch.object(stats, "gh", side_effect=gh), \
                mock.patch.object(stats.subprocess, "run", side_effect=AssertionError("network")):
            enriched = stats.cycle_events(cfg, rows, START, END)
        result = stats.weekly_cycle_metrics(enriched, START, END, 6.6)
        self.assertEqual(result["cycle_median_hours"], 6)
        self.assertEqual(result["cycle_n"], 1)
        self.assertEqual(result["cycle_open"], 1)

    def test_github_failure_is_missing_evidence_not_claim_time(self) -> None:
        cfg = config.Config(Path("/unused"), "acme/widgets")
        rows = [event("claimed", 1, 1), event("merged", 1, 10, pr=11)]
        with mock.patch.object(stats, "gh", side_effect=subprocess.CalledProcessError(1, "gh")):
            result = stats.weekly_cycle_metrics(stats.cycle_events(cfg, rows, START, END), START, END, 6.6)
        self.assertIsNone(result["cycle_median_hours"])
        self.assertTrue(result["cycle_reason"])


if __name__ == "__main__":
    unittest.main()
