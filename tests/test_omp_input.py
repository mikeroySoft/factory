import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch


class ReviewerPromptTransportTest(unittest.TestCase):
    def test_oversized_prompt_and_multimegabyte_diff_reach_reviewer_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = root / 'omp'
            probe.write_text(
                f'#!{sys.executable}\n'
                'import json, os, stat, sys\n'
                'print(json.dumps({"prompt": sys.stdin.read(), '
                '"mode": stat.S_IMODE(os.fstat(0).st_mode), '
                '"argv": sys.argv[1:]}))\n'
            )
            probe.chmod(0o700)
            prompt = 'context π' * 20000 + '\n' + '+full diff\n' * 300000
            result = dispatch._run_reviewer(
                [str(probe), '-p', prompt], prompt=prompt,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            received = json.loads(result.stdout)
            self.assertEqual(received['prompt'], prompt)
            self.assertEqual(received['mode'], 0o600)
            self.assertEqual(received['argv'], ['-p'])

    def test_prompt_creation_errno_propagates(self):
        with (
            mock.patch.object(dispatch.tempfile, 'mkstemp', side_effect=OSError(errno.ENOSPC, 'full')),
            self.assertRaises(OSError) as caught,
        ):
            dispatch._run_reviewer(['omp', '-p', 'review'], prompt='review')
        self.assertEqual(caught.exception.errno, errno.ENOSPC)

    def test_launch_failure_removes_prompt(self):
        created = []
        original = tempfile.mkstemp

        def capture(*args, **kwargs):
            fd, path = original(*args, **kwargs)
            created.append(path)
            return fd, path

        with (
            mock.patch.object(dispatch.tempfile, 'mkstemp', side_effect=capture),
            self.assertRaises(FileNotFoundError),
        ):
            dispatch._run_reviewer(['/missing/omp', '-p', 'review'], prompt='review')
        self.assertTrue(created)
        self.assertTrue(all(not os.path.exists(path) for path in created))

    def _repository(self, root, content):
        import subprocess

        from factory import config

        repo = root / 'repo'
        repo.mkdir()

        def git(*args):
            return subprocess.run(
                ['git', '-C', str(repo), *args], check=True,
                capture_output=True, text=True,
                env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1'},
            ).stdout.strip()

        git('init')
        git('config', 'user.email', 'probe@example.invalid')
        git('config', 'user.name', 'Prompt probe')
        (repo / 'payload.txt').write_text('base\n')
        git('add', 'payload.txt')
        git('commit', '-m', 'base')
        base = git('rev-parse', 'HEAD')
        (repo / 'payload.txt').write_text(content)
        git('commit', '-am', 'payload')
        head = git('rev-parse', 'HEAD')
        diff = subprocess.run(
            ['git', '-C', str(repo), 'diff', '--no-color', '--no-ext-diff',
             '--src-prefix=a/', '--dst-prefix=b/', f'{base}..{head}'],
            check=True, capture_output=True, text=True,
        ).stdout
        self.enterContext(mock.patch.object(
            dispatch, 'cfg', config.Config(
                root=repo, repo='example/probe', reviewer_json_mode=False,
            ), create=True,
        ))
        return repo, base, head, diff

    def test_run_reviewer_transports_oversized_prompt_and_separate_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, base, head, diff = self._repository(root, 'repository π\n' * 200000)
            prompt = 'review context π\n' * 20000
            receipt = root / 'receipt.json'
            probe = root / 'omp'
            probe.write_text(
                f'#!{sys.executable}\n'
                'import json, os, stat, sys\n'
                'from pathlib import Path\n'
                f'Path({str(receipt)!r}).write_text(json.dumps({{'
                '"prompt": sys.stdin.read(), '
                '"diff": Path("review.diff").read_text(), '
                '"mode": stat.S_IMODE(os.fstat(0).st_mode), '
                '"cwd": str(Path.cwd()), "argv": sys.argv[1:]}))\n'
                'print(\'```json\\n{"verdict":"APPROVE","findings":[]}\\n'
                '```\\nVERDICT: APPROVE\')\n'
            )
            probe.chmod(0o700)
            with mock.patch.object(dispatch.cfg, 'reviewer_json_mode', False):
                result = dispatch.run_reviewer(
                    repo, prompt, head, base_ref=base,
                    reviewer=[str(probe), '-p', '{prompt}'],
                )
            self.assertTrue(result['accepted'], result)
            self.assertEqual(result['verdict'], 'APPROVE')
            received = json.loads(receipt.read_text())
            self.assertGreater(len(prompt.encode()), 128 * 1024)
            self.assertGreater(len(diff.encode()), 2 * 1024 * 1024)
            self.assertEqual(received['prompt'], prompt)
            self.assertEqual(received['diff'], diff)
            self.assertEqual(received['argv'], ['-p'])
            self.assertEqual(received['mode'], 0o600)
            self.assertFalse(Path(received['cwd']).exists())

    def test_run_reviewer_prompt_file_failure_is_unavailable_with_errno(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, head, _ = self._repository(Path(tmp), 'changed\n')
            with (
                mock.patch.object(
                    dispatch.tempfile, 'mkstemp',
                    side_effect=OSError(errno.ENOSPC, 'prompt filesystem full'),
                ),
                mock.patch.object(dispatch, '_review_retry_delay', return_value=0),
            ):
                result = dispatch.run_reviewer(
                    repo, 'review', head, base_ref=base,
                    reviewer=[str(Path(tmp) / 'omp'), '-p', '{prompt}'],
                )
            self.assertFalse(result['accepted'])
            self.assertIsNone(result['verdict'])
            self.assertEqual(result['reason'], 'reviewer_unavailable')
            self.assertIn(f'[Errno {errno.ENOSPC}]', result['failure_reason'])
            self.assertIn('prompt filesystem full', result['findings'])

    def test_run_reviewer_transports_prompt_with_noninterned_executable_name(self):
        class NoninternedPath(type(Path())):
            @property
            def name(self):
                return super().name.encode().decode()

        basename = NoninternedPath('/temporary/omp').name
        self.assertEqual(basename, 'omp')
        self.assertIsNot(basename, sys.intern('omp'))
        with mock.patch.object(dispatch, 'Path', NoninternedPath):
            self.test_run_reviewer_transports_oversized_prompt_and_separate_diff()
