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
        self.enterContext(patch.object(onboard, '_dashboard_port_check', return_value=(True, 'available')))
        self.out = self.enterContext(redirect_stdout(io.StringIO()))

    def runner(self, argv, **kwargs):
        # Existing lifecycle assertions record mutations; recovery tests record probes too.
        if argv[:2] == ['launchctl', 'print']:
            return subprocess.CompletedProcess(argv, 0, 'loaded\n', '')
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
                def run(argv, status=status, calls=calls, **kwargs):
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

    def test_retry_bootstrap_after_failure_and_manual_bootout(self):
        self.cfg.install['python'] = None
        self.cfg.triage_service = None
        self.cfg.eval_schedule = False
        path = self.home / 'Library/LaunchAgents' / f'{self.cfg.unit}.plist'
        domain = f'gui/{os.getuid()}'
        loaded = False
        fail_bootstrap = True
        calls = []

        def runner(argv, **kwargs):
            nonlocal loaded, fail_bootstrap
            calls.append(argv)
            action = argv[1]
            status = 0
            if action == 'print':
                status = 0 if loaded else 113
            elif action == 'bootstrap':
                if fail_bootstrap:
                    fail_bootstrap = False
                    status = 5
                else:
                    loaded = True
            return subprocess.CompletedProcess(argv, status, '', 'failed' if status else '')

        with patch.object(onboard.sys, 'platform', 'darwin'), patch.object(onboard, 'sh', side_effect=runner):
            with self.assertRaises(config.ConfigError):
                onboard.install(['--no-dashboard'])
            self.assertTrue(path.exists())
            calls.clear()
            self.assertEqual(onboard.install(['--no-dashboard']), 0)
            self.assertTrue(loaded, 'retry must load the plist left by a failed bootstrap')
            self.assertEqual(calls, [['launchctl', 'print', f'{domain}/{self.cfg.unit}'],
                                     ['launchctl', 'bootstrap', domain, str(path)]])
            calls.clear()
            self.assertEqual(onboard.install(['--no-dashboard']), 0)
            self.assertEqual(calls, [['launchctl', 'print', f'{domain}/{self.cfg.unit}']])
            loaded = False  # District/manual bootout, with the identical plist still present.
            self.assertEqual(onboard.install(['--no-dashboard']), 0)
            self.assertTrue(loaded)

    def test_changed_and_dropped_unloaded_agents_ignore_only_missing_service(self):
        self.cfg.install['python'] = None
        directory = self.home / 'Library/LaunchAgents'
        with patch.object(onboard.sys, 'platform', 'darwin'):
            self.assertEqual(onboard.install(['--dashboard']), 0)
            old = (directory / f'{self.cfg.unit}.plist').read_text()

            def runner(argv, **kwargs):
                self.calls.append(argv)
                return subprocess.CompletedProcess(argv, 3 if argv[1] == 'bootout' else 0,
                                                   '', 'No such process')

            self.calls.clear()
            with patch.object(onboard, 'sh', side_effect=runner):
                self.assertEqual(onboard.install(['--dashboard', '--every', '3min']), 0)
                self.assertNotEqual((directory / f'{self.cfg.unit}.plist').read_text(), old)
                self.cfg.triage_service = None
                self.cfg.eval_schedule = False
                self.assertEqual(onboard.install(['--no-dashboard', '--every', '3min']), 0)
            for suffix in ('-dashboard', '-triage-probe', '-eval'):
                self.assertFalse((directory / f'{self.cfg.unit}{suffix}.plist').exists())
            self.assertFalse(any(call[0] == 'systemctl' for call in self.calls))
            before = (directory / f'{self.cfg.unit}.plist').read_text()
            with patch.object(onboard, 'sh', return_value=subprocess.CompletedProcess([], 5, '', 'Input/output error')):
                with self.assertRaisesRegex(config.ConfigError, 'Input/output error'):
                    onboard.install(['--no-dashboard', '--every', '4min'])
            self.assertEqual((directory / f'{self.cfg.unit}.plist').read_text(), before)

    def test_sigterm_of_pass_wrapper_runs_watch_with_signal_result(self):
        import json
        import signal
        import sys
        import textwrap

        # Run the wrapper and a real sleeping child, not a child returning -SIGTERM.
        script = textwrap.dedent("""
            import json, os, subprocess, sys
            from unittest.mock import patch
            from factory import onboard
            run = subprocess.run
            phase = sys.argv[1]
            def child(argv, **kwargs):
                command = argv[4]
                if command == phase:
                    return run([sys.executable, '-u', '-c',
                                'import time; print("ready", flush=True); time.sleep(60)'])
                if command == 'watch':
                    print(json.dumps({k: kwargs['env'][k] for k in
                          ('SERVICE_RESULT', 'EXIT_CODE', 'EXIT_STATUS')}), flush=True)
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(onboard.subprocess, 'run', side_effect=child):
                sys.exit(onboard.pass_main([]))
        """)
        for phase in ('triage', 'dispatch'):
            with self.subTest(phase=phase):
                proc = subprocess.Popen([sys.executable, '-u', '-c', script, phase],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, start_new_session=True)
                try:
                    self.assertEqual(proc.stdout.readline().strip(), 'ready')
                    proc.send_signal(signal.SIGTERM)
                    try:
                        stdout, stderr = proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.fail('SIGTERM did not stop the wrapper and child and run watch')
                    self.assertEqual(proc.returncode, 143, stderr)
                    self.assertEqual(json.loads(stdout), {'SERVICE_RESULT': 'signal',
                                                         'EXIT_CODE': 'killed', 'EXIT_STATUS': '15'})
                finally:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.communicate()


if __name__ == '__main__':
    unittest.main()
