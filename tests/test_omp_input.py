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
