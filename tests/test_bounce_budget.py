"""Review-bounce budget cuts retain PR evidence without inventing gate failures."""
from __future__ import annotations

import json
import os
import sys
import unittest
from contextlib import chdir
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, stats
from tests import test_dispatch_lifecycle as fixtures


class BounceBudgetTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config
    commit_config = fixtures.LocalCLI.commit_config

    def exercise_bounce(self, killed):
        self.write_config(extra=(
            '\n[[gate.check]]\nname = "pr-proof"\nwhen = "pr"\nrun = '
            + json.dumps([sys.executable, '-c', 'print("bounce-proof-evidence"); raise SystemExit(1)'])
            + '\n'
        ))
        self.commit_config()
        clock = [0]
        workers = []
        gates = []
        original_worker = dispatch.run_worker
        original_gate = dispatch.run_gate

        def worker(*args, **kwargs):
            workers.append(args)
            if len(workers) == 1:
                return original_worker(*args, **kwargs)
            if killed:
                clock[0] = 120
                return 143, 'budget_min'
            return 0, None

        def gate(wt, ticket, **kwargs):
            gates.append(ticket)
            if len(workers) > 1:
                return False, '- code: FAIL\nactual bounce gate failure'
            return original_gate(wt, ticket, **kwargs)

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, 'run_worker', side_effect=worker), \
                mock.patch.object(dispatch, 'run_gate', side_effect=gate), \
                mock.patch.object(dispatch.risk, 'classify', return_value=('Medium', ['medium:fixture'])):
            dispatch.configure(config.load(self.repo))
            dispatch.process_ticket(dispatch.gh_json(['issue', 'view', '7']), 1, False, forced=True)
        events = lifecycle.read_events(self.events)
        escalation, = [row for row in events if row.get('event') == 'escalate']
        return events, escalation, gates

    def test_failed_pr_proof_then_budget_cut_is_budget(self):
        events, escalation, gates = self.exercise_bounce(True)
        self.assertEqual(escalation['code'], 'budget')
        self.assertIn('worker killed by budget_min before gate after review bounce 1', escalation['reason'])
        self.assertIn('last attempt-gate result: PASS', escalation['reason'])
        self.assertIn('last PR-tier result: FAIL', escalation['reason'])
        self.assertIn('bounce-proof-evidence', Path(escalation['packet']).read_text())
        self.assertEqual(gates, [7, '7-pr'])
        attempts = [row for row in events if row.get('event') == 'attempt']
        self.assertIsNone(attempts[-1]['gate'])
        self.assertEqual(attempts[-1]['timeout'], 'budget_min')
        now = datetime.now(UTC)
        self.assertEqual(stats.weekly_metrics(events, now, now, None)['escalations_by_code'], {'budget': 1})

    def test_real_bounce_gate_failure_is_gate_failed(self):
        _, escalation, gates = self.exercise_bounce(False)
        self.assertEqual(escalation['code'], 'gate_failed')
        self.assertEqual(gates, [7, '7-pr', 7])

    def test_reviewer_routed_pr_failure_reports_actual_result(self):
        original = dispatch.pr_gate_and_review

        def reviewer_routed(*args, **kwargs):
            verdict, findings, _ = original(*args, **kwargs)
            # High-risk mutation-only failures reach review with pr_failed=False.
            return verdict, findings, False

        with mock.patch.object(dispatch, 'pr_gate_and_review', side_effect=reviewer_routed):
            _, escalation, _ = self.exercise_bounce(True)
        self.assertEqual(escalation['code'], 'budget')
        self.assertIn('last PR-tier result: FAIL', escalation['reason'])
        self.assertIn('bounce-proof-evidence', Path(escalation['packet']).read_text())

    def test_absent_pr_run_is_not_reported_as_pass(self):
        def no_pr_run(*args, **kwargs):
            return 'REVISE', 'Reviewer requests changes without PR-tier checks', False

        with mock.patch.object(dispatch, 'pr_gate_and_review', side_effect=no_pr_run):
            _, escalation, gates = self.exercise_bounce(True)
        self.assertEqual(escalation['code'], 'budget')
        self.assertIn('last PR-tier result: not run', escalation['reason'])
        self.assertEqual(gates, [7])
