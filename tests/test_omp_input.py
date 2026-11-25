"""Oversized OMP review inputs cross the real exec boundary without using argv."""
import errno
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle

PROBE = r'''
import json, os, stat, sys
from pathlib import Path
args = sys.argv[1:]
prompt = next((arg for arg in args if len(arg.encode()) > 128 * 1024), "")
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
answer = '```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE'
if "--mode" in args:
    print(json.dumps({"type": "message_end", "message": {"role": "assistant", "content": answer}}))
else:
    print(answer)
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
        self.enterContext(mock.patch.dict(os.environ, {
            "FACTORY_PROBE": str(self.probe), lifecycle.CONTEXT_ENV: "",
        }))
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

    def test_second_omp_compatible_reviewer_uses_file_stdin(self):
        alias = self.tmp / "reviewer-alias"
        self.omp.rename(alias)
        self.cfg.reviewer_json_mode = True
        reviewer = [str(alias), "--model", "second/model", "prefix {prompt} suffix"]
        result = dispatch.run_reviewer(self.repo, self.prompt, self.head, reviewer=reviewer)
        self.assertTrue(result["accepted"], result["findings"])
        seen = json.loads(self.probe.read_text())
        self.assertEqual(seen["prompt"], f"prefix {self.prompt} suffix")
        self.assertEqual(seen["diff"], self.diff)
        self.assertEqual(seen["stdin_mode"], 0o600)

    def test_external_pr_reviews_full_multimegabyte_inline_diff(self):
        real_run = dispatch.run
        published = []

        def run(command, **kwargs):
            if command[0] == "gh":
                if "/compare/" in " ".join(command):
                    return subprocess.CompletedProcess(command, 0, self.diff, "")
                published.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")
            return real_run(command, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run):
            dispatch.review_external_pr(911, self.base, self.head)
        seen = json.loads(self.probe.read_text())
        supplied = seen["prompt"].split("--- BEGIN UNTRUSTED DIFF ---\n", 1)[1]
        self.assertEqual(supplied, self.diff + "\n--- END UNTRUSTED DIFF ---")
        self.assertLess(sum(len(a.encode()) for a in seen["argv"]), 4096)
        self.assertTrue(published)
        exits = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("kind") == "exit"]
        self.assertEqual(exits[-1]["outcome"], "approved")

    def test_prompt_file_io_failures_preserve_errno_and_fail_closed(self):
        real_temporary = tempfile.NamedTemporaryFile
        real_open = open
        for operation, code in (("create", errno.ENOSPC), ("write", errno.ENOSPC),
                                ("read", errno.EACCES)):
            paths = []
            failure = OSError(code, os.strerror(code), "reviewer-prompt")

            @contextmanager
            def temporary(*args, operation=operation, failure=failure, paths=paths, **kwargs):
                if operation == "create":
                    raise failure
                with real_temporary(*args, **kwargs) as file:
                    paths.append(Path(file.name))
                    if operation == "write":
                        with mock.patch.object(file, "write", side_effect=failure):
                            yield file
                    else:
                        yield file

            def read(path, *args, operation=operation, paths=paths, failure=failure, **kwargs):
                if operation == "read" and Path(path) in paths:
                    raise failure
                return real_open(path, *args, **kwargs)

            with self.subTest(operation=operation), \
                    mock.patch.object(dispatch.tempfile, "NamedTemporaryFile", temporary), \
                    mock.patch("builtins.open", side_effect=read):
                result = dispatch.run_reviewer(self.repo, self.prompt, self.head)
            self.assertFalse(result["accepted"])
            self.assertIsNone(result["verdict"])
            self.assertEqual(result["reason"], "reviewer_unavailable")
            self.assertIn(f"[Errno {code}]", result["findings"])
            self.assertIn(os.strerror(code), result["failure_reason"])
            self.assertEqual(result["attempts"], 3)
            self.assertFalse(self.probe.exists(), "reviewer launched despite prompt I/O failure")
            self.assertTrue(all(not p.exists() for p in paths))

    def test_prompt_file_removed_after_timeout_or_launch_error(self):
        real_run = dispatch.run
        for failure in (subprocess.TimeoutExpired([str(self.omp)], 1),
                        OSError(errno.ENOENT, os.strerror(errno.ENOENT))):
            paths = []

            def run(command, failure=failure, paths=paths, **kwargs):
                if command[0] == str(self.omp):
                    stdin = kwargs["stdin"]
                    paths.append(Path(stdin.name))
                    self.assertEqual(stdin.read().decode("utf-8"), self.prompt)
                    raise failure
                return real_run(command, **kwargs)

            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(dispatch, "run", side_effect=run):
                result = dispatch.run_reviewer(self.repo, self.prompt, self.head)
            self.assertEqual(result["reason"], "reviewer_unavailable")
            self.assertIsNone(result["verdict"])
            self.assertEqual(len(paths), 3)
            self.assertTrue(all(not p.exists() for p in paths))

    def test_calibration_launch_preserves_oversized_prompt(self):
        experiment = Path(__file__).resolve().parents[1] / "experiments" / "reviewer-calibration"
        real_run = subprocess.run
        paths = []

        def worktree(_case, destination):
            real_run(["git", "clone", "-q", "--shared", str(self.repo), str(destination)], check=True)
            real_run(["git", "-C", str(destination), "update-ref",
                      "refs/remotes/origin/main", self.base], check=True)

        def run(command, **kwargs):
            if command[0] == "omp":
                paths.append(Path(kwargs["stdin"].name))
            return real_run(command, **kwargs)

        with mock.patch.object(sys, "path", [str(experiment), *sys.path]), \
                mock.patch.dict(sys.modules), \
                mock.patch.dict(os.environ, {"PATH": str(self.tmp) + os.pathsep + os.environ["PATH"]}):
            spec = importlib.util.spec_from_file_location("calibration_transport", experiment / "run_tools.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with mock.patch.object(module, "worktree", side_effect=worktree), \
                    mock.patch.object(module, "capture", return_value=self.prompt), \
                    mock.patch.object(module, "contract_commit", return_value=self.head), \
                    mock.patch.object(module.subprocess, "run", side_effect=run):
                result = module.review("current", "fixture", 1, "fixture/model")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("VERDICT: APPROVE", result["output"])
        seen = json.loads(self.probe.read_text())
        self.assertEqual(seen["prompt"], self.prompt)
        self.assertEqual(seen["diff"], self.diff)
        self.assertEqual(seen["stdin_mode"], 0o600)
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())


if __name__ == "__main__":
    unittest.main()
