"""#629: held tickets and unverified/non-OMP runners never launch ticket workers."""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from factory import config, dispatch
from tests import test_worker_health as health_suite


class DispatchFailClosedTest(unittest.TestCase):
    setUp = health_suite.WorkerHealthTest.setUp
    patched = health_suite.WorkerHealthTest.patched
    events = health_suite.WorkerHealthTest.events
    ticket = health_suite.WorkerHealthTest.ticket

    def test_held_labels_override_agent_label_and_forced_dispatch(self) -> None:
        for label in (config.LABEL_HUMAN, "factory-held"):
            for forced in (False, True):
                with self.subTest(label=label, forced=forced):
                    self.ticket(label)
                    with self.patched(), mock.patch.object(dispatch, "run_worker", side_effect=AssertionError("ticket worker spawned")) as worker:
                        issue = self.github.issue()
                        dispatch.process_ticket(issue, 30, False, forced=forced)
                    worker.assert_not_called()
                    self.assertEqual(self.events("claimed"), [])
                    self.assertEqual(self.github.assignees, [])
                    self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT, label})

    def test_frontier_marks_held_tickets_ineligible(self) -> None:
        for label in (config.LABEL_HUMAN, "factory-held"):
            with self.subTest(label=label):
                self.ticket(label)
                with self.patched():
                    [issue] = dispatch.frontier()
                self.assertIn(label, issue.get("held", ""))

    def test_fresh_read_stops_a_ticket_escalated_after_frontier(self) -> None:
        self.ticket()
        stale = self.github.issue()
        for label in (config.LABEL_HUMAN, "factory-held"):
            with self.subTest(label=label):
                self.github.issue_labels = {config.LABEL_AGENT, label}
                with self.patched(), mock.patch.object(dispatch, "run_worker", side_effect=AssertionError("ticket worker spawned")) as worker:
                    dispatch.process_ticket(stale, 30, False)
                worker.assert_not_called()
                self.assertEqual(self.events("claimed"), [])

    def test_worker_round_rechecks_labels_before_every_spawn(self) -> None:
        self.ticket()
        self.cfg.workers = {"default": ["omp", "-p", "@{prompt}"]}
        self.cfg.worker_probe = {"default": [sys.executable, "-c", "pass"]}
        wt = dispatch.ensure_worktree(9)
        dispatch.LOGS.mkdir(parents=True, exist_ok=True)
        for label in (config.LABEL_HUMAN, "factory-held"):
            with self.subTest(label=label):
                self.github.issue_labels = {config.LABEL_AGENT, label}
                with self.patched(), mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                        mock.patch.object(dispatch.shutil, "which", return_value="/bin/omp"), \
                        mock.patch.object(dispatch, "run_worker", side_effect=AssertionError("ticket worker spawned")) as worker, \
                        self.assertRaisesRegex(dispatch.WorkerUnavailable, label):
                    dispatch.worker_round(9, wt, {config.LABEL_AGENT}, "ticket", "", 2, float("inf"))
                worker.assert_not_called()

    def test_runner_rejects_non_omp_or_missing_credentials_without_fallback(self) -> None:
        self.ticket()
        wt = dispatch.ensure_worktree(9)
        dispatch.LOGS.mkdir(parents=True, exist_ok=True)
        cases = (
            (["droid", "exec", "{prompt}"], [sys.executable, "-c", "pass"]),
            (["omp", "-p", "@{prompt}"], None),
            (["omp", "-p", "@{prompt}"], [sys.executable, "-c", "raise SystemExit(1)"]),
        )
        for command, probe in cases:
            with self.subTest(command=command, probe=probe):
                self.cfg.workers = {"default": ["droid", "exec"], "chosen": command}
                self.cfg.worker_probe = {"chosen": probe} if probe else {}
                with self.patched(), mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                        mock.patch.object(dispatch.shutil, "which", return_value="/bin/omp"), \
                        mock.patch.object(dispatch, "run_worker", side_effect=AssertionError("ticket worker spawned")) as worker, \
                        self.assertRaises(dispatch.WorkerUnavailable):
                    dispatch.worker_round(9, wt, {"chosen"}, "ticket", "", 1, float("inf"))
                worker.assert_not_called()
                self.assertEqual(self.events("attempt"), [])

    def test_invalid_selected_profile_never_claims_even_with_available_default(self) -> None:
        for forced in (False, True):
            for prepass in (False, True):
                for problem in ("non-omp", "no-probe", "failed-probe", "missing-executable"):
                    with self.subTest(forced=forced, prepass=prepass, problem=problem):
                        self.ticket("chosen")
                        command = self.cfg.workers["default"]
                        probe = [sys.executable, "-c", "pass"]
                        if problem == "non-omp":
                            command = ["droid", "exec"]
                        elif problem == "no-probe":
                            probe = None
                        elif problem == "failed-probe":
                            probe = [sys.executable, "-c", "raise SystemExit(1)"]
                        else:
                            command = [str(self.tmp / "missing" / "omp")]
                        self.cfg.workers["chosen"] = command
                        if probe:
                            self.cfg.worker_probe["chosen"] = probe
                        dispatch.configure(self.cfg)
                        if prepass:
                            dispatch.worker_health_pass()
                        with self.patched(), mock.patch.object(dispatch, "run_worker") as worker:
                            dispatch.process_ticket(self.github.issue(), 30, False, forced=forced)
                        self.assertEqual(self.events("claimed"), [])
                        self.assertEqual(self.events("plan-bound"), [])
                        self.assertFalse(any("--add-assignee" in cmd for cmd in self.github.calls))
                        self.assertFalse((dispatch.FACTORY / "wt-9").exists())
                        worker.assert_not_called()

    def test_invalid_profile_is_refused_before_host_claim(self) -> None:
        self.ticket()
        self.cfg.host_name = "fixture-host"
        self.cfg.worker_probe.clear()
        with self.patched(), \
                mock.patch.object(dispatch, "host_claim_state", return_value=([], "")), \
                mock.patch.object(dispatch, "release_stale_claims", side_effect=lambda n, fresh, claims: fresh), \
                mock.patch.object(dispatch, "acquire_host_claim", return_value=None) as claim, \
                mock.patch.object(dispatch, "run_worker") as worker:
            dispatch.process_ticket(self.github.issue(), 30, False, forced=True)
        claim.assert_not_called()
        worker.assert_not_called()
        self.assertEqual(self.events("claimed"), [])

    def test_dry_run_reports_fresh_holds_instead_of_planning_a_worker(self) -> None:
        self.ticket()
        stale = self.github.issue()
        for label in (config.LABEL_HUMAN, "factory-held"):
            with self.subTest(label=label):
                self.github.issue_labels = {config.LABEL_AGENT, label}
                with self.patched(), mock.patch.object(dispatch, "log") as log:
                    dispatch.process_ticket(stale, 30, True, forced=True)
                messages = "\n".join(call.args[0] for call in log.call_args_list)
                self.assertIn(label, messages)
                self.assertNotIn("would claim", messages)
                self.assertEqual(self.events("claimed"), [])
