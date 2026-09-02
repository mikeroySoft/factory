"""Contract checks: config resolution and the gate, against a throwaway git repo.

Run: python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_factory import config  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(tmp: Path, toml: str = "") -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / "README.md").write_text("hello\n")
    if toml:
        (repo / config.CONFIG_NAME).write_text(toml)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    # The gate diffs against origin/<main>; a local ref stands in for the remote.
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def gate(cwd: Path, *args: str) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-m", "agent_factory", "gate", *args],
        cwd=cwd, capture_output=True, text=True, env=env, check=False,
    )
    report = cwd / ".factory" / "gate-report.md"
    return proc.returncode, proc.stdout + proc.stderr, report.read_text() if report.exists() else ""


class ConfigTest(unittest.TestCase):
    def test_defaults_from_origin(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            cfg = config.load(repo)
            self.assertEqual(cfg.repo, "acme/widgets")
            self.assertEqual(cfg.name, "widgets")
            self.assertEqual(cfg.unit, "factory-widgets")
            self.assertIsNone(cfg.upstream)
            self.assertEqual(cfg.checks, [])
            self.assertEqual(cfg.factory, repo / ".factory")
            self.assertEqual(cfg.worker({"chore"}, Path("/p"), Path("/w"))[0], "droid")
            self.assertEqual(cfg.worker(set(), Path("/p"), Path("/w")), ["omp", "-p", "--cwd", "/w", "@/p"])

    def test_overrides_and_worktree_root(self) -> None:
        toml = """
[repo]
slug = "other/name"
upstream = "up"
main = "trunk"
[dispatch]
max_active = 5
signoff = false
[workers]
default = ["agent", "{prompt}"]
[review]
command = ["rev", "--ask", "{prompt}"]
[gate]
timeout = 7
lock = "/tmp/x.lock"
[[gate.check]]
name = "unit"
run = ["true"]
exclusive = true
[leak_scan]
pattern = ""
exclude = ["vendor"]
[triage]
model = "m"
[dashboard]
port = 1
"""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml)
            cfg = config.load(repo)
            self.assertEqual((cfg.repo, cfg.upstream, cfg.main), ("other/name", "up", "trunk"))
            self.assertEqual((cfg.max_active, cfg.signoff, cfg.check_timeout), (5, False, 7))
            self.assertEqual(cfg.review_cmd("hi {x}"), ["rev", "--ask", "hi {x}"])
            self.assertEqual([(c.name, c.exclusive) for c in cfg.checks], [("unit", True)])
            self.assertIsNone(cfg.leak_pattern)
            self.assertEqual((cfg.leak_exclude, cfg.llm_model, cfg.dashboard_port), (["vendor"], "m", 1))
            # A worktree resolves to the main checkout, not itself.
            wt = Path(d) / "wt"
            git(repo, "worktree", "add", "-q", str(wt), "-b", "agent/1")
            self.assertEqual(config.load(wt).root, repo)

    def test_rejects_reserved_check_names(self) -> None:
        toml = '[[gate.check]]\nname = "leak-scan"\nrun = ["true"]\n'
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml)
            with self.assertRaises(SystemExit):
                config.load(repo)


class GateTest(unittest.TestCase):
    def test_pass_fail_skip_and_leak(self) -> None:
        toml = (
            '[[gate.check]]\nname = "ok"\nrun = ["true"]\n'
            '[[gate.check]]\nname = "bad"\nrun = ["sh", "-c", "echo boom; exit 3"]\nexclusive = true\n'
            '[gate]\nlock = "{lock}"\n'
        )
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml.replace("{lock}", str(Path(d) / "lock")))
            code, out, report = gate(repo)
            self.assertEqual(code, 1, out)
            self.assertIn("- conflict-markers: PASS", report)
            self.assertIn("- ok: PASS", report)
            self.assertIn("- bad: FAIL", report)
            self.assertIn("- leak-scan: PASS", report)
            self.assertIn("boom", report)

            code, out, report = gate(repo, "--skip", "bad")
            self.assertEqual(code, 0, out)
            self.assertIn("- bad: SKIP", report)

            # An added line matching the leak pattern fails the scan.
            (repo / "notes.md").write_text("see the CONFIDENTIAL doc\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "leak")
            code, out, report = gate(repo, "--skip", "bad")
            self.assertEqual(code, 1, out)
            self.assertIn("- leak-scan: FAIL", report)
            self.assertIn("CONFIDENTIAL", report)

    def test_timeout_fails_instead_of_hanging(self) -> None:
        toml = '[gate]\ntimeout = 1\n[[gate.check]]\nname = "slow"\nrun = ["sleep", "5"]\n'
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml)
            code, out, report = gate(repo)
            self.assertEqual(code, 1, out)
            self.assertIn("- slow: FAIL", report)
            self.assertIn("timed out", report)

    def test_timeout_kills_the_whole_process_tree(self) -> None:
        # A check that spawns a grandchild which outlives its parent: the gate
        # must FAIL and the grandchild must be gone (it held the GPU lock once).
        toml = (
            '[gate]\ntimeout = 1\n[[gate.check]]\nname = "slow"\n'
            'run = ["sh", "-c", "sleep 30 & echo $! > gc.pid; sleep 30"]\n'
        )
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml)
            code, out, report = gate(repo)
            self.assertEqual(code, 1, out)
            self.assertIn("- slow: FAIL", report)
            self.assertIn("timed out", report)
            pid = int((repo / "gc.pid").read_text())
            import time
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


class DispatchTest(unittest.TestCase):
    def test_prompt_carries_handoff_and_events_append(self) -> None:
        from unittest import mock

        from agent_factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            dispatch.configure(config.load(repo))
            wt = dispatch.FACTORY / "wt-7"
            (wt / ".factory").mkdir(parents=True)
            (wt / ".factory" / "handoff-7.md").write_text("left the migration unverified")
            issue = {"title": "t", "body": "b", "comments": []}
            with mock.patch.object(dispatch, "gh_json", return_value=issue):
                prompt = dispatch.build_prompt(7, wt, "## extra")
            self.assertIn("## Handoff from the previous attempt", prompt)
            self.assertIn("left the migration unverified", prompt)
            self.assertIn("handoff-7.md", prompt)
            self.assertIn("git commit -s", prompt)
            self.assertTrue(prompt.rstrip().endswith("## extra"))

            dispatch.record("claimed", ticket=7)
            dispatch.record("attempt", ticket=7, attempt=1, gate="FAIL")
            rows = [json.loads(line) for line in dispatch.EVENTS.read_text().splitlines()]
            self.assertEqual([r["event"] for r in rows], ["claimed", "attempt"])
            self.assertEqual(rows[1]["gate"], "FAIL")
            self.assertTrue(all("at" in r for r in rows))


if __name__ == "__main__":
    unittest.main()
