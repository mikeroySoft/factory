"""Slow verification must not consume the worker allowance; total time stays bounded."""
from contextlib import chdir
import json
import os
import sys
import unittest
from unittest import mock

import test_dispatch_lifecycle as fixtures
from factory import config, dispatch, lifecycle


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
