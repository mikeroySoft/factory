"""Round admission reserves validation time; active worker gates survive the cutoff."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as fixtures


class RoundGateBudgetTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config

    def configure_round(self):
        dispatch.configure(config.load(self.repo))
        dispatch.LOGS.mkdir(parents=True, exist_ok=True)
        wt = dispatch.ensure_worktree(7)
        (wt / ".factory").mkdir(exist_ok=True)
        return wt

    def measure(self, wt, seconds):
        dispatch._record_gate_decision(
            7, False, "- code: FAIL\n", self.git("rev-parse", "HEAD").stdout.strip(),
            str(wt / ".factory/gate-report-7.md"), seconds,
        )

    def test_resume_refuses_work_when_gate_consumes_remaining_budget(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 50)
            head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout
            with mock.patch.object(dispatch.time, "monotonic", return_value=10), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)) as worker, \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
                ok, report, _, _ = dispatch.worker_round(7, wt, set(), "fix", "resume", 1, 60)
            self.assertFalse(ok)
            self.assertIn("budget", report)
            worker.assert_not_called()
            gate.assert_not_called()
            self.assertEqual(self.git("-C", str(wt), "rev-parse", "HEAD").stdout, head)
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "round-budget"]
        self.assertEqual(row["gate_reserve_seconds"], 50)
        self.assertEqual(row["gate_reserve_source"], "last_gate")
        self.assertFalse(row["admitted"])

    def test_bounce_stops_work_at_reserve_and_runs_full_gate(self):
        clock = [0]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 20)

            def work(*args, **kwargs):
                self.assertEqual(args[4], 40)
                clock[0] = 40
                (wt / "change.txt").write_text("fixed\n")
                return 143, "budget_min"

            original_gate = dispatch.run_gate

            def gate(*args, **kwargs):
                self.assertNotIn("timeout", kwargs)
                result = original_gate(*args, **kwargs)
                clock[0] = 65  # estimate was low: an admitted gate must still finish
                return result

            with mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "run_worker", side_effect=work), \
                    mock.patch.object(dispatch, "run_gate", side_effect=gate):
                ok, report, _, head = dispatch.worker_round(7, wt, set(), "fix", "bounce", 4, 60)
            self.assertTrue(ok, report)
            self.assertIn("- code: PASS", report)
            self.assertEqual(head, self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip())
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "round-budget"]
        self.assertEqual(row["gate_reserve_source"], "last_gate")
        self.assertEqual(row["gate_reserve_seconds"], 20)

    def test_configured_floor_is_used_without_worktree_history(self):
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text().replace("[dispatch]\n", "[dispatch]\ngate_reserve_seconds = 45\n"))
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            # Neither another worktree nor the PR tier estimates this attempt gate.
            self.measure(self.base, 100)
            dispatch._record_gate_decision(7, True, "PASS", "a" * 40,
                                           str(wt / ".factory/gate-report-7-pr.md"), 100)
            with mock.patch.object(dispatch.time, "monotonic", return_value=0), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)) as worker, \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
                dispatch.worker_round(7, wt, set(), "fix", "resume", 1, 60)
            self.assertEqual(worker.call_args.args[4], 15)
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "round-budget"]
        self.assertEqual(row["gate_reserve_source"], "floor")
        self.assertEqual(row["gate_reserve_seconds"], 45)

    def test_running_worker_gate_survives_round_deadline(self):
        """A real gate scope, nested beneath this worker, spans the budget cutoff."""
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            clock = [0]
            polls = [0]
            proc = mock.Mock()
            proc.pid = os.getpid()
            state = (None, "head", "", None)
            with lifecycle.scope(self.events, "worker", ticket=7, attempt=4):
                worker_gate = lifecycle.Execution(self.events, "gate")
                worker_gate.emit("enter")

                def wait(**kwargs):
                    polls[0] += 1
                    clock[0] = 61
                    if polls[0] == 1:
                        raise subprocess.TimeoutExpired("worker", 5)
                    worker_gate.emit("exit")
                    return 0

                proc.wait.side_effect = wait
                with mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                        mock.patch.object(dispatch, "_activity", return_value=state):
                    fired = dispatch._watch(proc, wt, self.base / "worker.log", self.base / "handoff", 60, state)
                self.assertIsNone(fired)
                self.assertEqual(polls[0], 2)

    def test_insufficient_bounce_does_not_push_an_unvalidated_head(self):
        clock = [0]
        original_run = dispatch.run
        calls = [0]

        def run(cmd, **kwargs):
            result = original_run(cmd, **kwargs)
            if cmd[3:6] == ["-m", "factory", "gate"]:
                calls[0] += 1
                if calls[0] == 1:
                    clock[0] = 50
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])):
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 1, False, forced=True)
        rows = lifecycle.read_events(self.events)
        attempts = [r for r in rows if r.get("event") == "attempt"]
        self.assertEqual(len([r for r in attempts if r.get("worker_exit") == 0]), 1)
        escalation, = [r for r in rows if r.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "budget")
        self.assertEqual(json.loads(self.state.read_text())["reviews"], 1)
        remote = self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0]
        self.assertEqual(remote, attempts[0]["head"])

    def test_active_gate_extension_has_a_hard_cap(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            dispatch.cfg.check_timeout = 10
            clock = [0]
            ticks = iter([61, 101])
            proc = mock.Mock()
            state = (None, "head", "", None)

            def wait(**kwargs):
                clock[0] = next(ticks)
                raise subprocess.TimeoutExpired("worker", 5)

            proc.wait.side_effect = wait
            with lifecycle.scope(self.events, "worker", ticket=7):
                active = lifecycle.Execution(self.events, "gate")
                active.emit("enter")
                with mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                        mock.patch.object(dispatch, "_activity", return_value=state):
                    fired = dispatch._watch(proc, wt, self.base / "log", self.base / "handoff", 60, state)
                active.emit("exit")
            self.assertEqual(fired, "gate_timeout")

    def test_unrelated_gate_does_not_extend_work(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            foreign = lifecycle.Execution(self.events, "gate", ticket=8)
            foreign.emit("enter")
            proc = mock.Mock()
            proc.wait.side_effect = subprocess.TimeoutExpired("worker", 5)
            state = (None, "head", "", None)
            with lifecycle.scope(self.events, "worker", ticket=7), \
                    mock.patch.object(dispatch.time, "monotonic", return_value=61), \
                    mock.patch.object(dispatch, "_activity", return_value=state):
                fired = dispatch._watch(proc, wt, self.base / "log", self.base / "handoff", 60, state)
            foreign.emit("exit")
            self.assertEqual(fired, "budget_min")

    def test_floor_rejects_invalid_config_values(self):
        path = self.repo / ".factory.toml"
        original = path.read_text()
        for value in ("0", "-1", "nan", "inf", "true", '"30"'):
            with self.subTest(value=value), mock.patch.dict(os.environ, self.env):
                path.write_text(original.replace("[dispatch]\n", f"[dispatch]\ngate_reserve_seconds = {value}\n"))
                with self.assertRaisesRegex(config.ConfigError, "finite positive"):
                    config.load(self.repo)


if __name__ == "__main__":
    unittest.main()
