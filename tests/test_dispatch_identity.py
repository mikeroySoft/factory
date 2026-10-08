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
    "print('VERDICT: APPROVE')"
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
        self.assertIn("Environment=UV_EXCLUDE_NEWER=7 days\nEnvironment=GH_CONFIG_DIR", after[service])
        self.assertEqual(after[dashboard], before[dashboard])
        # Empty or absent (base) renders as before: both units carry the same environment lines.
        self.assertEqual(onboard.units(unset, "10min", "127.0.0.1"), before)
        lines = [[ln for ln in before[u].splitlines() if ln.startswith("Environment=")] for u in (service, dashboard)]
        self.assertEqual(lines[0], lines[1])
        self.assertIn("Environment=UV_EXCLUDE_NEWER=7 days", lines[0])


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


if __name__ == "__main__":
    unittest.main()
