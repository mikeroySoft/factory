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

from factory import __version__, config, manage  # noqa: E402


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


def curate_diff(*paths: str) -> str:
    """A unified diff creating each path with one line, as a manager's CURATE body."""
    return "".join(
        f"diff --git a/{p} b/{p}\nnew file mode 100644\n--- /dev/null\n+++ b/{p}\n@@ -0,0 +1 @@\n+Run `make test` first.\n"
        for p in paths
    )


def build_fork(tmp: Path) -> tuple[Path, Path, Path]:
    """upstream (one commit, u0), origin forked from it, and root cloned from
    origin with an `upstream` remote. Callers add commits/branches on top."""
    upstream = tmp / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    git(upstream, "config", "user.email", "u@example.com")
    git(upstream, "config", "user.name", "U")
    (upstream / "u0.txt").write_text("u0")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "u0")

    origin = tmp / "origin"
    git(tmp, "clone", "-q", str(upstream), str(origin))
    git(origin, "remote", "remove", "origin")
    git(origin, "config", "user.email", "a@example.com")
    git(origin, "config", "user.name", "A")

    root = tmp / "root"
    git(tmp, "clone", "-q", str(origin), str(root))
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "remote", "add", "upstream", str(upstream))
    return root, origin, upstream


def merge_stage_mocks(origin: Path, pr_number: int, branch: str, title: str, original_run):
    """gh_json/run fakes for merge_pass_locked: PR list/checks/compare/view are
    canned; `gh pr merge` is simulated as the equivalent local git operation on
    `origin` (what GitHub would do), everything else runs for real."""

    def fake_gh_json(args):
        if args[:2] == ["pr", "list"]:
            return [{"number": pr_number, "headRefName": branch, "isDraft": False,
                      "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED"}]
        if args[0] == "api":
            return {"behind_by": 0}
        if args[:2] == ["pr", "view"]:
            return {"title": title}
        raise AssertionError(args)

    def fake_run(cmd, *a, **kw):
        if cmd[:3] == ["gh", "pr", "merge"]:
            method = "merge" if "--merge" in cmd else "squash"
            git(origin, "checkout", "-q", "main")
            if method == "merge":
                git(origin, "merge", "-q", "--no-ff", "-m", "Merge PR", branch)
            else:
                git(origin, "merge", "-q", "--squash", branch)
                git(origin, "commit", "-q", "-m", "Squash PR")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return original_run(cmd, *a, **kw)

    return fake_gh_json, fake_run


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
    def test_manager_prompt_file_and_omp_inline_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prompt = root / "manager-prompt-7.md"
            text = "Issue body\n" + "packet " * 30_000
            prompt.write_text(text)
            cfg = config.Config(root, "acme/widgets", manager=[
                "omp", "-p", "--no-session", "--model", "openai-codex/gpt-6-astra",
                "--cwd", "{cwd}", "@{prompt}",
            ])
            argv = cfg.manager_cmd(prompt, root)
            self.assertEqual(argv[-1], "@" + str(prompt))
            self.assertEqual(Path(argv[-1][1:]).read_text(), text)
            cfg.manager[-1] = "{prompt}"
            with self.assertRaisesRegex(config.ConfigError, r'@\{prompt\}'):
                cfg.manager_cmd(prompt, root)

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

    def test_worker_tables_and_legacy_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), '''
[workers]
default = ["agent", "{prompt}"]
[workers.chore]
command = ["special", "{cwd}", "{prompt}"]
when = "Mechanical edits"
''')
            cfg = config.load(repo)
            self.assertEqual(cfg.worker({"chore"}, Path("/p"), Path("/w")), ["special", "/w", "/p"])
            self.assertEqual(cfg.worker(set(), Path("/p"), Path("/w")), ["agent", "/p"])
            self.assertEqual(cfg.worker_when, {"chore": "Mechanical edits"})
            for entry in ('{command = "shell command"}', '{command = []}', '{command = [3]}',
                          '{command = ["agent"], when = 3}', '{when = "missing command"}'):
                with self.subTest(entry=entry):
                    (repo / config.CONFIG_NAME).write_text("[workers]\ndefault = " + entry)
                    with self.assertRaises(config.ConfigError):
                        config.load(repo)

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

    def test_doctor_accepts_district_engine_metadata_without_loading_it(self) -> None:
        metadata = (
            '[defaults.triage]\nurl = "http://127.0.0.1:1/v1/chat/completions"\n'
            '[defaults.workers]\ndefault = ["worker", "{prompt}"]\n'
            '[defaults.engine]\nref = "v0.3.0"\nsha = "abc"\nprevious = "def"\n'
            'installed_at = "2026-09-09T00:00:00Z"\n'
            '[defaults.engine.workers]\ndefault = ["not-a-worker"]\n'
        )
        host_file(metadata)
        gh = 'case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac\nexit 0'
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            stubs = stub_bin(Path(d), gh=gh, systemctl="echo inactive")
            proc = factory(repo, "doctor", "--json", path=stubs)
            rows = {r["label"]: r for r in json.loads(proc.stdout)["rows"]}
            self.assertEqual(rows["host config"]["status"], "PASS", rows["host config"])
            self.assertNotIn("engine", config.host_filter(config.host_config()["defaults"]))
            self.assertEqual(config.load(repo).worker(set(), Path("/p"), repo), ["worker", "/p"])

            # Only defaults.engine is metadata; typos and misplaced tables still warn.
            host_file(metadata + '[defaults.engien]\nsha = "bad"\n[repo."acme/widgets".engine]\nsha = "bad"\n')
            proc = factory(repo, "doctor", "--json", path=stubs)
            rows = {r["label"]: r for r in json.loads(proc.stdout)["rows"]}
            self.assertEqual(rows["host config"]["status"], "WARN")
            self.assertIn("defaults.engien", rows["host config"]["detail"])
            self.assertIn('repo."acme/widgets".engine', rows["host config"]["detail"])
            self.assertNotIn("defaults.engine", rows["host config"]["detail"])

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

    def test_manager_caps_from_host_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            cfg = config.load(repo)
            self.assertEqual((cfg.manager_max_active_cap, cfg.manager_budget_min_cap), (None, None))
            host_file('[defaults.manager]\nmax_active_cap = 4\nbudget_min_cap = 240\n')
            cfg = config.load(repo)
            self.assertEqual((cfg.manager_max_active_cap, cfg.manager_budget_min_cap), (4, 240))
            self.assertNotIn("manager.max_active_cap", config.unknown_keys({"manager": {"max_active_cap": 4, "budget_min_cap": 1}}))
            for bad in ('max_active_cap = 0\n', 'budget_min_cap = -5\n', 'max_active_cap = "many"\n'):
                with self.subTest(bad=bad):
                    host_file("[defaults.manager]\n" + bad)
                    with self.assertRaises(config.ConfigError):
                        config.load(repo)

    def test_doctor_manager_command(self) -> None:
        cases = [
            (None, "WARN", ["unset", "no automated diagnosis"]),
            (["manage"], "FAIL", ["{prompt}", "{cwd}"]),
            (["manage", "{prompt}"], "FAIL", ["{cwd}"]),
            (["manage", "{cwd}"], "FAIL", ["{prompt}"]),
            (["missing-manager-executable", "{prompt}", "{cwd}"], "FAIL", ["missing-manager-executable"]),
            (["omp", "{prompt}", "--cwd", "{cwd}"], "FAIL", ['use "@{prompt}"']),
            (["omp", "@{prompt}", "--cwd", "{cwd}"], "PASS", []),
            (["manage", "{prompt}", "{cwd}"], "PASS", []),
        ]
        host_file("")
        for command, status, details in cases:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as d:
                settings = '[triage]\nurl = "http://127.0.0.1:1/v1/chat/completions"\n'
                if command is not None:
                    settings += "[manager]\ncommand = " + json.dumps(command) + "\n"
                repo = make_repo(Path(d), settings)
                stubs = stub_bin(Path(d), gh='case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac',
                                 systemctl="echo inactive", manage="exit 0", omp="exit 0")
                result = factory(repo, "doctor", "--json", path=stubs)
                rows = {row["label"]: row for row in json.loads(result.stdout)["rows"]}
                self.assertEqual(rows["manager command"]["status"], status)
                for detail in details:
                    self.assertIn(detail, rows["manager command"]["detail"])
                text = factory(repo, "doctor", path=stubs)
                self.assertIn(f"{status}  manager command", text.stdout)


class StatsTest(unittest.TestCase):
    def test_stats_by_worker_uses_claim_labels(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d), toml='''[workers]
default = ["agent"]
chore = ["agent"]
special = ["agent"]
unused = ["agent"]
''')
            state = repo / ".factory"
            state.mkdir()
            events = [
                {"event": "attempt", "ticket": 9, "attempt": 1, "gate": "PASS", "cost": 99},
                {"event": "claimed", "ticket": 1, "labels": ["special", "chore"]},
                {"event": "attempt", "ticket": 1, "attempt": 1, "gate": "FAIL", "cost": 1.25, "brief": True},
                {"event": "attempt", "ticket": 1, "attempt": 2, "gate": "PASS", "cost": 2},
                {"event": "claimed", "ticket": 2, "labels": ["chore"]},
                {"event": "attempt", "ticket": 2, "attempt": 1, "gate": "PASS", "cost": 0, "brief": True},
                {"event": "attempt", "ticket": 2, "attempt": 4, "gate": "FAIL"},
                {"event": "claimed", "ticket": 1, "labels": ["special"]},
                {"event": "attempt", "ticket": 1, "attempt": 1, "gate": "PASS", "cost": 4},
                {"event": "claimed", "ticket": 3, "labels": ["unmatched"]},
                {"event": "attempt", "ticket": 3, "attempt": 1, "gate": "FAIL"},
                {"event": "claimed", "ticket": 4, "labels": ["unused"]},
            ]
            tail = {"event": "attempt", "ticket": 4, "attempt": 1, "gate": "PASS", "cost": 100}
            (state / "events.jsonl").write_text(
                "\n".join(map(json.dumps, events)) + "\n" + json.dumps(tail)
            )
            result = factory(repo, "stats", "--by-worker", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [
                {"worker": "default", "first_pass": 0.0, "attempts": 1, "cost": None},
                {"worker": "chore", "first_pass": 0.5, "attempts": 4, "cost": 3.25},
                {"worker": "special", "first_pass": 1.0, "attempts": 1, "cost": 4},
                {"worker": "unused", "first_pass": None, "attempts": 0, "cost": None},
            ])
            result = factory(repo, "stats", "--by-worker")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([line.split() for line in result.stdout.splitlines()[1:]], [
                ["default", "0.0%", "1", "n/a"],
                ["chore", "50.0%", "4", "$3.25"],
                ["special", "100.0%", "1", "$4.00"],
                ["unused", "n/a", "0", "n/a"],
            ])
            result = factory(repo, "stats", "--by-brief", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [
                {"tickets": "with brief", "first_pass": 0.5, "attempts": 2},
                {"tickets": "without brief", "first_pass": 2 / 3, "attempts": 3},
            ])
            result = factory(repo, "stats", "--by-brief")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([line.split() for line in result.stdout.splitlines()[1:]], [
                ["with", "brief", "50.0%", "2"],
                ["without", "brief", "66.7%", "3"],
            ])
            from unittest import mock

            from factory import dashboard, dispatch

            with mock.patch.dict(dashboard.__dict__), mock.patch.dict(dispatch.__dict__):
                dashboard.configure(config.load(repo))
                with mock.patch.object(dashboard, "github", side_effect=RuntimeError("offline")), \
                     mock.patch.object(dashboard, "dispatcher", return_value={}), \
                     mock.patch.object(dashboard, "upstream_state", return_value={}), \
                     mock.patch.object(dashboard, "triage_llm_online", return_value=False), \
                     mock.patch.object(dashboard.lifecycle, "_rows",
                                       wraps=dashboard.lifecycle._rows) as journal_reads:
                    snapshot = dashboard.snapshot()
            self.assertEqual(snapshot["workers"], [
                {"worker": "default", "first_pass": 0.0, "attempts": 1, "cost": None},
                {"worker": "chore", "first_pass": 0.5, "attempts": 4, "cost": 3.25},
                {"worker": "special", "first_pass": 1.0, "attempts": 1, "cost": 4},
                {"worker": "unused", "first_pass": None, "attempts": 0, "cost": None},
            ])
            self.assertEqual(snapshot["spend"], {"seconds": 0, "cost": 106.25, "tickets": 4})
            self.assertEqual(journal_reads.call_count, 1)

    def test_timeline_actor_attribution_in_stats(self) -> None:
        from unittest import mock

        from factory import stats

        def label(event: str, minute: int, name: str, actor: dict | None) -> dict:
            return {"event": event, "created_at": f"2026-09-01T00:{minute:02}:00Z",
                    "label": {"name": name}, "actor": actor}

        human = {"login": "maintainer", "type": "User"}
        bot = {"login": "factory[bot]", "type": "Bot"}
        timeline = [
            label("labeled", 0, config.LABEL_AGENT, human),
            label("labeled", 1, config.LABEL_HUMAN, bot),
            label("unlabeled", 11, config.LABEL_HUMAN, human),
            label("labeled", 11, config.LABEL_AGENT, human),
            label("labeled", 12, config.LABEL_HUMAN, human),
            label("unlabeled", 32, config.LABEL_HUMAN, bot),
            label("labeled", 32, config.LABEL_AGENT, bot),
            label("labeled", 33, config.LABEL_HUMAN, bot),
            label("unlabeled", 38, config.LABEL_HUMAN, None),
        ]
        details = {"number": 7, "title": "fixed", "createdAt": "2026-09-01T00:00:00Z",
                   "closedAt": "2026-09-01T01:00:00Z", "state": "CLOSED", "comments": []}

        def github(*args: str):
            if args[:2] == ("pr", "list"):
                return []
            if args[:2] == ("issue", "list"):
                return []
            if args[:2] == ("issue", "view"):
                return details
            if args[0] == "api":
                return [timeline[:4], timeline[4:]]
            self.fail(f"unexpected gh call: {args}")

        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            stats.configure(config.load(repo))
            stats.cfg.factory.mkdir()
            audit = [
                {"at": "2026-09-01T00:00:00Z", "event": "claimed", "ticket": 7},
                *({"at": f"2026-09-01T00:{m:02}:00Z", "event": "escalate", "ticket": 7} for m in (1, 12, 33)),
                {"at": "2026-09-01T01:00:00Z", "event": "merged", "ticket": 7},
            ]
            (stats.cfg.factory / "events.jsonl").write_text("\n".join(map(json.dumps, audit)) + "\npartial")
            with mock.patch.object(stats, "gh", side_effect=github):
                row, = stats.collect_rows()
            self.assertEqual(row["escalation_count"], 3)
            self.assertEqual(row["resolutions"], [
                {"actor": "maintainer", "resolved_by": "human"},
                {"actor": "factory[bot]", "resolved_by": "factory"},
                {"actor": None, "resolved_by": "unknown"},
            ])
            self.assertEqual(row["ready_for_human_minutes"], 35)
            self.assertEqual(row["requeue_count"], 2)
            from datetime import datetime, timezone
            from factory import dashboard

            totals = stats.human_touch_metrics([row], datetime(2026, 9, 8, 0, 12, tzinfo=timezone.utc))
            self.assertEqual(totals, {"escalations_per_week": 2, "human_resolved_pct": 50.0})
            dashboard.configure(stats.cfg)
            ticket_issue = {
                **details, "url": "", "updatedAt": details["closedAt"],
                "timelineItems": {"nodes": [
                    {"__typename": "LabeledEvent" if e["event"] == "labeled" else "UnlabeledEvent",
                     "createdAt": e["created_at"], "label": e["label"],
                     "actor": {"login": (e["actor"] or {}).get("login"),
                               "__typename": (e["actor"] or {}).get("type")}}
                    for e in timeline
                ]},
            }
            ticket = dashboard.build_ticket(ticket_issue, None, {
                "attempts": [], "gate": None, "lock_held": False,
            }, audit=audit)
            self.assertEqual(ticket["human_touch"]["ready_for_human_minutes"], 35)
            self.assertEqual(dashboard.metrics([ticket])["human_resolved_pct"], 50.0)


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
        for ticket, count in zip(tickets, (0, 1, 1, 0)):
            ticket["human_touch"] = {"escalation_count": count}
        m = dashboard.metrics(tickets)
        self.assertEqual(m, {"first_pass": 0.5, "bounce_rate": 0.5, "escalations": 2, "med_attempts": 2,
                             "escalations_per_week": 0, "human_resolved_pct": None})
        self.assertEqual(dashboard.metrics([]), {"first_pass": None, "bounce_rate": None, "escalations": 0,
                                               "med_attempts": None, "escalations_per_week": 0, "human_resolved_pct": None})

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
        from unittest.mock import patch
        from factory import lifecycle

        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
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
  "pr list") echo '[]';;
  "issue list") echo '[{{"number":7,"title":"Fix gate","body":"Original body","labels":[{{"name":"ready-for-human"}}]}}]';;
  "api repos/acme/widgets/issues/7/timeline") echo '{timeline}';;
  "issue create") echo "https://github.com/acme/widgets/issues/8";;
  "issue comment"|"issue edit")
    python3 -c 'import json; from pathlib import Path; assert any(json.loads(line).get("event") == "manage" for line in Path("{state}/events.jsonl").read_text().splitlines())' || exit 1;;
esac
''')
        return repo, stubs, packet

    def test_manager_reads_prompt_file_with_district_command(self) -> None:
        repo, stubs, packet = self.scenario()
        text = "Escalation evidence\n" + "packet " * 30_000
        packet.write_text(text)
        stub_bin(Path(stubs).parent, omp='''
python3 - "$5" <<'PY'
import pathlib, sys
assert sys.argv[1].startswith("@"), sys.argv
prompt = pathlib.Path(sys.argv[1][1:]).read_text()
assert "Original body" in prompt
assert "packet " * 30_000 in prompt
print("DECISION: HUMAN\\nRead the complete prompt")
PY
''')
        (repo / config.CONFIG_NAME).write_text(
            '[manager]\ncommand = ["omp", "-p", "--cwd", "{cwd}", "--no-session", "@{prompt}"]\n')
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Factory manager: Read the complete prompt", (Path(stubs) / "gh.log").read_text())
        self.assertIn(text, (repo / ".factory/manager-prompt-7.md").read_text())

    def test_manager_failure_bounds_stderr_and_records_reason(self) -> None:
        from unittest.mock import patch
        from factory import dispatch, stats

        repo, _, packet = self.scenario()
        command = [sys.executable, "-c",
                   "import sys; sys.stderr.write(''.join(f'error-{i}\\n' for i in range(20))); sys.exit(1)",
                   "{prompt}", "{cwd}"]
        cfg = config.Config(repo, "acme/widgets", manager=command, manager_rounds=2)
        dispatch.configure(cfg)
        with patch.object(dispatch, "gh_json", return_value=[
            {"number": 7, "title": "Fix gate", "body": "Original body"}
        ]), patch.object(manage, "human_activity", return_value=False), patch.object(manage, "apply") as apply:
            manage.manage_pass()
        _, _, decision, body, _, _ = apply.call_args.args
        self.assertEqual(decision, "HUMAN")
        self.assertTrue(body.startswith("Manager command exited 1 (argv: "), body)
        self.assertIn("{prompt}", body.splitlines()[0])
        self.assertIn("{cwd}", body.splitlines()[0])
        self.assertEqual(body.splitlines()[1:], [f"error-{i}" for i in range(15, 20)])
        events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
        failed = [e for e in events if e.get("event") == "escalate" and e.get("reason") == "manager_failed"]
        self.assertEqual([(e["event"], e["ticket"], e["round"], e["packet"]) for e in failed],
                         [("escalate", 7, 1, str(packet))])
        touch = stats.human_touch([], events)
        self.assertEqual(touch["escalation_count"], 1)
        self.assertEqual(touch["manager_failures"], 1)
        now = stats.datetime.fromisoformat(events[0]["at"].replace("Z", "+00:00"))
        self.assertEqual(stats.human_touch_metrics([touch], now)["escalations_per_week"], 1)
        packet, round_number = dispatch.escalation_packet(7, "gate_failed", None, repo / ".factory/wt-7")
        self.assertEqual(round_number, 2)
        dispatch.record("escalate", ticket=7, round=round_number, packet=str(packet), reason="gate_failed")
        command[:] = ["printf", "DECISION: RETRY\nTry again"]
        with patch.object(dispatch, "gh_json", return_value=[
            {"number": 7, "title": "Fix gate", "body": "Original body"}
        ]), patch.object(manage, "human_activity", return_value=False), patch.object(manage, "apply") as apply:
            manage.manage_pass()
        self.assertEqual(apply.call_args.args[2:4], ("RETRY", "Try again"))

    def test_manager_config_error_leaves_diagnosis_without_replaying(self) -> None:
        repo, stubs, _ = self.scenario()
        (repo / config.CONFIG_NAME).write_text(
            '[manager]\ncommand = ["omp", "-p", "{prompt}", "--cwd", "{cwd}"]\n')
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (Path(stubs) / "gh.log").read_text()
        self.assertIn('use "@{prompt}"', calls)
        events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
        self.assertEqual([(e["decision"], e["round"]) for e in events if e.get("event") == "manage"],
                         [("HUMAN", 1)])
        self.assertEqual(factory(repo, "manage", path=stubs).returncode, 0)
        self.assertNotIn("issue comment", (Path(stubs) / "gh.log").read_text()[len(calls):])

    def test_manage_retry_records_before_comment_and_relabels(self) -> None:
        repo, stubs, packet = self.scenario()
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (Path(stubs) / "gh.log").read_text()
        self.assertIn("Factory manager: Use the existing helper", calls)
        self.assertIn("--remove-label ready-for-human --add-label ready-for-agent", calls)
        event = next(e for e in map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()) if e.get("event") == "manage")
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
        self.assertFalse(any(e.get("event") == "manage" for e in map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines())))

    def test_manage_skips_human_comment_after_escalation(self) -> None:
        repo, stubs, _ = self.scenario(activity=[{
            "event": "commented", "created_at": "2026-01-01T00:00:01Z",
            "actor": {"login": "maintainer", "type": "User"}, "body": "I will handle this",
        }])
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue comment", (Path(stubs) / "gh.log").read_text())
        self.assertFalse(any(e.get("event") == "manage" for e in map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines())))

    def test_manager_failure_preserves_human_takeover_on_next_pass(self) -> None:
        from unittest.mock import patch
        from factory import dispatch

        repo, _, _ = self.scenario()
        marker = repo / "human-takeover"
        command = [sys.executable, "-c",
                   "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('taken'); sys.exit(1)",
                   str(marker)]
        dispatch.configure(config.Config(repo, "acme/widgets", manager=command))

        def github(args: list[str]) -> list[dict]:
            if args[:2] == ["issue", "list"]:
                return [{"number": 7, "title": "Fix gate", "body": "Original body"}]
            return [{"event": "commented", "created_at": "2026-01-01T00:00:01Z",
                     "body": "I will handle this"}] if marker.exists() else []

        with patch.object(dispatch, "gh_json", side_effect=github), \
             patch.object(dispatch.time, "strftime", return_value="2026-01-01T00:00:02Z"), \
             patch.object(manage, "apply") as apply:
            manage.manage_pass()
            manage.manage_pass()
        self.assertTrue(marker.exists())
        apply.assert_not_called()

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

    def test_route_uses_only_listed_worker_labels_and_when_rules(self) -> None:
        for configured in (False, True):
            with self.subTest(configured=configured):
                repo, stubs, _ = self.scenario()
                prompt = repo / ".factory/manager-prompt.txt"
                output = 'DECISION: ROUTE\n{"add":["chore"],"guidance":"Use the mechanical worker"}'
                command = [sys.executable, "-c",
                           "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text()); print(sys.argv[3])",
                           str(prompt), "{prompt}", output]
                settings = "[manager]\ncommand = " + json.dumps(command) + '\n[workers]\ndefault = ["false"]\n'
                if configured:
                    settings += '[workers.chore]\ncommand = ["true"]\nwhen = "Mechanical edits only"\n'
                (repo / config.CONFIG_NAME).write_text(settings)
                result = factory(repo, "manage", path=stubs)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = (Path(stubs) / "gh.log").read_text()
                events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
                decision = next(e["decision"] for e in events if e.get("event") == "manage")
                self.assertEqual(decision, "ROUTE" if configured else "HUMAN")
                if configured:
                    self.assertIn("chore: Mechanical edits only", prompt.read_text())
                    self.assertIn("--add-label chore", calls)
                else:
                    self.assertNotIn("issue edit", calls)

    def test_fix_runs_selected_worker_on_red_ci_and_requires_gate_and_review(self) -> None:
        cases = ((False, "APPROVE", False), (True, "REVISE", False),
                 (True, "APPROVE", False), (True, "APPROVE", True))
        for gate_ok, verdict, rebase in cases:
            with self.subTest(gate_ok=gate_ok, verdict=verdict, rebase=rebase):
                repo, stubs, packet = self.scenario()
                packet.write_text("PR #9: CI failed (unit); factory-approved label removed")
                worker = "conflict" if rebase else "ci-fix"
                command = ["printf", "%s", "DECISION: FIX\n" + json.dumps(
                    {"worker": worker, "guidance": "Read the failing unit job log"})]
                (repo / config.CONFIG_NAME).write_text(
                    "[manager]\ncommand = " + json.dumps(command)
                    + '\n[workers]\ndefault = ["false"]\nchore = ["false"]'
                    + f'\n[workers.{worker}]\ncommand = ["fix-worker", "{{prompt}}"]\nwhen = "Red CI"'
                    + '\n[review]\ncommand = ["printf", "VERDICT: ' + verdict + '"]'
                    + '\n[[gate.check]]\nname = "unit"\nrun = ["' + ("true" if gate_ok else "false") + '"]\n'
                    + '\n[repo]\nslug = "acme/widgets"\n'
                )
                wt = repo / ".factory/wt-7"
                git(repo, "worktree", "add", "-q", str(wt), "-b", "agent/7")
                remote = repo.parent / "origin.git"
                git(repo, "init", "--bare", str(remote))
                git(repo, "remote", "set-url", "origin", str(remote))
                (wt / "README.md").write_text("branch intent\n")
                git(wt, "add", "README.md")
                git(wt, "commit", "-qm", "Branch intent")
                git(wt, "push", "-u", "origin", "agent/7")
                original = git(remote, "rev-parse", "agent/7")
                (repo / "main.txt").write_text("main intent\n")
                git(repo, "add", "main.txt")
                git(repo, "commit", "-qm", "Main intent")
                git(repo, "push", "origin", "main")
                stub_bin(Path(stubs).parent, **{
                    "gh": '''
case "$1 $2" in
  "pr list") echo '[]';;
  "issue list") echo '[{"number":7,"title":"Fix CI","body":"Original","labels":[{"name":"chore"}]}]';;
  "api repos/acme/widgets/issues/7/timeline") echo '[]';;
  "issue view") echo '{"title":"Fix CI","body":"Original","comments":[]}';;
  "pr view") echo '{"number":9,"state":"OPEN","headRefName":"agent/7","reviewDecision":""}';;
  "pr checks") echo '[{"name":"unit","bucket":"fail"}]';;
esac
''',
                    "fix-worker": ('git rebase origin/main || exit 1\n' if rebase else "")
                    + 'mkdir -p .factory\ncat "$1" > .factory/worker-input\nprintf "fixed\\n" > README.md',
                })
                result = factory(repo, "manage", path=stubs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((wt / "README.md").read_text(), "fixed\n")
                guidance = (wt / ".factory/worker-input").read_text()
                self.assertIn("Read the failing unit job log", guidance)
                self.assertIn("CI failed (unit)", guidance)
                events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
                self.assertEqual(next(e["decision"] for e in events if e.get("event") == "manage"), "FIX")
                calls = (Path(stubs) / "gh.log").read_text()
                self.assertEqual("--add-label factory-approved" in calls, gate_ok and verdict == "APPROVE")
                self.assertNotIn("--add-label ready-for-agent", calls)
                self.assertEqual(git(remote, "rev-parse", "agent/7"),
                                 git(wt, "rev-parse", "HEAD") if gate_ok else original)
                if rebase:
                    self.assertEqual(git(remote, "show", "agent/7:main.txt"), "main intent")

    def test_fix_rejects_unlisted_workers(self) -> None:
        for worker in ("ci-fix", "default", "ready-for-agent", ["chore"]):
            with self.subTest(worker=worker):
                repo, stubs, _ = self.scenario()
                command = ["printf", "%s", "DECISION: FIX\n" + json.dumps({"worker": worker, "guidance": "Fix CI"})]
                (repo / config.CONFIG_NAME).write_text("[manager]\ncommand = " + json.dumps(command))
                result = factory(repo, "manage", path=stubs)
                self.assertEqual(result.returncode, 0, result.stderr)
                events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
                self.assertEqual(next(e["decision"] for e in events if e.get("event") == "manage"), "HUMAN")
                self.assertNotIn("issue edit", (Path(stubs) / "gh.log").read_text())

    def test_manage_skips_human_label_change(self) -> None:
        repo, stubs, _ = self.scenario(activity=[{
            "event": "labeled", "created_at": "2026-01-01T00:00:01Z",
            "actor": {"login": "maintainer"}, "label": {"name": "chore"},
        }])
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue edit", (Path(stubs) / "gh.log").read_text())

    def test_manage_dry_run_does_not_create_ticket_lock_or_record_decision(self) -> None:
        repo, stubs, _ = self.scenario()
        before = (repo / ".factory/events.jsonl").read_bytes()
        result = factory(repo, "manage", "--dry-run", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would manage escalation round 1", result.stdout)
        self.assertFalse((repo / ".factory/locks/7.lock").exists())
        self.assertEqual((repo / ".factory/events.jsonl").read_bytes(), before)
        self.assertNotIn("issue comment", (Path(stubs) / "gh.log").read_text())

    def test_manager_notes_round_trip_and_refused_replacements(self) -> None:
        repo, stubs, _ = self.scenario()
        events_path = repo / ".factory/events.jsonl"
        escalation = json.loads(events_path.read_text())
        prompt = repo / ".factory/manager-prompt.txt"
        notes = repo / ".factory/manager/notes.md"
        first = "2026-01-01: `unit` flakes on a cold cache; RETRY re-run cleared it.\n"

        def run_manager(round_number: int, output: str) -> str:
            with events_path.open("a") as events:
                events.write(json.dumps({**escalation, "round": round_number}) + "\n")
            command = [sys.executable, "-c",
                       "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text()); sys.stdout.write(sys.argv[3])",
                       str(prompt), "{prompt}", output]
            (repo / config.CONFIG_NAME).write_text(
                "[manager]\nrounds = 3\ncommand = " + json.dumps(command) + "\n")
            result = factory(repo, "manage", path=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            return prompt.read_text()

        def status(round_number: int) -> object:
            events = map(json.loads, events_path.read_text().splitlines())
            return next(e["notes"] for e in events if e.get("event") == "manage" and e["round"] == round_number)

        sent = run_manager(1, f"DECISION: RETRY\nUse the existing helper\n\n```notes\n{first}```\n")
        self.assertNotIn(first, sent)  # nothing to carry on the first run
        self.assertEqual(notes.read_text(), first)
        self.assertEqual(status(1), "written")
        calls = (Path(stubs) / "gh.log").read_text()
        self.assertIn("Factory manager: Use the existing helper", calls)
        self.assertNotIn("notes", calls)  # the block is not part of the guidance comment

        sent = run_manager(2, "DECISION: HUMAN\nStill stuck\n\n```notes\n\n```\n")
        self.assertIn(first, sent)  # the second prompt carries the first run's notes
        self.assertEqual(notes.read_text(), first)
        self.assertEqual(status(2), "empty_rejected")

        oversize = "2026-01-02: " + "x" * manage.NOTES_CAP + "\n"
        run_manager(3, f"DECISION: HUMAN\nStill stuck\n\n```notes\n{oversize}```\n")
        self.assertEqual(notes.read_text(), first)
        self.assertEqual(status(3), "oversize_rejected")

    def test_failed_rewrite_is_not_requeued_or_replayed_and_next_ticket_runs(self) -> None:
        repo, stubs, packet = self.scenario()
        events_path = repo / ".factory/events.jsonl"
        escalation = json.loads(events_path.read_text())
        escalation["ticket"] = 8
        with events_path.open("a") as events:
            events.write(json.dumps(escalation) + "\n")
        command = ["printf", "%s", "DECISION: REWRITE\nReplacement body"]
        (repo / config.CONFIG_NAME).write_text("[manager]\ncommand = " + json.dumps(command) + "\n")
        stub_bin(Path(stubs).parent, gh='''
case "$1 $2 $3" in
  "pr list --repo") echo '[]';;
  "issue list --repo") echo '[{"number":7,"title":"First","body":"Old"},{"number":8,"title":"Next","body":"Old"}]';;
  "api repos/acme/widgets/issues/"*) echo '[]';;
  "issue edit 7") echo 'GitHub rejected body edit' >&2; exit 1;;
esac
''')
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (Path(stubs) / "gh.log").read_text()
        self.assertIn("issue edit 7 --repo acme/widgets --body Replacement body", calls)
        self.assertNotIn("issue edit 7 --repo acme/widgets --remove-label", calls)
        self.assertIn("issue edit 8 --repo acme/widgets --remove-label ready-for-human --add-label ready-for-agent", calls)
        events = list(map(json.loads, events_path.read_text().splitlines()))
        terminal = next(e for e in events if e.get("kind") == "exit" and e.get("stage") == "manage" and e.get("ticket") == 7)
        self.assertEqual(terminal["outcome"], "mechanism_failure")
        self.assertEqual(terminal["reason"], "github_command_failed")
        runtime = factory(repo, "dashboard", "--runtime-json", path=stubs)
        self.assertEqual(runtime.returncode, 0, runtime.stderr)
        observed = next(e for e in json.loads(runtime.stdout)["executions"] if e["execution_id"] == terminal["execution_id"])
        self.assertEqual((observed["state"], observed["reason"]), ("failed", "github_command_failed"))
        self.assertEqual(factory(repo, "manage", path=stubs).returncode, 0)
        self.assertNotIn("issue edit", (Path(stubs) / "gh.log").read_text()[len(calls):])

    def test_manage_rejects_curate_from_escalation_packet(self) -> None:
        repo, stubs, _ = self.scenario()
        command = ["printf", "%s", "DECISION: CURATE\n" + curate_diff("AGENTS.md")]
        (repo / config.CONFIG_NAME).write_text("[manager]\ncommand = " + json.dumps(command) + "\n")
        result = factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (Path(stubs) / "gh.log").read_text()
        self.assertIn("Factory manager: Rejected CURATE", calls)
        self.assertNotIn("--add-label ready-for-agent", calls)
        self.assertNotIn("pr create", calls)
        events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
        manage = next(e for e in events if e.get("event") == "manage")
        self.assertEqual(manage["decision"], "HUMAN")
        self.assertEqual(manage["rejected"], "CURATE")


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

    def test_brief_names_defining_file_and_last_pr(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            (repo / "pkg").mkdir()
            (repo / "pkg" / "mod.py").write_text("def frobnicate():\n    return 1\n")
            (repo / "pkg" / "other.py").write_text("x = 1\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "feat: add frobnicate (#12)")
            (repo / "pkg" / "mod.py").write_text("def frobnicate():\n    return 2\n")
            git(repo, "commit", "-q", "-am", "Merge pull request #15 from acme/fix-frobnicate")
            (repo / config.LESSONS_NAME).write_text("- Run `make test` first.\n- frobnicate must stay pure.\n")
            dispatch.configure(config.load(repo))
            issue = {"title": "frobnicate returns the wrong value", "body": "`frobnicate` should return 1.",
                     "comments": [{"author": {"login": "bot"}, "body": "Triage: ok\n\nAgent brief: keep it pure."}]}
            with mock.patch.object(dispatch, "gh_json", return_value=issue):
                prompt = dispatch.build_prompt(7, repo)
            brief = prompt.split("## Brief", 1)[1]
            self.assertIn("- pkg/mod.py", brief)
            self.assertNotIn("other.py", brief)
            self.assertIn("- #15 Merge pull request #15", brief)
            self.assertIn("- #12 feat: add frobnicate (#12)", brief)
            self.assertIn("keep it pure.", brief)
            self.assertIn("- frobnicate must stay pure.", brief)
            self.assertNotIn("make test", brief)
            self.assertEqual((repo / ".factory" / "brief-7.md").read_text(), brief.strip())
            # No matches: no file, no section.
            empty = {"title": "nothing here", "body": "", "comments": []}
            with mock.patch.object(dispatch, "gh_json", return_value=empty):
                self.assertNotIn("## Brief", dispatch.build_prompt(8, repo))
            self.assertFalse((repo / ".factory" / "brief-8.md").exists())

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

    def learn_scenario(self, root: Path, reply: str) -> tuple[Path, Path, str, Path]:
        """Repo with a bare origin and a fake manager that records its prompt and prints `root/reply.txt`."""
        repo = make_repo(root)
        bare = root / "origin.git"
        git(root, "init", "-q", "--bare", str(bare))
        git(repo, "remote", "set-url", "origin", str(bare))
        prompt = root / "prompt.txt"
        (root / "reply.txt").write_text(reply)
        command = [sys.executable, "-c",
                   "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text()); "
                   "sys.stdout.write(pathlib.Path(sys.argv[3]).read_text())",
                   str(prompt), "{prompt}", str(root / "reply.txt")]
        (repo / config.CONFIG_NAME).write_text(
            '[repo]\nslug = "acme/widgets"\n[manager]\ncommand = ' + json.dumps(command) + "\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "cfg")
        git(repo, "push", "-q", "origin", "main")
        state = repo / ".factory"
        notes = state / "manager/notes.md"
        notes.parent.mkdir(parents=True)
        notes.write_text("2026-01-01: `unit` flakes on a cold cache.\n")
        (state / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [
            {"event": "claimed", "ticket": 3, "title": "fix parser", "at": "2026-01-01T00:00:00Z"},
            {"event": "escalate", "ticket": 3, "reason": "gate failed 3 times", "at": "2026-01-01T00:01:00Z"},
        ]))
        stubs = stub_bin(root, gh='case "$1 $2" in "pr list") echo "[]";; esac')
        return repo, bare, stubs, prompt

    def test_learn_with_manager_opens_chore_pr(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            reply = json.dumps({"lessons": ["Run `make test` before the gate."]})
            repo, bare, stubs, prompt = self.learn_scenario(root, reply)
            result = factory(repo, "learn", path=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("`unit` flakes on a cold cache", prompt.read_text())  # notes.md is evidence
            self.assertFalse((repo / config.LESSONS_NAME).exists())  # nothing written in ROOT
            calls = (Path(stubs) / "gh.log").read_text()
            self.assertIn("pr create --repo acme/widgets --head agent/lessons-", calls)
            self.assertIn("--label chore", calls)
            branch = git(bare, "branch", "--list", "agent/lessons-*").lstrip("* ")
            self.assertTrue(branch.startswith("agent/lessons-"), branch)
            self.assertEqual(git(bare, "diff", "--name-only", "main", branch), config.LESSONS_NAME)
            self.assertIn("- Run `make test` before the gate.", git(bare, "show", f"{branch}:{config.LESSONS_NAME}"))
            self.assertNotIn("agent/curate-", calls)

    def test_learn_curate_opens_chore_pr_touching_only_context_paths(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            diff = curate_diff("AGENTS.md", ".omp/skills/testing/SKILL.md")
            reply = json.dumps({"lessons": ["Run `make test` before the gate."]}) + "\nDECISION: CURATE\n" + diff
            repo, bare, stubs, prompt = self.learn_scenario(root, reply)
            result = factory(repo, "learn", path=stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DECISION: CURATE", prompt.read_text())  # the prompt offers the decision
            calls = (Path(stubs) / "gh.log").read_text()
            self.assertIn("pr create --repo acme/widgets --head agent/lessons-", calls)
            self.assertIn("pr create --repo acme/widgets --head agent/curate-", calls)
            self.assertEqual(calls.count("--label chore"), 2)
            branch = git(bare, "branch", "--list", "agent/curate-*").lstrip("* ")
            self.assertTrue(branch.startswith("agent/curate-"), branch)
            self.assertEqual(git(bare, "diff", "--name-only", "main", branch).split(),
                             [".omp/skills/testing/SKILL.md", "AGENTS.md"])
            self.assertEqual(git(bare, "show", f"{branch}:AGENTS.md"), "Run `make test` first.")
            body = (repo / ".factory" / f"pr-body-{branch.removeprefix('agent/')}.md").read_text()
            self.assertIn("#3", body)  # cites the tickets
            self.assertIn("`unit` flakes on a cold cache", body)  # and the manager notes
            events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
            self.assertEqual(next(e for e in events if e.get("event") == "learn")["curate"], branch)

    def test_learn_curate_rejects_verification_paths(self) -> None:
        for bad in (".github/workflows/ci.yml", ".factory.toml", "src/main.py", "AGENTS.mdx"):
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                repo, bare, stubs, _ = self.learn_scenario(root, "")
                if bad == config.CONFIG_NAME:  # a real edit to the committed gate config
                    with (repo / bad).open("a") as f:
                        f.write('[[gate.check]]\nname = "skip"\nrun = ["true"]\n')
                    diff = curate_diff("AGENTS.md") + git(repo, "diff") + "\n"
                    git(repo, "checkout", "--", bad)
                else:
                    diff = curate_diff("AGENTS.md", bad)
                (root / "reply.txt").write_text(
                    json.dumps({"lessons": ["Run `make test` before the gate."]}) + "\nDECISION: CURATE\n" + diff)
                result = factory(repo, "learn", path=stubs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"CURATE rejected: {bad}", result.stdout)
                calls = (Path(stubs) / "gh.log").read_text()
                self.assertIn("--head agent/lessons-", calls)  # lessons PR still opens
                self.assertNotIn("agent/curate-", calls)
                self.assertEqual(git(bare, "branch", "--list", "agent/curate-*"), "")
                self.assertFalse(list((repo / ".factory").glob("wt-curate-*")))
                self.assertFalse(list((repo / ".factory").glob("curate-*.patch")))
                events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
                self.assertIn(bad, next(e for e in events if e.get("event") == "learn")["curate"])

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

    def test_merge_stage_merges_sync_pr_despite_upstream_advancing_past_tip(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            root, origin, upstream = build_fork(tmp)

            (upstream / "u1.txt").write_text("u1")
            git(upstream, "add", "-A")
            git(upstream, "commit", "-q", "-m", "u1")
            u1 = git(upstream, "rev-parse", "HEAD")

            git(origin, "checkout", "-q", "-b", "agent/31")
            git(origin, "remote", "add", "up", str(upstream))
            git(origin, "fetch", "-q", "up")
            git(origin, "merge", "-q", "--no-ff", "-m", "merge upstream", u1)
            git(origin, "remote", "remove", "up")
            git(origin, "checkout", "-q", "main")

            # Upstream advances past the tip the PR actually carries.
            (upstream / "u2.txt").write_text("u2")
            git(upstream, "add", "-A")
            git(upstream, "commit", "-q", "-m", "u2")

            cfg = config.Config(root=root, repo="acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)

            fake_gh_json, fake_run = merge_stage_mocks(
                origin, 100, "agent/31", "upstream sync: pick up u1", dispatch.run
            )
            with mock.patch.object(dispatch, "gh_json", side_effect=fake_gh_json), \
                 mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                 mock.patch.object(dispatch, "run", side_effect=fake_run):
                dispatch.merge_pass_locked(False)

            self.assertEqual(
                subprocess.run(["git", "-C", str(origin), "merge-base", "--is-ancestor", u1, "main"]).returncode,
                0,
            )
            parents = git(origin, "log", "-1", "--pretty=%P", "main").split()
            self.assertEqual(len(parents), 2, "expected a merge commit, not a squash")

    def test_merge_stage_squashes_ordinary_pr_with_no_upstream_commits(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            root, origin, upstream = build_fork(tmp)

            git(origin, "checkout", "-q", "-b", "agent/99")
            (origin / "feature.txt").write_text("feature")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "feature")
            git(origin, "checkout", "-q", "main")

            cfg = config.Config(root=root, repo="acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)

            fake_gh_json, fake_run = merge_stage_mocks(
                origin, 200, "agent/99", "add feature", dispatch.run
            )
            with mock.patch.object(dispatch, "gh_json", side_effect=fake_gh_json), \
                 mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                 mock.patch.object(dispatch, "run", side_effect=fake_run):
                dispatch.merge_pass_locked(False)

            parents = git(origin, "log", "-1", "--pretty=%P", "main").split()
            self.assertEqual(len(parents), 1, "expected a squash commit, not a merge")

    def test_refresh_merges_main_into_sync_pr_instead_of_rebasing(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            root, origin, upstream = build_fork(tmp)

            (upstream / "u1.txt").write_text("u1")
            git(upstream, "add", "-A")
            git(upstream, "commit", "-q", "-m", "u1")
            u1 = git(upstream, "rev-parse", "HEAD")

            git(origin, "checkout", "-q", "-b", "agent/31")
            git(origin, "remote", "add", "up", str(upstream))
            git(origin, "fetch", "-q", "up")
            git(origin, "merge", "-q", "--no-ff", "-m", "merge upstream", u1)
            git(origin, "remote", "remove", "up")
            git(origin, "checkout", "-q", "main")

            # main moves (another PR lands) before the sync PR is merged.
            (origin / "other.txt").write_text("other pr")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "other pr landed")

            cfg = config.Config(root=root, repo="acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)

            # A worktree already exists from the original attempt.
            git(root, "fetch", "origin")
            git(root, "branch", "agent/31", "origin/agent/31")
            wt = dispatch.FACTORY / "wt-31"
            git(root, "worktree", "add", str(wt), "agent/31")

            with mock.patch.object(dispatch, "run_gate", return_value=(True, "ok")):
                dispatch.refresh_pr_branch(31, 100, True)

            self.assertEqual(
                subprocess.run(["git", "-C", str(wt), "merge-base", "--is-ancestor", u1, "HEAD"]).returncode,
                0,
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(origin), "merge-base", "--is-ancestor", u1, "agent/31"]
                ).returncode,
                0,
            )

    def test_refresh_starts_from_remote_branch_when_no_local_copy(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            root, origin, upstream = build_fork(tmp)

            git(origin, "checkout", "-q", "-b", "agent/7")
            (origin / "feature.txt").write_text("feature")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "feature")
            feature = git(origin, "rev-parse", "HEAD")
            git(origin, "checkout", "-q", "main")
            main_tip = git(origin, "rev-parse", "main")

            cfg = config.Config(root=root, repo="acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)

            # No local agent/7 and no worktree: the PR was pushed from elsewhere.
            with mock.patch.object(dispatch, "run_gate", return_value=(True, "ok")), \
                 mock.patch.object(dispatch, "escalate") as esc:
                dispatch.refresh_pr_branch(7, 100, False)

            esc.assert_not_called()
            self.assertEqual(git(origin, "rev-parse", "agent/7"), feature)
            self.assertNotEqual(git(origin, "rev-parse", "agent/7"), main_tip)

    def test_refresh_with_nothing_ahead_of_main_escalates_instead_of_pushing(self) -> None:
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            root, origin, upstream = build_fork(tmp)

            # agent/8 adds one commit, then main lands the same change (squash).
            git(origin, "checkout", "-q", "-b", "agent/8")
            (origin / "feature.txt").write_text("feature")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "feature")
            before = git(origin, "rev-parse", "HEAD")
            git(origin, "checkout", "-q", "main")
            (origin / "feature.txt").write_text("feature")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "feature landed")

            cfg = config.Config(root=root, repo="acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)

            real_run = dispatch.run
            gh_calls: list[list[str]] = []

            def run_spy(cmd, **kw):
                if cmd[0] == "gh":
                    gh_calls.append(cmd)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return real_run(cmd, **kw)

            with mock.patch.object(dispatch, "run", side_effect=run_spy), \
                 mock.patch.object(dispatch, "run_gate") as gate, \
                 mock.patch.object(dispatch, "escalate") as esc:
                dispatch.refresh_pr_branch(8, 101, False)

            esc.assert_called_once()
            gate.assert_not_called()  # diagnosed before burning a gate run
            self.assertEqual(git(origin, "rev-parse", "agent/8"), before)
            # Pulled from merge candidacy so it escalates once, not every pass.
            self.assertIn(
                ["gh", "pr", "edit", "101", "--repo", "acme/widgets", "--remove-label", "factory-approved"],
                gh_calls,
            )

    def test_refresh_conflict_withdraws_pr_from_candidacy(self) -> None:
        # district#5: a rebase conflict escalated every pass (146 comments)
        # because `factory-approved` stayed on the PR.
        from unittest import mock

        from factory import dispatch

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            root, origin, upstream = build_fork(tmp)

            git(origin, "checkout", "-q", "-b", "agent/9")
            (origin / "shared.txt").write_text("agent version")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "agent change")
            before = git(origin, "rev-parse", "HEAD")
            git(origin, "checkout", "-q", "main")
            (origin / "shared.txt").write_text("main version")
            git(origin, "add", "-A")
            git(origin, "commit", "-q", "-m", "conflicting main change")

            cfg = config.Config(root=root, repo="acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)

            real_run = dispatch.run
            gh_calls: list[list[str]] = []

            def run_spy(cmd, **kw):
                if cmd[0] == "gh":
                    gh_calls.append(cmd)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return real_run(cmd, **kw)

            with mock.patch.object(dispatch, "run", side_effect=run_spy), \
                 mock.patch.object(dispatch, "run_gate") as gate, \
                 mock.patch.object(dispatch, "escalate") as esc:
                dispatch.refresh_pr_branch(9, 102, False)

            esc.assert_called_once()
            self.assertIn("conflicts", esc.call_args.args[1])
            gate.assert_not_called()
            self.assertEqual(git(origin, "rev-parse", "agent/9"), before)  # nothing pushed
            wt = root / ".factory" / "wt-9"
            self.assertFalse(Path(git(wt, "rev-parse", "--git-path", "rebase-merge")).exists())  # aborted
            self.assertIn(
                ["gh", "pr", "edit", "102", "--repo", "acme/widgets", "--remove-label", "factory-approved"],
                gh_calls,
            )


if __name__ == "__main__":
    unittest.main()
