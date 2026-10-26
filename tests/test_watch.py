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
REAL_FAILED_UNITS = watch.failed_units  # setUp patches these out for the filing tests
REAL_RUNTIME_ERRORS = watch.runtime_errors


def exit_row(stage: str, outcome: str, reason: str | None, at: str | None = None, **fields) -> dict:
    at = at or lifecycle._now()
    return {"event": "lifecycle", "kind": "exit", "stage": stage, "outcome": outcome, "reason": reason,
            "at": at, "execution_id": f"exec-{stage}-{reason}-{at}", "ticket": None, "attempt": None, **fields}


def escalate_row(ticket: int, code: str | None, reason: str, at: str | None = None) -> dict:
    row = {"event": "escalate", "ticket": ticket, "reason": reason, "at": at or lifecycle._now(),
           "packet": f".factory/escalations/{ticket}.md", "round": 1}
    if code is not None:
        row["code"] = code
    return row


class FakeGh:
    """Answer `gh issue view/create`; record every call."""

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.calls: list[list[str]] = []
        self.next_issue = 100

    def close(self, number: int) -> None:
        self.issues[number] = {"state": "CLOSED", "closedAt": lifecycle._now()}

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "create"]:
            self.next_issue += 1
            self.issues[self.next_issue] = {"state": "OPEN", "closedAt": None}
            self.issues[self.next_issue]["body"] = cmd[cmd.index("--body") + 1]
            return subprocess.CompletedProcess(cmd, 0, f"https://github.com/{REPO}/issues/{self.next_issue}\n", "")
        if cmd[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.issues[int(cmd[3])]), "")
        if cmd[:3] == ["gh", "issue", "list"]:  # open filings: fingerprint dedupe before filing
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                [{"number": n, "body": i.get("body", "")} for n, i in self.issues.items() if i["state"] == "OPEN"]), "")
        raise AssertionError(f"unexpected command {cmd}")

    @property
    def created(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["gh", "issue", "create"]]


class WatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = config.Config(self.root, REPO)
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.gh = FakeGh()
        patches = [
            mock.patch.object(dispatch, "run", self.gh),
            mock.patch.object(watch, "failed_units", return_value=[]),
            mock.patch.object(watch, "runtime_errors", return_value=[]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def journal(self, *rows: dict) -> None:
        for row in rows:
            lifecycle.append(dispatch.EVENTS, row)

    def events(self, kind: str) -> list[dict]:
        return [r for r in lifecycle.read_events(dispatch.EVENTS) if r.get("event") == kind]

    def test_new_fingerprint_files_one_bug_and_journals_it(self) -> None:
        self.journal(exit_row("manage", "mechanism_failure", "KeyError: 'state'"))
        filed = watch.run_pass(self.cfg)
        self.assertEqual(len(filed), 1)
        self.assertEqual(len(self.gh.created), 1)
        create = self.gh.created[0]
        self.assertEqual(create[create.index("--label") + 1], "bug,needs-triage")
        body = create[create.index("--body") + 1]
        self.assertIn(f"Fingerprint: {filed[0]['fingerprint']}", body)
        self.assertIn("## Expected behavior", body)
        self.assertIn("KeyError: 'state'", body)
        self.assertGreaterEqual(len(body.strip()), 80)
        rows = self.events("watch")
        self.assertEqual([(r["fingerprint"], r["issue"]) for r in rows], [(filed[0]["fingerprint"], 101)])
        self.assertIn("at", rows[0])

    def test_open_issue_is_not_refiled_closed_issue_with_recurrence_is(self) -> None:
        self.journal(exit_row("manage", "mechanism_failure", "KeyError: 'state'"))
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1)

        watch.run_pass(self.cfg)
        self.journal(exit_row("manage", "mechanism_failure", "KeyError: 'state'"))
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1, "open issue must not be refiled")

        self.gh.close(101)
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1, "closed issue without recurrence stays closed")

        self.journal(exit_row("manage", "mechanism_failure", "KeyError: 'state'"))
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 2, "recurrence after close refiles")
        self.assertEqual([r["issue"] for r in self.events("watch")], [101, 102])

    def test_no_signals_makes_no_call(self) -> None:
        self.journal(exit_row("dispatch", "completed", None), exit_row("worker", "unknown", "scope exited early"),
                     exit_row("review", "interrupted", "stuck"))
        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.events("watch"), [])

    def test_sixth_fingerprint_waits_for_the_next_pass(self) -> None:
        self.journal(*(exit_row("worker", "mechanism_failure", f"RuntimeError: boom {code}") for code in range(1, 7)))
        first = watch.run_pass(self.cfg)
        self.assertEqual(len(first), 5)
        self.assertEqual(len(self.gh.created), 5)
        second = watch.run_pass(self.cfg)
        self.assertEqual(len(second), 1)
        self.assertEqual(len({r["fingerprint"] for r in self.events("watch")}), 6)

    def test_dry_run_prints_without_gh_or_journal(self) -> None:
        self.journal(exit_row("gate", "unknown", "check_timeout"))
        with mock.patch("builtins.print") as out:
            self.assertEqual(len(watch.run_pass(self.cfg, dry_run=True)), 1)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.events("watch"), [])
        self.assertTrue(any("check_timeout" in str(call) for call in out.call_args_list))

    def test_body_carries_attempt_log_tail_and_reproduce_command(self) -> None:
        log = self.cfg.factory / "logs" / "7-attempt-1.log"
        log.parent.mkdir()
        log.write_text("".join(f"line {i}\n" for i in range(60)))
        self.journal(
            {"event": "attempt", "at": "2026-01-02T11:59:00Z", "ticket": 7, "attempt": 1, "log": str(log)},
            exit_row("worker", "mechanism_failure", "FileNotFoundError: 'omp'", ticket=7, attempt=1),
        )
        watch.run_pass(self.cfg)
        body = self.gh.created[0][self.gh.created[0].index("--body") + 1]
        self.assertIn("line 59", body)
        self.assertNotIn("line 19\n", body)
        self.assertIn("events.jsonl", body)

    def test_host_conditions_are_not_filed(self) -> None:
        self.journal(exit_row("worker", "unknown", "worker_exit:1"), exit_row("worker", "unknown", "worker_exit:-15"),
                     exit_row("triage", "mechanism_failure", "triage_endpoint_unavailable"),
                     exit_row("triage-ticket", "unknown", "triage_endpoint_timeout"),
                     exit_row("manage", "mechanism_failure", "github_command_failed"))
        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.gh.calls, [])

    def test_one_issue_per_reason_across_stages_and_per_journal_source_across_codes(self) -> None:
        self.journal(*(exit_row(stage, "mechanism_failure", "FileNotFoundError: 'omp'")
                       for stage in ("worker", "ticket", "dispatcher")))
        errors = [{"source": "events.jsonl", "scope": "executions", "code": "missing_enter"},
                  {"source": "events.jsonl", "scope": "executions", "code": "identity_conflict"},
                  {"source": "filesystem", "scope": "resources", "code": "lock_unavailable"}]
        with mock.patch.object(watch, "runtime_errors", return_value=errors):
            filed = watch.run_pass(self.cfg)
        titles = sorted(watch.title(s) for s in filed)
        self.assertEqual(titles, [
            "factory runtime failure: lifecycle worker, ticket, dispatcher FileNotFoundError: 'omp'",
            "factory runtime failure: runtime events.jsonl identity_conflict, missing_enter",
            "factory runtime failure: runtime filesystem lock_unavailable",
        ])
        self.assertEqual(len(self.gh.created), 3)
        journal = next(b for b in (c[c.index("--body") + 1] for c in self.gh.created) if "identity_conflict" in b)
        self.assertIn("missing_enter", journal)

    def test_unit_and_runtime_signals_are_fingerprinted(self) -> None:
        with mock.patch.object(watch, "failed_units", return_value=[("factory-widgets-dashboard.service", "● failed")]), \
             mock.patch.object(watch, "runtime_errors", return_value=[{"source": "events.jsonl", "scope": "journal", "code": "unreadable"}]):
            filed = watch.run_pass(self.cfg)
        self.assertEqual({f["kind"] for f in filed}, {"unit", "runtime"})
        self.assertEqual(len(self.gh.created), 2)
        bodies = [c[c.index("--body") + 1] for c in self.gh.created]
        self.assertTrue(any("factory-widgets-dashboard.service" in b and "systemctl --user" in b for b in bodies))
        self.assertTrue(any("unreadable" in b and "--runtime-json" in b for b in bodies))

    def test_failed_units_sees_own_crashed_pass_and_only_this_repo(self) -> None:
        """Under ExecStopPost= the pass unit is still active, so its failure arrives as $SERVICE_RESULT."""
        listed = ("factory-widgets-dashboard.service loaded failed failed factory dashboard\n"
                  "factory-other.service loaded failed failed another repo\n")

        def fake_run(cmd, **kwargs):
            out = listed if "list-units" in cmd else f"● {cmd[-1]}: code=exited, status=1/FAILURE\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")

        env = {"SERVICE_RESULT": "exit-code", "EXIT_CODE": "exited", "EXIT_STATUS": "1"}
        with mock.patch.object(subprocess, "run", fake_run), mock.patch.dict("os.environ", env):
            units = dict(REAL_FAILED_UNITS(self.cfg))
        self.assertEqual(set(units), {"factory-widgets-dashboard.service", "factory-widgets.service"})
        self.assertIn("SERVICE_RESULT=exit-code EXIT_CODE=exited EXIT_STATUS=1", units["factory-widgets.service"])
        with mock.patch.object(subprocess, "run", fake_run), mock.patch.dict("os.environ", {"SERVICE_RESULT": "success"}):
            self.assertEqual([n for n, _ in REAL_FAILED_UNITS(self.cfg)], ["factory-widgets-dashboard.service"])

    def test_failed_units_excludes_other_repositories_with_same_prefix(self) -> None:
        listed = ("factory-widgets.service loaded failed failed factory pass\n"
                  "factory-widgets-dashboard.service loaded failed failed factory dashboard\n"
                  "factory-widgets-api.service loaded failed failed another repo pass\n"
                  "factory-widgets-api-dashboard.service loaded failed failed another repo dashboard\n")

        def fake_run(cmd, **kwargs):
            out = listed if "list-units" in cmd else f"● {cmd[-1]}: failed\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")

        with mock.patch.object(subprocess, "run", side_effect=fake_run) as run, \
             mock.patch.dict("os.environ", {"SERVICE_RESULT": "success"}):
            units = REAL_FAILED_UNITS(self.cfg)
        expected = ["factory-widgets.service", "factory-widgets-dashboard.service"]
        self.assertCountEqual([name for name, _ in units], expected)
        status_units = [call.args[0][-1] for call in run.call_args_list if "status" in call.args[0]]
        self.assertCountEqual(status_units, expected)

    def test_runtime_errors_skip_steady_state_codes(self) -> None:
        payload = json.dumps({"errors": [
            {"source": "unit", "scope": "systemd", "code": "transitioning"},
            {"source": "events.jsonl", "scope": "journal", "code": "byte_limit"},
            {"source": "events.jsonl", "scope": "history", "code": "event_limit"},
            {"source": "proc", "scope": "executions", "code": "descendants_not_scanned"},
            {"source": "events.jsonl", "scope": "journal", "code": "unreadable"},
        ]})
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, "")):
            errors = REAL_RUNTIME_ERRORS(self.cfg)
        self.assertEqual([e["code"] for e in errors], ["unreadable"])

    def test_runtime_errors_skip_audit_rows_in_the_shared_journal(self) -> None:
        # `dispatch.record` audit rows share events.jsonl with lifecycle rows by design, so
        # every live journal reports `unsupported_record`; it is steady state, not a failure.
        payload = json.dumps({"errors": [
            {"source": "events.jsonl", "scope": "history", "code": "unsupported_record"},
            {"source": "events.jsonl", "scope": "history", "code": "invalid_json"},
        ]})
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, "")):
            errors = REAL_RUNTIME_ERRORS(self.cfg)
        self.assertEqual([e["code"] for e in errors], ["invalid_json"])

    @mock.patch.object(watch, "OUTCOME_CODES", watch.OUTCOME_CODES - {"gate_failed"})
    def test_repeated_escalation_code_files_one_bug_listing_every_ticket(self) -> None:
        self.journal(*(escalate_row(n, "gate_failed", f"gate failed on #{n}") for n in (1, 2, 3)))
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1)
        body = self.gh.created[0][self.gh.created[0].index("--body") + 1]
        self.assertIn("Fingerprint: escalation:gate_failed", body)
        for n in (1, 2, 3):
            self.assertIn(f"#{n}", body)
            self.assertIn(f"gate failed on #{n}", body)
            self.assertIn(f".factory/escalations/{n}.md", body)
        self.assertEqual([r["fingerprint"] for r in self.events("watch")], ["escalation:gate_failed"])

    def test_repeated_escalation_needs_distinct_tickets_inside_the_window(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=watch.REPEAT_DAYS + 1)).isoformat().replace("+00:00", "Z")
        self.journal(
            escalate_row(1, "gate_failed", "two tickets"), escalate_row(2, "gate_failed", "two tickets"),
            *(escalate_row(5, "budget", f"same ticket {i}") for i in range(3)),
            escalate_row(7, "worker_stuck", "stale", at=old),
            escalate_row(8, "worker_stuck", "fresh"), escalate_row(9, "worker_stuck", "fresh"),
            *(escalate_row(n, None, "pre-code row") for n in (11, 12, 13)),
        )
        watch.run_pass(self.cfg)
        self.assertEqual(self.gh.created, [])
        self.assertEqual(self.events("watch"), [])

    def test_repeated_no_change_outcome_is_not_filed(self) -> None:
        # #300: a worker that finds its ticket already fixed commits nothing; filing that as a
        # factory bug would feed watch the next no-change ticket.
        self.journal(*(escalate_row(n, "no_change", "committed nothing") for n in (1, 2, 3)))
        watch.run_pass(self.cfg)
        self.assertEqual(self.gh.created, [])

    def test_repeated_review_revise_outcome_is_not_filed(self) -> None:
        self.journal(*(escalate_row(n, "review_revise", "review requested changes") for n in (1, 2, 3)))
        watch.run_pass(self.cfg)
        self.assertEqual(self.gh.created, [])


class HostFakeGh(FakeGh):
    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        if cmd[:3] == ["gh", "issue", "create"]:
            result = super().__call__(cmd, **kwargs)
            issue = self.issues[self.next_issue]
            issue.update(assignees=[], comments=[],
                         labels=[{"name": label} for label in cmd[cmd.index("--label") + 1].split(",")])
            return result
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.issues[int(cmd[3])]), "")
        if cmd[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                [{"number": n, "body": issue["body"]} for n, issue in self.issues.items()
                 if issue["state"] == "OPEN"]), "")
        if cmd[:3] == ["gh", "issue", "comment"]:
            self.issues[int(cmd[3])]["comments"].append(
                {"body": cmd[cmd.index("--body") + 1], "createdAt": lifecycle._now()})
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["gh", "issue", "edit"]:
            self.issues[int(cmd[3])]["body"] = cmd[cmd.index("--body") + 1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["gh", "issue", "close"]:
            self.issues[int(cmd[3])].update(state="CLOSED", closedAt=lifecycle._now())
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")


class PersistentHostConditionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = config.Config(self.root, REPO)
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.gh = HostFakeGh()
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

    @staticmethod
    def ago(minutes: int) -> str:
        return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")

    def failures(self, first: int, last: int) -> tuple[dict, ...]:
        return (
            exit_row("triage", "mechanism_failure", "triage_endpoint_unavailable", at=self.ago(first)),
            exit_row("triage", "mechanism_failure", "triage_endpoint_unavailable",
                     at=self.ago((first + last) // 2)),
            exit_row("triage", "mechanism_failure", "triage_endpoint_unavailable", at=self.ago(last)),
        )

    def test_persistent_host_condition_files_with_evidence_and_host_label(self) -> None:
        rows = self.failures(70, 5)
        self.journal(*rows)

        filed = watch.run_pass(self.cfg)

        self.assertEqual(len(filed), 1)
        create = self.gh.created[0]
        self.assertEqual(create[create.index("--label") + 1], "bug,host,needs-triage")
        body = create[create.index("--body") + 1]
        self.assertIn("persistent host condition", body)
        for row in rows:
            self.assertIn(row["execution_id"], body)

    def test_transient_host_condition_is_not_filed(self) -> None:
        self.journal(*self.failures(10, 1))

        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.gh.calls, [])

    def test_successful_triage_pass_clears_and_closes_host_condition(self) -> None:
        self.journal(*self.failures(70, 5))
        watch.run_pass(self.cfg)
        self.journal(exit_row("triage", "completed", None))

        with mock.patch.object(watch, "QUIET_PERIOD", timedelta(0)):
            watch.run_pass(self.cfg)

        self.assertEqual(self.gh.issues[101]["state"], "CLOSED")

    def test_two_installs_dedupe_the_same_host_condition(self) -> None:
        self.journal(*self.failures(70, 5))
        watch.run_pass(self.cfg)

        other_root = self.root / "other"
        other = config.Config(other_root, "other/widgets")
        other.factory.mkdir(parents=True)
        dispatch.configure(other)
        for row in self.failures(70, 5):
            lifecycle.append(dispatch.EVENTS, row)
        watch.run_pass(other)

        self.assertEqual(len(self.gh.created), 1)
        self.assertIn("- `other/widgets`", self.gh.issues[101]["body"])

    def test_host_failure_threshold_is_configurable(self) -> None:
        root = self.root / "configured"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        (root / config.CONFIG_NAME).write_text(
            '[repo]\nslug = "acme/widgets"\n[watch]\nhost_failure_minutes = 15\n'
        )

        with mock.patch.object(config, "host_config", return_value={}):
            loaded = config.load(root)

        self.assertEqual(loaded.watch_host_failure_minutes, 15)

    def test_host_condition_keeps_watch_triage_route_until_success(self) -> None:
        self.journal(*self.failures(70, 5))
        fingerprint = watch.fingerprint("host", "triage_endpoint_unavailable")

        signals = {signal["fingerprint"]: signal for signal in watch.signals(
            self.cfg, lifecycle.read_events(dispatch.EVENTS)
        )}
        self.assertIn(fingerprint, signals)
        self.assertEqual(
            watch.title(signals[fingerprint]),
            "factory runtime failure: host triage triage_endpoint_unavailable",
        )

        self.journal(exit_row("triage", "completed", None))
        self.assertNotIn(
            fingerprint,
            {signal["fingerprint"] for signal in watch.signals(
                self.cfg, lifecycle.read_events(dispatch.EVENTS)
            )},
        )

    def test_success_separates_host_blips(self) -> None:
        self.journal(
            exit_row("manage", "mechanism_failure", "github_command_failed", at=self.ago(3 * 24 * 60)),
            exit_row("manage", "completed", None, at=self.ago(2 * 24 * 60)),
            exit_row("manage", "mechanism_failure", "github_command_failed", at=self.ago(1)),
        )

        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.gh.calls, [])

    def test_long_gap_between_host_failures_breaks_streak(self) -> None:
        self.journal(
            exit_row("manage", "mechanism_failure", "github_command_failed", at=self.ago(3 * 24 * 60)),
            exit_row("manage", "mechanism_failure", "github_command_failed", at=self.ago(1)),
        )

        self.assertEqual(watch.signals(self.cfg, lifecycle.read_events(dispatch.EVENTS)), [])
        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.gh.calls, [])

    def test_host_failure_gap_uses_configured_threshold(self) -> None:
        self.cfg.watch_host_failure_minutes = 15
        self.journal(*self.failures(70, 5))

        self.assertEqual(watch.signals(self.cfg, lifecycle.read_events(dispatch.EVENTS)), [])
        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.gh.calls, [])


class WatchOutcomeEscalationTest(unittest.TestCase):
    def test_repeated_ci_failed_outcome_is_not_filed(self) -> None:
        rows = [escalate_row(n, "ci_failed", "PR checks failed") for n in (1, 2, 3)]

        self.assertEqual(watch.repeated_escalations(rows), {})

    def test_repeated_pr_feedback_outcome_is_not_filed(self) -> None:
        rows = [escalate_row(n, "pr_feedback", "PR feedback remains") for n in (1, 2, 3)]

        self.assertEqual(watch.repeated_escalations(rows), {})

    def test_repeated_gate_failed_outcome_is_not_filed(self) -> None:
        rows = [escalate_row(n, "gate_failed", "project gate failed") for n in (1, 2, 3)]

        self.assertEqual(watch.repeated_escalations(rows), {})




class ControlBandRuleTest(unittest.TestCase):
    def test_in_band_data_fires_nothing_and_each_rule_fires_once(self) -> None:
        rules = (
            config.BandRule(kind="sigma", tier="log", sigma=3.0, count=1, span=1),
            config.BandRule(kind="sigma", tier="log", sigma=2.0, count=2, span=3),
            config.BandRule(kind="same_side", tier="log", count=8, span=8),
        )

        self.assertEqual(watch.fired_rules(rules, mean=0.0, deviation=1.0, values=[0.5, -0.5] * 4), [])
        cases = (
            ([0.0] * 7 + [3.1], rules[0]),
            ([0.0] * 5 + [2.1, 0.0, 2.1], rules[1]),
            ([0.5] * 8, rules[2]),
        )
        for values, expected in cases:
            with self.subTest(rule=expected):
                self.assertEqual(watch.fired_rules(rules, mean=0.0, deviation=1.0, values=values), [expected])

    def test_daily_metrics_cover_rates_cycle_time_rework_checks_and_tests(self) -> None:
        day = datetime(2026, 10, 24, tzinfo=UTC)
        at = lambda hour: day.replace(hour=hour).isoformat()
        test_id = "tests.test_status.StatusTest.test_port"
        rows = [
            {"event": "attempt", "ticket": 1, "gate": "FAIL", "at": at(10)},
            {"event": "attempt", "ticket": 2, "gate": "PASS", "at": at(10)},
            {"event": "lifecycle", "kind": "result", "stage": "gate-check", "ticket": 1,
             "check": "test", "passed": False, "failed_tests": [test_id], "at": at(10)},
            {"event": "claimed", "ticket": 2, "at": at(9)},
            {"event": "review", "ticket": 2, "at": at(10)},
            {"event": "review", "ticket": 2, "at": at(11)},
            {"event": "review", "ticket": 2, "at": at(12)},
            {"event": "merged", "ticket": 2, "at": at(13)},
            {"event": "escalate", "ticket": 1, "code": "gate_failed", "at": at(13)},
        ]

        points = watch.metric_series(rows, day.date(), day.date())
        values = {name: series[day.date().isoformat()]["value"] for name, series in points.items()}

        self.assertEqual(values["gate_failure_rate"], 0.5)
        self.assertEqual(values["gate_failures_by_check:test"], 1.0)
        self.assertEqual(values[f"gate_failures_by_test:{test_id}"], 1.0)
        self.assertEqual(values["ready_for_human_rate"], 0.5)
        self.assertEqual(values["claim_to_merge_hours"], 4.0)
        self.assertEqual(values["rework_rounds"], 1.0)


class ControlBandWatchTest(unittest.TestCase):
    TEST_ID = "comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone"
    ENGINE = "factory/engine"
    NOW = datetime(2026, 10, 24, 23, tzinfo=UTC)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = config.Config(self.root, REPO)
        self.cfg.watch_engine_repo = self.ENGINE
        self.cfg.watch_bands = [
            config.Band(
                metric="gate_failures_by_test",
                window_days=28,
                rules=(config.BandRule(
                    kind="sigma", tier="act", sigma=3.0, count=1, span=1,
                    min_tickets=2,
                ),),
            ),
        ]
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.gh = HostFakeGh()
        for patch in (
            mock.patch.object(dispatch, "run", self.gh),
            mock.patch.object(watch, "failed_units", return_value=[]),
            mock.patch.object(watch, "runtime_errors", return_value=[]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def rows(self) -> list[dict]:
        rows = []
        for ago in range(28, 0, -1):
            at = (self.NOW - timedelta(days=ago)).replace(hour=12).isoformat()
            rows.append({"event": "attempt", "ticket": 100 + ago, "gate": "PASS", "at": at})
        for ticket, pr, minute in ((17, 25, 6), (73, 74, 56)):
            at = self.NOW.replace(hour=15, minute=minute).isoformat()
            rows += [
                {"event": "pr-opened", "ticket": ticket, "pr": pr, "at": at},
                {"event": "lifecycle", "kind": "result", "stage": "gate-check",
                 "ticket": ticket, "check": "test", "passed": False,
                 "failed_tests": [self.TEST_ID], "changed_paths": [f"src/change_{ticket}.py"], "at": at},
                escalate_row(ticket, "gate_failed", "gate failed after rebase onto moved main", at=at),
            ]
        return rows

    def journal(self, rows: list[dict]) -> None:
        for row in rows:
            lifecycle.append(dispatch.EVENTS, row)

    def test_rocm_cli_replay_files_one_issue_then_dedupes_and_closes_in_band(self) -> None:
        self.journal(self.rows())

        filed = watch.run_pass(self.cfg, now=self.NOW)

        self.assertEqual(len(filed), 1)
        self.assertEqual(len(self.gh.created), 1)
        create = self.gh.created[0]
        self.assertEqual(create[create.index("--label") + 1], "bug,needs-triage")
        body = create[create.index("--body") + 1]
        self.assertIn(self.TEST_ID, body)
        self.assertIn("PR #25", body)
        self.assertIn("PR #74", body)
        self.assertFalse(any("pr" in call[:3] for call in self.gh.calls))
        self.assertTrue(all(
            call[call.index("--repo") + 1] == self.ENGINE
            for call in self.gh.calls if "--repo" in call
        ))

        watch.run_pass(self.cfg, now=self.NOW)
        self.assertEqual(len(self.gh.created), 1)

        with mock.patch.object(watch, "_age", return_value=timedelta(days=2)):
            watch.run_pass(self.cfg, now=self.NOW + timedelta(days=2))
        self.assertEqual(self.gh.issues[101]["state"], "CLOSED")

    def test_single_new_test_failure_files_nothing(self) -> None:
        self.journal([row for row in self.rows() if row.get("ticket") != 73])

        self.assertEqual(watch.run_pass(self.cfg, now=self.NOW), [])
        self.assertEqual(self.gh.created, [])

    def test_changed_test_does_not_count_as_flake_evidence(self) -> None:
        rows = self.rows()
        failed = next(
            row for row in rows
            if row.get("ticket") == 17 and row.get("stage") == "gate-check"
        )
        failed["changed_paths"] = ["src/comfyui.rs"]

        self.assertEqual(watch.band_signals(self.cfg, rows, self.NOW), [])

    def test_log_and_diagnose_tiers_make_no_github_write(self) -> None:
        base = {
            "kind": "band", "stage": "control-band", "key": "gate_failure_rate",
            "metric": "gate_failure_rate", "rule": "1 of 1 beyond 3 sigma",
            "rows": [], "excerpt": "metric evidence", "log": None,
            "latest": self.NOW.isoformat(), "command": "factory watch --dry-run",
        }
        observed = [
            {**base, "fingerprint": "band-log", "tier": "log"},
            {**base, "fingerprint": "band-diagnose", "tier": "diagnose"},
        ]

        with mock.patch.object(watch, "signals", return_value=observed), \
                mock.patch("factory.triage.call_llm", return_value="Likely shared port contention.") as model:
            returned = watch.run_pass(self.cfg, now=self.NOW)

        self.assertEqual(returned, observed)
        self.assertEqual(observed[1]["diagnosis"], "Likely shared port contention.")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.gh.calls, [])

if __name__ == "__main__":
    unittest.main()
