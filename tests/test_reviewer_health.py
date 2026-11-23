"""Reviewer credential failures are host admission failures, not ticket failures."""
import copy
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config


class ReviewerHealthTest(unittest.TestCase):
    def run_pass(self, code, dry_run=False, recover=False):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            calls = root / 'calls'
            script = root / 'reviewer.py'
            script.write_text(
                f'from pathlib import Path\np = Path({str(calls)!r})\n'
                'with p.open("a") as f: f.write("call\\n")\n' + code
            )
            cfg = Config(root=root, repo='owner/repo')
            cfg.host_name = 'tank'
            cfg.reviewer = [sys.executable, str(script), '--model', 'provider/model', '{prompt}']
            cfg.reviewer_json_mode = False
            cfg.release_auto = False
            stack.enter_context(mock.patch.dict('os.environ', {lifecycle.CONTEXT_ENV: ''}))
            stack.enter_context(mock.patch.object(dispatch, 'pin_root', return_value=[]))
            stack.enter_context(mock.patch.object(dispatch.config, 'load', return_value=cfg))
            for name in ('engine_commit', 'engine_target'):
                stack.enter_context(mock.patch.object(dispatch.config, name, return_value=None))
            stack.enter_context(mock.patch.object(dispatch, 'github_preflight', return_value=None))
            stack.enter_context(mock.patch.object(dispatch, '_sweep_stale_factory_tempdirs'))
            stack.enter_context(mock.patch('factory.results.prune', return_value={'status': 'complete'}))
            tickets = [
                {'number': n, 'state': 'OPEN', 'isDraft': False, 'labels': [{'name': 'needs-review'}],
                 'reviewRequests': [], 'headRefOid': str(n) * 40, 'baseRefOid': 'a' * 40,
                 'headRefName': f'agent/{n}'}
                for n in (7, 8)
            ]
            before = copy.deepcopy(tickets)
            journal = root / '.factory' / 'events.jsonl'
            journal.parent.mkdir(parents=True, exist_ok=True)
            for n in (7, 8):
                lifecycle.append(journal, {'event': 'attempt', 'ticket': n, 'attempt': 2})
                lifecycle.append(journal, {'event': 'review', 'ticket': n, 'round': 1, 'verdict': 'REVISE'})
            prior_history = journal.read_bytes()
            stack.enter_context(mock.patch.object(dispatch, 'gh_json', return_value=tickets))
            stack.enter_context(mock.patch.object(dispatch, 'review_readiness'))
            rounds = []
            intake = stack.enter_context(mock.patch.object(
                dispatch, 'review_external_pr', side_effect=lambda n, *a, **kw: rounds.append(n),
            ))
            for name in ('worker_health_pass', 'land_pass', 'ticket_pass'):
                stack.enter_context(mock.patch.object(dispatch, name))
            stack.enter_context(mock.patch('factory.manage.manage_pass'))
            stack.enter_context(mock.patch('factory.learn.learn_pass'))
            dispatch.main(['--dry-run'] if dry_run else [])
            if recover:
                dispatch.main([])
            self.assertTrue(journal.read_bytes().startswith(prior_history))
            events = lifecycle.read_events(journal)[4:]
            self.assertEqual(tickets, before)
            return calls.read_text().splitlines() if calls.exists() else [], rounds, events, intake.call_count

    def test_auth_missing_blocks_two_reviews_without_state_or_budget_changes(self):
        calls, rounds, events, _ = self.run_pass('import sys\nprint("No API key found for anthropic.", file=sys.stderr)\nsys.exit(1)\n')
        self.assertEqual(calls, ['call'])
        self.assertEqual(rounds, [])
        gates = [e for e in events if e.get('reason') == 'reviewer_auth_missing']
        self.assertEqual(len(gates), 1)
        self.assertIn('tank', str(gates))
        self.assertIn('provider/model', str(gates))
        self.assertFalse(any(e.get('event') in ('review', 'escalate', 'attempt') for e in events))
        self.assertEqual(sum(e.get('wait', {}).get('reason') == 'reviewer_auth_missing' for e in events), 1)
        self.assertFalse(any(e.get('event') == 'review-intake' for e in events))

    def test_success_preserves_review_path(self):
        calls, rounds, _, _ = self.run_pass('print("OK")\n')
        self.assertEqual(calls, ['call'])
        self.assertEqual(rounds, [7, 8])

    def test_unknown_failure_preserves_review_path(self):
        calls, rounds, _, _ = self.run_pass('import sys\nprint("service unavailable")\nsys.exit(1)\n')
        self.assertEqual(calls, ['call'])
        self.assertEqual(rounds, [7, 8])

    def test_dry_run_does_not_probe(self):
        calls, _, _, _ = self.run_pass('raise RuntimeError()\n', dry_run=True)
        self.assertEqual(calls, [])

    def test_timeout_and_launch_failure_leave_admission_open(self):
        for failure in (OSError('missing executable'), subprocess.TimeoutExpired('reviewer', 30)):
            with (
                self.subTest(failure=failure),
                mock.patch.object(dispatch, '_review_command', return_value=(['fake'], False)),
                mock.patch.object(dispatch, '_run_reviewer', side_effect=failure),
            ):
                self.assertFalse(dispatch.reviewer_auth_missing())

    def test_next_pass_recovers_without_changing_heads(self):
        calls, rounds, events, _ = self.run_pass(
            'import sys\n'
            'if len(p.read_text().splitlines()) == 1:\n'
            '    print("No API key found for anthropic.", file=sys.stderr)\n'
            '    sys.exit(1)\n'
            'print("OK")\n',
            recover=True,
        )
        self.assertEqual(calls, ['call', 'call'])
        self.assertEqual(rounds, [7, 8])
        self.assertEqual(sum(e.get('reason') == 'reviewer_auth_missing' for e in events), 1)
