"""Boundary coverage for unchanged worker and review rounds."""
from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, results
from tests import test_actionable_rework as rework_tests
from tests import test_risk_merge as risk_tests

HEAD = "f" * 40
BASE = "e" * 40
NEW_HEAD = "1" * 40


class WorkerRoundCacheEdgesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.fixups = []
        dispatch.configure(self.cfg)
        self.wt = dispatch.FACTORY / "wt-7"
        (self.wt / ".factory").mkdir(parents=True)
        dispatch.LOGS.mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.rows: list[dict] = []
        self.head_values = [HEAD, HEAD, HEAD]
        self.record = self.patch(dispatch, "record")
        self.gate = self.patch(dispatch, "run_gate", return_value=(False, "fresh gate report"))
        self.patch(dispatch, "build_prompt", return_value="brief")
        self.patch(dispatch, "fallback_worker", return_value="default")
        self.patch(self.cfg, "worker_spec", return_value=SimpleNamespace(
            argv=["worker"], transcript=None, wrap=None, env={}, version=None, model=None,
        ))
        self.patch(dispatch, "_with_transcript", return_value=(["worker"], None))
        self.patch(dispatch, "run_worker", return_value=(0, None))
        self.patch(dispatch, "set_worker_health")
        self.patch(dispatch, "journal_attempt_plan")
        self.patch(dispatch, "_transcript", return_value=None)
        self.patch(dispatch, "worker_usage", return_value={"cost": None})
        self.patch(dispatch, "sync_event", return_value=None)
        self.patch(dispatch, "restore_unplanned_lockfiles")
        self.patch(dispatch, "commit_leftovers")
        self.patch(results, "source_metadata", return_value={})
        self.patch(dispatch.generation, "current", side_effect=lambda _events, _ticket: SimpleNamespace(rows=self.rows))
        self.patch(dispatch.lifecycle, "read_events", return_value=[])
        self.patch(config, "engine_commit", return_value="engine-revision")
        self.patch(dispatch, "run", side_effect=self.command_result)

    def patch(self, target, name, **kwargs):
        return self.stack.enter_context(mock.patch.object(target, name, **kwargs))

    def command_result(self, command, **_kwargs):
        if command[:2] == ["git", "rev-parse"]:
            if command[2] == "HEAD":
                output = self.head_values.pop(0)
            elif command[2] == "origin/main":
                output = BASE
            else:
                raise AssertionError(command)
            return subprocess.CompletedProcess(command, 0, output + "\n", "")
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    def gate_key(self, head: str = HEAD) -> tuple[str, ...]:
        flags = json.dumps(dispatch.gate_flags(None, set()), sort_keys=True)
        return head, BASE, flags, dispatch.__version__, "engine-revision"

    def cached_row(self, report: bytes, gate: str = "PASS", event: str = "attempt") -> dict:
        return json.loads(json.dumps({
            "event": event,
            "ticket": 7,
            "head": HEAD,
            "actual_head": HEAD,
            "clean": True,
            "gate": gate,
            "gate_key": list(self.gate_key()),
            "gate_sha256": hashlib.sha256(report).hexdigest(),
        }))

    def write_cache(self, report: bytes) -> Path:
        path = dispatch.FACTORY / "gate-cache" / "7.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(report)
        return path

    def run_round(self, cache: dict | None = None):
        return dispatch.worker_round(
            7, self.wt, set(), "edge coverage", "", 1, float("inf"), gate_cache=cache,
        )

    def test_persisted_cache_uses_latest_attempt_not_a_later_event(self) -> None:
        report = b"recorded report"
        attempt = self.cached_row(report, "PASS")
        later_non_attempt = {"event": "approved", "ticket": 7}
        self.rows = [attempt, later_non_attempt]
        self.write_cache(report)

        ok, text, _log, _head = self.run_round({})

        self.assertTrue(ok)
        self.assertEqual(text, report.decode())
        self.gate.assert_not_called()

    def test_persisted_cache_classifies_only_exact_pass_as_success(self) -> None:
        for status, expected in (("PASS", True), ("FAIL", False)):
            with self.subTest(status=status):
                report = f"{status} report".encode()
                self.rows = [self.cached_row(report, status)]
                self.write_cache(report)
                self.head_values = [HEAD, HEAD, HEAD]
                self.gate.reset_mock()

                ok, text, _log, _head = self.run_round({})

                self.assertIs(ok, expected)
                self.assertEqual(text, report.decode())
                self.gate.assert_not_called()

    def test_tampered_cache_with_ordered_real_digests_is_not_reused(self) -> None:
        reports = [f"report {index}".encode() for index in range(20)]
        original, tampered = next(
            (left, right) for left in reports for right in reports
            if hashlib.sha256(left).hexdigest() < hashlib.sha256(right).hexdigest()
        )
        self.rows = [self.cached_row(original)]
        self.write_cache(tampered)

        ok, text, _log, _head = self.run_round({})

        self.assertFalse(ok)
        self.assertEqual(text, "fresh gate report")
        self.gate.assert_called_once()

    def test_gate_that_changes_head_never_populates_the_cache(self) -> None:
        self.gate.return_value = (True, "passing but stale")
        self.head_values = [HEAD, HEAD, "0" * 40]
        cache: dict = {}
        path = dispatch.FACTORY / "gate-cache" / "7.md"

        ok, report, _log, _head = self.run_round(cache)

        self.assertFalse(ok)
        self.assertIn("HEAD changed", report)
        self.assertEqual(cache, {})
        self.assertFalse(path.exists())

    def test_fixup_cache_is_rekeyed_to_the_repaired_head(self) -> None:
        self.cfg.fixups = [["fix"]]
        self.head_values = [HEAD, HEAD, HEAD, NEW_HEAD]
        self.patch(
            dispatch, "retry_gate_fixups",
            return_value=(True, "fixed gate report", NEW_HEAD),
        )
        cache: dict = {}

        ok, _report, _log, head = self.run_round(cache)

        expected_key = (NEW_HEAD, *self.gate_key()[1:])
        self.assertTrue(ok)
        self.assertEqual(head, NEW_HEAD)
        self.assertEqual(list(cache), [expected_key])
        attempt = next(call for call in self.record.call_args_list if call.args == ("attempt",))
        self.assertEqual(attempt.kwargs["gate_key"], list(expected_key))


class AddOnlyRequestEdgesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.wt = Path(self.temp.name)
        (self.wt / ".factory").mkdir()

    def request(self, value: str) -> list[str]:
        (self.wt / ".factory" / "handoff-7.md").write_text(
            f"Needs: add-only-authorization {value}\n",
        )
        return dispatch.add_only_request(self.wt, 7, set())

    def test_absolute_traversal_and_malformed_handoff_paths_are_rejected(self) -> None:
        for value in ("/tmp/test_contract.py:12", "tests/../contract.py:12", ".", "'unterminated"):
            with self.subTest(value=value):
                self.assertEqual(self.request(value), [])


class ProcessTicketUnchangedEdgesTest(unittest.TestCase):
    def fixture(self):
        fixture = rework_tests.ActionableReworkTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = fixture.command_result
        fixture.command.side_effect = lambda args, **kwargs: (
            subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "status"] else original(args, **kwargs)
        )
        return fixture

    def prior_attempt(self, *, head=risk_tests.HEAD, actual_head=risk_tests.HEAD, clean=True) -> None:
        dispatch.record(
            "attempt", ticket=7, head=head, actual_head=actual_head,
            clean=clean, gate="FAIL", worker_model="worker/model",
        )

    def test_unchanged_deferred_rework_stops_before_pr_consumers(self) -> None:
        fixture = self.fixture()
        findings = rework_tests.SURVIVORS
        dispatch.record(
            "review-rework", ticket=7, head=risk_tests.HEAD, round=1,
            findings=findings, deferred=True,
        )
        review = fixture.patch(
            dispatch, "pr_gate_and_review", return_value=("APPROVE", "approved", False),
        )

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)

        review.assert_not_called()
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "mutation_survivors_unresolved")
        self.assertEqual(fixture.escalate.call_args.kwargs["extra"], findings)

    def test_changed_head_does_not_match_carried_deferred_rework(self) -> None:
        fixture = self.fixture()
        dispatch.record(
            "review-rework", ticket=7, head=risk_tests.HEAD, round=1,
            findings="old findings", deferred=True,
        )
        fixture.heads.append(risk_tests.HEAD)
        review = fixture.patch(
            dispatch, "pr_gate_and_review", return_value=("APPROVE", "approved", False),
        )
        fixture.patch(dispatch, "approve_pr", return_value=True)

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)

        review.assert_called_once()
        fixture.escalate.assert_not_called()

    def test_mismatched_actual_head_is_not_a_previous_unchanged_attempt(self) -> None:
        fixture = self.fixture()
        prior = "f" * 40
        self.prior_attempt(head=prior, actual_head="0" * 40)
        heads = iter((prior, "e" * 40, "d" * 40))
        fixture.worker.side_effect = lambda *_args, **_kwargs: (
            False, "gate failed", fixture.root / "worker.log", next(heads),
        )

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)

        self.assertEqual(fixture.worker.call_count, dispatch.MAX_ATTEMPTS)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "gate_failed")

    def test_dirty_previous_attempt_is_not_reused_as_unchanged(self) -> None:
        fixture = self.fixture()
        self.prior_attempt(clean=False)
        heads = iter((risk_tests.HEAD, "e" * 40, "d" * 40))
        fixture.worker.side_effect = lambda *_args, **_kwargs: (
            False, "gate failed", fixture.root / "worker.log", next(heads),
        )

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)

        self.assertEqual(fixture.worker.call_count, dispatch.MAX_ATTEMPTS)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "gate_failed")

    def deadline_fixture(self, end: float):
        fixture = self.fixture()
        fixture.clock = 0.0
        fixture.patch(dispatch.time, "monotonic", side_effect=lambda: fixture.clock)
        self.prior_attempt()

        def work(*_args, **_kwargs):
            fixture.clock = end
            return False, "gate failed", fixture.root / "worker.log", risk_tests.HEAD

        fixture.worker.side_effect = work
        dispatch.process_ticket(fixture.issue, 1, False, forced=True)
        return fixture

    def test_unchanged_round_at_exact_deadline_keeps_no_progress_cause(self) -> None:
        fixture = self.deadline_fixture(60.0)

        self.assertEqual(fixture.worker.call_count, 1)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "worker_no_progress")

    def test_unchanged_round_after_deadline_keeps_budget_cause(self) -> None:
        fixture = self.deadline_fixture(61.0)

        self.assertEqual(fixture.worker.call_count, 1)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "budget")

    def authorization_bounce(self, *, bounce_ok: bool, pr_failed: bool):
        from factory import manage

        fixture = self.fixture()
        fixture.cfg.review_rounds = 1
        original_work = fixture.work

        def work(*args, **kwargs):
            result = original_work(*args, **kwargs)
            if len(fixture.heads) == 2:
                return bounce_ok, "bounce gate report", result[2], result[3]
            return result

        fixture.worker.side_effect = work
        fixture.patch(
            dispatch, "pr_gate_and_review",
            return_value=("REVISE", "open review findings", pr_failed),
        )
        fixture.patch(dispatch, "_actionable_review", return_value=True)
        fixture.patch(dispatch, "add_only_request", return_value=["tests/test_contract.py:12"])
        request = fixture.patch(manage, "request_add_only_authorization")

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)
        return fixture, request

    def test_failed_attempt_gate_on_review_bounce_preserves_findings_and_report(self) -> None:
        fixture, request = self.authorization_bounce(bounce_ok=False, pr_failed=False)

        request.assert_called_once()
        self.assertEqual(request.call_args.args[3], "open review findings\n\nbounce gate report")
        fixture.escalate.assert_not_called()

    def test_failed_pr_gate_on_review_bounce_preserves_findings_and_report(self) -> None:
        fixture, request = self.authorization_bounce(bounce_ok=True, pr_failed=True)

        request.assert_called_once()
        self.assertEqual(request.call_args.args[3], "open review findings\n\nbounce gate report")
        fixture.escalate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
