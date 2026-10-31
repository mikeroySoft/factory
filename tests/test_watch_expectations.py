from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, lifecycle, onboard, triage, watch


class GitHub:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.filings: dict[int, dict] = {}
        self.issues: list[dict] = []
        self.timelines: dict[int, list[list[dict]]] = {}

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        if command[:2] == ["gh", "api"] and command[2].endswith("/timeline"):
            number = int(command[2].split("/")[-2])
            default = [
                {"event": "labeled", "label": {"name": config.LABEL_TRIAGE},
                 "created_at": issue["createdAt"]}
                for issue in self.issues if issue["number"] == number
            ]
            return subprocess.CompletedProcess(
                command, 0, json.dumps(self.timelines.get(number, [default])), "",
            )
        if command[:2] == ["gh", "api"] and command[2].endswith("/issues"):
            pages = [
                self.issues[offset:offset + 100]
                for offset in range(0, len(self.issues), 100)
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(pages), "")
        if command[:3] == ["gh", "issue", "list"]:
            label = command[command.index("--label") + 1]
            rows = self.issues[:int(command[command.index("--limit") + 1])] if label == config.LABEL_TRIAGE else [
                {"number": number, "body": issue["body"]}
                for number, issue in self.filings.items()
                if issue["state"] == "OPEN"
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        if command[:3] == ["gh", "issue", "create"]:
            self.filings[901] = {
                "state": "OPEN",
                "closedAt": None,
                "body": command[command.index("--body") + 1],
                "assignees": [],
                "labels": [{"name": config.LABEL_TRIAGE}],
                "comments": [],
            }
            return subprocess.CompletedProcess(
                command, 0, "https://github.com/mikeroySoft/factory/issues/901\n", "",
            )
        if command[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                command, 0, json.dumps(self.filings[int(command[3])]), "",
            )
        if command[:3] == ["gh", "issue", "comment"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["gh", "issue", "close"]:
            self.filings[int(command[3])]["state"] = "CLOSED"
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)


class WatchExpectationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(self.root, "mikeroySoft/factory")
        (self.root / config.CONFIG_NAME).write_text("# fixture config\n")
        self.cfg.factory.mkdir()
        self.github = GitHub()
        dispatch.configure(self.cfg)
        for patcher in (
            mock.patch.object(dispatch, "run", self.github),
            mock.patch.object(watch, "failed_units", return_value=[]),
            mock.patch.object(watch, "runtime_errors", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def append(self, row: dict) -> None:
        lifecycle.append(self.cfg.factory / "events.jsonl", row)

    def dispatch_pass(self, at: datetime) -> None:
        timestamp = at.isoformat().replace("+00:00", "Z")
        with mock.patch.object(lifecycle, "_now", return_value=timestamp):
            lifecycle.record_stage_success(self.cfg.factory / "events.jsonl", "dispatch")

    @staticmethod
    def eval_summary(at: datetime) -> dict:
        return {
            "event": "eval-summary",
            "profile": "default",
            "status": "completed",
            "at": at.isoformat(),
        }

    def test_enabled_eval_summary_nine_days_old_is_act_signal(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append(self.eval_summary(now - timedelta(days=9)))
        self.dispatch_pass(now)

        signals = watch.run_pass(self.cfg, dry_run=True, now=now)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["tier"], "act")
        self.assertEqual(signals[0]["name"], "eval suite")
        self.assertEqual(self.github.calls, [])

    def test_enabled_eval_summary_one_day_old_is_not_overdue(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append(self.eval_summary(now - timedelta(days=1)))

        self.dispatch_pass(now)
        self.assertEqual(watch.run_pass(self.cfg, dry_run=True, now=now), [])
        self.assertEqual(self.github.calls, [])

    def test_factory_pass_uses_success_projection_when_dispatcher_scope_is_quiet(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.dispatch_pass(now - timedelta(minutes=31))

        signals = watch.run_pass(self.cfg, dry_run=True, now=now)

        self.assertEqual([signal["name"] for signal in signals], ["factory pass"])

    def test_factory_pass_is_overdue_when_journal_is_old_without_success(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append({
            "event": "lifecycle",
            "kind": "exit",
            "stage": "watch",
            "outcome": "pass",
            "at": (now - timedelta(minutes=31)).isoformat(),
        })

        signals = watch.run_pass(self.cfg, dry_run=True, now=now)

        self.assertEqual([signal["name"] for signal in signals], ["factory pass"])

    def test_needs_triage_issue_is_overdue_after_two_passes_not_elapsed_time(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.github.issues = [{
            "number": 7,
            "createdAt": (now - timedelta(hours=3)).isoformat(),
        }]
        self.dispatch_pass(now - timedelta(minutes=20))
        self.assertEqual(watch.run_pass(self.cfg, now=now - timedelta(minutes=20)), [])

        self.dispatch_pass(now - timedelta(minutes=10))
        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue mikeroySoft/factory#7"],
        )
        creates = [
            call for call in self.github.calls
            if call[:3] == ["gh", "issue", "create"]
        ]
        self.assertEqual(len(creates), 1)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual([row["issue"] for row in rows if row.get("event") == "watch"], [901])
        self.assertEqual(
            len([row for row in rows if row.get("event") == "stage-success"]),
            2,
        )

    def test_overdue_eval_remains_active_until_a_new_summary(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append(self.eval_summary(now - timedelta(days=9)))
        self.dispatch_pass(now)
        watch.run_pass(self.cfg, now=now)

        with mock.patch.object(watch, "_age", return_value=timedelta(days=2)):
            self.dispatch_pass(now + timedelta(days=2))
            signals = watch.run_pass(self.cfg, now=now + timedelta(days=2))

        self.assertEqual([signal["name"] for signal in signals], ["eval suite"])
        self.assertEqual(self.github.filings[901]["state"], "OPEN")

    def test_watch_files_and_journals_intended_switch_mismatch(self) -> None:
        self.dispatch_pass(datetime.now(UTC))

        signals = watch.run_pass(self.cfg)

        self.assertEqual([signal["name"] for signal in signals], ["eval.schedule"])
        creates = [call for call in self.github.calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("eval.schedule", " ".join(creates[0]))
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual([row["issue"] for row in rows if row.get("event") == "watch"], [901])

    def queue_event(self, event: str, at: datetime) -> dict:
        return {
            "event": event,
            "label": {"name": config.LABEL_TRIAGE},
            "created_at": at.isoformat(),
        }

    def test_watch_filings_cannot_recursively_become_triage_expectations(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.assertEqual(self.cfg.repo, self.cfg.watch_engine_repo)
        self.github.issues = [
            {"number": 7, "body": None, "createdAt": (now - timedelta(days=9)).isoformat()},
            {"number": 901, "body": "Fingerprint: expectation:already-filed\n\nWatch alert",
             "createdAt": (now - timedelta(days=9)).isoformat()},
        ]
        self.dispatch_pass(now - timedelta(minutes=20))
        watch.run_pass(self.cfg, now=now - timedelta(minutes=20))
        self.dispatch_pass(now - timedelta(minutes=10))

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue mikeroySoft/factory#7"],
        )
        listing = next(call for call in self.github.calls if call[:3] == ["gh", "issue", "list"])
        self.assertIn("body", listing[listing.index("--json") + 1].split(","))
        self.assertFalse(any("/issues/901/timeline" in call for call in self.github.calls))
        creates = [call for call in self.github.calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual(len([row for row in rows if row.get("event") == "watch"]), 1)

    def test_same_ticket_number_in_two_repositories_has_distinct_identity(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        entered = now - timedelta(minutes=30)
        self.github.issues = [{
            "number": 7, "body": "", "createdAt": entered.isoformat(),
        }]
        events = [
            {"event": "stage-success", "stage": "dispatch", "at": at.isoformat()}
            for at in (
                now - timedelta(minutes=20),
                now - timedelta(minutes=10),
            )
        ]
        repositories = ("mikeroySoft/factory", "ROCm/rocm-cli")
        found = []

        for repository in repositories:
            cfg = config.Config(self.root, repository)
            cfg.eval_schedule = True
            found.append(next(
                signal for signal in watch.signals(
                    cfg, events, now, include_external=True,
                )
                if signal["kind"] == "expectation"
            ))

        self.assertEqual(
            [signal["name"] for signal in found],
            [f"needs-triage issue {repository}#7" for repository in repositories],
        )
        self.assertEqual(len({signal["fingerprint"] for signal in found}), 2)

    def test_one_failing_github_alert_does_not_stop_the_next_signal(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.append(self.eval_summary(now - timedelta(days=9)))
        self.dispatch_pass(now - timedelta(minutes=31))
        failed: list[list[str]] = []

        def fail_first_create(
            command: list[str], **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if command[:3] == ["gh", "issue", "create"] and not failed:
                failed.append(command)
                raise subprocess.CalledProcessError(
                    1, command, stderr="GitHub API rate limit exceeded",
                )
            return self.github(command, **kwargs)

        with mock.patch.object(dispatch, "run", fail_first_create):
            signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual([signal["name"] for signal in signals], ["eval suite"])
        self.assertEqual(len(failed), 1)
        self.assertEqual(
            [
                row["fingerprint"]
                for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                if row.get("event") == "watch-error"
                and "rate limit" in str(row.get("detail"))
            ],
            [f"expectation:{watch.fingerprint('expectation', 'factory pass')}"],
        )
        creates = [
            call for call in self.github.calls
            if call[:3] == ["gh", "issue", "create"]
        ]
        self.assertEqual(len(creates), 1)

    def test_one_failing_timeline_does_not_stop_other_triage_alerts(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        entered = now - timedelta(minutes=30)
        self.github.issues = [
            {"number": number, "body": "", "createdAt": entered.isoformat()}
            for number in (7, 8)
        ]
        self.dispatch_pass(now - timedelta(minutes=20))
        self.assertEqual(watch.run_pass(self.cfg, now=now - timedelta(minutes=20)), [])
        self.dispatch_pass(now - timedelta(minutes=10))

        def fail_issue_seven(
            command: list[str], **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if command[:2] == ["gh", "api"] and "/issues/7/timeline" in command[2]:
                raise subprocess.CalledProcessError(
                    1, command, stderr="GitHub API rate limit exceeded",
                )
            return self.github(command, **kwargs)

        with mock.patch.object(dispatch, "run", fail_issue_seven):
            signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue mikeroySoft/factory#8"],
        )
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual(
            [
                row["fingerprint"]
                for row in rows
                if row.get("event") == "watch-error"
                and "rate limit" in str(row.get("detail"))
            ],
            [
                f"expectation:{watch.fingerprint('expectation', 'needs-triage issue mikeroySoft/factory#7')}"
            ],
        )
        self.assertEqual(
            len([row for row in rows if row.get("event") == "watch"]),
            1,
        )

    def test_timeline_failure_keeps_existing_alert_open(self) -> None:
        now = datetime.now(UTC)
        self.cfg.eval_schedule = True
        name = "needs-triage issue mikeroySoft/factory#7"
        fp = f"expectation:{watch.fingerprint('expectation', name)}"
        self.github.issues = [{
            "number": 7, "body": "", "createdAt": (now - timedelta(hours=1)).isoformat(),
        }]
        self.dispatch_pass(now - timedelta(minutes=20))
        self.assertEqual(
            watch.run_pass(self.cfg, now=now - timedelta(minutes=20)),
            [],
        )
        self.dispatch_pass(now - timedelta(minutes=10))
        self.github.filings[901] = {
            "state": "OPEN",
            "closedAt": None,
            "body": f"Fingerprint: {fp}\n\nWatch alert",
            "assignees": [],
            "labels": [{"name": config.LABEL_TRIAGE}],
            "comments": [],
        }
        self.append({
            "event": "watch", "fingerprint": fp, "issue": 901,
            "repo": self.cfg.watch_engine_repo, "latest": None,
            "at": (now - timedelta(hours=25)).isoformat(),
        })

        def fail_timeline(
            command: list[str], **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if command[:2] == ["gh", "api"] and "/issues/7/timeline" in command[2]:
                raise subprocess.CalledProcessError(
                    1, command, stderr="GitHub API rate limit exceeded",
                )
            return self.github(command, **kwargs)

        with mock.patch.object(dispatch, "run", fail_timeline):
            self.assertEqual(watch.run_pass(self.cfg, now=now), [])

        self.assertEqual(self.github.filings[901]["state"], "OPEN")
        self.assertFalse(any(
            call[:3] == ["gh", "issue", "close"] for call in self.github.calls
        ))

        self.github.calls.clear()

        def fail_list(
            command: list[str], **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if command[:3] == ["gh", "issue", "list"]:
                raise subprocess.CalledProcessError(
                    1, command, stderr="GitHub API rate limit exceeded",
                )
            return self.github(command, **kwargs)

        with mock.patch.object(dispatch, "run", fail_list):
            self.assertEqual(watch.run_pass(self.cfg, now=now), [])

        self.assertEqual(self.github.filings[901]["state"], "OPEN")
        self.assertFalse(any(
            call[:3] == ["gh", "issue", "close"] for call in self.github.calls
        ))

    def test_triage_issue_discovery_continues_past_first_200_results(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        entered = now - timedelta(minutes=30)
        self.github.issues = [
            {
                "number": 1000 + number,
                "body": f"Fingerprint: expectation:watch-{number}\n\nWatch alert",
                "createdAt": entered.isoformat(),
            }
            for number in range(200)
        ] + [{"number": 7, "body": "", "createdAt": entered.isoformat()}]
        self.dispatch_pass(now - timedelta(minutes=20))
        self.assertEqual(
            watch.run_pass(self.cfg, now=now - timedelta(minutes=20)),
            [],
        )
        self.dispatch_pass(now - timedelta(minutes=10))

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue mikeroySoft/factory#7"],
        )
        listing = next(
            call for call in self.github.calls
            if call[:2] == ["gh", "api"] and call[2].endswith("/issues")
        )
        self.assertIn("--paginate", listing)
        self.assertIn("--slurp", listing)

    def test_old_issue_newly_queued_by_build_gets_two_subsequent_successes(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.github.issues = [{
            "number": 7, "body": "Manager BUILD sent this to triage.",
            "createdAt": (now - timedelta(days=90)).isoformat(),
            "updatedAt": (now - timedelta(days=1)).isoformat(),
        }]
        self.github.timelines[7] = [[self.queue_event("labeled", now)]]
        for minutes in (30, 20, 10):
            self.dispatch_pass(now - timedelta(minutes=minutes))
            self.assertEqual(watch.run_pass(self.cfg, now=now), [])

        self.dispatch_pass(now + timedelta(minutes=10))
        self.assertEqual(watch.run_pass(self.cfg, now=now + timedelta(minutes=10)), [])
        self.dispatch_pass(now + timedelta(minutes=20))
        signals = watch.run_pass(self.cfg, now=now + timedelta(minutes=20))

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue mikeroySoft/factory#7"],
        )
        self.assertIn(now.isoformat(), signals[0]["excerpt"])
        timeline_calls = [call for call in self.github.calls if call[:2] == ["gh", "api"]]
        self.assertTrue(timeline_calls)
        self.assertTrue(all("--paginate" in call and "--slurp" in call for call in timeline_calls))

    def test_requeued_issue_ignores_prior_episode_exit_and_successes(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        old = now - timedelta(days=2)
        self.cfg.eval_schedule = True
        self.github.issues = [{
            "number": 7, "body": "", "createdAt": old.isoformat(),
            "updatedAt": (now + timedelta(minutes=1)).isoformat(),
        }]
        self.github.timelines[7] = [
            [self.queue_event("labeled", old)],
            [self.queue_event("unlabeled", old + timedelta(hours=1)),
             self.queue_event("labeled", now)],
        ]
        self.append({
            "event": "lifecycle", "kind": "exit", "stage": "triage-ticket",
            "ticket": 7, "outcome": "completed", "at": (old + timedelta(minutes=5)).isoformat(),
        })
        for minutes in (30, 20, 10):
            self.dispatch_pass(now - timedelta(minutes=minutes))
            self.assertEqual(watch.run_pass(self.cfg, now=now), [])
        self.dispatch_pass(now + timedelta(minutes=10))
        self.assertEqual(watch.run_pass(self.cfg, now=now + timedelta(minutes=10)), [])
        self.dispatch_pass(now + timedelta(minutes=20))
        signals = watch.run_pass(self.cfg, now=now + timedelta(minutes=20))

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue mikeroySoft/factory#7"],
        )
        self.assertEqual(signals[0]["rows"], [])

    def test_current_queue_triage_exit_and_only_successful_passes_count(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.github.issues = [{"number": 7, "createdAt": (now - timedelta(days=90)).isoformat()}]
        self.github.timelines[7] = [[self.queue_event("labeled", now)]]
        self.append({
            "event": "lifecycle", "kind": "exit", "stage": "triage-ticket",
            "ticket": 8, "outcome": "completed", "at": (now + timedelta(minutes=1)).isoformat(),
        })
        for minutes in (2, 3):
            self.append({
                "event": "lifecycle", "kind": "exit", "stage": "dispatch",
                "outcome": "not_admitted", "at": (now + timedelta(minutes=minutes)).isoformat(),
            })
        self.dispatch_pass(now + timedelta(minutes=5))
        self.assertEqual(watch.run_pass(self.cfg, now=now + timedelta(minutes=5)), [])
        self.append({
            "event": "lifecycle", "kind": "exit", "stage": "triage-ticket",
            "ticket": 7, "outcome": "completed", "at": (now + timedelta(minutes=6)).isoformat(),
        })
        self.dispatch_pass(now + timedelta(minutes=10))

        self.assertEqual(watch.run_pass(self.cfg, now=now + timedelta(minutes=10)), [])

    def test_triage_filing_has_read_only_queue_episode_reproduction(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.github.issues = [{"number": 7, "createdAt": (now - timedelta(hours=1)).isoformat()}]
        self.dispatch_pass(now - timedelta(minutes=20))
        watch.run_pass(self.cfg, now=now - timedelta(minutes=20))
        self.dispatch_pass(now - timedelta(minutes=10))
        watch.run_pass(self.cfg, now=now)

        text = self.github.filings[901]["body"]
        self.assertIn("gh api repos/mikeroySoft/factory/issues/7/timeline", text)
        self.assertIn("--paginate", text)
        self.assertIn("created_at", text)
        self.assertIn("stage-success", text)
        self.assertIn("triage-ticket", text)
        self.assertIn("queue entry", text)
        self.assertNotIn("factory watch --dry-run", text)

        self.github.calls.clear()
        self.assertEqual(watch.run_pass(self.cfg, dry_run=True, now=now), [])
        self.assertEqual(self.github.calls, [])

    def test_overdue_uses_value_equality_and_journal_start(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        old = now - timedelta(days=9)
        eval_expectation = watch.EXPECTATIONS[1]
        dynamic_event = json.loads('"eval-summary"')

        self.assertEqual(
            [signal["name"] for signal in watch.overdue(
                [eval_expectation],
                [{"event": dynamic_event, "at": old.isoformat()}],
                now,
            )],
            ["eval suite"],
        )
        self.assertEqual(
            [signal["name"] for signal in watch.overdue(
                [eval_expectation],
                [{"event": "unrelated", "at": old.isoformat()}],
                now,
            )],
            ["eval suite"],
        )
        self.assertEqual(
            watch.overdue(
                [eval_expectation],
                [{"event": "unrelated", "at": {}}, {"at": "not-a-time"}],
                now,
            ),
            [],
        )

    def test_per_issue_rows_match_ticket_and_exact_queue_entry(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        since = now - timedelta(hours=1)
        expectation = watch.Expectation(
            "needs-triage issue example/repo#123456789",
            watch._triage_exit,
            timedelta(0),
            per_triage_issue=True,
            since=since,
        )
        matching = {
            "event": json.loads('"lifecycle"'),
            "kind": json.loads('"exit"'),
            "stage": json.loads('"triage-ticket"'),
            "ticket": 123456789,
            "at": since.isoformat(),
        }
        other = matching | {
            "ticket": 8,
            "at": (since + timedelta(minutes=1)).isoformat(),
        }

        signals = watch.overdue([expectation], [matching, other], now)

        self.assertEqual(signals[0]["rows"], [matching])
        self.assertEqual(signals[0]["ticket"], "123456789")

    def test_triage_predicate_and_queue_label_require_exact_values(self) -> None:
        self.assertFalse(watch._triage_exit({
            "event": "lifecycle", "kind": "enter", "stage": "triage-ticket",
        }))
        self.assertFalse(watch._triage_exit({
            "event": "lifecycle", "kind": "exit", "stage": "dispatch",
        }))
        with mock.patch.object(dispatch, "gh_json", return_value=[{
            "event": "labeled",
            "label": {"name": "zz-triage"},
            "created_at": datetime.now(UTC).isoformat(),
        }]):
            self.assertIsNone(watch._triage_queue_entry(self.cfg.repo, 7))

    def test_per_issue_pass_threshold_is_strict_and_signals_stay_isolated(self) -> None:
        anchor = datetime(2026, 10, 30, tzinfo=UTC)
        expectations = [
            watch.Expectation(
                f"needs-triage issue example/repo#{ticket}",
                watch._triage_exit,
                None,
                per_triage_issue=True,
                since=anchor,
                max_passes=2,
            )
            for ticket in (7, 8)
        ]
        rows = [
            {"event": "stage-success", "stage": "dispatch", "at": at.isoformat()}
            for at in (anchor, anchor + timedelta(minutes=1))
        ]

        self.assertEqual(watch.overdue(expectations, rows, anchor), [])
        rows.extend([
            {
                "event": "stage-success",
                "stage": "dispatch",
                "at": (anchor + timedelta(minutes=minutes)).isoformat(),
            }
            for minutes in (2, 3)
        ])
        signals = watch.overdue(expectations, rows, anchor + timedelta(minutes=3))

        self.assertEqual([signal["ticket"] for signal in signals], ["7", "8"])
        self.assertTrue(all(
            "2 successful factory passes" in signal["excerpt"]
            for signal in signals
        ))

    def test_each_overdue_ticket_filing_retains_its_own_queue_entry(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        entries = {
            7: now - timedelta(hours=2),
            8: now - timedelta(hours=1),
        }
        self.github.issues = [
            {"number": ticket, "body": "", "createdAt": entered.isoformat()}
            for ticket, entered in entries.items()
        ]
        self.dispatch_pass(now - timedelta(minutes=10))
        watch.run_pass(self.cfg, now=now - timedelta(minutes=10))
        self.dispatch_pass(now)

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual([signal["ticket"] for signal in signals], ["7", "8"])
        creates = [
            call for call in self.github.calls
            if call[:3] == ["gh", "issue", "create"]
        ]
        self.assertEqual(len(creates), 2)
        for signal, call, (ticket, entered) in zip(signals, creates, entries.items()):
            own_entry = f"Current needs-triage queue entry: {entered.isoformat()}."
            other_entry = entries[8 if ticket == 7 else 7].isoformat()
            self.assertIn(own_entry, signal["excerpt"])
            self.assertNotIn(other_entry, signal["excerpt"])
            self.assertIn(f"{self.cfg.repo}#{ticket}", call[call.index("--title") + 1])
            self.assertIn(own_entry, call[call.index("--body") + 1])
            self.assertNotIn(other_entry, call[call.index("--body") + 1])

    def test_unrelated_event_does_not_satisfy_a_ticket_triage_exit(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        entered = now - timedelta(hours=1)
        self.github.issues = [{
            "number": 7, "body": "", "createdAt": entered.isoformat(),
        }]
        self.dispatch_pass(now - timedelta(minutes=10))
        watch.run_pass(self.cfg, now=now - timedelta(minutes=10))
        self.dispatch_pass(now)
        self.append({
            "event": "diagnostic",
            "kind": "exit",
            "stage": "triage-ticket",
            "ticket": 7,
            "at": (now - timedelta(minutes=5)).isoformat(),
        })

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual(
            [signal["name"] for signal in signals],
            [f"needs-triage issue {self.cfg.repo}#7"],
        )
        self.assertEqual(signals[0]["rows"], [])
        self.assertEqual(len([
            call for call in self.github.calls
            if call[:3] == ["gh", "issue", "create"]
        ]), 1)

    def test_unanchored_expectation_does_not_use_unrelated_journal_history(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        old = now - timedelta(days=9)
        expectation = watch.Expectation(
            "external report",
            lambda row: row.get("event") == "external-report",
            timedelta(days=8),
        )
        rows = [{"event": "unrelated", "at": old.isoformat()}]

        self.assertEqual(watch.overdue([expectation], rows, now), [])
        rows.append({"event": "external-report", "at": old.isoformat()})
        signals = watch.overdue([expectation], rows, now)
        self.assertEqual([signal["name"] for signal in signals], ["external report"])
        self.assertEqual(signals[0]["tier"], "act")
        self.assertEqual(signals[0]["rows"], [rows[-1]])

    def test_external_expectations_require_two_passes_and_are_opt_in(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.cfg.eval_schedule = True
        self.github.issues = [{
            "number": 7, "body": "", "createdAt": now.isoformat(),
        }]
        one_pass = [{
            "event": "stage-success", "stage": "dispatch", "at": now.isoformat(),
        }]

        active = watch._active_expectations(
            self.cfg, one_pass, True, set(),
        )

        self.assertFalse(any(item.per_triage_issue for item in active))
        self.assertEqual(self.github.calls, [])
        two_passes = one_pass + [{
            "event": "stage-success",
            "stage": "dispatch",
            "at": (now + timedelta(minutes=1)).isoformat(),
        }]
        self.assertFalse(any(
            signal["name"].startswith("needs-triage")
            for signal in watch.signals(self.cfg, two_passes, now)
        ))
        self.assertEqual(self.github.calls, [])

    def test_199_triage_issues_do_not_use_pagination_fallback(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.github.issues = [
            {
                "number": number,
                "body": f"Fingerprint: expectation:watch-{number}\n",
                "createdAt": now.isoformat(),
            }
            for number in range(199)
        ]
        rows = [
            {
                "event": "stage-success",
                "stage": "dispatch",
                "at": (now + timedelta(minutes=number)).isoformat(),
            }
            for number in (1, 2)
        ]

        watch._active_expectations(self.cfg, rows, True, set())

        self.assertFalse(any(
            call[:2] == ["gh", "api"] and call[2].endswith("/issues")
            for call in self.github.calls
        ))

    def test_dispatch_projection_uses_values_and_deduplicates(self) -> None:
        self.assertFalse(watch._dispatch_success({
            "event": "zzz", "stage": "dispatch",
        }))
        self.assertFalse(watch._dispatch_success({
            "event": "stage-success", "stage": "zzz",
        }))
        at = b"2026-10-30T00:00:00+00:00".decode()
        row_at = at.encode().decode()
        self.assertIsNot(at, row_at)
        rows = [{
            "event": json.loads('"stage-success"'),
            "stage": json.loads('"dispatch"'),
            "at": row_at,
        }]

        with mock.patch.object(
            lifecycle, "last_successful_passes", return_value={"dispatch": at},
        ):
            watch._add_dispatch_success(self.cfg, rows, persist=False)

        self.assertEqual(len(rows), 1)

    def test_queue_entry_accepts_flat_timeline_and_empty_response(self) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC)
        with mock.patch.object(dispatch, "gh_json", return_value=[]):
            self.assertIsNone(watch._triage_queue_entry(self.cfg.repo, 7))
        with mock.patch.object(dispatch, "gh_json", return_value=[
            self.queue_event("labeled", now),
        ]):
            self.assertEqual(
                watch._triage_queue_entry(self.cfg.repo, 7),
                now,
            )

    def test_titles_use_kind_values_and_full_limit(self) -> None:
        expectation = json.loads('"expectation"')
        configuration = json.loads('"configuration"')
        self.assertTrue(watch.title({
            "kind": expectation, "name": "eval suite",
        }).startswith("factory expected event overdue"))
        self.assertTrue(watch.title({
            "kind": configuration, "name": "eval.schedule",
        }).startswith("factory intended state mismatch"))
        self.assertTrue(watch.title({
            "kind": "aaa", "stage": "stage", "key": "key",
        }).startswith("factory runtime failure"))
        self.assertEqual(len(watch.title({
            "kind": expectation, "name": "x" * 300,
        })), 200)
        self.assertEqual(len(watch.title({
            "kind": configuration, "name": "x" * 300,
        })), 200)

    def test_dynamic_host_signal_uses_host_label_and_exact_quiet_boundary(self) -> None:
        signal = {
            "fingerprint": "host:test",
            "kind": json.loads('"host"'),
            "stage": "host",
            "key": "resource pressure",
            "tier": "act",
            "rows": [],
            "excerpt": "persistent",
            "log": None,
            "latest": None,
            "command": "factory watch --dry-run",
        }
        watch._file_or_comment(
            signal, None, {}, self.cfg.watch_engine_repo, self.cfg.repo,
        )
        create = next(
            call for call in self.github.calls
            if call[:3] == ["gh", "issue", "create"]
        )
        self.assertEqual(
            create[create.index("--label") + 1],
            watch.HOST_LABELS,
        )

        self.github.calls.clear()
        previous = {
            "issue": 901,
            "repo": self.cfg.watch_engine_repo,
            "at": datetime.now(UTC).isoformat(),
        }
        with mock.patch.object(watch, "_age", return_value=watch.QUIET_PERIOD):
            watch._file_or_comment(
                signal, previous, {}, self.cfg.watch_engine_repo, self.cfg.repo,
            )
        self.assertTrue(any(
            call[:3] == ["gh", "issue", "comment"]
            for call in self.github.calls
        ))

    def test_filing_uses_the_last_create_output_line(self) -> None:
        signal = {
            "fingerprint": "expectation:test",
            "kind": "expectation",
            "stage": "expected-events",
            "key": "test",
            "name": "test",
            "tier": "act",
            "rows": [],
            "excerpt": "overdue",
            "log": None,
            "latest": None,
            "command": "factory watch --dry-run",
        }

        def create_with_notice(
            command: list[str], **kwargs: object,
        ) -> subprocess.CompletedProcess:
            if command[:3] == ["gh", "issue", "create"]:
                self.github.calls.append(command)
                return subprocess.CompletedProcess(
                    command, 0,
                    "notice\nhttps://github.com/mikeroySoft/factory/issues/902\n", "",
                )
            return self.github(command, **kwargs)

        with mock.patch.object(dispatch, "run", create_with_notice):
            watch._file_or_comment(
                signal, None, {}, self.cfg.watch_engine_repo, self.cfg.repo,
            )

        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual(rows[-1]["issue"], 902)

    def test_existing_remote_and_superseded_filings_report_exact_changes(self) -> None:
        signal = {
            "fingerprint": "expectation:test",
            "kind": "expectation",
            "stage": "expected-events",
            "key": "test",
            "name": "test",
            "tier": "act",
            "rows": [],
            "excerpt": "overdue",
            "log": None,
            "latest": None,
            "command": "factory watch --dry-run",
        }
        self.github.filings[900] = {
            "state": "OPEN", "closedAt": None, "body": None,
        }
        previous = {
            "issue": 900,
            "repo": "example/remote",
            "at": datetime.now(UTC).isoformat(),
        }

        _, changed = watch._file_or_comment(
            signal, previous, {}, self.cfg.watch_engine_repo, self.cfg.repo,
        )

        self.assertFalse(changed)
        self.github.filings[900]["state"] = "CLOSED"
        self.github.filings[900]["closedAt"] = datetime.now(UTC).isoformat()
        previous["repo"] = self.cfg.watch_engine_repo
        self.github.calls.clear()
        _, changed = watch._file_or_comment(
            signal,
            previous,
            {signal["fingerprint"]: {"number": 901, "body": None}},
            self.cfg.watch_engine_repo,
            self.cfg.repo,
        )

        self.assertTrue(changed)
        self.assertTrue(any(
            call[:4] == ["gh", "issue", "comment", "901"]
            for call in self.github.calls
        ))

    def test_replacement_filing_with_lower_number_gets_current_evidence(self) -> None:
        now = datetime.now(UTC)
        self.dispatch_pass(now)
        fp = watch.intended_state_signals(self.cfg)[0]["fingerprint"]
        self.append({
            "event": "watch",
            "fingerprint": fp,
            "issue": 902,
            "repo": self.cfg.watch_engine_repo,
            "latest": None,
            "at": now.isoformat(),
        })
        self.github.filings[902] = {
            "state": "CLOSED", "closedAt": now.isoformat(), "body": None,
        }
        self.github.filings[901] = {
            "state": "OPEN",
            "body": f"Fingerprint: {fp}\n\n{watch.OBSERVED}\n- `{self.cfg.repo}`\n",
        }

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual([signal["name"] for signal in signals], ["eval.schedule"])
        comments = [
            call for call in self.github.calls
            if call[:3] == ["gh", "issue", "comment"]
        ]
        self.assertEqual([call[3] for call in comments], ["901"])
        self.assertIn("eval.schedule", comments[0][comments[0].index("--body") + 1])
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        reports = [row for row in rows if row.get("event") == "watch"]
        self.assertEqual(reports[-1]["issue"], 901)
        self.assertTrue(reports[-1]["commented"])
        self.assertFalse(any(
            call[:3] == ["gh", "issue", "create"] for call in self.github.calls
        ))

    def test_intended_state_inspection_command_finds_the_live_switch_table(self) -> None:
        now = datetime.now(UTC)
        self.dispatch_pass(now)
        (self.root / config.CONFIG_NAME).write_text("[eval]\nschedule = false\n")
        docs = self.root / "docs"
        docs.mkdir()
        (docs / config.INTENDED_STATE_NAME).write_text(
            '[repo."mikeroySoft/factory"]\n"eval.schedule" = true\n',
        )

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual([signal["name"] for signal in signals], ["eval.schedule"])
        inspection = subprocess.run(
            signals[0]["command"], shell=True, cwd=self.root,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(inspection.returncode, 0, inspection.stderr)
        self.assertIn('"eval.schedule" = true', inspection.stdout)
        self.assertIn("[eval]", inspection.stdout)
        self.assertEqual(
            (self.root / config.CONFIG_NAME).read_text(), "[eval]\nschedule = false\n",
        )

    def test_unrelated_band_does_not_load_escape_attribution(self) -> None:
        self.cfg.eval_schedule = True
        self.cfg.watch_bands = [config.Band("aaa", 28, ())]
        with mock.patch.object(watch, "signals", return_value=[]), \
                mock.patch.object(watch.stats, "configure") as configure:
            watch.run_pass(self.cfg)

        configure.assert_not_called()

    def test_cli_dry_run_makes_no_gh_calls_with_escape_band(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / config.CONFIG_NAME).write_text('[repo]\nslug = "example/fixture"\n')
        (self.root / config.BANDS_NAME).write_text(
            'schema_version = 1\n[[band]]\nmetric = "revert_rate_by_tier"\n'
            'window_days = 28\nrules = [{kind = "sigma", sigma = 3.0, count = 1, span = 1, tier = "act"}]\n'
        )
        stub = self.root / "gh"
        calls = self.root / "gh.calls"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$WATCH_SMOKE_GH_LOG"\nexit 97\n')
        stub.chmod(0o755)
        env = dict(
            os.environ, PATH=f"{self.root}:{os.environ['PATH']}",
            PYTHONPATH=str(Path(config.__file__).resolve().parent.parent),
            XDG_CONFIG_HOME=str(self.root / "host-config"),
            WATCH_SMOKE_GH_LOG=str(calls),
        )

        result = subprocess.run(
            [sys.executable, "-m", "factory", "watch", "--dry-run"],
            cwd=self.root, env=env, text=True, capture_output=True, timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")
        self.assertFalse((self.cfg.factory / "events.jsonl").exists())


class IntendedStateCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / config.CONFIG_NAME).write_text("# [eval] intentionally omitted\n")
        self.cfg = config.Config(self.root, "mikeroySoft/factory", signoff=False)
        self.cfg.factory.mkdir()
        self.host_path = self.root / "config" / "factory" / "config.toml"

    def command(self, command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(command, 0, "ADMIN\n", "")
        if command[:3] == ["gh", "label", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(list(config.LABELS)), "")
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(command, 0, "active\n", "")
        if command[0] == "loginctl":
            return subprocess.CompletedProcess(command, 0, "yes\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_cli_doctor_fails_once_for_omitted_intended_eval_switch(self) -> None:
        output = io.StringIO()
        patcher = mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "available"))
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard.config, "host_config", return_value={}), \
                mock.patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=self.command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ready")), \
                redirect_stdout(output):
            code = cli.main(["doctor"])

        lines = [
            line for line in output.getvalue().splitlines() if "eval.schedule" in line
        ]
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertIn("FAIL", lines[0])
        self.cfg.eval_schedule = True
        control = io.StringIO()
        with mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard.config, "host_config", return_value={}), \
                mock.patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=self.command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ready")), \
                redirect_stdout(control):
            self.assertEqual(cli.main(["doctor"]), 0)
        self.assertFalse(any(
            "FAIL" in line and "eval.schedule" in line for line in control.getvalue().splitlines()
        ))


    def test_watch_checks_intended_state_without_repository_config(self) -> None:
        (self.root / config.CONFIG_NAME).unlink()
        self.cfg.is_loaded = True

        self.assertEqual(
            [signal["name"] for signal in watch.intended_state_signals(self.cfg)],
            ["eval.schedule"],
        )

    def test_undecided_switch_warns_without_failing_doctor_or_alerting_watch(self) -> None:
        source = self.root / "source" / "factory" / "config.py"
        source.parent.mkdir(parents=True)
        intended = source.parent.parent / "docs" / config.INTENDED_STATE_NAME
        intended.parent.mkdir()
        intended.write_text(
            '[repo."mikeroySoft/factory"]\n'
            '"release.auto" = "undecided"\n'
        )
        output = io.StringIO()
        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard.config, "host_config", return_value={}), \
                mock.patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=self.command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "available")), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ready")), \
                redirect_stdout(output):
            code = cli.main(["doctor"])

        lines = [
            line for line in output.getvalue().splitlines() if "release.auto" in line
        ]
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 1)
        self.assertIn("WARN", lines[0])
        self.assertIn("configured false", lines[0])

        dispatch.configure(self.cfg)
        watch_output = io.StringIO()
        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(watch, "failed_units", return_value=[]), \
                mock.patch.object(watch, "runtime_errors", return_value=[]), \
                redirect_stdout(watch_output):
            signals = watch.run_pass(self.cfg, dry_run=True)

        self.assertEqual(
            [(signal["name"], signal["tier"]) for signal in signals],
            [("release.auto", "warn")],
        )
        self.assertIn("watch: warn", watch_output.getvalue())
        self.assertNotIn("would file", watch_output.getvalue())

    def test_undecided_watch_warns_again_with_the_changed_live_state(self) -> None:
        source = self.root / "source" / "factory" / "config.py"
        source.parent.mkdir(parents=True)
        intended = source.parent.parent / "docs" / config.INTENDED_STATE_NAME
        intended.parent.mkdir()
        intended.write_text(
            '[repo."mikeroySoft/factory"]\n'
            '"release.auto" = "undecided"\n'
        )
        dispatch.configure(self.cfg)

        first = io.StringIO()
        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(watch, "failed_units", return_value=[]), \
                mock.patch.object(watch, "runtime_errors", return_value=[]), \
                redirect_stdout(first):
            watch.run_pass(self.cfg)
        unchanged = io.StringIO()
        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(watch, "failed_units", return_value=[]), \
                mock.patch.object(watch, "runtime_errors", return_value=[]), \
                redirect_stdout(unchanged):
            watch.run_pass(self.cfg)

        self.cfg.release_auto = True
        second = io.StringIO()
        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(watch, "failed_units", return_value=[]), \
                mock.patch.object(watch, "runtime_errors", return_value=[]), \
                redirect_stdout(second):
            watch.run_pass(self.cfg)
        self.cfg.release_auto = False
        third = io.StringIO()
        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(watch, "failed_units", return_value=[]), \
                mock.patch.object(watch, "runtime_errors", return_value=[]), \
                redirect_stdout(third):
            watch.run_pass(self.cfg)

        self.assertIn("release.auto = false", first.getvalue())
        self.assertEqual(unchanged.getvalue(), "")
        self.assertIn("release.auto = true", second.getvalue())
        self.assertIn("release.auto = false", third.getvalue())

    def test_doctor_rejects_invalid_intended_switch_value(self) -> None:
        source = self.root / "source" / "factory" / "config.py"
        source.parent.mkdir(parents=True)
        intended = source.parent.parent / "docs" / config.INTENDED_STATE_NAME
        intended.parent.mkdir()
        intended.write_text(
            '[repo."mikeroySoft/factory"]\n'
            '"release.auto" = "yes"\n'
        )

        with mock.patch.object(config, "__file__", str(source)), \
                mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard.config, "host_config", return_value={}), \
                mock.patch.object(onboard.config, "host_config_path", return_value=self.host_path), \
                mock.patch.object(onboard.config, "engine_drift", return_value=None), \
                mock.patch.object(onboard, "sh", side_effect=self.command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "available")), \
                mock.patch.object(triage, "probe_endpoint", return_value=(True, "ready")), \
                redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(
                    config.ConfigError,
                    f"{config.INTENDED_STATE_NAME}: intended switches must be true, false, or undecided",
                ):
            cli.main(["doctor"])

    def test_install_keeps_intended_but_disabled_eval_units_off(self) -> None:
        units = self.root / "units"
        units.mkdir()
        eval_service = units / f"{self.cfg.unit}-eval.service"
        eval_timer = units / f"{self.cfg.unit}-eval.timer"
        eval_service.write_text("stale\n")
        eval_timer.write_text("stale\n")
        calls: list[list[str]] = []

        def command(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
            calls.append(argv)
            return self.command(argv, cwd)

        with mock.patch.object(onboard.config, "load", return_value=self.cfg), \
                mock.patch.object(onboard, "unit_dir", return_value=units), \
                mock.patch.object(onboard, "sh", side_effect=command), \
                mock.patch.object(onboard.shutil, "which", return_value="/bin/systemctl"), \
                mock.patch.dict(os.environ, {"PATH": os.environ["PATH"]}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(onboard.install(["--no-dashboard"]), 0)

        self.assertTrue((units / f"{self.cfg.unit}.service").exists())
        self.assertFalse(eval_service.exists())
        self.assertFalse(eval_timer.exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", eval_timer.name], calls)


if __name__ == "__main__":
    unittest.main()
