"""Oversized OMP review inputs cross the real exec boundary without using argv."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch


PROBE = r'''
import json, os, stat, sys
from pathlib import Path
args = sys.argv[1:]
prompt = args[-1] if args and not args[-1].startswith("--") else ""
if prompt.startswith("@"):
    prompt = Path(prompt[1:]).read_text(encoding="utf-8")
elif not prompt:
    prompt = sys.stdin.read()
cwd = Path.cwd()
Path(os.environ["FACTORY_PROBE"]).write_text(json.dumps({
    "prompt": prompt,
    "diff": (cwd / "review.diff").read_text() if (cwd / "review.diff").exists() else None,
    "argv": args,
    "stdin_mode": stat.S_IMODE(os.fstat(0).st_mode),
    "stdin_regular": stat.S_ISREG(os.fstat(0).st_mode),
}), encoding="utf-8")
print('```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE')
'''


class ReviewerPromptTransportTest(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.tmp = Path(temporary)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.com")
        (self.repo / "source.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        (self.repo / "source.txt").write_text("diff payload λ\n" * 200_000)
        self.git("add", ".")
        self.git("commit", "-qm", "large diff")
        self.head = self.git("rev-parse", "HEAD")
        self.diff = self.git("diff", "--no-color", "--no-ext-diff", "--src-prefix=a/",
                             "--dst-prefix=b/", f"{self.base}...{self.head}", strip=False)
        self.probe = self.tmp / "seen.json"
        self.omp = self.tmp / "omp"
        self.omp.write_text(f"#!{sys.executable}\n" + PROBE)
        self.omp.chmod(0o700)
        self.cfg = config.Config(root=self.repo, repo="test/repo",
                                 reviewer=[str(self.omp), "{prompt}"], reviewer_json_mode=False)
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        self.enterContext(mock.patch.dict(os.environ, {"FACTORY_PROBE": str(self.probe)}))
        self.enterContext(mock.patch.object(dispatch.time, "sleep"))
        self.prompt = "Full reviewer context λ\n" * 10_000
        self.assertGreater(len(self.prompt.encode()), 128 * 1024)
        self.assertGreater(len(self.diff.encode()), 3 * 1024 * 1024)

    def git(self, *args, strip=True):
        out = subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                             capture_output=True, text=True).stdout
        return out.strip() if strip else out

    def test_oversized_prompt_and_multimegabyte_diff_reach_reviewer_intact(self):
        stdin_paths = []
        real_run = dispatch.run

        def run(command, **kwargs):
            if command[0] == str(self.omp) and kwargs.get("stdin") is not None:
                stdin_paths.append(Path(kwargs["stdin"].name))
            return real_run(command, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run):
            result = dispatch.run_reviewer(self.repo, self.prompt, self.head)
        self.assertTrue(result["accepted"], result["findings"])
        seen = json.loads(self.probe.read_text())
        self.assertEqual(seen["prompt"], self.prompt)
        self.assertEqual(seen["diff"], self.diff)
        self.assertLess(sum(len(a.encode()) for a in seen["argv"]), 4096)
        self.assertTrue(seen["stdin_regular"])
        self.assertEqual(seen["stdin_mode"], 0o600)
        self.assertTrue(stdin_paths)
        self.assertTrue(all(not p.exists() for p in stdin_paths))


if __name__ == "__main__":
    unittest.main()
