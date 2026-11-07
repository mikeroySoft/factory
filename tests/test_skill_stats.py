"""Trailing-week skill health uses position-matched session controls (#439)."""

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

from factory import config, stats

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
START = NOW - timedelta(days=7)


def stamp(minutes: int = 0) -> str:
    return (START + timedelta(days=1, minutes=minutes)).isoformat().replace("+00:00", "Z")


def score_row(session: str, errors: list[bool], ticket: int, minute: int = 0) -> dict:
    return {"event": "score", "ticket": ticket, "attempt": 1, "transcript": session,
            "call_errors": errors, "at": stamp(minute)}


def load_row(skill: str, session: str, ticket: int, *, index: int = 0, tokens: int = 4,
             explicit: bool = True, partial: bool = False, ok: bool = True, minute: int = 0) -> dict:
    return {"event": "skill-load", "ticket": ticket, "attempt": 1, "session": session,
            "harness": "default", "skill": skill, "path": f"skill://{skill}", "ok": ok,
            "error": None if ok else f"Unknown skill: {skill}", "partial": partial,
            "call_index": index, "explicit": explicit, "tokens": tokens, "at": stamp(minute)}


class SkillMetricsTest(unittest.TestCase):
    def test_exact_counts_for_two_skills_and_coverage(self) -> None:
        rows: list[dict] = []
        for ticket in range(1, 11):
            session = f"load-{ticket}"
            rows += [
                {"event": "attempt", "ticket": ticket, "attempt": 1, "transcript": session, "at": stamp(ticket)},
                score_row(session, [False] * 12, ticket, ticket),
                load_row("alpha", session, ticket, explicit=ticket <= 5, partial=ticket <= 2,
                         ok=ticket != 10, tokens=5, minute=ticket),
            ]
        rows.append(load_row("alpha", "load-1", 1, index=1, explicit=False, tokens=5, minute=1))
        for ticket in (20, 21):
            session = f"beta-{ticket}"
            rows += [
                {"event": "attempt", "ticket": ticket, "attempt": 1, "transcript": session, "at": stamp(ticket)},
                score_row(session, [False] * 12, ticket, ticket),
                load_row("beta", session, ticket, tokens=8, minute=ticket),
            ]
        rows += [
            {"event": "attempt", "ticket": 30, "attempt": 1, "transcript": None, "at": stamp(30)},
            {"event": "skill-coverage", "ticket": 30, "attempt": 1, "harness": "chore",
             "reason": "no transcript", "at": stamp(30)},
        ]

        result = stats.weekly_metrics(rows, START, NOW, None)
        alpha, beta = result["skills"]

        self.assertEqual({key: alpha[key] for key in (
            "skill", "loads", "sessions", "tickets", "explicit_share", "reloads_per_session",
            "partial_share", "load_failures", "tokens", "context_spent",
        )}, {
            "skill": "alpha", "loads": 11, "sessions": 10, "tickets": 10,
            "explicit_share": 5 / 11, "reloads_per_session": 0.1, "partial_share": 2 / 11,
            "load_failures": 1, "tokens": 5, "context_spent": 55,
        })
        self.assertEqual((beta["loads"], beta["sessions"], beta["tickets"], beta["tokens"],
                          beta["context_spent"], beta["after_load_error_rate"]), (2, 2, 2, 8, 16, None))
        self.assertEqual(result["skill_coverage"], 12 / 13)

    def test_position_matched_baseline_removes_early_session_bias(self) -> None:
        rows = []
        early = [False, True, False, False, False, False, True, False, False, False, False]
        for ticket in range(1, 11):
            session = f"loaded-{ticket}"
            rows += [score_row(session, early + [True] + [False] * 19, ticket),
                     load_row("alpha", session, ticket)]
        for ticket in range(20, 30):
            rows.append(score_row(f"control-{ticket}", early + [True] + [False] * 19, ticket))

        [metric] = stats.weekly_metrics(rows, START, NOW, None)["skills"]

        self.assertEqual(metric["after_load_error_rate"], 0.2)
        self.assertEqual(metric["position_matched_baseline_error_rate"], 0.2)
        self.assertEqual(metric["error_rate_excess"], 0.0)


class SkillStatsCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.journal = self.cfg.factory / "events.jsonl"
        self.response = subprocess.CompletedProcess([], 0, "[]", "")

    def run_week(self) -> str:
        out = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
             mock.patch.object(stats, "_now", return_value=NOW), \
             mock.patch.object(stats.subprocess, "run", return_value=self.response), \
             contextlib.redirect_stdout(out):
            self.assertEqual(stats.main(["--week"]), 0)
        return out.getvalue()

    def test_text_includes_rotated_load_failure_and_n_under_ten(self) -> None:
        self.journal.write_text(json.dumps({
            **load_row("outside", "old", 1), "at": (START - timedelta(seconds=1)).isoformat(),
        }) + "\n")
        with gzip.open(self.cfg.factory / "events.jsonl.1.gz", "wt") as stream:
            stream.write(json.dumps(load_row("ub-mode", "s", 2, ok=False)) + "\n")

        text = self.run_week()

        self.assertIn("Skills", text)
        self.assertIn("ub-mode", text)
        self.assertIn("Unknown skill: ub-mode", text)
        self.assertIn("n<10", text)
        self.assertNotIn("outside", text)

    def test_text_says_when_no_loads_are_recorded(self) -> None:
        self.journal.write_text(json.dumps({"event": "merged", "ticket": 1, "at": stamp()}) + "\n")
        self.assertIn("no skill loads recorded", self.run_week())


if __name__ == "__main__":
    unittest.main()
