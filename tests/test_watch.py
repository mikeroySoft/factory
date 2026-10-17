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
            return subprocess.CompletedProcess(cmd, 0, f"https://github.com/{REPO}/issues/{self.next_issue}\n", "")
        if cmd[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.issues[int(cmd[3])]), "")
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
        self.journal(exit_row("manage", "mechanism_failure", "github_command_failed"))
        filed = watch.run_pass(self.cfg)
        self.assertEqual(len(filed), 1)
        self.assertEqual(len(self.gh.created), 1)
        create = self.gh.created[0]
        self.assertEqual(create[create.index("--label") + 1], "bug,needs-triage")
        body = create[create.index("--body") + 1]
        self.assertIn(f"Fingerprint: {filed[0]['fingerprint']}", body)
        self.assertIn("## Expected behavior", body)
        self.assertIn("github_command_failed", body)
        self.assertGreaterEqual(len(body.strip()), 80)
        rows = self.events("watch")
        self.assertEqual([(r["fingerprint"], r["issue"]) for r in rows], [(filed[0]["fingerprint"], 101)])
        self.assertIn("at", rows[0])

    def test_open_issue_is_not_refiled_closed_issue_with_recurrence_is(self) -> None:
        self.journal(exit_row("manage", "mechanism_failure", "github_command_failed"))
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1)

        watch.run_pass(self.cfg)
        self.journal(exit_row("manage", "mechanism_failure", "github_command_failed"))
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1, "open issue must not be refiled")

        self.gh.close(101)
        watch.run_pass(self.cfg)
        self.assertEqual(len(self.gh.created), 1, "closed issue without recurrence stays closed")

        self.journal(exit_row("manage", "mechanism_failure", "github_command_failed"))
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
        self.journal(*(exit_row("worker", "unknown", f"worker_exit:{code}") for code in range(1, 7)))
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
            exit_row("worker", "unknown", "worker_exit:2", ticket=7, attempt=1),
        )
        watch.run_pass(self.cfg)
        body = self.gh.created[0][self.gh.created[0].index("--body") + 1]
        self.assertIn("line 59", body)
        self.assertNotIn("line 19\n", body)
        self.assertIn("events.jsonl", body)

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


if __name__ == "__main__":
    unittest.main()
