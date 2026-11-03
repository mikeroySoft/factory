"""Native install contracts, executable on both Linux and macOS."""
from __future__ import annotations

import io
import os
import plistlib
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import cli, config, onboard, watch


class LaunchdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.home = Path(self.tmp)
        self.cfg = config.Config(root=self.home / 'widgets', repo='acme/widgets',
                                 triage_service='model.service', eval_schedule=True, eval_profile='prime')
        self.cfg.root.mkdir()
        self.cfg.install = {**config.DEFAULT_INSTALL, 'python': '/opt/factory/bin/python',
                            'env': {'POLICY': '24 hours 50%'},
                            'dispatch_env': {'GH_CONFIG_DIR': '/machine/gh space%',
                                             'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'user.name',
                                             'GIT_CONFIG_VALUE_0': 'Factory Bot 50%'}}
        self.calls = []
        self.enterContext(patch.object(onboard.config, 'load', return_value=self.cfg))
        self.enterContext(patch.object(Path, 'home', return_value=self.home))
        self.enterContext(patch.dict(os.environ, {'PATH': '/opt/bin:/usr/bin'}))
        self.enterContext(patch.object(onboard.shutil, 'which', side_effect=lambda exe: '/bin/' + exe if exe != 'systemctl' else None))
        self.enterContext(patch.object(onboard, 'sh', side_effect=self.runner))
        self.out = self.enterContext(redirect_stdout(io.StringIO()))

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, 'yes\n', '')

    def render(self):
        self.out.seek(0)
        self.out.truncate()
        self.assertEqual(cli.main(['install', '--print', '--dashboard']), 0)
        result = {}
        for chunk in self.out.getvalue().split('# ')[1:]:
            name, xml = chunk.split('\n', 1)
            result[name] = plistlib.loads(xml.encode())
        return result

    def test_macos_print_renders_plists_and_identity_split(self):
        with patch.object(onboard.sys, 'platform', 'darwin'):
            agents = self.render()
        self.assertNotIn('[Unit]', self.out.getvalue())
        self.assertNotIn('ExecStart=', self.out.getvalue())
        self.assertEqual(set(agents), {f'{self.cfg.unit}{suffix}.plist' for suffix in ('', '-dashboard', '-triage-probe', '-eval')})
        for name, agent in agents.items():
            self.assertEqual(agent['Label'], Path(name).stem)
            self.assertEqual(agent['WorkingDirectory'], str(self.cfg.root))
            self.assertEqual(agent['ProgramArguments'][:4], ['/opt/factory/bin/python', '-P', '-m', 'factory'])
            self.assertEqual(agent['EnvironmentVariables']['PATH'], '/opt/bin:/usr/bin')
            self.assertEqual(agent['EnvironmentVariables']['POLICY'], '24 hours 50%')
            for stream in ('Out', 'Error'):
                ending = 'stdout' if stream == 'Out' else 'stderr'
                self.assertEqual(agent[f'Standard{stream}Path'], str(self.cfg.factory / 'logs' / f'{agent["Label"]}.{ending}.log'))
            if name != f'{self.cfg.unit}.plist':
                self.assertTrue(set(agent['EnvironmentVariables']).isdisjoint(self.cfg.install['dispatch_env']))
        agent = agents[f'{self.cfg.unit}.plist']
        self.assertEqual(agent['ProgramArguments'][4:], ['pass'])
        self.assertEqual(agent['StartInterval'], 600)
        self.assertTrue(agent['RunAtLoad'])
        self.assertEqual(agent['EnvironmentVariables'], {'PATH': '/opt/bin:/usr/bin', **self.cfg.install['env'], **self.cfg.install['dispatch_env']})
        dashboard = agents[f'{self.cfg.unit}-dashboard.plist']
        self.assertTrue(dashboard['KeepAlive'])
        self.assertEqual(dashboard['ProgramArguments'][4:], ['dashboard', '--host', '127.0.0.1', '--port', '8765', '--no-open'])
        self.assertEqual(agents[f'{self.cfg.unit}-triage-probe.plist']['StartInterval'], 120)
        self.assertEqual(agents[f'{self.cfg.unit}-eval.plist']['StartCalendarInterval'], {'Weekday': 1, 'Hour': 0, 'Minute': 0})
        self.assertEqual(agents[f'{self.cfg.unit}-eval.plist']['ProgramArguments'][4:], ['eval', '--profile', 'prime', '--scheduled'])
        self.assertEqual(self.calls, [])

    def test_install_reload_noop_and_remove_optional_agents(self):
        # No configured python here: its separate preflight is exercised below.
        self.cfg.install['python'] = None
        domain = f'gui/{os.getuid()}'
        directory = self.home / 'Library/LaunchAgents'
        with patch.object(onboard.sys, 'platform', 'darwin'):
            self.assertEqual(onboard.install(['--dashboard']), 0)
            paths = [directory / f'{self.cfg.unit}{suffix}.plist' for suffix in ('', '-dashboard', '-triage-probe', '-eval')]
            self.assertEqual(self.calls, [['launchctl', 'bootstrap', domain, str(p)] for p in paths])
            self.assertTrue(all(p.exists() for p in paths))
            self.assertTrue((self.cfg.factory / 'logs').is_dir())
            self.calls.clear()
            self.assertEqual(onboard.install(['--dashboard']), 0)
            self.assertEqual(self.calls, [])
            old = paths[0].read_text()
            def ordered(argv, **kwargs):
                if argv[1] == 'bootout':
                    self.assertEqual(paths[0].read_text(), old)
                elif argv[1] == 'bootstrap':
                    self.assertEqual(plistlib.loads(paths[0].read_bytes())['StartInterval'], 180)
                return self.runner(argv, **kwargs)
            with patch.object(onboard, 'sh', side_effect=ordered):
                self.assertEqual(onboard.install(['--dashboard', '--every', '3min']), 0)
            self.assertEqual(self.calls, [['launchctl', 'bootout', f'{domain}/{self.cfg.unit}'], ['launchctl', 'bootstrap', domain, str(paths[0])]])
            self.calls.clear()
            self.cfg.triage_service = None
            self.cfg.eval_schedule = False
            self.assertEqual(onboard.install(['--no-dashboard', '--every', '3min']), 0)
            self.assertEqual(self.calls, [['launchctl', 'bootout', f'{domain}/{p.stem}'] for p in paths[1:]])
            self.assertTrue(paths[0].exists())
            self.assertTrue(all(not p.exists() for p in paths[1:]))

    def test_macos_interval_validation_and_override(self):
        with patch.object(onboard.sys, 'platform', 'darwin'):
            for value, seconds in [('90s', 90), ('1h 30min', 5400), ('2d', 172800), ('60', 60)]:
                with self.subTest(value=value):
                    xml = onboard.launchd_units(self.cfg, value, '127.0.0.1')[f'{self.cfg.unit}.plist']
                    self.assertEqual(plistlib.loads(xml.encode())['StartInterval'], seconds)
            for value in ('0', '-1min', 'infinity', 'nonsense'):
                with self.subTest(value=value), self.assertRaises(config.ConfigError):
                    onboard.install(['--print', f'--every={value}'])
        self.assertEqual(self.calls, [])

    def test_configured_python_probe_does_not_use_systemctl_on_macos(self):
        self.cfg.install['python'] = os.sys.executable
        import json
        result = {'origins': {'factory': '/opt/site-packages/factory/__init__.py'}, 'purelib': '/opt/site-packages', 'version': 'test'}
        with patch.object(onboard.sys, 'platform', 'darwin'), patch.object(onboard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(result), '')) as run:
            ok, detail = onboard.interpreter_probe(self.cfg)
        self.assertTrue(ok, detail)
        self.assertEqual(self.calls, [])
        self.assertEqual(run.call_args.kwargs['env']['POLICY'], '24 hours 50%')
        self.assertEqual(run.call_args.kwargs['env']['PATH'], '/opt/bin:/usr/bin')

    def test_linux_golden_and_systemctl_install(self):
        self.cfg.root = Path('/srv/widgets')
        self.cfg.install['dispatch_env'] = {'GH_CONFIG_DIR': '/machine/gh'}
        with patch.object(onboard.sys, 'platform', 'linux'):
            self.assertEqual(onboard.install(['--print', '--dashboard']), 0)
        self.assertEqual(self.out.getvalue(), (Path(__file__).parent / 'fixtures/install-linux.txt').read_text())
        self.cfg.root = self.home / 'widgets'
        self.cfg.install['python'] = None
        with patch.object(onboard.sys, 'platform', 'linux'), patch.object(onboard, 'unit_dir', return_value=self.home / 'units'), patch.object(onboard.shutil, 'which', return_value='/bin/tool'), patch.object(onboard, '_dashboard_port_check', return_value=(True, 'available')):
            self.assertEqual(onboard.install(['--dashboard']), 0)
        self.assertIn(['systemctl', '--user', 'daemon-reload'], self.calls)
        self.assertIn(['systemctl', '--user', 'enable', '--now', f'{self.cfg.unit}.timer'], self.calls)

    def test_pass_always_watches_dispatch_result_despite_triage_failure(self):
        for status in (0, 7, -15):
            with self.subTest(status=status):
                calls = []
                def run(argv, **kwargs):
                    command = argv[4]
                    calls.append(command)
                    if command == 'watch':
                        self.assertEqual(kwargs['env']['SERVICE_RESULT'], 'success' if status == 0 else 'signal' if status < 0 else 'exit-code')
                        self.assertEqual(kwargs['env']['EXIT_STATUS'], str(abs(status)))
                        return subprocess.CompletedProcess(argv, 9)
                    return subprocess.CompletedProcess(argv, 3 if command == 'triage' else status)
                with patch.object(onboard.subprocess, 'run', side_effect=run):
                    self.assertEqual(cli.main(['pass']), 128 - status if status < 0 else status)
                self.assertEqual(calls, ['triage', 'dispatch', 'watch'])

    def test_macos_watch_reads_failed_pass_without_systemctl(self):
        with patch.object(watch.sys, 'platform', 'darwin'), patch.dict(os.environ, {'SERVICE_RESULT': 'exit-code', 'EXIT_CODE': 'exited', 'EXIT_STATUS': '7'}), patch.object(watch.subprocess, 'run', side_effect=FileNotFoundError):
            failures = watch.failed_units(self.cfg)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], self.cfg.unit)
        self.assertIn('EXIT_STATUS=7', failures[0][1])


if __name__ == '__main__':
    unittest.main()
