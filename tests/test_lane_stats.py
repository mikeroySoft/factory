"""Journal-only lane metrics and CLI rendering."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, stats


class LaneMetricsTest(unittest.TestCase):
    def test_reclaims_legacy_rows_duration_and_strict_first_pass_domain(self) -> None:
        audit = {
            7: [
                {"event": "claimed", "lane": 1, "at": "2026-11-16T00:00:00Z"},
                {"event": "attempt", "lane": 1, "attempt": 1, "gate": "FAIL"},
                {"event": "review", "attempt": 1, "gate": "PASS"},
                {"event": "escalate", "at": "2026-11-16T00:00:10Z"},
                {"event": "claimed", "lane": 2, "at": "2026-11-16T00:00:20Z"},
                {"event": "attempt", "attempt": 1, "gate": "PASS"},
                {"event": "review"},
                {"event": "merged", "at": "2026-11-16T00:00:50Z"},
                {"event": "merged", "at": "2026-11-16T00:01:00Z"},
            ],
            9: [
                {"event": "claimed", "at": "2026-11-16T01:00:00Z"},
                {"event": "attempt", "attempt": 1, "gate": "PASS"},
                {"event": "merged", "at": "2026-11-16T01:00:15Z"},
            ],
        }

        self.assertEqual(stats.lane_metrics(audit), [
            {"lane": 1, "claimed": 1, "attempts": 1, "reviews": 1, "merged": 0,
             "escalations": 1, "seconds": 10.0, "first_pass": 0.0},
            {"lane": 2, "claimed": 1, "attempts": 1, "reviews": 1, "merged": 2,
             "escalations": 0, "seconds": 30.0, "first_pass": 1.0},
            {"lane": None, "claimed": 1, "attempts": 1, "reviews": 0, "merged": 1,
             "escalations": 0, "seconds": 15.0, "first_pass": 1.0},
        ])

    def test_duration_requires_both_parseable_endpoints(self) -> None:
        audit = {
            1: [
                {"event": "claimed", "lane": 1, "at": "2026-11-16T00:00:00Z"},
                {"event": "merged", "lane": 1},
            ],
            2: [
                {"event": "claimed", "lane": 2},
                {"event": "escalate", "lane": 2, "at": "2026-11-16T00:00:10Z"},
            ],
            3: [
                {"event": "claimed", "lane": 3, "at": "not-a-time"},
                {"event": "merged", "lane": 3, "at": "2026-11-16T00:00:10Z"},
            ],
        }

        self.assertEqual([row["seconds"] for row in stats.lane_metrics(audit)], [0.0, 0.0, 0.0])

    def test_event_lane_overrides_claim_and_invalid_lanes_are_unknown(self) -> None:
        audit = {7: [
            {"event": "claimed", "lane": 3},
            {"event": "attempt", "lane": 4, "attempt": 1, "gate": "PASS"},
            {"event": "review", "lane": True},
            {"event": "merged", "lane": 0},
            {"event": "escalate", "lane": "3"},
        ]}

        self.assertEqual(stats.lane_metrics(audit), [
            {"lane": 3, "claimed": 1, "attempts": 0, "reviews": 0, "merged": 0,
             "escalations": 0, "seconds": 0.0, "first_pass": None},
            {"lane": 4, "claimed": 0, "attempts": 1, "reviews": 0, "merged": 0,
             "escalations": 0, "seconds": 0.0, "first_pass": 1.0},
            {"lane": None, "claimed": 0, "attempts": 0, "reviews": 1, "merged": 1,
             "escalations": 1, "seconds": 0.0, "first_pass": None},
        ])


class LaneStatsCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(root=self.root, repo="acme/widgets")
        for row in (
            {"event": "claimed", "ticket": 1, "lane": 2, "at": "2026-11-16T00:00:00Z"},
            {"event": "attempt", "ticket": 1, "attempt": 1, "gate": "PASS"},
            {"event": "merged", "ticket": 1, "at": "2026-11-16T00:00:05Z"},
            {"event": "claimed", "ticket": 9},
        ):
            lifecycle.append(self.cfg.factory / "events.jsonl", row)

    def run_stats(self, *argv: str) -> str:
        output = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
                mock.patch.object(stats, "gh", side_effect=AssertionError("lane stats must be journal-only")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(stats.main(list(argv)), 0)
        return output.getvalue()

    def test_json_and_text_rendering(self) -> None:
        parsed = json.loads(self.run_stats("--by-lane", "--json"))
        self.assertEqual(parsed, [
            {"lane": 2, "claimed": 1, "attempts": 1, "reviews": 0, "merged": 1,
             "escalations": 0, "seconds": 5.0, "first_pass": 1.0},
            {"lane": None, "claimed": 1, "attempts": 0, "reviews": 0, "merged": 0,
             "escalations": 0, "seconds": 0.0, "first_pass": None},
        ])

        lines = self.run_stats("--by-lane").splitlines()
        self.assertEqual(lines[0].split(),
                         ["lane", "claimed", "attempts", "reviews", "merged", "escalations", "seconds", "first-pass"])
        self.assertEqual(lines[1].split(), ["2", "1", "1", "0", "1", "0", "5.0", "100.0%"])
        self.assertEqual(lines[2].split(), ["unknown", "1", "0", "0", "0", "0", "0.0", "n/a"])


if __name__ == "__main__":
    unittest.main()
