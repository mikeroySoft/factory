"""Machine-user dispatcher identity (#198): unit env, agent credentials, opt-in, doctor."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, dispatch, onboard

# Records what a child sees: GitHub token variables, GH_CONFIG_DIR and its contents.
PROBE = (
    "import json, os, sys; d = os.environ.get('GH_CONFIG_DIR'); "
    "json.dump({'tokens': [k for k in ('GH_TOKEN', 'GITHUB_TOKEN') if k in os.environ], "
    "'dir': d, 'entries': os.listdir(d) if d and os.path.isdir(d) else None}, open(sys.argv[1], 'w')); "
    "print('```json\\n{\"verdict\":\"APPROVE\",\"findings\":[]}\\n```\\nVERDICT: APPROVE')"
)
DISPATCHER_ENV = {"GH_TOKEN": "t1", "GITHUB_TOKEN": "t2", "GH_CONFIG_DIR": "/machine/gh"}


def make_cfg(root: Path, **install) -> config.Config:
    (root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n')
    cfg = config.Config(root, "acme/widgets", signoff=False)
    cfg.install = config.merge(config.DEFAULT_INSTALL, install)
    return cfg


class UnitEnvTest(unittest.TestCase):
    def test_dispatch_env_renders_only_into_dispatcher_unit(self) -> None:
        env = {"UV_EXCLUDE_NEWER": "7 days"}
        with tempfile.TemporaryDirectory() as d:
            base = make_cfg(Path(d), env=env)
            machine = make_cfg(Path(d), env=env,
                               dispatch_env={"GH_CONFIG_DIR": "/machine/gh", "GIT_AUTHOR_NAME": "factory-bot"})
            unset = make_cfg(Path(d), env=env, dispatch_env={})
        before, after = onboard.units(base, "10min", "127.0.0.1"), onboard.units(machine, "10min", "127.0.0.1")
        service, dashboard = "factory-widgets.service", "factory-widgets-dashboard.service"
        for line in ("Environment=GH_CONFIG_DIR=/machine/gh\n", "Environment=GIT_AUTHOR_NAME=factory-bot\n"):
            self.assertIn(line, after[service])
            self.assertNotIn(line, after[dashboard])
        # [install].env stays in both; dispatch_env comes after it so it wins.
        self.assertIn('Environment="UV_EXCLUDE_NEWER=7 days"\nEnvironment=GH_CONFIG_DIR', after[service])
        self.assertEqual(after[dashboard], before[dashboard])
        # Empty or absent (base) renders as before: both units carry the same environment lines.
        self.assertEqual(onboard.units(unset, "10min", "127.0.0.1"), before)
        lines = [[ln for ln in before[u].splitlines() if ln.startswith("Environment=")] for u in (service, dashboard)]
        self.assertEqual(lines[0], lines[1])
        self.assertIn('Environment="UV_EXCLUDE_NEWER=7 days"', lines[0])


class AgentCredentialTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.cfg = make_cfg(self.tmp)
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        environ = mock.patch.dict(os.environ, DISPATCHER_ENV)
        environ.start()
        self.addCleanup(environ.stop)

    def assert_no_github(self, seen: dict) -> None:
        self.assertEqual(seen["tokens"], [])
        self.assertNotEqual(seen["dir"], DISPATCHER_ENV["GH_CONFIG_DIR"])
        self.assertEqual(seen["entries"], [])
        self.assertFalse(Path(seen["dir"]).exists())  # removed after the agent exits

    def test_worker_gets_no_github_credential(self) -> None:
        out, wt = self.tmp / "seen.json", self.tmp / "wt"
        wt.mkdir()
        code, fired = dispatch.run_worker([sys.executable, "-c", PROBE, str(out)], wt,
                                          self.tmp / "worker.log", wt / "handoff.md")
        self.assertEqual((code, fired), (0, None))
        self.assert_no_github(json.loads(out.read_text()))

    def test_reviewer_gets_no_github_credential(self) -> None:
        out = self.tmp / "seen.json"
        self.cfg.reviewer = [sys.executable, "-c", PROBE, str(out)]
        real_run = dispatch.run

        def run(cmd, **kwargs):
            if cmd[0] != "gh":
                return real_run(cmd, **kwargs)
            return subprocess.CompletedProcess(cmd, 0, "diff", "")

        with mock.patch.object(dispatch, "run", side_effect=run):
            dispatch.review_external_pr(17, "base", "head-1")
        self.assert_no_github(json.loads(out.read_text()))

    def test_dispatcher_commands_keep_configured_identity(self) -> None:
        code = "import os; print(os.environ.get('GH_CONFIG_DIR'), os.environ.get('GH_TOKEN'))"
        self.assertEqual(dispatch.run([sys.executable, "-c", code]).stdout.split(), ["/machine/gh", "t1"])


class ReviewRequestOptInTest(unittest.TestCase):
    def test_requests_to_dispatcher_or_human_login_opt_in(self) -> None:
        def pr(n, login):
            return {"number": n, "state": "OPEN", "isDraft": False, "headRefOid": f"head-{n}",
                    "baseRefOid": "base", "labels": [], "reviewRequests": [{"login": login}]}

        prs = [pr(1, "mikeroySoft"), pr(2, "Factory-Bot"), pr(3, "someone-else")]

        def github(args, env=None):
            if args == ["api", "user"]:
                # Under the dashboard's environment gh sees the human's store.
                human = env is not None and "GH_CONFIG_DIR" not in env
                return {"login": "mikeroysoft" if human else "factory-bot"}
            return prs

        with tempfile.TemporaryDirectory() as d:
            dispatch.configure(make_cfg(Path(d), dispatch_env={"GH_CONFIG_DIR": "/machine/gh"}))
            logs: list[str] = []
            with mock.patch.dict(os.environ, {"GH_CONFIG_DIR": "/machine/gh"}), \
                    mock.patch.object(dispatch, "gh_json", side_effect=github), \
                    mock.patch.object(dispatch, "log", side_effect=logs.append):
                dispatch.review_intake_pass(True)
        self.assertEqual([m.split()[1] for m in logs if "review intake" in m], ["#1:", "#2:"])


class DoctorIdentityTest(unittest.TestCase):
    def doctor(self, logins: dict[str, str], **install) -> tuple[int, list[dict], int]:
        queried: list[dict] = []

        def login(env):
            queried.append(env)
            return logins[env.get("GH_CONFIG_DIR", "human")]

        def command(cmd, cwd=None):
            out = {"view": "ADMIN\n", "list": json.dumps(list(config.LABELS))}.get(cmd[2] if cmd[0] == "gh" else "", "")
            return subprocess.CompletedProcess(cmd, 0, out, "")

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d), **install)
            with mock.patch.object(onboard.config, "load", return_value=cfg), \
                    mock.patch.object(onboard.config, "host_config", return_value={}), \
                    mock.patch.object(onboard, "sh", side_effect=command), \
                    mock.patch.object(onboard, "gh_login", side_effect=login), \
                    mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "")), \
                    mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                    mock.patch.object(onboard.urllib.request, "urlopen", return_value=mock.MagicMock()), \
                    mock.patch.dict(os.environ), redirect_stdout(output):
                os.environ.pop("GH_CONFIG_DIR", None)
                code = onboard.doctor(["--json"])
        rows = [r for r in json.loads(output.getvalue())["rows"] if r["label"] == "GitHub identities"]
        return code, rows, len(queried)

    def test_fails_when_logins_equal_passes_when_they_differ(self) -> None:
        machine = {"dispatch_env": {"GH_CONFIG_DIR": "/machine/gh"}}
        code, rows, _ = self.doctor({"/machine/gh": "factory-bot", "human": "mikeroySoft"}, **machine)
        self.assertEqual((code, rows), (0, [{"status": "PASS", "label": "GitHub identities",
                                             "detail": "dispatcher factory-bot, dashboard mikeroySoft"}]))
        code, rows, _ = self.doctor({"/machine/gh": "MikeroySoft", "human": "mikeroySoft"}, **machine)
        self.assertEqual((code, rows[0]["status"]), (1, "FAIL"))
        self.assertIn("dispatcher MikeroySoft, dashboard mikeroySoft", rows[0]["detail"])

    def test_silent_when_dispatch_env_unset(self) -> None:
        self.assertEqual(self.doctor({}), (0, [], 0))


class AgentPushLeaseTest(unittest.TestCase):
    """The dispatcher, the only pusher, force-pushes `agent/<n>` against the sha it last fetched."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        tmp = Path(temporary.name)
        identity = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
                    for what, value in (("NAME", "t"), ("EMAIL", "t@example.invalid"))}
        environ = mock.patch.dict(os.environ, identity)
        environ.start()
        self.addCleanup(environ.stop)
        dispatch.configure(make_cfg(tmp))
        dispatch.FACTORY.mkdir()
        self.origin, self.wt, self.other = tmp / "origin.git", tmp / "wt", tmp / "other"
        self.git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(tmp, "clone", "-q", str(self.origin), str(self.wt))
        self.commit(self.wt, "base")
        self.git(self.wt, "push", "-q", "origin", "HEAD:main")
        self.git(self.wt, "checkout", "-q", "-b", "agent/7")
        self.commit(self.wt, "work")
        self.git(self.wt, "push", "-q", "origin", "agent/7")  # the dispatcher's earlier push
        self.fetched, self.main = self.remote("agent/7"), self.remote("main")
        self.git(tmp, "clone", "-q", "-b", "agent/7", str(self.origin), str(self.other))

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def remote(self, ref: str) -> str:
        return self.git(self.origin, "rev-parse", ref)

    def commit(self, repo: Path, message: str, *flags: str) -> str:
        self.git(repo, "commit", "-q", "--allow-empty", *flags, "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def someone_else_pushes(self) -> str:
        self.commit(self.other, "someone else's push")
        self.git(self.other, "push", "-q", "origin", "agent/7")
        return self.remote("agent/7")

    def test_rewritten_worker_history_lands(self) -> None:
        rebased = self.commit(self.wt, "work, rebased", "--amend")  # not a descendant of the remote head
        self.assertEqual(dispatch.push_agent(self.wt, 7, self.fetched), rebased)
        self.assertEqual(self.remote("agent/7"), rebased)

    def test_remote_moved_since_fetch_is_refused_not_overwritten(self) -> None:
        moved = self.someone_else_pushes()
        self.git(self.wt, "fetch", "-q", "origin")  # tracking ref now matches; a bare lease would clobber
        self.commit(self.wt, "work, rebased", "--amend")
        self.assertIsNone(dispatch.push_agent(self.wt, 7, self.fetched, check=False))
        with self.assertRaises(subprocess.CalledProcessError):
            dispatch.push_agent(self.wt, 7, self.fetched)
        self.assertEqual(self.remote("agent/7"), moved)

    def test_lease_is_the_unchanged_remote_head_missing_from_worktree_history(self) -> None:
        self.git(self.wt, "checkout", "-q", "--orphan", "stale")
        self.commit(self.wt, "stale local head")
        lease = dispatch.agent_lease(self.wt, 7)
        worker_head = self.commit(self.wt, "worker history rewrite", "--amend")
        self.assertEqual(lease, self.fetched)
        self.assertEqual(dispatch.push_agent(self.wt, 7, lease), worker_head)
        self.assertEqual(self.remote("agent/7"), worker_head)

    def test_stale_worktree_rebased_onto_unchanged_remote_lands(self) -> None:
        self.git(self.wt, "checkout", "-q", "main")
        lease = dispatch.agent_lease(self.wt, 7)
        self.git(self.wt, "checkout", "-q", "--detach", lease)
        worker_head = self.commit(self.wt, "worker rebased onto remote")
        self.assertEqual(dispatch.push_agent(self.wt, 7, lease), worker_head)
        self.assertEqual(self.remote("agent/7"), worker_head)

    def test_refusal_names_leased_and_current_remote_heads(self) -> None:
        moved = self.someone_else_pushes()
        with self.assertRaises(dispatch.LeaseRefused) as raised:
            dispatch.push_agent(self.wt, 7, self.fetched)
        self.assertIn(self.fetched[:12], str(raised.exception))
        self.assertIn(moved[:12], str(raised.exception))

    def test_refusal_survives_diagnostic_remote_lookup_failure(self) -> None:
        self.someone_else_pushes()
        real_run = dispatch.run

        def run(command, *args, **kwargs):
            if command[:2] == ["git", "ls-remote"]:
                if kwargs.get("check", True):
                    raise subprocess.CalledProcessError(2, command, "", "lookup failed")
                return subprocess.CompletedProcess(command, 2, "", "lookup failed")
            return real_run(command, *args, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run), \
                self.assertRaises(dispatch.LeaseRefused) as raised:
            dispatch.push_agent(self.wt, 7, self.fetched)
        self.assertIn("remote now (unavailable)", str(raised.exception))

    def test_stale_linear_refusal_reports_the_unchanged_remote(self) -> None:
        self.git(self.wt, "checkout", "-q", "--orphan", "stale")
        self.commit(self.wt, "stale local head")
        lease = dispatch.agent_lease(self.wt, 7)
        self.commit(self.wt, "linear worker commit")

        copied_lease = lease.encode().decode()
        self.assertIsNot(copied_lease, lease)
        with self.assertRaises(dispatch.LeaseRefused) as raised:
            dispatch.push_agent(self.wt, 7, copied_lease)
        self.assertEqual(raised.exception.returncode, 1)
        self.assertIn(f"leased {lease}, remote now {lease}", str(raised.exception))
        self.assertEqual(self.remote("agent/7"), lease)

    def test_failed_initial_remote_lookup_aborts_lease_acquisition(self) -> None:
        self.git(self.wt, "remote", "set-url", "origin", str(self.origin / "missing"))
        with self.assertRaises(subprocess.CalledProcessError):
            dispatch.agent_lease(self.wt, 7)
        self.assertEqual(self.remote("agent/7"), self.fetched)

    def test_stale_linear_refusal_survives_diagnostic_lookup_failure(self) -> None:
        self.git(self.wt, "checkout", "-q", "--orphan", "stale")
        self.commit(self.wt, "stale local head")
        lease = dispatch.agent_lease(self.wt, 7)
        self.commit(self.wt, "linear worker commit")
        self.git(self.wt, "remote", "set-url", "origin", str(self.origin / "missing"))

        with self.assertRaises(dispatch.LeaseRefused) as raised:
            dispatch.push_agent(self.wt, 7, lease)
        self.assertIn(f"leased {lease}, remote now (unavailable)", str(raised.exception))
        self.assertEqual(self.remote("agent/7"), lease)

    def test_absent_remote_then_successive_pushes_use_returned_leases(self) -> None:
        lease = dispatch.agent_lease(self.wt, 8)
        self.assertEqual(lease, "")
        first = self.commit(self.wt, "first worker commit")
        lease = dispatch.push_agent(self.wt, 8, lease)
        self.assertEqual(lease, first)
        self.assertEqual(self.remote("agent/8"), first)

        second = self.commit(self.wt, "rewritten after review", "--amend")
        lease = dispatch.push_agent(self.wt, 8, lease)
        self.assertEqual(lease, second)
        self.assertEqual(self.remote("agent/8"), second)

        self.git(self.other, "fetch", "-q", "origin")
        self.git(self.other, "checkout", "-q", "--detach", "origin/agent/8")
        moved = self.commit(self.other, "concurrent push after review")
        self.git(self.other, "push", "-q", "origin", "HEAD:agent/8")
        self.commit(self.wt, "another rewrite", "--amend")
        with self.assertRaises(dispatch.LeaseRefused) as raised:
            dispatch.push_agent(self.wt, 8, lease)
        self.assertIn(f"leased {lease}, remote now {moved}", str(raised.exception))
        self.assertEqual(self.remote("agent/8"), moved)

    def test_lease_taken_before_the_worker_never_admits_an_unseen_push(self) -> None:
        self.assertEqual(dispatch.agent_lease(self.wt, 7), self.fetched)
        self.assertEqual(dispatch.agent_lease(self.wt, 8), "")  # not on the remote: must not exist
        moved = self.someone_else_pushes()  # e.g. a human fix on a kept, now stale worktree
        lease = dispatch.agent_lease(self.wt, 7)
        self.commit(self.wt, "worker commit")
        self.assertIsNone(dispatch.push_agent(self.wt, 7, lease, check=False))
        self.assertEqual(self.remote("agent/7"), moved)

    def test_force_push_only_targets_agent_branch(self) -> None:
        real_run, pushes = dispatch.run, []

        def run(cmd, *args, **kwargs):
            if cmd[0] == "gh":
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/acme/widgets/pull/1\n", "")
            if cmd[:2] == ["git", "push"]:
                pushes.append(cmd)
            return real_run(cmd, *args, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "gh_json", return_value=[]):
            rebased = self.commit(self.wt, "work, rebased", "--amend")
            self.assertTrue(dispatch.push_and_pr(self.wt, "agent/7", "t", "b", ticket=7, lease=self.fetched))
            self.assertEqual(self.remote("agent/7"), rebased)
            # A non-ticket branch is pushed plainly: a rewrite of it is rejected, not forced.
            self.git(self.wt, "checkout", "-q", "-b", "agent/curate-x")
            self.git(self.wt, "push", "-q", "origin", "agent/curate-x")
            curated = self.remote("agent/curate-x")
            self.commit(self.wt, "curate, rewritten", "--amend")
            with self.assertRaises(subprocess.CalledProcessError):
                dispatch.push_and_pr(self.wt, "agent/curate-x", "t", "b")
        self.assertEqual((self.remote("agent/curate-x"), self.remote("main")), (curated, self.main))
        forced = [cmd for cmd in pushes if any(arg.startswith("--force") for arg in cmd)]
        self.assertEqual(forced, [["git", "push", f"--force-with-lease=agent/7:{self.fetched}",
                                   "origin", f"{rebased}:refs/heads/agent/7"]])


if __name__ == "__main__":
    unittest.main()
