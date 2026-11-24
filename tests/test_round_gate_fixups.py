"""Mechanical gate repairs stop cleanly at their reserved work cutoff."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as fixtures


class RoundGateFixupsTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config

    REPORT = "ORIGINAL GATE REPORT\n- code: FAIL\n"

    def configure_round(self):
        dispatch.configure(config.load(self.repo))
        wt = dispatch.ensure_worktree(7)
        (wt / ".factory").mkdir(exist_ok=True)
        head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        return wt, head

    def budget_events(self):
        return [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "gate-fixup-budget"
        ]

    def test_fixup_is_not_launched_at_or_after_cutoff_and_journal_clamps_remaining(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt, head = self.configure_round()
            for now in (10.0, 12.0):
                with self.subTest(seconds_remaining=10.0 - now):
                    if self.events.exists():
                        self.events.unlink()
                    with mock.patch.object(dispatch.cfg, "fixups", [["repair"]]), \
                            mock.patch.object(dispatch.time, "monotonic", return_value=now), \
                            mock.patch.object(dispatch, "run", wraps=dispatch.run) as run, \
                            mock.patch.object(dispatch, "commit_leftovers") as commit, \
                            mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
                        ok, report, candidate = dispatch.retry_gate_fixups(
                            wt=wt, n=7, report=self.REPORT, head=head, work_deadline=10.0,
                        )

                    self.assertFalse(ok)
                    self.assertEqual(candidate, head)
                    self.assertIn(self.REPORT, report)
                    self.assertEqual((wt / ".factory/gate-report-7.md").read_text(), report)
                    self.assertFalse(any(call.args[0] == ["repair"] for call in run.call_args_list))
                    commit.assert_not_called()
                    gate.assert_not_called()
                    [event] = self.budget_events()
                    self.assertEqual(event["seconds_remaining"], 0)
                    self.assertEqual(event["work_deadline"], 10.0)

    def exercise_successful_fixup_cutoff(self, commands, ended_at=10.0):
        clock = [9.0]
        repair_timeouts = []
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt, head = self.configure_round()
            original_run = dispatch.run

            def run(cmd, *args, **kwargs):
                if cmd == ["repair"]:
                    repair_timeouts.append(kwargs["timeout"])
                    (wt / "partial-fix.txt").write_text("repaired\n")
                    clock[0] = ended_at
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if cmd == ["must-not-run"]:
                    self.fail("launched a repair after the work cutoff")
                return original_run(cmd, *args, **kwargs)
            with mock.patch.object(dispatch.cfg, "fixups", commands), \
                    mock.patch.object(dispatch.cfg, "check_timeout", 30), \
                    mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(dispatch, "commit_leftovers") as commit, \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
                ok, report, candidate = dispatch.retry_gate_fixups(
                    wt, 7, self.REPORT, head, work_deadline=10.0,
                )

            self.assertEqual(repair_timeouts, [1.0])
            self.assertFalse(ok)
            self.assertEqual(candidate, head)
            self.assertEqual(self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip(), head)
            self.assertIn(self.REPORT, report)
            self.assertEqual((wt / ".factory/gate-report-7.md").read_text(), report)
            commit.assert_not_called()
            gate.assert_not_called()
            [event] = self.budget_events()
            self.assertEqual(event["seconds_remaining"], 0)

    def test_successful_fixup_finishing_exactly_at_cutoff_stops_next_repair(self):
        self.exercise_successful_fixup_cutoff([["repair"], ["must-not-run"]])

    def test_last_fixup_finishing_exactly_at_cutoff_is_not_committed_or_regated(self):
        self.exercise_successful_fixup_cutoff([["repair"]])

    def test_last_fixup_finishing_after_cutoff_is_not_committed_or_regated(self):
        self.exercise_successful_fixup_cutoff([["repair"]], ended_at=11.0)

    def test_nonzero_repair_retains_both_output_streams_without_raising(self):
        command = [sys.executable, "-c",
                   "import sys; print('repair stdout'); print('repair stderr', file=sys.stderr); raise SystemExit(2)"]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt, head = self.configure_round()
            with mock.patch.object(dispatch.cfg, "fixups", [command]), \
                    mock.patch.object(dispatch.time, "monotonic", return_value=5), \
                    mock.patch.object(dispatch, "commit_leftovers") as commit, \
                    mock.patch.object(dispatch, "run_gate") as gate:
                ok, report, candidate = dispatch.retry_gate_fixups(wt, 7, self.REPORT, head, work_deadline=10)
            self.assertFalse(ok)
            self.assertEqual(candidate, head)
            self.assertIn(self.REPORT, report)
            self.assertIn("repair stdout", report)
            self.assertIn("repair stderr", report)
            self.assertIn("exit 2", report)
            self.assertEqual(self.budget_events(), [])
            commit.assert_not_called()
            gate.assert_not_called()

    def exercise_exception(self, exception, moment):
        clock = [5.0]
        repair_timeouts = []
        wt, head = self.configure_round()
        original_run = dispatch.run

        def run(cmd, *args, **kwargs):
            if cmd == ["repair"]:
                repair_timeouts.append(kwargs["timeout"])
                clock[0] = {"before": 9.0, "equal": 10.0, "after": 11.0}[moment]
                if exception == "timeout":
                    raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
                raise OSError("repair unavailable")
            if cmd == ["must-not-run"]:
                self.fail("launched a second repair after the first failed")
            return original_run(cmd, *args, **kwargs)

        with mock.patch.object(dispatch.cfg, "fixups", [["repair"], ["must-not-run"]]), \
                mock.patch.object(dispatch.cfg, "check_timeout", 30), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "commit_leftovers") as commit, \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
            ok, report, candidate = dispatch.retry_gate_fixups(
                wt, 7, self.REPORT, head, work_deadline=10.0,
            )

        self.assertEqual(repair_timeouts, [5.0], "repair timeout must not exceed remaining work time")
        self.assertFalse(ok)
        self.assertEqual(candidate, head)
        self.assertEqual(self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip(), head)
        self.assertIn(self.REPORT, report)
        self.assertEqual((wt / ".factory/gate-report-7.md").read_text(), report)
        commit.assert_not_called()
        gate.assert_not_called()
        return report

    def test_oserror_before_at_and_after_deadline_is_a_fixup_failure_not_a_budget_stop(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            for moment in ("before", "equal", "after"):
                with self.subTest(moment=moment):
                    if self.events.exists():
                        self.events.unlink()
                    report = self.exercise_exception("oserror", moment)
                    self.assertTrue(report.startswith(self.REPORT))
                    self.assertEqual(self.budget_events(), [])

    def test_timeout_before_deadline_is_fixup_failure_but_at_or_after_is_budget_stop(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            for moment in ("before", "equal", "after"):
                with self.subTest(moment=moment):
                    if self.events.exists():
                        self.events.unlink()
                    report = self.exercise_exception("timeout", moment)
                    events = self.budget_events()
                    if moment == "before":
                        self.assertTrue(report.startswith(self.REPORT))
                        self.assertEqual(events, [])
                    else:
                        self.assertTrue(report.startswith("budget exceeded during mechanical fixups"))
                        [event] = events
                        self.assertEqual(event["seconds_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
