from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from factory import config, dashboard, dispatch, stats, watch


class RiskStatsTest(unittest.TestCase):
    def test_per_tier_metrics_reuse_human_round_and_escape_fields(self) -> None:
        rows = [
            {
                "ticket": 1, "state": "merged", "risk_tier": "Low",
                "escalation_count": 0, "ready_for_human_minutes": 0,
                "review_rounds": 1, "escalation_codes": [],
            },
            {
                "ticket": 2, "state": "merged", "risk_tier": "High",
                "escalation_count": 1, "ready_for_human_minutes": 12,
                "review_rounds": 2,
                "escalation_codes": [("2026-10-27T00:00:00+00:00", "risk_disagreement")],
            },
        ]
        result = stats.risk_metrics(rows, escape_tickets={2})
        self.assertEqual(result["Low"]["human_interventions_per_merged_pr"], 0.0)
        self.assertEqual(result["High"]["ready_for_human_minutes_per_merged_pr"], 12.0)
        self.assertEqual(result["High"]["rounds_to_pass"], 2.0)
        self.assertEqual(result["High"]["risk_disagreements_per_merged_pr"], 1.0)
        self.assertEqual(result["High"]["escape_rate"], 1.0)

    def test_second_review_does_not_count_as_rework(self) -> None:
        reviews = [
            {"event": "review", "role": "primary"},
            {"event": "review", "role": "second"},
        ]
        self.assertEqual(stats.rework_rounds(reviews), 0)
        reviews.append({"event": "review", "role": "primary"})
        self.assertEqual(stats.rework_rounds(reviews), 1)

    def test_dashboard_reports_metrics_by_recorded_tier(self) -> None:
        tickets = []
        for tier in ("Low", "High"):
            tickets.append({
                "number": len(tickets) + 1,
                "risk_tier": tier,
                "attempts": [{"attempt": 1}],
                "pr": {"merged_at": "2026-10-27T00:00:00Z", "corrections": {}},
                "human_touch": {
                    "escalation_count": int(tier == "High"),
                    "ready_for_human_minutes": 3 if tier == "High" else 0,
                    "escalation_codes": [], "escalation_times": [], "resolutions": [],
                },
                "spend": {"rounds": 1},
            })
        dashboard.MAX_ATTEMPTS = 3
        result = dashboard.metrics(tickets, escape_tickets={2})
        self.assertEqual(result["risk"]["Low"]["merged_prs"], 1)
        self.assertEqual(result["risk"]["High"]["human_interventions_per_merged_pr"], 1.0)
        self.assertEqual(result["risk"]["High"]["escape_rate"], 1.0)

    def test_revert_rate_control_band_is_split_by_tier(self) -> None:
        day = date(2026, 10, 27)
        events = [
            {"event": "risk", "ticket": 7, "tier": "High", "at": "2026-10-27T01:00:00Z"},
            {"event": "merged", "ticket": 7, "at": "2026-10-27T02:00:00Z"},
            {"event": "escape", "ticket": 7, "at": "2026-10-27T03:00:00Z"},
        ]
        series = watch.metric_series(events, day, day)
        self.assertEqual(series["revert_rate_by_tier:High"][day.isoformat()]["value"], 1.0)

    def test_revert_rate_attributes_escape_closed_after_merge(self) -> None:
        merged = date(2026, 10, 1)
        escaped = date(2026, 10, 10)
        events = [
            {"event": "risk", "ticket": 7, "tier": "High", "at": "2026-10-01T01:00:00Z"},
            {"event": "merged", "ticket": 7, "at": "2026-10-01T02:00:00Z"},
            {"event": "escape", "ticket": 7, "at": "2026-10-10T03:00:00Z"},
        ]
        series = watch.metric_series(events, merged, escaped)["revert_rate_by_tier:High"]
        self.assertEqual(series[merged.isoformat()]["value"], 0.0)
        self.assertEqual(series[escaped.isoformat()]["value"], 1.0)

    def test_escape_events_use_real_stats_attribution_rows(self) -> None:
        rows = [{
            "bug": 419,
            "closed_at": "2026-10-27T03:00:00Z",
            "candidates": [{"ticket": 7}, {"ticket": 8}],
        }]
        self.assertEqual(
            stats.escape_events(rows),
            [
                {"event": "escape", "ticket": 7, "bug": 419, "at": "2026-10-27T03:00:00Z"},
                {"event": "escape", "ticket": 8, "bug": 419, "at": "2026-10-27T03:00:00Z"},
            ],
        )

    def test_dashboard_does_not_scan_escapes_without_risk_tickets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", lock=Path(directory) / "gpu.lock")
            dashboard.configure(cfg)
            with (
                mock.patch.object(dashboard, "github", side_effect=RuntimeError("offline")),
                mock.patch.object(dashboard, "dispatcher", return_value={}),
                mock.patch.object(dashboard, "upstream_state", return_value={}),
                mock.patch.object(dashboard, "triage_llm_online", return_value=False),
                mock.patch.object(dashboard.stats, "dispatcher_login", return_value=""),
                mock.patch.object(dashboard.stats, "worker_metrics", return_value={}),
                mock.patch.object(dashboard.stats, "escapes", side_effect=AssertionError("escape scan")),
                mock.patch.object(dashboard.config, "engine_commit", return_value=None),
                mock.patch.object(dashboard.config, "engine_drift", return_value=None),
            ):
                snapshot = dashboard.snapshot()

        self.assertNotIn("risk", snapshot["metrics"])

    def test_tier_escape_control_band_keeps_fractional_rates(self) -> None:
        day = date(2026, 10, 27)
        events = [
            {"event": "risk", "ticket": 7, "tier": "High", "at": "2026-10-27T01:00:00Z"},
            {"event": "risk", "ticket": 8, "tier": "High", "at": "2026-10-27T01:00:00Z"},
            {"event": "merged", "ticket": 7, "at": "2026-10-27T02:00:00Z"},
            {"event": "merged", "ticket": 8, "at": "2026-10-27T02:00:00Z"},
            {"event": "escape", "ticket": 7, "at": "2026-10-27T03:00:00Z"},
        ]
        series = watch.metric_series(events, day, day)
        self.assertEqual(series["revert_rate_by_tier:High"][day.isoformat()]["value"], 0.5)

    def test_watch_escape_scan_key_error_is_visible_and_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets")
            cfg.watch_bands = [mock.Mock(metric="revert_rate_by_tier")]
            dispatch.configure(cfg)
            with mock.patch.object(stats, "escapes", side_effect=KeyError("candidates")), \
                    mock.patch.object(watch, "signals", return_value=[]), \
                    mock.patch.object(dispatch, "log") as log:
                self.assertEqual(watch.run_pass(cfg), [])
            log.assert_called_once_with("watch: escape attribution unavailable (KeyError)")


class DashboardEscapeCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        dashboard.configure(config.Config(root, "acme/widgets", lock=root / "gpu.lock"))
        self.ticket = {
            "number": 7, "risk_tier": "High", "pr": {"merged_at": "2026-10-27"},
            "lock_held": False,
        }
        patches = {
            "github": {"repository": {
                "issues": {"nodes": [{"number": 7}]}, "pullRequests": {"nodes": []},
            }},
            "selected_issue": True, "build_ticket": self.ticket,
            "dispatcher": {}, "upstream_state": {}, "triage_llm_online": False,
            "metrics": {}, "review_queue": [],
        }
        for name, result in patches.items():
            patcher = mock.patch.object(dashboard, name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, result in (("dispatcher_login", ""), ("worker_metrics", {})):
            patcher = mock.patch.object(dashboard.stats, name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("engine_commit", "engine_drift"):
            patcher = mock.patch.object(dashboard.config, name, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dashboard_escape_failure_is_reported_and_retried(self) -> None:
        with mock.patch.object(dashboard.stats, "escapes", side_effect=[
            RuntimeError("scan offline"),
            [{"bug": 9, "closed_at": "2026-10-27", "candidates": [{"ticket": 7}]}],
        ]) as scan:
            first = dashboard.snapshot()
            second = dashboard.snapshot()
        self.assertTrue(any("escapes: scan offline" in error for error in first["errors"]))
        self.assertEqual(scan.call_count, 2)
        self.assertFalse(any(error.startswith("escapes:") for error in second["errors"]))
        self.assertEqual(dashboard._escape_tickets, {7})

    def test_dashboard_successful_escape_cache_refreshes(self) -> None:
        with mock.patch.object(dashboard.time, "monotonic", side_effect=[100.0, 101.0, 1000.0]), \
                mock.patch.object(dashboard.stats, "escapes", side_effect=[
                    [], [{"bug": 9, "closed_at": "2026-10-27", "candidates": [{"ticket": 7}]}],
                ]) as scan:
            dashboard.snapshot()
            dashboard.snapshot()
            self.assertEqual(scan.call_count, 1)
            dashboard.snapshot()
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(dashboard._escape_tickets, {7})


if __name__ == "__main__":
    unittest.main()
