"""Slow verification must not consume the worker allowance; total time stays bounded."""
import json
import os
import sys
import time
import unittest
from contextlib import chdir
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as fixtures


class DispatchBudgetTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config
    commit_config = fixtures.LocalCLI.commit_config

    def test_slow_gates_complete_review_rounds_above_old_budget(self):
        self.write_config(extra='\n[[gate.check]]\nname = "mutation"\nwhen = "pr"\nrun = '
                          + json.dumps([sys.executable, "-c", "print('mutation complete')"]) + '\n')
        self.commit_config()
        clock = [0.0]
        original_run = dispatch._run

        def measured_run(cmd, *args, **kwargs):
            result = original_run(cmd, *args, **kwargs)
            if "gate" in cmd and "factory" in cmd:
                clock[0] += 6000 if "--tier" in cmd else 5401
            elif str(self.reviewer) in cmd:
                clock[0] += 600
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "_run", side_effect=measured_run), \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["fixture"])):
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 90, False, forced=True)
        events = lifecycle.read_events(self.events)
        self.assertFalse([row for row in events if row.get("event") == "escalate"])
        self.assertTrue(any(row.get("event") == "approved" for row in events))
        self.assertEqual(json.loads(self.state.read_text())["reviews"], 2)
        budgets = [row for row in events if row.get("event") == "dispatch-budget"]
        self.assertEqual({row["attempt"] for row in budgets}, {1, 3})
        final = budgets[-1]
        self.assertEqual(final["floor_seconds"], 5400)
        self.assertGreater(final["effective_seconds"], 5400)
        self.assertGreaterEqual(final["measured_seconds"]["attempt"], 10802)
        self.assertGreaterEqual(final["measured_seconds"]["pr"], 12000)
        self.assertGreaterEqual(final["measured_seconds"]["review"], 1200)

    def hung_phase(self, phase):
        pidfile = self.base / "hung.pid"
        sleeper = (
            "import os, time\nfrom pathlib import Path\n"
            f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        check = [sys.executable, "-c", sleeper]
        if phase == "review":
            self.reviewer.write_text(sleeper)
        if phase == "worker":
            self.worker.write_text(sleeper)
        extra = (
            '\n[[gate.check]]\nname = "mutation"\nwhen = "pr"\nrun = '
            + json.dumps(check) + "\n"
        ) if phase == "pr" else ""
        self.write_config(check=check if phase == "attempt" else None, extra=extra)
        if phase in {"attempt", "pr"}:
            self.commit_config()
        original_run, original_worker = dispatch._run, dispatch.run_worker
        source = str(Path(__file__).resolve().parents[1])

        def shorten_cap():
            budget = dispatch._BUDGET.get()
            budget.cap_seconds = budget.floor_seconds
            budget.started = time.monotonic() - budget.cap_seconds + 0.5

        def bounded_run(cmd, *args, **kwargs):
            is_gate = "gate" in cmd and "factory" in cmd
            if (is_gate and phase == ("pr" if "--tier" in cmd else "attempt")
                    or phase == "review" and str(self.reviewer) in cmd):
                shorten_cap()
            if is_gate:
                # The installed runner intentionally ignores PYTHONPATH; exercise this revision.
                cmd = [sys.executable, "-c",
                       (f"import sys; sys.path.insert(0, {source!r}); "
                        "from factory.cli import main; sys.exit(main())"), *cmd[5:]]
            return original_run(cmd, *args, **kwargs)

        def bounded_worker(*args, **kwargs):
            if phase == "worker":
                shorten_cap()
            return original_worker(*args, **kwargs)

        started = time.monotonic()
        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "_run", side_effect=bounded_run), \
                mock.patch.object(dispatch, "run_worker", side_effect=bounded_worker), \
                mock.patch.object(dispatch, "WATCH_POLL", 0.01), \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["fixture"])):
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 90, False, forced=True)
        self.assertLess(time.monotonic() - started, 10)
        events = lifecycle.read_events(self.events)
        escalations = [row for row in events if row.get("event") == "escalate"]
        self.assertEqual([row["code"] for row in escalations], ["budget"])
        self.assertIn("hard wall-clock cap", escalations[0]["reason"])
        self.assertFalse(any(row.get("event") == "approved" for row in events))
        self.assertTrue(pidfile.exists(), "the hanging subprocess must actually start")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_hung_attempt_gate_stops_at_cap(self):
        self.hung_phase("attempt")

    def test_hung_mutation_gate_stops_at_cap(self):
        self.hung_phase("pr")

    def test_hung_review_stops_at_cap(self):
        self.hung_phase("review")

    def test_hung_worker_stops_at_cap(self):
        self.hung_phase("worker")
