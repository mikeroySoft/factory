from __future__ import annotations

import unittest
from datetime import date

from factory import dashboard, stats, watch


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
        result = dashboard.metrics(tickets)
        self.assertEqual(result["risk"]["Low"]["merged_prs"], 1)
        self.assertEqual(result["risk"]["High"]["human_interventions_per_merged_pr"], 1.0)

    def test_revert_rate_control_band_is_split_by_tier(self) -> None:
        day = date(2026, 10, 27)
        events = [
            {"event": "risk", "ticket": 7, "tier": "High", "at": "2026-10-27T01:00:00Z"},
            {"event": "merged", "ticket": 7, "at": "2026-10-27T02:00:00Z"},
            {"event": "revert", "ticket": 7, "at": "2026-10-27T03:00:00Z"},
        ]
        series = watch.metric_series(events, day, day)
        self.assertEqual(series["revert_rate_by_tier:High"][day.isoformat()]["value"], 1.0)


if __name__ == "__main__":
    unittest.main()
