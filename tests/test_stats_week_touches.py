"""Weekly human touches use the release bar's decision identity contract."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, release, stats


class WeeklyHumanTouchesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.end = datetime(2026, 10, 27, 12, tzinfo=UTC)
        self.start = self.end - timedelta(days=7)
        self.rows = [
            {"event": "human-decision", "decision_id": "shared", "ticket": ticket,
             "status": status}
            for status in ("prepared", "started", "applied")
            for ticket in (1, 2)
        ] + [
            {"event": "human-decision", "decision_id": "prepared-only", "ticket": 1,
             "status": "prepared"},
            {"event": "human-decision", "ticket": 1, "status": "applied"},
            {"event": "human-decision", "decision_id": "", "ticket": 1, "status": "applied"},
            {"event": "manage", "ticket": 1, "decision": "HUMAN"},
            {"event": "manage", "ticket": 1, "decision": "HUMAN"},
            {"event": "manage", "ticket": 1, "decision": "FIX"},
        ]
        for row in self.rows:
            row["at"] = (self.end - timedelta(days=1)).isoformat()

    def test_deduplicates_decision_statuses_and_targets_like_release(self) -> None:
        rows = self.rows + [
            {"at": self.end.isoformat(), "event": "merged", "ticket": ticket}
            for ticket in (1, 1, 2)
        ]
        result = stats.weekly_metrics(rows, self.start, self.end, None)

        self.assertEqual(result["human_touches"], 6)
        self.assertEqual(result["human_touches_per_merge"], 3.0)
        cfg = config.Config(Path("/unused"), "acme/widgets")
        with mock.patch.object(release.lifecycle, "read_events", return_value=rows):
            measured = release.measure(cfg, self.start)
        self.assertEqual(result["human_touches"], measured["human_touches"])
        self.assertEqual(result["human_touches_per_merge"], measured["human_rate"])

    def test_counts_touches_without_merges(self) -> None:
        result = stats.weekly_metrics(self.rows, self.start, self.end, None)

        self.assertEqual(result["human_touches"], 6)
        self.assertIsNone(result["human_touches_per_merge"])


if __name__ == "__main__":
    unittest.main()
