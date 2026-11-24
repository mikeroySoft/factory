"""Round admission reserves validation time; active worker gates survive the cutoff."""
from __future__ import annotations

import json
import os
import subprocess
import sys
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
                self.assertEqual(args[4], 20)
                clock[0] = 20
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

    def test_shutdown_overhead_leaves_the_gate_inside_round_budget(self):
        """Drive run_worker, its polling watchdog and the full SIGTERM/SIGKILL grace."""
        clock = [0]
        proc = mock.Mock(pid=2**30, returncode=None)
        proc.poll.side_effect = lambda: proc.returncode

        def wait(timeout=None):
            if proc.returncode is not None:
                return proc.returncode
            clock[0] += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

        def killpg(pid, sig):
            self.assertEqual(pid, proc.pid)
            if sig == dispatch.signal.SIGKILL:
                proc.returncode = -sig

        proc.wait.side_effect = wait
        original_popen = subprocess.Popen
        original_usage = dispatch.worker_usage
        original_gate = dispatch.run_gate
        gate_started = []

        def launch(cmd, *args, **kwargs):
            if str(self.worker) in cmd:
                (wt / "change.txt").write_text("fixed\n")
                return proc
            return original_popen(cmd, *args, **kwargs)

        def usage(*args, **kwargs):
            clock[0] += 3  # finalization between reaping the worker and starting validation
            return original_usage(*args, **kwargs)

        def gate(*args, **kwargs):
            gate_started.append(clock[0])
            result = original_gate(*args, **kwargs)
            clock[0] += 1
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 1)
            with mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(subprocess, "Popen", side_effect=launch), \
                    mock.patch.object(dispatch.os, "killpg", side_effect=killpg), \
                    mock.patch.object(dispatch, "worker_usage", side_effect=usage), \
                    mock.patch.object(dispatch, "run_gate", side_effect=gate):
                ok, report, _, _ = dispatch.worker_round(7, wt, set(), "fix", "bounce", 4, 60)
            self.assertTrue(ok, report)
            self.assertIn("- code: PASS", report)
            self.assertLessEqual(gate_started[0], 59)
            self.assertLessEqual(clock[0], 60)
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "attempt"]
        self.assertEqual((row["gate"], row["timeout"]), ("PASS", "budget_min"))

    def test_resume_refuses_when_only_shutdown_and_gate_fit(self):
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 1)
            with mock.patch.object(dispatch.time, "monotonic", return_value=45), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)) as worker, \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
                ok, report, _, _ = dispatch.worker_round(7, wt, set(), "fix", "resume", 1, 60)
            self.assertFalse(ok)
            self.assertIn("budget exceeded before work", report)
            worker.assert_not_called()
            gate.assert_not_called()
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "round-budget"]
        self.assertFalse(row["admitted"])
        self.assertEqual(row["stop_reserve_seconds"], 20)

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
                dispatch.worker_round(7, wt, set(), "fix", "resume", 1, 90)
            self.assertEqual(worker.call_args.args[4], 25)
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

    def test_retry_admission_refusal_preserves_previous_gate_failure(self):
        self.write_config(check=[sys.executable, "-c", "print('prior gate failed'); raise SystemExit(1)"])
        fixtures.LocalCLI.commit_config(self)
        clock = [0]
        original_gate = dispatch.run_gate

        def gate(*args, **kwargs):
            result = original_gate(*args, **kwargs)
            clock[0] = 50  # work cutoff exhausted, but the overall deadline is still 60
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run_gate", side_effect=gate) as check, \
                mock.patch.object(dispatch, "run_worker", wraps=dispatch.run_worker) as worker, \
                mock.patch.object(dispatch, "push_and_pr") as publish:
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 1, False, forced=True)
        worker.assert_called_once()
        check.assert_called_once()
        publish.assert_not_called()
        rows = lifecycle.read_events(self.events)
        self.assertEqual([r["admitted"] for r in rows if r.get("event") == "round-budget"], [True, False])
        self.assertEqual([r["gate"] for r in rows if r.get("event") == "attempt"], ["FAIL", None])
        escalation, = [r for r in rows if r.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "gate_failed")
        self.assertIn("gate failed", escalation["reason"])
        self.assertNotIn("budget_min / budget_exceeded", escalation["reason"])
        self.assertIn("prior gate failed", (dispatch.FACTORY / "escalations/7.md").read_text())
        self.assertFalse(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout)

    def test_expired_attempt_deadline_preserves_previous_gate_failure(self):
        self.write_config(check=[sys.executable, "-c", "print('retained gate failure'); raise SystemExit(1)"])
        fixtures.LocalCLI.commit_config(self)
        clock = [0]
        original_worker = dispatch.run_worker
        calls = []

        def worker(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return original_worker(*args, **kwargs)
            clock[0] = 61
            return 143, "gate_timeout"

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "run_gate", wraps=dispatch.run_gate) as gate, \
                mock.patch.object(dispatch, "push_and_pr") as publish:
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 1, False, forced=True)
        self.assertEqual(len(calls), 2)
        gate.assert_called_once()
        publish.assert_not_called()
        rows = lifecycle.read_events(self.events)
        attempts = [r for r in rows if r.get("event") == "attempt"]
        self.assertEqual([r["gate"] for r in attempts], ["FAIL", None])
        self.assertEqual(attempts[-1]["timeout"], "gate_timeout")
        escalation, = [r for r in rows if r.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "gate_failed")
        self.assertEqual(escalation["log"], attempts[0]["log"])
        self.assertIn("gate failed", escalation["reason"])
        self.assertNotIn("wall-clock budget", escalation["reason"])
        self.assertIn("retained gate failure", (dispatch.FACTORY / "escalations/7.md").read_text())
        self.assertFalse(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout)

    def assert_pr_failure_survives_bounce_budget(self, cutoff):
        self.write_config(extra=(
            '[[gate.check]]\nname = "pr-code"\nwhen = "pr"\nrun = '
            + json.dumps([sys.executable, "-c", "print('retained PR failure'); raise SystemExit(1)"])
            + "\n"
        ))
        fixtures.LocalCLI.commit_config(self)
        clock = [0]
        original_gate = dispatch.run_gate

        def gate(*args, **kwargs):
            result = original_gate(*args, **kwargs)
            if kwargs.get("tier") == "pr":
                clock[0] = cutoff
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run_gate", side_effect=gate) as check, \
                mock.patch.object(dispatch, "run_worker", wraps=dispatch.run_worker) as worker, \
                mock.patch.object(dispatch, "push_agent", wraps=dispatch.push_agent) as push, \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])):
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 1, False, forced=True)
        worker.assert_called_once()
        self.assertEqual(check.call_count, 2)
        push.assert_called_once()  # only the attempt-validated head, before the PR failure
        rows = lifecycle.read_events(self.events)
        self.assertEqual(
            [r["admitted"] for r in rows if r.get("event") == "round-budget"],
            [True, False] if cutoff < 60 else [True],
        )
        escalation, = [r for r in rows if r.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "pr_checks_failed")
        self.assertEqual(escalation["reason"], "pr checks failed")
        packet = (dispatch.FACTORY / "escalations/7.md").read_text()
        self.assertIn("## PR-tier gate report", packet)
        self.assertIn("- pr-code: FAIL", packet)
        self.assertIn("retained PR failure", packet)
        self.assertNotIn("budget_min / budget_exceeded", packet)
        self.assertFalse(any(r.get("event") in {"review", "approved"} for r in rows))
        attempt, = [r for r in rows if r.get("event") == "attempt" and r.get("gate") == "PASS"]
        remote = self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0]
        self.assertEqual(remote, attempt["head"])

    def test_pr_gate_failure_precedes_bounce_admission_refusal(self):
        self.assert_pr_failure_survives_bounce_budget(50)

    def test_pr_gate_failure_precedes_expired_bounce_budget(self):
        self.assert_pr_failure_survives_bounce_budget(61)

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

    def test_setup_and_fallback_cannot_launch_after_work_cutoff(self):
        def check(delay):
            with chdir(self.repo), mock.patch.dict(os.environ, self.env):
                wt = self.configure_round()
                self.measure(wt, 20)
                clock = [0]
                launches = []
                original_popen = subprocess.Popen
                original_activity = dispatch._activity

                def prompt(*args, **kwargs):
                    if delay == "prompt":
                        clock[0] = 50
                    return "fix"

                def activity(*args, **kwargs):
                    if delay == "setup":
                        clock[0] = 50
                    return original_activity(*args, **kwargs)

                def launch(cmd, *args, **kwargs):
                    if str(self.worker) in cmd:
                        launches.append(cmd)
                        if delay == "fallback" and len(launches) == 1:
                            clock[0] = 50
                            raise FileNotFoundError("worker vanished")
                    return original_popen(cmd, *args, **kwargs)

                with mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                        mock.patch.object(dispatch, "build_prompt", side_effect=prompt), \
                        mock.patch.object(dispatch, "_activity", side_effect=activity), \
                        mock.patch.object(subprocess, "Popen", side_effect=launch), \
                        mock.patch.object(dispatch, "fallback_worker", return_value="default"), \
                        mock.patch.object(dispatch, "set_worker_health"), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate:
                    ok, report, _, _ = dispatch.worker_round(7, wt, set(), "fix", "resume", 1, 60)
                self.assertFalse(ok)
                self.assertTrue(report.startswith("budget exceeded before work"), report)
                self.assertEqual(len(launches), int(delay == "fallback"))
                gate.assert_not_called()
                attempt = [r for r in lifecycle.read_events(self.events) if r.get("event") == "attempt"][-1]
                self.assertIsNone(attempt["worker_exit"])
                self.assertIsNone(attempt["gate"])
                self.assertEqual(attempt["reason"], "budget_exceeded")

        for delay in ("prompt", "setup", "fallback"):
            with self.subTest(delay=delay):
                check(delay)

    def test_short_fixup_window_is_reported_as_budget_not_gate_failure(self):
        clock = [0]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 20)

            def gate(*args, **kwargs):
                clock[0] = 45
                return False, "- code: FAIL"

            with mock.patch.object(dispatch.cfg, "fixups", [["repair"]]), \
                    mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)), \
                    mock.patch.object(dispatch, "run_gate", side_effect=gate), \
                    mock.patch.object(dispatch, "run", wraps=dispatch.run) as run:
                ok, report, _, _ = dispatch.worker_round(7, wt, set(), "fix", "bounce", 4, 60)
            self.assertFalse(ok)
            self.assertTrue(report.startswith("budget exceeded"), report)
            self.assertIn("Mechanical fixups", report)
            self.assertIn("not run", report)
            self.assertFalse(any(call.args[0] == ["repair"] for call in run.call_args_list))
            self.assertEqual((wt / ".factory/gate-report-7.md").read_text(), report)
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "gate-fixup-budget"]
        self.assertEqual(row["reason"], "budget_exceeded")

    def test_fixup_timeout_preserves_the_gate_reservation(self):
        clock = [0]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 20)
            original_run = dispatch.run
            timeouts = []

            def gate(*args, **kwargs):
                clock[0] = 19  # only one second of repair work remains
                return False, "- code: FAIL"

            def run(cmd, *args, **kwargs):
                if cmd == ["repair"]:
                    timeout = kwargs["timeout"]
                    timeouts.append(timeout)
                    (wt / "partial.txt").write_text("unfinished repair\n")
                    clock[0] += timeout
                    raise subprocess.TimeoutExpired(cmd, timeout)
                return original_run(cmd, *args, **kwargs)

            with mock.patch.object(dispatch.cfg, "fixups", [["repair"], ["must-not-run"]]), \
                    mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)), \
                    mock.patch.object(dispatch, "run_gate", side_effect=gate) as check, \
                    mock.patch.object(dispatch, "run", side_effect=run):
                head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
                ok, report, _, candidate = dispatch.worker_round(7, wt, set(), "fix", "bounce", 4, 60)
            self.assertEqual(timeouts, [1])
            self.assertFalse(ok)
            self.assertTrue(report.startswith("budget exceeded"), report)
            self.assertIn("Mechanical fixups", report)
            self.assertEqual(candidate, head)
            self.assertEqual(check.call_count, 1)
            self.assertEqual(clock[0], 20)
        row, = [r for r in lifecycle.read_events(self.events) if r.get("event") == "gate-fixup-budget"]
        self.assertEqual(row["reason"], "budget_exceeded")

    def test_fixups_share_work_cutoff_then_run_the_full_gate(self):
        clock = [0]
        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            self.measure(wt, 20)
            original_run = dispatch.run
            timeouts = []
            gate_starts = []

            def gate(*args, **kwargs):
                self.assertNotIn("timeout", kwargs)
                gate_starts.append(clock[0])
                if len(gate_starts) == 1:
                    clock[0] = 17
                    return False, "- code: FAIL"
                clock[0] = 65  # admitted validation still finishes if the estimate was low
                return True, "- code: PASS"

            def run(cmd, *args, **kwargs):
                if cmd in (["repair-1"], ["repair-2"]):
                    timeouts.append(kwargs["timeout"])
                    clock[0] += 1
                    (wt / "fixed.txt").write_text("fixed\n")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return original_run(cmd, *args, **kwargs)

            with mock.patch.object(dispatch.cfg, "fixups", [["repair-1"], ["repair-2"]]), \
                    mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)), \
                    mock.patch.object(dispatch, "run_gate", side_effect=gate), \
                    mock.patch.object(dispatch, "run", side_effect=run):
                ok, report, _, head = dispatch.worker_round(7, wt, set(), "fix", "bounce", 4, 60)
            self.assertTrue(ok, report)
            self.assertEqual(timeouts, [3, 2])
            self.assertEqual(gate_starts, [0, 19])
            self.assertEqual(self.git("show", f"{head}:fixed.txt").stdout, "fixed\n")
            self.assertIn("Re-ran gate", report)

    def assert_gate_failure_precedes_fixup_budget(self, mode, check="protected-paths"):
        from factory import manage

        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
            logfile = dispatch.LOGS / "7-attempt-1.log"
            detail = f"- {check}: FAIL\n\n" + "offending diff\n" * 6000 + "report-tail\n"
            with mock.patch.object(dispatch.cfg, "fixups", [["must-not-run"]]):
                ok, report, candidate = dispatch.retry_gate_fixups(
                    wt, 7, detail, head, work_deadline=0,
                )
            self.assertFalse(ok)
            failure = (ok, report, logfile, candidate)
            rounds = [(True, "PASS", logfile, head), failure] if mode == "bounce" else [failure]
            issue = dispatch.gh_json(["issue", "view", "7"])
            packet = self.repo / ".factory" / "manager-packet.md"
            packet.write_text("fix it")
            clock = [0]

            def worker_round(*args, **kwargs):
                result = rounds.pop(0)
                if not result[0]:
                    clock[0] = 61  # the failed gate also exhausted the round deadline
                return result

            with mock.patch.object(dispatch, "worker_round", side_effect=worker_round), \
                    mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(dispatch, "log"), \
                    mock.patch.object(dispatch, "push_and_pr", return_value=True) as publish, \
                    mock.patch.object(dispatch, "push_agent") as push, \
                    mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])), \
                    mock.patch.object(dispatch, "pr_gate_and_review", return_value=("REVISE", "fix it", False)), \
                    mock.patch.object(dispatch, "run", wraps=dispatch.run) as run:
                if mode == "manager":
                    manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
                else:
                    dispatch.process_ticket(issue, 1, False, forced=True)
            push.assert_not_called()
            self.assertEqual(publish.call_count, int(mode == "bounce"))
            comments = [
                call.args[0][-1] for call in run.call_args_list
                if call.args[0][:3] == ["gh", "issue", "comment"]
            ]
            self.assertEqual(len(comments), 1)
            self.assertLess(len(comments[0]), 65536, "gate explanation must fit a GitHub comment")
            self.assertNotIn("budget_min / budget_exceeded", comments[0])
            self.assertNotIn("offending diff", comments[0])
            self.assertIn(detail, (wt / ".factory/gate-report-7.md").read_text())
            escalation, = [
                row for row in lifecycle.read_events(self.events) if row.get("event") == "escalate"
            ]
            self.assertEqual(escalation["code"], "protected_paths" if check == "protected-paths" else "gate_failed")
            self.assertNotIn("offending diff", escalation["reason"])
            self.assertIn("report-tail", (dispatch.FACTORY / "escalations/7.md").read_text())
            if check == "protected-paths":
                for text in (comments[0], (dispatch.FACTORY / "escalations/7.md").read_text()):
                    self.assertIn("factory-protected-override", text)
                    self.assertIn("Human-only authority", text)

    def test_attempt_gate_failure_precedes_fixup_budget(self):
        self.assert_gate_failure_precedes_fixup_budget("attempt")

    def test_bounce_gate_failure_precedes_fixup_budget(self):
        self.assert_gate_failure_precedes_fixup_budget("bounce")

    def test_manager_fix_gate_failure_precedes_fixup_budget(self):
        self.assert_gate_failure_precedes_fixup_budget("manager")

    def test_attempt_generic_gate_failure_precedes_fixup_budget(self):
        self.assert_gate_failure_precedes_fixup_budget("attempt", "code")

    def test_bounce_generic_gate_failure_precedes_fixup_budget(self):
        self.assert_gate_failure_precedes_fixup_budget("bounce", "code")

    def test_manager_fix_generic_gate_failure_precedes_fixup_budget(self):
        self.assert_gate_failure_precedes_fixup_budget("manager", "code")

    def assert_runner_failure_precedes_fixup_budget(self, mode):
        from factory import manage

        with chdir(self.repo), mock.patch.dict(os.environ, self.env):
            wt = self.configure_round()
            head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
            logfile = dispatch.LOGS / "7-attempt-1.log"
            original_run = dispatch.run

            def crashed_runner(cmd, **kwargs):
                if cmd[:6] == [sys.executable, "-P", "-E", "-m", "factory", "gate"]:
                    return subprocess.CompletedProcess(cmd, 1, "", "runner crashed\n" * 6000 + "report-tail\n")
                return original_run(cmd, **kwargs)

            with mock.patch.object(dispatch, "run", side_effect=crashed_runner):
                ok, detail = dispatch.run_gate(wt, 7)
            self.assertFalse(ok)
            self.assertTrue(detail.startswith("gate runner did not write "))
            self.assertIn("(exit 1)", detail)
            with mock.patch.object(dispatch.cfg, "fixups", [["must-not-run"]]):
                ok, report, candidate = dispatch.retry_gate_fixups(wt, 7, detail, head, work_deadline=0)
            self.assertTrue(report.startswith("budget exceeded"))
            failure = (ok, report, logfile, candidate)
            rounds = [(True, "PASS", logfile, head), failure] if mode == "bounce" else [failure] * dispatch.MAX_ATTEMPTS
            issue = dispatch.gh_json(["issue", "view", "7"])
            packet = self.repo / ".factory" / "manager-packet.md"
            packet.write_text("fix it")
            with mock.patch.object(dispatch, "worker_round", side_effect=rounds), \
                    mock.patch.object(dispatch.time, "monotonic", return_value=0), \
                    mock.patch.object(dispatch, "push_and_pr", return_value=True) as publish, \
                    mock.patch.object(dispatch, "push_agent") as push, \
                    mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])), \
                    mock.patch.object(dispatch, "pr_gate_and_review", return_value=("REVISE", "fix it", False)), \
                    mock.patch.object(dispatch, "run", wraps=dispatch.run) as run:
                if mode == "manager":
                    manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
                else:
                    dispatch.process_ticket(issue, 1, False, forced=True)
            push.assert_not_called()
            self.assertEqual(publish.call_count, int(mode == "bounce"))
            escalation, = [row for row in lifecycle.read_events(self.events) if row.get("event") == "escalate"]
            self.assertEqual(escalation["code"], "gate_failed")
            self.assertNotIn("budget_min / budget_exceeded", escalation["reason"])
            comments = [
                call.args[0][-1] for call in run.call_args_list
                if call.args[0][:3] == ["gh", "issue", "comment"]
            ]
            self.assertEqual(len(comments), 1)
            self.assertLess(len(comments[0]), 65536)
            self.assertNotIn("budget_min / budget_exceeded", comments[0])
            self.assertIn(detail, (wt / ".factory/gate-report-7.md").read_text())
            self.assertIn("report-tail", (dispatch.FACTORY / "escalations/7.md").read_text())

    def test_attempt_runner_failure_precedes_fixup_budget(self):
        self.assert_runner_failure_precedes_fixup_budget("attempt")

    def test_bounce_runner_failure_precedes_fixup_budget(self):
        self.assert_runner_failure_precedes_fixup_budget("bounce")

    def test_manager_fix_runner_failure_precedes_fixup_budget(self):
        self.assert_runner_failure_precedes_fixup_budget("manager")

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
