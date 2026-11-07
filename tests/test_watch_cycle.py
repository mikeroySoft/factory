from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, watch

REPO = "acme/widgets"
NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
WEEK_START = datetime(2026, 10, 26, tzinfo=UTC)
WEEK_END = datetime(2026, 11, 2, tzinfo=UTC)


def ready(ticket: int, at: datetime) -> dict:
    return {
        "event": "labeled",
        "ticket": ticket,
        "label": {"name": "ready-for-agent"},
        "at": at.isoformat(),
    }


def merged(ticket: int, at: datetime) -> dict:
    return {"event": "merged", "ticket": ticket, "at": at.isoformat()}


def cycle(ticket: int, merged_at: datetime, hours: float) -> list[dict]:
    return [ready(ticket, merged_at - timedelta(hours=hours)), merged(ticket, merged_at)]


class CycleSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = config.Config(Path("."), REPO)

    def test_latest_complete_utc_week_includes_start_and_excludes_end(self) -> None:
        rows = [
            *cycle(1, WEEK_START, 8),
            *cycle(2, WEEK_END, 100),
        ]

        signal = watch.cycle_signals(self.cfg, rows, NOW)[0]

        self.assertEqual(signal["latest"], WEEK_END.isoformat())
        self.assertEqual({row["ticket"] for row in signal["rows"]}, {1})
        self.assertIn("n=1", watch.title(signal))
        self.assertIn("median 8h", watch.title(signal))
        self.assertIn("target 6.6h", watch.title(signal))
        self.assertIn("95% CI", watch.title(signal))
        body = watch.body(signal, REPO)
        self.assertEqual(
            signal["fingerprint"],
            watch.cycle_signals(self.cfg, rows, NOW + timedelta(days=1))[0]["fingerprint"],
        )
        other = config.Config(Path("."), "acme/other")
        self.assertNotEqual(
            signal["fingerprint"],
            watch.cycle_signals(other, rows, NOW)[0]["fingerprint"],
        )
        for text in ("Median: 8h", "95% CI:", "n: 1", "Target: 6.6h"):
            self.assertIn(text, body)

    def test_exact_target_lower_cycle_and_no_sample_do_not_alarm(self) -> None:
        cases = {
            "exact target": cycle(1, WEEK_START + timedelta(days=1), 6.6),
            "below target": cycle(1, WEEK_START + timedelta(days=1), 5),
            "no readiness": [merged(1, WEEK_START + timedelta(days=1))],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                self.assertEqual(watch.cycle_signals(self.cfg, rows, NOW), [])

    def test_configured_target_controls_alarm_and_evidence(self) -> None:
        rows = cycle(1, WEEK_START + timedelta(days=1), 8)
        self.cfg.stats_cycle_target_hours = 9
        self.assertEqual(watch.cycle_signals(self.cfg, rows, NOW), [])

        self.cfg.stats_cycle_target_hours = 7
        signal = watch.cycle_signals(self.cfg, rows, NOW)[0]
        self.assertIn("target 7h", watch.title(signal))
        self.assertIn("Target: 7h", signal["excerpt"])


class FakeGh:
    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        if command[:3] == ["gh", "issue", "create"]:
            number = 101 + len(self.issues)
            body = command[command.index("--body") + 1]
            self.issues[number] = {"number": number, "body": body, "state": "OPEN"}
            output = f"https://github.com/{REPO}/issues/{number}\n"
        elif command[:3] == ["gh", "issue", "list"]:
            output = json.dumps([
                {"number": number, "body": issue["body"]}
                for number, issue in self.issues.items()
                if issue["state"] == "OPEN"
            ])
        else:
            raise AssertionError(f"unexpected command {command}")
        return subprocess.CompletedProcess(command, 0, output, "")

    @property
    def created(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "issue", "create"]]


class CycleFilingTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), REPO)
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.gh = FakeGh()
        for patch in (
            mock.patch.object(dispatch, "run", self.gh),
            mock.patch.object(watch, "failed_units", return_value=[]),
            mock.patch.object(watch, "runtime_errors", return_value=[]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def journal(self, *rows: dict) -> None:
        for row in rows:
            lifecycle.append(dispatch.EVENTS, row)

    def test_filing_is_deduped_for_week_and_next_week_is_distinct(self) -> None:
        self.journal(
            *cycle(1, WEEK_START + timedelta(days=2), 8),
            *cycle(2, WEEK_END + timedelta(days=2), 9),
        )

        first = watch.run_pass(self.cfg, now=NOW)
        again = watch.run_pass(self.cfg, now=NOW + timedelta(days=1))
        second = watch.run_pass(self.cfg, now=NOW + timedelta(days=7))

        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(len(self.gh.created), 2)
        self.assertNotEqual(first[0]["fingerprint"], second[0]["fingerprint"])
        watch_rows = [
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "watch"
        ]
        self.assertEqual(len({row["fingerprint"] for row in watch_rows}), 2)
        self.assertTrue(all(call[:2] == ["gh", "issue"] for call in self.gh.calls))


if __name__ == "__main__":
    unittest.main()
