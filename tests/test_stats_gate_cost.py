from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, stats

NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)


def stamp(seconds: int) -> str:
    return (NOW + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def evidence() -> list[dict]:
    return [
        {"at": stamp(-30), "event": "merged", "ticket": 1, "pr": 11,
         "work_type": "speed", "work_type_source": "pr", "work_type_labels": ["speed"]},
        # A repeated receipt is one merge, and its explicit class survives.
        {"at": stamp(-29), "event": "merged", "pr": 11},
        {"at": stamp(-28), "event": "merged", "ticket": 2, "pr": 12,
         "work_type": "unclassified", "work_type_source": "ticket:2", "work_type_labels": []},
        {"at": stamp(-27), "event": "merged", "pr": 13,
         "work_type": "polish", "work_type_source": "pr", "work_type_labels": ["polish"]},
        {"at": stamp(-26), "event": "merged", "ticket": 3, "pr": 14},
        {"at": stamp(-25), "event": "lifecycle", "gate_event": "gate-start", "run_id": "attempt-1",
         "started_at": stamp(-25), "ended_at": None, "duration_seconds": None,
         "ticket": 1, "head": "aaa", "host": "alpha", "tier": "attempt", "rerun": False},
        {"at": stamp(-20), "event": "lifecycle", "gate_event": "gate-run", "run_id": "attempt-1",
         "started_at": stamp(-25), "ended_at": stamp(-20), "duration_seconds": 5,
         "ticket": 1, "head": "aaa", "host": "alpha", "tier": "attempt", "rerun": False},
        {"at": stamp(-18), "event": "lifecycle", "gate_event": "gate-run", "run_id": "pr-1",
         "started_at": stamp(-20), "ended_at": stamp(-18), "duration_seconds": 2,
         "ticket": 1, "head": "aaa", "host": "beta", "tier": "pr", "rerun": True},
        {"at": stamp(-17), "event": "lifecycle", "gate_event": "gate-run", "run_id": "release-2",
         "started_at": stamp(-18), "ended_at": stamp(-17), "duration_seconds": 1,
         "ticket": 2, "head": "bbb", "host": "alpha", "tier": "release", "rerun": True},
        # An unmatched start is a run with unknown duration, never zero.
        {"at": stamp(-16), "event": "lifecycle", "gate_event": "gate-start", "run_id": "release-open",
         "started_at": stamp(-16), "ended_at": None, "duration_seconds": None,
         "ticket": 2, "head": "bbb", "host": "alpha", "tier": "release", "rerun": False},
        # Legacy timestamps alone do not supply the new measured duration/tier/rerun.
        {"at": stamp(-15), "event": "lifecycle", "stage": "gate", "kind": "enter",
         "execution_id": "legacy-life", "ticket": 3, "attempt": 1},
        {"at": stamp(-12), "event": "lifecycle", "stage": "gate", "kind": "exit",
         "execution_id": "legacy-life", "ticket": 3, "attempt": 1},
        # This is the same legacy run and must not be counted a second time.
        {"at": stamp(-11), "event": "attempt", "ticket": 3, "attempt": 1, "gate": "PASS"},
        {"at": stamp(-10), "event": "attempt", "ticket": 3, "attempt": 2, "gate": "FAIL"},
        {"at": stamp(-9), "event": "manage", "host": "idle", "decision": "FIX"},
        {"at": stamp(-8), "event": "lifecycle", "gate_event": "gate-run", "run_id": ""},
        {"at": stamp(-7), "event": "lifecycle", "stage": "gate", "kind": "noise",
         "execution_id": "malformed"},
        {"at": stamp(-6), "event": "attempt", "ticket": 4, "gate": "noise"},
    ]


class GateCostAggregationTest(unittest.TestCase):
    def test_work_split_deduplicates_receipts_and_keeps_unknown_separate(self) -> None:
        result = stats.work_type_metrics(evidence())

        self.assertEqual(result["total"], 4)
        self.assertEqual(result["counts"], {
            "speed": 1, "bug": 0, "polish": 1, "unclassified": 1, "unknown": 1,
        })
        self.assertEqual(result["percentages"], {
            "speed": 25.0, "bug": 0.0, "polish": 25.0,
            "unclassified": 25.0, "unknown": 25.0,
        })

    def test_pairs_runs_across_tiers_and_reports_unknowns_honestly(self) -> None:
        result = stats.gate_cost_metrics(evidence())

        ticket1 = result["by_ticket"]["1"]
        self.assertEqual(ticket1["total_count"], 2)
        self.assertEqual(ticket1["rerun_count"], 1)
        self.assertEqual(ticket1["total_wall_time_seconds"], 7)
        self.assertEqual(ticket1["rerun_wall_time_seconds"], 2)
        self.assertEqual(ticket1["rerun_share"], 0.5)
        self.assertEqual(set(ticket1["by_tier"]), {"attempt", "pr"})
        self.assertEqual(ticket1["by_host"]["alpha"]["total_count"], 1)
        self.assertEqual(ticket1["by_host"]["beta"]["total_count"], 1)
        self.assertEqual(ticket1["by_host"]["idle"]["total_count"], 0)
        self.assertIsNone(ticket1["by_host"]["idle"]["total_wall_time_seconds"])

        ticket2 = result["by_ticket"]["2"]
        self.assertEqual(ticket2["total_count"], 2)
        self.assertEqual(ticket2["known_total_wall_time_seconds"], 1)
        self.assertEqual(ticket2["missing_duration_count"], 1)
        self.assertIsNone(ticket2["total_wall_time_seconds"])
        self.assertEqual(ticket2["rerun_wall_time_seconds"], 1)

        ticket3 = result["by_ticket"]["3"]
        self.assertEqual(ticket3["total_count"], 2)
        self.assertEqual(ticket3["known_total_wall_time_seconds"], 0)
        self.assertIsNone(ticket3["rerun_count"])
        self.assertEqual(ticket3["missing_rerun_count"], 2)
        self.assertEqual(result["by_host"]["idle"]["total_count"], 0)
        self.assertIsNone(result["by_host"]["idle"]["total_wall_time_seconds"])

    def test_window_uses_completion_and_audit_rows_do_not_duplicate_modern_runs(self) -> None:
        rows = [
            {"event": "lifecycle", "gate_event": "gate-start", "run_id": "cross", "ticket": 8, "head": "abc",
             "host": "alpha", "tier": "attempt", "rerun": True,
             "at": stamp(-700000), "started_at": stamp(-700000)},
            {"event": "lifecycle", "gate_event": "gate-run", "run_id": "cross", "ticket": 8, "head": "abc",
             "host": "alpha", "tier": "attempt", "rerun": True, "duration_seconds": 600000,
             "at": stamp(-100000), "ended_at": stamp(-100000)},
            {"event": "attempt", "ticket": 8, "head": "abc", "attempt": 1,
             "gate": "PASS", "at": stamp(-99999)},
            {"event": "lifecycle", "gate_event": "gate-start", "run_id": "later", "ticket": 9,
             "at": stamp(-1), "started_at": stamp(-1)},
            {"event": "lifecycle", "gate_event": "gate-run", "run_id": "later", "ticket": 9,
             "at": stamp(1), "ended_at": stamp(1), "duration_seconds": 2},
        ]
        result = stats.gate_cost_metrics(rows, NOW - timedelta(days=7), NOW)
        self.assertEqual(result["by_ticket"]["8"]["total_count"], 1)
        self.assertEqual(result["by_ticket"]["8"]["rerun_wall_time_seconds"], 600000)
        self.assertEqual(result["by_ticket"]["9"]["total_count"], 0)
        self.assertIsNone(result["by_ticket"]["9"]["rerun_count"])

    def test_default_rows_keep_list_shape_and_expose_per_ticket_class_and_hosts(self) -> None:
        rows = [
            {"ticket": ticket, "state": "merged"}
            for ticket in (1, 2, 3, 4)
        ]
        stats.enrich_ticket_rows(rows, evidence())

        self.assertEqual(rows[0]["work_type"], "speed")
        self.assertEqual(rows[0]["work_type_source"], "pr")
        self.assertEqual(rows[0]["work_type_count"], 1)
        self.assertEqual(rows[0]["work_type_percentage"], 20.0)
        self.assertIsNone(rows[2]["work_type"])
        self.assertIsNone(rows[2]["work_type_source"])
        self.assertEqual(rows[2]["work_type_count"], 2)
        self.assertEqual(rows[2]["work_type_percentage"], 40.0)
        self.assertIn("idle", rows[0]["gate_cost"]["by_host"])


class StatsCliOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.journal = self.cfg.factory / "events.jsonl"
        self.rows = evidence()
        self.journal.write_text("".join(json.dumps(row) + "\n" for row in self.rows))

    def run_main(self, args: list[str]) -> str:
        output = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
             mock.patch.object(stats, "_now", return_value=NOW), \
             mock.patch.object(stats, "stuck_over_days", return_value=[]), \
             contextlib.redirect_stdout(output):
            self.assertEqual(stats.main(args), 0)
        return output.getvalue()

    def test_week_json_and_text_add_aggregates_without_changing_weekly_metrics(self) -> None:
        result = json.loads(self.run_main(["--week", "--json"]))

        self.assertEqual(result["merges"], 3)
        self.assertEqual(result["work_types"]["total"], 4)
        self.assertEqual(result["work_types"]["counts"]["unclassified"], 1)
        self.assertEqual(result["work_types"]["counts"]["unknown"], 1)
        self.assertEqual(result["gate_cost"]["by_ticket"]["1"]["total_count"], 2)
        self.assertEqual(result["gate_cost"]["by_host"]["idle"]["total_count"], 0)

        text = self.run_main(["--week"])
        self.assertIn("Work classes", text)
        self.assertIn("unclassified", text)
        self.assertIn("Gate cost by ticket", text)
        self.assertIn("Gate cost by host", text)
        self.assertIn("idle", text)
        self.assertIn("unknown", text)

    def test_default_json_remains_a_list_and_default_text_has_shared_tables(self) -> None:
        ticket_rows = [
            {
                "ticket": 1, "title": "fast work", "state": "merged", "attempts": 1,
                "review_rounds": 0, "merge_hours": 1.0, "llm_usage": {},
                "review_findings": [], "escalation_count": 0, "manager_failures": 0,
                "resolutions": [], "ready_for_human_minutes": 0.0, "requeue_count": 0,
            },
            {
                "ticket": 3, "title": "legacy work", "state": "merged", "attempts": 2,
                "review_rounds": 0, "merge_hours": 2.0, "llm_usage": {},
                "review_findings": [], "escalation_count": 0, "manager_failures": 0,
                "resolutions": [], "ready_for_human_minutes": 0.0, "requeue_count": 0,
            },
        ]

        def collect(*, progress: bool, journal_rows: list[dict]) -> list[dict]:
            del progress
            stats.enrich_ticket_rows(ticket_rows, journal_rows)
            return ticket_rows

        with mock.patch.object(stats, "collect_rows", side_effect=collect):
            result = json.loads(self.run_main(["--json", "--quiet"]))
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["work_type"], "speed")
        self.assertIsNone(result[1]["work_type"])
        self.assertIn("by_host", result[0]["gate_cost"])

        with mock.patch.object(stats, "collect_rows", side_effect=collect):
            text = self.run_main(["--quiet"])
        self.assertIn("Work classes", text)
        self.assertIn("Gate cost by ticket", text)
        self.assertIn("Gate cost by host", text)


if __name__ == "__main__":
    unittest.main()
