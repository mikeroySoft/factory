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

        self.assertEqual([signal["name"] for signal in signals], ["needs-triage issue #7"])
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

        self.assertEqual([signal["name"] for signal in signals], ["needs-triage issue #7"])
        listing = next(call for call in self.github.calls if call[:3] == ["gh", "issue", "list"])
        self.assertIn("body", listing[listing.index("--json") + 1].split(","))
        self.assertFalse(any("/issues/901/timeline" in call for call in self.github.calls))
        creates = [call for call in self.github.calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual(len([row for row in rows if row.get("event") == "watch"]), 1)

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
            ["needs-triage issue #8"],
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
                f"expectation:{watch.fingerprint('expectation', 'needs-triage issue #7')}"
            ],
        )
        self.assertEqual(
            len([row for row in rows if row.get("event") == "watch"]),
            1,
        )

    def test_timeline_failure_keeps_existing_alert_open(self) -> None:
        now = datetime.now(UTC)
        self.cfg.eval_schedule = True
        name = "needs-triage issue #7"
        fp = f"expectation:{watch.fingerprint('expectation', name)}"
        self.github.issues = [{
            "number": 7, "body": "", "createdAt": (now - timedelta(hours=1)).isoformat(),
        }]
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
        self.dispatch_pass(now - timedelta(minutes=20))
        self.dispatch_pass(now - timedelta(minutes=10))

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
        self.dispatch_pass(now - timedelta(minutes=10))

        signals = watch.run_pass(self.cfg, now=now)

        self.assertEqual(
            [signal["name"] for signal in signals],
            ["needs-triage issue #7"],
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

        self.assertEqual([signal["name"] for signal in signals], ["needs-triage issue #7"])
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

        self.assertEqual([signal["name"] for signal in signals], ["needs-triage issue #7"])
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
