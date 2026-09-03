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
# Never read the operator's real host config; HostConfigTest writes its own here.
XDG = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(XDG)

from agent_factory import config  # noqa: E402


def host_file(text: str) -> None:
    path = XDG / "agent-factory" / "config.toml"
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    elif path.exists():
        path.unlink()


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


def factory(cwd: Path, *argv: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    if path:
        env["PATH"] = f"{path}:{env['PATH']}"
    return subprocess.run(
        [sys.executable, "-m", "agent_factory", *argv], cwd=cwd, capture_output=True, text=True, env=env, check=False,
    )



def stub_bin(tmp: Path, **scripts: str) -> str:
    """Fake executables first on PATH: name -> sh body; each appends its argv to <bin>/<name>.log."""
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        exe = bindir / name
        exe.write_text(f'#!/bin/sh\necho "$@" >> "{bindir / name}.log"\n{body}\n')
        exe.chmod(0o755)
    return str(bindir)

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


class HostConfigTest(unittest.TestCase):
    """`$XDG_CONFIG_HOME/agent-factory/config.toml` layers under the repo file."""

    def tearDown(self) -> None:
        host_file("")

    def test_precedence_and_filter(self) -> None:
        host_file(
            '[defaults.triage]\nurl = "http://h/v1/chat/completions"\nmodel = "d"\n'
            '[defaults.dashboard]\nport = 9000\ntheme = "host.css"\n'
            '[defaults.gate]\nlock = "/tmp/host.lock"\n[[defaults.gate.check]]\nname = "evil"\nrun = ["true"]\n'
            '[defaults.leak_scan]\npattern = ""\n[defaults.repo]\nupstream = "evil"\n'
            '[defaults.install]\nevery = "5min"\ndashboard = true\n[defaults.install.env]\nA = "1"\n'
            '[repo."acme/widgets"]\npath = "/x"\n[repo."acme/widgets".triage]\nmodel = "r"\n'
            '[repo."acme/widgets".dashboard]\nport = 9001\n'
        )
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), '[triage]\nmodel = "f"\n')
            cfg = config.load(repo)
            # defaults < per-repo < repo file
            self.assertEqual((cfg.llm_url, cfg.llm_model, cfg.dashboard_port), ("http://h/v1/chat/completions", "f", 9001))
            self.assertEqual(cfg.lock, Path("/tmp/host.lock"))
            self.assertEqual(cfg.install, {"every": "5min", "dashboard": True, "host": "127.0.0.1", "env": {"A": "1"}})
            # repo-owned keys never come from the host
            self.assertEqual(cfg.checks, [])
            self.assertEqual(cfg.leak_pattern, config.DEFAULT_LEAK_PATTERN)
            self.assertIsNone(cfg.upstream)
            self.assertIsNone(cfg.dashboard_theme)
            self.assertEqual(cfg.raw_repo, {"triage": {"model": "f"}})

    def test_missing_host_file_is_current_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(make_repo(Path(d)))
            self.assertEqual((cfg.llm_url, cfg.dashboard_port, cfg.install), (config.DEFAULT_LLM_URL, 8765, config.DEFAULT_INSTALL))

    def test_repo_table_matched_by_resolved_slug(self) -> None:
        host_file('[repo."other/name".dashboard]\nport = 7\n[repo."acme/widgets".dashboard]\nport = 8\n')
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), '[repo]\nslug = "other/name"\n')
            self.assertEqual(config.load(repo).dashboard_port, 7)

    def test_unknown_keys(self) -> None:
        raw = {"triage": {"mdoel": "x"}, "gate": {"check": [{"name": "a", "run": [], "exclusiv": True}]}, "bogus": {}}
        self.assertEqual(config.unknown_keys(raw), ["triage.mdoel", "gate.check[0].exclusiv", "bogus"])

    def test_install_print_uses_host_defaults_and_env(self) -> None:
        host_file('[defaults.install]\nevery = "5min"\ndashboard = true\n[defaults.install.env]\nUV_EXCLUDE_NEWER = "2026-01-01T00:00:00Z"\n')
        with tempfile.TemporaryDirectory() as d:
            proc = factory(make_repo(Path(d)), "install", "--print")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("OnUnitActiveSec=5min", proc.stdout)
            self.assertIn("Environment=UV_EXCLUDE_NEWER=2026-01-01T00:00:00Z", proc.stdout)
            self.assertIn("# factory-widgets-dashboard.service", proc.stdout)
            self.assertIn("--host 127.0.0.1", proc.stdout)
            (Path(d) / "b").mkdir()
            proc = factory(make_repo(Path(d) / "b"), "install", "--print", "--no-dashboard")
            self.assertNotIn("dashboard.service", proc.stdout)

    def test_init_labels_only_touches_nothing_and_fails_on_gh(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            proc = factory(repo, "init", "--labels-only", path=stub_bin(Path(d), gh="exit 0"))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            calls = (Path(d) / "bin" / "gh.log").read_text().splitlines()
            self.assertEqual(len(calls), len(config.LABELS))
            self.assertTrue(all(c.startswith("label create ") and "--repo acme/widgets" in c for c in calls))
            proc = factory(repo, "init", "--labels-only", path=stub_bin(Path(d), gh="echo nope >&2; exit 1"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("nope", proc.stdout)


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

    def test_learn_writes_lessons_from_events(self) -> None:
        from unittest import mock

        from agent_factory import dispatch, learn, triage

        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            cfg = config.load(repo)
            dispatch.configure(cfg)
            triage.configure(cfg)
            dispatch.record("claimed", ticket=3, title="fix parser")
            dispatch.record("attempt", ticket=3, attempt=1, gate="FAIL", seconds=5, log=str(repo / "nope.log"))
            dispatch.record("escalate", ticket=3, reason="gate failed 3 times")
            dispatch.record("claimed", ticket=4, title="in flight")  # unfinished: excluded
            tickets, ev = learn.evidence(10)
            self.assertEqual(tickets, [3])
            self.assertIn("gate failed 3 times", ev)
            reply = json.dumps({"lessons": ["Run `make test` before the gate."]})
            with mock.patch.object(triage, "call_llm", return_value=reply), \
                 mock.patch.object(config, "load", return_value=cfg):
                self.assertEqual(learn.main([]), 0)
            lessons = (repo / config.LESSONS_NAME).read_text()
            self.assertIn("- Run `make test` before the gate.", lessons)
            # The next worker prompt carries the lessons.
            wt = dispatch.FACTORY / "wt-3"
            wt.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(dispatch, "gh_json", return_value={"title": "t", "body": "b", "comments": []}):
                self.assertIn("## Lessons from previous tickets", dispatch.build_prompt(3, wt))

    def test_cost_pattern_sums_worker_log(self) -> None:
        from agent_factory import dispatch

        toml = "[dispatch]\ncost_pattern = 'Total cost:\\s*\\$([0-9.]+)'\nreview_rounds = 3\n"
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml)
            cfg = config.load(repo)
            self.assertEqual(cfg.review_rounds, 3)
            dispatch.configure(cfg)
            log = Path(d) / "w.log"
            log.write_text("... Total cost: $0.25\nmore\nTotal cost: $1.00\n")
            self.assertEqual(dispatch.log_cost(log), 1.25)


if __name__ == "__main__":
    unittest.main()
