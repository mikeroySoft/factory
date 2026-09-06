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

from factory import __version__, config  # noqa: E402


def host_file(text: str) -> None:
    path = XDG / "factory" / "config.toml"
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
        [sys.executable, "-m", "factory", *argv], cwd=cwd, capture_output=True, text=True, env=env, check=False,
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
        [sys.executable, "-m", "factory", "gate", *args],
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
[manager]
command = ["manage", "--prompt", "{prompt}", "--cwd", "{cwd}"]
rounds = 2
review = "all"
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
            self.assertEqual(cfg.manager, ["manage", "--prompt", "{prompt}", "--cwd", "{cwd}"])
            self.assertEqual((cfg.manager_rounds, cfg.manager_review), (2, "all"))
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
    """`$XDG_CONFIG_HOME/factory/config.toml` layers under the repo file."""

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
            self.assertIn("RandomizedDelaySec=90", proc.stdout)
            self.assertLess(proc.stdout.index("ExecStart=-"), proc.stdout.index(" dispatch\n"))
            self.assertIn(" triage\nExecStart=", proc.stdout)
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

    def test_init_writes_ci_workflow_and_doctor_warns_on_placeholder(self) -> None:
        gh = 'case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac\nexit 0'
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            stubs = stub_bin(Path(d), gh=gh, systemctl="echo inactive")
            doctor = lambda: {r["label"]: r for r in json.loads(factory(repo, "doctor", "--json", path=stubs).stdout)["rows"]}  # noqa: E731
            proc = factory(repo, "init", "--no-labels")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            ci = repo / ".github/workflows/ci.yml"
            self.assertIn('run: "true"', ci.read_text())
            self.assertIn("wrote .github/workflows/ci.yml", proc.stdout)
            row = doctor()["github workflow"]
            self.assertEqual((row["status"], "placeholder" in row["detail"]), ("WARN", True))
            ci.write_text(ci.read_text().replace('run: "true"', "run: make test"))
            self.assertEqual(doctor()["github workflow"]["status"], "PASS")
            self.assertIn("kept existing .github/workflows", factory(repo, "init", "--no-labels").stdout)
            ci.unlink()
            row = doctor()["github workflow"]
            self.assertEqual((row["status"], row["detail"].startswith("none")), ("WARN", True))

    def test_doctor_json_reports_drift(self) -> None:
        host_file('[defaults.triage]\nurl = "http://127.0.0.1:1/v1/chat/completions"\n[defaults.leak_scan]\npattern = ""\n[repo."acme/widgets"]\npath = "/x"\n[repo."acme/widgets".dashboard]\nport = 1\ntheme = "no"\n')
        gh = 'case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac\nexit 0'
        toml = '[triage]\nmodel = "m"\n[dashboard]\ntheme = "t.css"\n[gate]\nlock = "/tmp/l"\ntimeout = 5\n[dispatch]\nmax_atempts = 2\n[manager]\nunknown = true\n'
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml)
            (repo / ".github/ISSUE_TEMPLATE").mkdir(parents=True)
            (repo / ".github/ISSUE_TEMPLATE/agent_task.md").write_text("custom\n")
            proc = factory(repo, "doctor", "--json", path=stub_bin(Path(d), gh=gh, systemctl="echo inactive"))
            out = json.loads(proc.stdout)
            self.assertEqual((out["repo"], out["root"], out["version"]), ("acme/widgets", str(repo), __version__))
            rows = {r["label"]: r for r in out["rows"]}
            self.assertEqual(rows[".factory.toml keys"]["status"], "WARN")
            self.assertIn("dispatch.max_atempts", rows[".factory.toml keys"]["detail"])
            self.assertIn("manager.unknown", rows[".factory.toml keys"]["detail"])
            self.assertEqual(rows["host settings committed"]["status"], "WARN")
            self.assertIn("triage, gate.lock", rows["host settings committed"]["detail"])
            self.assertNotIn("dashboard", rows["host settings committed"]["detail"])  # theme is repo-owned
            self.assertEqual(rows["defaults in effect"]["status"], "INFO")
            self.assertIn("dispatch.review_rounds", rows["defaults in effect"]["detail"])
            self.assertNotIn("gate.timeout", rows["defaults in effect"]["detail"])
            self.assertEqual(rows[".github/ISSUE_TEMPLATE/agent_task.md"]["status"], "WARN")
            self.assertEqual(rows["host config"]["status"], "WARN")
            self.assertIn('defaults.leak_scan, repo."acme/widgets".dashboard.theme', rows["host config"]["detail"])
            self.assertEqual(rows["push access to acme/widgets"]["status"], "PASS")
            self.assertEqual(out["ok"], proc.returncode == 0)

    def test_manager_legacy_command_and_invalid_settings(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            host_file('[defaults.manager]\ncommand = \'manage --model fallback/model "{prompt} with spaces"\'\nmodel = "preferred/model"\n')
            cfg = config.load(repo)
            self.assertEqual(cfg.manager, ["manage", "--model", "fallback/model", "{prompt} with spaces"])
            self.assertEqual(cfg.manager_model, "preferred/model")
            for settings in (
                '[manager]\ncommand = 5\n',
                '[manager]\ncommand = [5]\n',
                'manager = 5\n',
                '[manager]\nreview = "typo"\n',
            ):
                with self.subTest(settings=settings):
                    (repo / ".factory.toml").write_text(settings)
                    with self.assertRaises(config.ConfigError):
                        config.load(repo)

    def test_doctor_reports_manager_only_when_configured(self) -> None:
        gh = 'case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac\nexit 0'
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            stubs = stub_bin(Path(d), gh=gh, systemctl="echo inactive", manage="exit 0")
            rows = lambda: {r["label"]: r for r in json.loads(factory(repo, "doctor", "--json", path=stubs).stdout)["rows"]}  # noqa: E731
            self.assertNotIn("manager: manage", rows())
            (repo / ".factory.toml").write_text('[manager]\ncommand = ["manage", "{prompt}"]\n')
            self.assertEqual(rows()["manager: manage"]["status"], "PASS")


class DashboardTest(unittest.TestCase):
    def test_metrics_from_synthetic_tickets(self) -> None:
        from factory import dashboard

        dashboard.MAX_ATTEMPTS = 3
        att = lambda *ns: [{"attempt": n} for n in ns]  # noqa: E731
        tickets = [
            {"pr": {"number": 1}, "attempts": att(1), "events": []},  # first-gate pass
            {"pr": {"number": 2}, "attempts": att(1, 2, 4), "events": [{"kind": "escalated"}]},  # 2 gate rounds + review bounce
            {"pr": None, "attempts": att(1, 2, 3), "events": [{"kind": "escalated"}, {"kind": "comment"}]},
            {"pr": None, "attempts": [], "events": []},
        ]
        m = dashboard.metrics(tickets)
        self.assertEqual(m, {"first_pass": 0.5, "bounce_rate": 0.5, "escalations": 2, "med_attempts": 2})
        self.assertEqual(dashboard.metrics([]), {"first_pass": None, "bounce_rate": None, "escalations": 0, "med_attempts": None})

    def test_consecutive_failures_from_journal(self) -> None:
        from factory import dashboard

        def entry(msg: str, ident: str = "systemd") -> str:
            return json.dumps({"MESSAGE": msg, "SYSLOG_IDENTIFIER": ident, "__REALTIME_TIMESTAMP": "1700000000000000"})

        unit = "factory-widgets.service"
        seq = [
            ("Starting factory dispatcher...", "systemd"), ("Finished factory dispatcher.", "systemd"),
            ("Starting factory dispatcher...", "systemd"), ("Failed to start factory dispatcher.", "systemd"),
            ("Starting factory dispatcher...", "systemd"), ("Traceback", "python"), (f"{unit}: Failed with result 'exit-code'.", "systemd"),
            ("Starting factory dispatcher...", "systemd"), ("Failed to start factory dispatcher.", "systemd"),
            ("Starting factory dispatcher...", "systemd"),  # still running: not counted either way
        ]
        runs = dashboard.parse_journal("\n".join(entry(m, i) for m, i in seq) + "\nnot json\n")
        self.assertEqual([r["result"] for r in runs], ["done", "failed", "failed", "failed", "running"])
        self.assertEqual(runs[2]["lines"], ["Traceback"])
        self.assertEqual(dashboard.consecutive_failures(runs), 3)
        self.assertEqual(dashboard.consecutive_failures(runs[:1]), 0)
        self.assertEqual(dashboard.consecutive_failures([]), 0)


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


class ManageTest(unittest.TestCase):
    def setUp(self) -> None:
        host_file("")

    def scenario(self, round_number: int = 1, activity: list | None = None) -> tuple:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = make_repo(root, '[manager]\ncommand = ["printf", "DECISION: RETRY\\nUse the existing helper"]\n')
        state = repo / ".factory"
        (state / "escalations").mkdir(parents=True)
        packet = state / "escalations/7.md"
        packet.write_text("gate failed")
        event = {"event": "escalate", "ticket": 7, "at": "2026-01-01T00:00:00Z",
                 "round": round_number, "packet": str(packet)}
        (state / "events.jsonl").write_text(json.dumps(event) + "\n")
        timeline = json.dumps(activity or [])
        stubs = stub_bin(root, gh=f'''
case "$1 $2" in
  "issue list") echo '[{{"number":7,"title":"Fix gate","body":"Original body","labels":[{{"name":"ready-for-human"}}]}}]';;
  "api repos/acme/widgets/issues/7/timeline") echo '{timeline}';;
  "issue create") echo "https://github.com/acme/widgets/issues/8";;
  "issue comment"|"issue edit")
    python3 -c 'import json; from pathlib import Path; assert json.loads(Path("{state}/events.jsonl").read_text().splitlines()[-1])["event"] == "manage"' || exit 1;;
esac
''')
        return repo, stubs, packet

    def test_manage_retry_records_before_comment_and_relabels(self) -> None:
        repo, stubs, packet = self.scenario()
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (Path(stubs) / "gh.log").read_text()
        self.assertIn("Factory manager: Use the existing helper", calls)
        self.assertIn("--remove-label ready-for-human --add-label ready-for-agent", calls)
        event = json.loads((repo / ".factory/events.jsonl").read_text().splitlines()[-1])
        self.assertEqual((event["event"], event["decision"], event["round"], event["packet"]),
                         ("manage", "RETRY", 1, str(packet)))
        before = calls
        self.assertEqual(factory(repo, "manage", path=stubs).returncode, 0)
        self.assertNotIn("issue comment", (Path(stubs) / "gh.log").read_text()[len(before):])

    def test_manage_skips_second_escalation_when_rounds_exhausted(self) -> None:
        repo, stubs, _ = self.scenario(round_number=2)
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue edit", (Path(stubs) / "gh.log").read_text())
        self.assertEqual(len((repo / ".factory/events.jsonl").read_text().splitlines()), 1)

    def test_manage_skips_human_comment_after_escalation(self) -> None:
        repo, stubs, _ = self.scenario(activity=[{
            "event": "commented", "created_at": "2026-01-01T00:00:01Z",
            "actor": {"login": "maintainer", "type": "User"}, "body": "I will handle this",
        }])
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue comment", (Path(stubs) / "gh.log").read_text())
        self.assertEqual(len((repo / ".factory/events.jsonl").read_text().splitlines()), 1)

    def test_manage_applies_closed_menu_and_rejects_unknown_route(self) -> None:
        cases = [
            ("REWRITE", "Replacement acceptance criteria", "issue edit 7 --repo acme/widgets --body Replacement acceptance criteria"),
            ("SPLIT", '[{"title":"Child","body":"Child acceptance criteria","blocked_by":[]}]', "Blocked by: #8"),
            ("ROUTE", '{"add":["chore"],"remove":[],"guidance":"Mechanical work"}', "--add-label chore"),
            ("HUMAN", "Requires a maintainer decision", "Factory manager: Requires a maintainer decision"),
            ("ROUTE", '{"add":["factory-approved"]}', "Factory manager: Unparseable manager output:"),
        ]
        for decision, body, expected in cases:
            with self.subTest(decision=decision, body=body):
                repo, stubs, _ = self.scenario()
                command = ["printf", "%s", f"DECISION: {decision}\n{body}"]
                (repo / config.CONFIG_NAME).write_text("[manager]\ncommand = " + json.dumps(command) + "\n")
                result = factory(repo, "manage", path=stubs)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = (Path(stubs) / "gh.log").read_text()
                self.assertIn(expected, calls)
                if decision == "REWRITE":
                    self.assertLess(calls.index("Previous body:\n\nOriginal body"), calls.index("--body Replacement"))
                if decision in {"HUMAN", "SPLIT"} or "factory-approved" in body:
                    self.assertNotIn("--add-label ready-for-agent", calls)
                if "factory-approved" in body:
                    self.assertNotIn("--add-label factory-approved", calls)

    def test_manage_skips_human_label_change(self) -> None:
        repo, stubs, _ = self.scenario(activity=[{
            "event": "labeled", "created_at": "2026-01-01T00:00:01Z",
            "actor": {"login": "maintainer"}, "label": {"name": "chore"},
        }])
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue edit", (Path(stubs) / "gh.log").read_text())


class DispatchTest(unittest.TestCase):
    def test_prompt_carries_handoff_and_events_append(self) -> None:
        from unittest import mock

        from factory import dispatch

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
            gate_report = wt / ".factory" / "gate-report-7.md"
            gate_report.write_text("gate detail\n")
            review = dispatch.FACTORY / "review-7.md"
            review.write_text("review detail\n")
            worker_log = dispatch.LOGS / "7-attempt-1.log"
            worker_log.parent.mkdir()
            worker_log.write_text("worker detail\n")
            with mock.patch.object(dispatch, "run"):
                dispatch.escalate(7, "gate failed", worker_log)

            packet = dispatch.FACTORY / "escalations" / "7.md"
            text = packet.read_text()
            self.assertIn("## Reason\n\ngate failed", text)
            self.assertIn("| 1 | FAIL |", text)
            self.assertIn("## Last gate report\n\ngate detail", text)
            self.assertIn("## Latest review findings\n\nreview detail", text)
            self.assertIn("## Handoff\n\nleft the migration unverified", text)
            self.assertIn(f"## Log paths\n\n- `{worker_log}`", text)
            self.assertIn(f"## Worktree path\n\n`{wt}`", text)
            escalation = json.loads(dispatch.EVENTS.read_text().splitlines()[-1])
            self.assertEqual(escalation["packet"], str(packet))
            self.assertEqual(escalation["round"], 1)
            with mock.patch.object(dispatch, "run"):
                dispatch.escalate(7, "gate failed again", worker_log)
            escalation = json.loads(dispatch.EVENTS.read_text().splitlines()[-1])
            self.assertEqual(escalation["round"], 2)

    def test_sync_escalation_writes_same_packet_shape(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            dispatch.configure(config.load(repo))
            wt = dispatch.FACTORY / "wt-upstream"
            (wt / ".factory").mkdir(parents=True)
            response = subprocess.CompletedProcess([], 0, "https://github.com/acme/widgets/issues/42\n", "")
            with mock.patch.object(dispatch, "run", return_value=response):
                url = dispatch.sync_escalate("abc123", "gate failed", "upstream gate detail")

            self.assertEqual(url, "https://github.com/acme/widgets/issues/42")
            packet = dispatch.FACTORY / "escalations" / "42.md"
            text = packet.read_text()
            self.assertIn("## Reason\n\ngate failed", text)
            self.assertIn("## Attempts", text)
            self.assertIn("## Last gate report\n\nupstream gate detail", text)
            self.assertIn("## Latest review findings\n\n(none recorded)", text)
            self.assertIn("## Handoff\n\n(none recorded)", text)
            self.assertIn("## Log paths\n\n- none recorded", text)
            self.assertIn(f"## Worktree path\n\n`{wt}`", text)
            escalation = json.loads(dispatch.EVENTS.read_text().splitlines()[-1])
            self.assertEqual(escalation["ticket"], 42)
            self.assertEqual(escalation["upstream"], "abc123")
            self.assertEqual(escalation["packet"], str(packet))
            self.assertEqual(escalation["round"], 1)

    def test_learn_writes_lessons_from_events(self) -> None:
        from unittest import mock

        from factory import dispatch, learn, triage

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
        from factory import dispatch

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
