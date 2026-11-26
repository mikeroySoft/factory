"""Boundary coverage for release-bar parsing and revision-bound soak evidence."""
from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle, release, stats
from tests import test_release as fixtures

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class ReleaseBoundaryTest(unittest.TestCase):
    new_repo = fixtures.ReleaseTest.new_repo
    git = fixtures.ReleaseTest.git
    write_release_docs = fixtures.ReleaseTest.write_release_docs

    def setUp(self) -> None:
        self.new_repo()
        self.cfg.host_name = "tank"
        self.cfg.watch_engine_repo = self.cfg.repo
        self.host_id = "tank-host-id"
        self.since = NOW - timedelta(hours=48)
        self.latest = {
            "tag": "v1.2.3",
            "sha": "a" * 40,
            "date": self.since.isoformat(),
        }
        self.enterContext(mock.patch.object(release, "datetime", FrozenDateTime))
        self.enterContext(mock.patch.object(lifecycle, "_host_id", return_value=self.host_id))
        self.filings = self.enterContext(mock.patch.object(release, "_gh", return_value=[[]]))
        self.enterContext(mock.patch.object(stats, "configure"))
        self.escapes = self.enterContext(mock.patch.object(stats, "escapes", return_value=[]))

    @staticmethod
    def bar_message(value: dict) -> str:
        return f"Release quality\n\n```json\n{json.dumps(value)}\n```"

    def append(self, row: dict) -> None:
        lifecycle.append(self.cfg.factory / "events.jsonl", row)

    def pass_pair(
        self,
        at: datetime,
        execution_id: str,
        *,
        entry: dict | None = None,
        exit: dict | None = None,
    ) -> None:
        common = {
            "schema_version": 1,
            "stage": "dispatcher",
            "execution_id": execution_id,
            "host_id": self.host_id,
            "at": at.isoformat(),
        }
        entered = {
            **common,
            "event": "lifecycle",
            "kind": "enter",
            "engine_version": self.latest["tag"][1:],
            "engine_commit": self.latest["sha"],
        }
        completed = {
            **common,
            "event": "lifecycle",
            "kind": "exit",
            "outcome": "completed",
        }
        entered.update(entry or {})
        completed.update(exit or {})
        self.append(entered)
        self.append(completed)

    def complete_soak(self) -> None:
        self.pass_pair(NOW - timedelta(hours=24, minutes=30), "first")
        self.pass_pair(NOW - timedelta(hours=8), "middle")
        self.pass_pair(NOW, "last")

    def test_bar_accepts_zero_and_exact_upper_bounds_in_current_format(self) -> None:
        pending = dict.fromkeys(release.PENDING, 0)
        bars = (
            {"human_rate": 0, "first_pass": 1, "tickets": 0,
             "human_ticket_rate": 1, **pending},
            {"human_rate": 1, "first_pass": 0, "tickets": 7,
             "human_ticket_rate": 0, "pending_tickets": 2,
             "pending_human_touches": 1, "pending_first_passes": 3,
             "pending_attempts": 4},
        )
        for bar in bars:
            with self.subTest(bar=bar):
                parsed = release._bar(self.bar_message(bar))
                self.assertEqual(parsed, bar)
                self.assertIsInstance(parsed["human_rate"], float)
                self.assertIsInstance(parsed["first_pass"], float)
                self.assertIsInstance(parsed["human_ticket_rate"], float)

    def test_bar_rejects_every_metric_outside_its_range(self) -> None:
        valid = {
            "human_rate": 0.5,
            "first_pass": 0.5,
            "tickets": 0,
            "human_ticket_rate": 0.5,
            **dict.fromkeys(release.PENDING, 0),
        }
        invalid = [
            *({key: value} for key in ("human_rate", "first_pass", "human_ticket_rate")
              for value in (-0.000001, 1.000001, float("nan"), float("inf"), float("-inf"))),
            {"tickets": -1},
            *({key: -1} for key in release.PENDING),
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "ranges"):
                release._bar(self.bar_message({**valid, **change}))

    def test_soak_window_is_since_exclusive_and_now_inclusive_with_exact_proof(self) -> None:
        self.complete_soak()
        self.pass_pair(self.since, "at-since")
        self.pass_pair(self.since - timedelta(microseconds=1), "before-since")
        self.pass_pair(NOW + timedelta(microseconds=1), "future")

        reason, proof = release._soak(self.cfg, self.latest)

        self.assertIsNone(reason)
        self.assertEqual(proof["passes"], 3)
        self.assertEqual(proof["hours"], 24.5)
        self.assertEqual(proof["first_pass"], (NOW - timedelta(hours=24, minutes=30)).isoformat())
        self.assertEqual(proof["last_pass"], NOW.isoformat())
        self.assertEqual(proof["checked_at"], NOW.isoformat())
        self.assertEqual(
            (proof["experimental_tag"], proof["commit"], proof["host"], proof["host_id"]),
            (self.latest["tag"], self.latest["sha"], "tank", self.host_id),
        )

    def test_soak_rejects_each_invalid_entry_or_exit_identity_field(self) -> None:
        cases = {
            "entry event": ({"event": "attempt"}, {}),
            "entry event after lifecycle": ({"event": "release"}, {}),
            "entry kind": ({"kind": "exit"}, {}),
            "entry kind before enter": ({"kind": "begin"}, {}),
            "entry stage": ({"stage": "dispatch"}, {}),
            "entry host": ({"host_id": "another-host"}, {}),
            "entry engine version": ({"engine_version": "1.2.4"}, {}),
            "entry engine commit": ({"engine_commit": "b" * 40}, {}),
            "entry schema": ({"schema_version": 2}, {}),
            "exit event": ({}, {"event": "attempt"}),
            "exit kind": ({}, {"kind": "enter"}),
            "exit stage": ({}, {"stage": "dispatch"}),
            "exit stage after dispatcher": ({}, {"stage": "worker"}),
            "exit host": ({}, {"host_id": "another-host"}),
            "exit outcome": ({}, {"outcome": "mechanism_failure"}),
            "execution identity": ({}, {"execution_id": "different-execution"}),
        }
        for label, (entry, exit) in cases.items():
            with self.subTest(field=label):
                (self.cfg.factory / "events.jsonl").unlink(missing_ok=True)
                self.pass_pair(NOW - timedelta(hours=26), "invalid", entry=entry, exit=exit)
                self.pass_pair(NOW - timedelta(hours=1), "valid")

                reason, proof = release._soak(self.cfg, self.latest)

                self.assertIn("24h", reason or "")
                self.assertEqual(proof["passes"], 1)
                self.assertEqual(proof["hours"], 0)
                self.assertNotIn("first_pass", proof)
                self.assertNotIn("last_pass", proof)

    def test_soak_requires_the_tank_runtime_host(self) -> None:
        self.cfg.host_name = "other-host"
        self.complete_soak()

        reason, proof = release._soak(self.cfg, self.latest)

        self.assertIn("Tank", reason or "")
        self.assertEqual(proof, {"experimental_tag": self.latest["tag"], "commit": self.latest["sha"]})

    def test_watch_blocks_only_non_pr_issues_with_fingerprints(self) -> None:
        self.complete_soak()
        self.filings.return_value = [[
            {"number": 1, "pull_request": {"url": "example"}, "body": "Fingerprint: pr-bug\n"},
            {"number": 2, "body": "ordinary issue without evidence"},
        ]]

        reason, proof = release._soak(self.cfg, self.latest)

        self.assertIsNone(reason)
        self.assertEqual(proof["open_watch_bugs"], 0)

        self.filings.return_value = [[{"number": 3, "body": "Fingerprint: engine-bug\n"}]]
        reason, proof = release._soak(self.cfg, self.latest)
        self.assertIn("watch-filed", reason or "")
        self.assertNotIn("open_watch_bugs", proof)

    def test_skipped_status_or_nonempty_skipped_fixes_make_escape_evidence_incomplete(self) -> None:
        self.complete_soak()
        escape_row = {
            "bug": 9,
            "status": "candidates",
            "closed_at": (NOW - timedelta(hours=2)).isoformat(),
            "candidates": [],
            "skipped": [],
        }
        for change in ({"status": "skipped"}, {"skipped": ["missing-fix"]}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "incomplete"):
                self.escapes.return_value = json.loads(json.dumps([{**escape_row, **change}]))
                release._soak(self.cfg, self.latest)

        self.escapes.return_value = [escape_row]
        reason, proof = release._soak(self.cfg, self.latest)
        self.assertIsNone(reason)
        self.assertEqual(proof["escape_rate"], 0)

    def test_escape_window_excludes_pre_tag_and_future_defects_but_includes_now(self) -> None:
        self.complete_soak()
        self.escapes.return_value = [
            {"bug": index, "status": "candidates", "skipped": [],
             "closed_at": at.isoformat(), "candidates": [{"ticket": index}]}
            for index, at in enumerate((
                self.since - timedelta(microseconds=1), self.since,
                NOW + timedelta(microseconds=1),
            ))
        ]
        reason, proof = release._soak(self.cfg, self.latest)
        self.assertIsNone(reason)
        self.assertEqual(proof["escape_rate"], 0)
        self.escapes.return_value.append({
            "bug": 99, "status": "candidates", "skipped": [],
            "closed_at": NOW.isoformat(), "candidates": [{"ticket": 99}],
        })
        reason, proof = release._soak(self.cfg, self.latest)
        self.assertIn("escaped defects", reason or "")
        self.assertEqual(proof["escape_rate"], 1)

    def test_soak_rates_with_no_merged_tickets_still_count_failures(self) -> None:
        self.complete_soak()
        self.append({"event": "escalate", "ticket": 1, "at": NOW.isoformat()})
        self.escapes.return_value = [{
            "bug": 99, "status": "candidates", "skipped": [],
            "closed_at": NOW.isoformat(), "candidates": [{"ticket": 1}],
        }]
        reason, proof = release._soak(self.cfg, self.latest)
        self.assertIn("escalations", reason or "")
        self.assertEqual(proof["escalation_rate"], 1)
        self.assertEqual(proof["escape_rate"], 1)

    def test_escalation_and_escape_rates_use_distinct_tickets_and_fractional_denominator(self) -> None:
        self.complete_soak()
        for ticket in (101, 102, 103):
            self.append({"event": "merged", "ticket": ticket,
                         "at": (NOW - timedelta(hours=6)).isoformat()})
        self.append({"event": "escalate", "ticket": 101,
                     "at": (NOW - timedelta(hours=5)).isoformat()})
        self.escapes.return_value = [{
            "bug": 8,
            "status": "candidates",
            "skipped": [],
            "closed_at": (NOW - timedelta(hours=4)).isoformat(),
            "candidates": [{"ticket": 102}, {"ticket": 102}],
        }]

        reason, proof = release._soak(self.cfg, self.latest)

        self.assertIn("escalations", reason or "")
        self.assertAlmostEqual(proof["escalation_rate"], 1 / 3)
        self.assertAlmostEqual(proof["escape_rate"], 1 / 3)

    def test_escape_rate_blocks_with_multiple_merged_tickets(self) -> None:
        self.complete_soak()
        for ticket in (201, 202, 203, 204):
            self.append({"event": "merged", "ticket": ticket,
                         "at": (NOW - timedelta(hours=6)).isoformat()})
        self.escapes.return_value = [{
            "bug": 7,
            "status": "candidates",
            "skipped": [],
            "closed_at": (NOW - timedelta(hours=3)).isoformat(),
            "candidates": [{"ticket": 202}],
        }]

        reason, proof = release._soak(self.cfg, self.latest)

        self.assertIn("escaped defects", reason or "")
        self.assertEqual(proof["escalation_rate"], 0)
        self.assertEqual(proof["escape_rate"], 0.25)


if __name__ == "__main__":
    unittest.main()
