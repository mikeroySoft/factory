"""A budget-killed bounce is not a failed project gate."""
from __future__ import annotations

import os
import unittest
from contextlib import chdir
from pathlib import Path
from unittest import mock

from factory import config, dispatch, generation, lifecycle
import test_dispatch_lifecycle as fixtures


class BudgetEscalationTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config

    def test_budget_killed_bounce_after_pass_names_budget_in_escalation(self):
        clock = [0]
        calls = []
        original_worker = dispatch.run_worker
        original_run = dispatch.run
        comments = []

        def worker(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return original_worker(*args, **kwargs)
            clock[0] = 120
            return 143, "budget_min"

        def run(cmd, **kwargs):
            if cmd[:3] == ["gh", "issue", "comment"]:
                comments.append(cmd[cmd.index("--body") + 1])
            return original_run(cmd, **kwargs)

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])):
            dispatch.configure(config.load(self.repo))
            issue = dispatch.gh_json(["issue", "view", "7"])
            dispatch.process_ticket(issue, 1, False, forced=True)

        events = lifecycle.read_events(self.events)
        attempts = [row for row in events if row.get("event") == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["PASS", None])
        self.assertEqual(attempts[-1]["worker_exit"], 143)
        self.assertEqual(attempts[-1]["timeout"], "budget_min")
        self.assertEqual(attempts[-1]["reason"], "budget_exceeded")
        escalation, = [row for row in events if row.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "budget")
        for text in (escalation["reason"], Path(escalation["packet"]).read_text(), comments[-1]):
            self.assertIn("budget_min", text)
            self.assertIn("budget_exceeded", text)
            self.assertNotIn("gate failed after review bounce", text)
        # The ordinary owner-requeue rule can consume the classified outcome unchanged.
        self.assertEqual(generation.owner_requeue(events, 7)["generation"], 1)


if __name__ == "__main__":
    unittest.main()
