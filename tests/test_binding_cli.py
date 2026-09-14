"""Bound execution across a live worker and a second human-rebaselined slice.

All GitHub operations hit a deny-by-default local adapter; git has a local origin.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import binding, config, dispatch, lifecycle

ROOT = Path(__file__).resolve().parents[1]
GH = '''#!/usr/bin/python
import json, os, sys
from pathlib import Path
p = Path(os.environ['BINDING_STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
with open(os.environ['BINDING_CALLS'], 'a') as out:
    out.write(json.dumps(a) + '\\n')
def flag(k):
    return a[a.index(k)+1] if k in a else None
if a[:2] == ['issue', 'view']:
    print(json.dumps(s['tickets'][a[2]]))
elif a[:2] == ['issue', 'list']:
    print(json.dumps([i for i in s['tickets'].values() if flag('--label') in [v['name'] for v in i['labels']]]))
elif a[:2] == ['pr', 'list']:
    print('[]')
elif a[:2] == ['pr', 'view']:
    sys.exit(1)
elif a[0] == 'api' and '--method' in a and flag('--method') == 'GET':
    if a[-1] != 'repos/acme/widgets/issues/50':
        sys.exit('unexpected GET: ' + repr(a))
    if s.get('unavailable'):
        sys.stderr.write('HTTP 404')
        sys.exit(1)
    print(json.dumps(s['initiative']))
elif a[0] == 'api' and '/dependencies/blocked_by' in a[1]:
    print('[]')
elif a[:2] == ['issue', 'edit']:
    i = s['tickets'][a[2]]
    if '--add-assignee' in a:
        i['assignees'] = [{'login': 'runner'}]
    if '--remove-assignee' in a:
        i['assignees'] = []
    names = [v['name'] for v in i['labels']]
    if flag('--remove-label') in names:
        names.remove(flag('--remove-label'))
    if flag('--add-label') and flag('--add-label') not in names:
        names.append(flag('--add-label'))
    i['labels'] = [{'name': n} for n in names]
    p.write_text(json.dumps(s))
elif a[:2] == ['issue', 'comment']:
    print('https://github.com/acme/widgets/issues/' + a[2] + '#issuecomment-100')
else:
    sys.exit('unexpected external operation: ' + repr(a))
'''
WORKER = '''import json, os, subprocess, sys
from pathlib import Path
prompt = Path(sys.argv[1])
root = Path(os.environ['BINDING_REPO'])
p = Path(os.environ['BINDING_STATE'])
s = json.loads(p.read_text())
n = prompt.parent.name.removeprefix('wt-')
copy = root.parent / ('prompt-' + n + '.txt')
if not copy.exists():
    copy.write_text(prompt.read_text())
else:
    assert copy.read_text().rstrip() == prompt.read_text().split('## Previous gate report')[0].rstrip()
if n == '7' and not s.get('edited'):
    s['initiative']['body'] = s['initiative']['body'].replace('old suffix', 'new suffix')
    s['tickets']['7']['body'] = 'Unreviewed replacement scope must never enter the worker prompt.'
    s['edited'] = True
    p.write_text(json.dumps(s))
    result = subprocess.run([sys.executable, '-m', 'factory', 'plan', 'drift', '7'], cwd=root, text=True, capture_output=True)
    (root.parent / 'active-drift.json').write_text(result.stdout)
    assert result.returncode == 0, result.stderr
print('isolated worker exercised ticket #' + n)
'''


class BindingCli(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        (self.bin / 'gh').write_text(GH)
        (self.bin / 'gh').chmod(0o755)
        self.worker = self.base / 'worker.py'
        self.worker.write_text(WORKER)
        self.state_path = self.base / 'state.json'
        self.calls = self.base / 'calls.jsonl'
        self.env = {**os.environ, 'PATH': str(self.bin) + ':/usr/bin:/bin',
                    'PYTHONPATH': str(ROOT), 'HOME': str(self.base / 'home'),
                    'XDG_CONFIG_HOME': str(self.base / 'xdg'), 'GH_TOKEN': '', 'GITHUB_TOKEN': '',
                    'BINDING_STATE': str(self.state_path), 'BINDING_CALLS': str(self.calls),
                    'BINDING_REPO': str(self.repo), lifecycle.CONTEXT_ENV: '',
                    'GIT_ALLOW_PROTOCOL': 'file'}
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        settings = ('[repo]\nslug = "acme/widgets"\nmain = "main"\n'
                    '[dispatch]\nmax_attempts = 2\nsignoff = false\n'
                    '[workers]\ndefault = ' + json.dumps([sys.executable, str(self.worker), '{prompt}']) + '\n'
                    '[[gate.check]]\nname = "deliberate-stop"\nrun = ["false"]\n')
        (self.repo / '.factory.toml').write_text(settings)
        (self.repo / '.gitignore').write_text('.factory/\n.factory-prompt.md\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        origin = self.base / 'origin.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(self.repo), str(origin)], env=self.env, check=True)
        self.git('remote', 'add', 'origin', str(origin))
        self.git('fetch', '-q', 'origin')
        self.state = {'tickets': {}, 'initiative': {
            'number': 50, 'body': '**Outcome**\nShip bounded work\n\n**Boundaries**\nNo authority expansion\n\n**Plan**\n'
            + 'x' * 5000 + ' old suffix\n\n**Success evidence**\nObservable result\n',
            'html_url': 'https://github.com/acme/widgets/issues/50',
            'labels': [{'name': 'initiative'}], 'state': 'open'}}
        self.save()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.env, check=True,
                              text=True, capture_output=True).stdout.strip()

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'factory', *args], cwd=self.repo,
                              env=self.env, capture_output=True, text=True, timeout=60)

    def proposed(self):
        result = self.cli('plan', 'baseline', '50')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)['baseline']

    def ticket(self, number, baseline):
        self.state['tickets'][str(number)] = {
            'number': number, 'title': 'Bounded slice', 'state': 'OPEN', 'assignees': [],
            'labels': [{'name': 'ready-for-agent'}], 'comments': [],
            'body': '**Scope**\nHuman-approved slice only.\n\n**Exit gate**\nProve bounded result.\n\n' + binding.render(baseline)}
        self.save()

    def test_active_contract_drift_and_explicit_new_slice(self):
        original = self.proposed()
        self.ticket(7, original)
        first = self.cli('dispatch')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        report = json.loads((self.base / 'active-drift.json').read_text())
        self.assertEqual(report['drift']['status'], 'changed')
        self.assertEqual(report['drift']['changed_sections'], ['Plan'])
        prompt = (self.base / 'prompt-7.txt').read_text()
        self.assertIn('old suffix', prompt)
        self.assertNotIn('new suffix', prompt)
        self.assertNotIn('Unreviewed replacement scope', prompt)
        logs = list((self.repo / '.factory/logs').glob('7-attempt-*.log'))
        self.assertEqual(len(logs), 2)
        for log in logs:
            self.assertIn('isolated worker exercised ticket #7', log.read_text())
        self.state = json.loads(self.state_path.read_text())
        self.state['tickets']['7']['labels'] = []  # Keep this fixture focused on admission, not handoff delivery.
        refreshed = self.proposed()
        self.assertNotEqual(original['sha256'], refreshed['sha256'])
        self.ticket(8, refreshed)
        second = self.cli('dispatch')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn('new suffix', (self.base / 'prompt-8.txt').read_text())
        accepted = [e for e in lifecycle.read_events(self.repo / '.factory/events.jsonl') if e['event'] == 'plan-bound']
        self.assertEqual([(e['ticket'], e['baseline']['sha256']) for e in accepted],
                         [(7, original['sha256']), (8, refreshed['sha256'])])
        self.observations = {'first_dispatch': first.stdout, 'second_dispatch': second.stdout,
                             'original': original['sha256'], 'refreshed': refreshed['sha256'],
                             'active_drift': report['drift']}

    def test_invalid_baseline_refuses_before_claim_even_forced(self):
        baseline = self.proposed()
        self.ticket(7, baseline)
        self.state['tickets']['7']['body'] = self.state['tickets']['7']['body'].replace(baseline['sha256'], '0' * 64)
        self.save()
        result = self.cli('dispatch', '--ticket', '7')
        self.assertIn('refused', result.stdout)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(a[:2] == ['issue', 'edit'] for a in calls))
        self.assertFalse((self.base / 'prompt-7.txt').exists())

    def test_stale_inaccessible_and_incomplete_sources_never_claim(self):
        self.ticket(7, self.proposed())
        original = self.state['initiative']['body']
        for body, unavailable, reason in (
            (original.replace('old suffix', 'changed suffix'), False, 'changed'),
            (original, True, 'unavailable'),
            (None, False, 'incomplete'),
        ):
            with self.subTest(reason=reason):
                self.state['initiative']['body'] = body
                self.state['unavailable'] = unavailable
                self.save()
                result = self.cli('dispatch', '--ticket', '7')
                self.assertIn('refused', result.stdout)
                self.assertIn(reason, result.stdout)
                self.assertFalse((self.base / 'prompt-7.txt').exists())
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(a[:2] == ['issue', 'edit'] for a in calls))

    def test_direct_prompt_cannot_bypass_admission(self):
        baseline = self.proposed()
        self.ticket(7, baseline)
        cfg = config.load(self.repo)
        dispatch.configure(cfg)
        issue = self.state['tickets']['7']
        with mock.patch.object(dispatch, 'gh_json', return_value=issue), \
                mock.patch.object(binding, 'observe', side_effect=binding.BindingError('incomplete-source')):
            with self.assertRaisesRegex(binding.BindingError, 'incomplete-source'):
                dispatch.build_prompt(7, self.repo)
        self.assertFalse((self.repo / '.factory/brief-7.md').exists())
