"""Expected branch misses must not obscure real GitHub failures (#873)."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import dispatch, handoff, plan


class MissingPrTest(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(repo="acme/widgets")
        self.route = {"status": "owned", "owner": "alice", "candidates": ["alice"]}
        self.logs = self.enterContext(mock.patch.object(dispatch, "log"))
        self.enterContext(mock.patch.object(plan.Reader, "route", self.route_ticket))
        self.paths = []

    def route_ticket(self, n, reason, paths):
        self.paths.append(paths)
        # Reader is constructed by observe; retain the result through __init__ below.
        self.result["route"] = self.route

    def reader(self, cfg, result):
        self.result = result

    def observe(self, stderr=None, code=1, pr=None):
        def command(cmd, **kwargs):
            if stderr is not None:
                raise subprocess.CalledProcessError(code, cmd, stderr=stderr)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(pr), "")
        with mock.patch.object(plan.Reader, "__init__", self.reader), mock.patch.object(dispatch, "_run", side_effect=lambda cmd, *a: command(cmd)):
            return handoff.observe(self.cfg, 7, {})

    def test_observe_missing_pr_is_quiet(self):
        result = self.observe('no pull requests found for branch "agent/7"\n')
        self.assertIsNone(result["pr_url"])
        self.assertEqual(self.paths, [[]])
        self.logs.assert_not_called()

    def test_real_failures_still_log(self):
        for stderr, code in [("authentication failed", 1), ("network unavailable", 1), ('no pull requests found for branch "agent/7"\nadditional failure', 1), ('no pull requests found for branch "agent/7"', 2), ('no pull requests found for branch "agent/8"', 1)]:
            with self.subTest(stderr=stderr, code=code):
                self.logs.reset_mock()
                self.observe(stderr, code)
                self.assertIn(f"command failed (exit {code}); stderr tail:", self.logs.call_args.args[0])

    def test_signal_exit_with_missing_pr_stderr_still_logs(self):
        self.observe('no pull requests found for branch "agent/7"', code=-1)
        self.assertIn("command failed (exit -1); stderr tail:", self.logs.call_args.args[0])

    def test_existing_pr_routes_files(self):
        result = self.observe(pr={"url": "https://github.com/acme/widgets/pull/9", "files": [{"path": "src/a.py"}]})
        self.assertEqual(result["pr_url"], "https://github.com/acme/widgets/pull/9")
        self.assertEqual(self.paths, [["src/a.py"]])

    def test_parked_receipts_do_not_post_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = SimpleNamespace(repo=self.cfg.repo, factory=root)
            rows = []
            for n in (7, 8, 9):
                rows.extend([{"event": "escalate", "ticket": n, "packet": "packet", "round": 1}, {"event": "comment", "ticket": n, "kind": "handoff", "request": f"{n}/1", "target": "@alice"}])
            def command(cmd, *args):
                if cmd[1:3] == ["issue", "list"]:
                    return subprocess.CompletedProcess(cmd, 0, json.dumps([{"number": n} for n in (7, 8, 9)]), "")
                branch = cmd[3]
                raise subprocess.CalledProcessError(1, cmd, stderr=f'no pull requests found for branch "{branch}"\n')
            with mock.patch.object(dispatch, "FACTORY", root, create=True), mock.patch.object(dispatch, "cfg", cfg, create=True), mock.patch.object(dispatch, "EVENTS", root / "events", create=True), mock.patch.object(dispatch, "_run", side_effect=command), mock.patch.object(dispatch, "initiative_kind", return_value=None), mock.patch.object(handoff.lifecycle, "read_events", return_value=rows), mock.patch.object(handoff, "terminal", return_value="human"), mock.patch.object(plan.Reader, "__init__", self.reader), mock.patch.object(handoff, "compose") as compose:
                handoff.handoff_pass()
            compose.assert_not_called()
            self.assertEqual(len(self.paths), 3)
            self.assertFalse(any("command failed" in call.args[0] for call in self.logs.call_args_list))


class LookupScopeTest(unittest.TestCase):
    def test_required_lookup_still_logs_after_optional_lookup(self):
        cmd = ["gh", "pr", "view", "agent/7"]
        error = subprocess.CalledProcessError(1, cmd, stderr='no pull requests found for branch "agent/7"')
        with mock.patch.object(dispatch, "_run", side_effect=error), mock.patch.object(dispatch, "log") as log:
            with dispatch.optional_pr_lookup("agent/7"), self.assertRaises(subprocess.CalledProcessError):
                dispatch.run(cmd)
            log.assert_not_called()
            with self.assertRaises(subprocess.CalledProcessError):
                dispatch.run(cmd)
            self.assertIn("command failed (exit 1)", log.call_args.args[0])

    def test_different_command_inside_scope_still_logs(self):
        cmd = ["gh", "issue", "view", "agent/7"]
        error = subprocess.CalledProcessError(1, cmd, stderr='no pull requests found for branch "agent/7"')
        with mock.patch.object(dispatch, "_run", side_effect=error), mock.patch.object(dispatch, "log") as log:
            with dispatch.optional_pr_lookup("agent/7"), self.assertRaises(subprocess.CalledProcessError):
                dispatch.run(cmd)
            self.assertIn("command failed (exit 1)", log.call_args.args[0])

    def test_run_view_inside_scope_still_logs_and_raises(self):
        cmd = ["gh", "run", "view", "agent/7"]
        error = subprocess.CalledProcessError(1, cmd, stderr='no pull requests found for branch "agent/7"')
        with mock.patch.object(dispatch, "_run", side_effect=error), mock.patch.object(dispatch, "log") as log:
            with dispatch.optional_pr_lookup("agent/7"), self.assertRaises(subprocess.CalledProcessError) as raised:
                dispatch.run(cmd)
            self.assertIs(raised.exception, error)
            self.assertIn("command failed (exit 1); stderr tail:", log.call_args.args[0])


class ManagerLookupTest(unittest.TestCase):
    def test_expected_miss_is_quiet_but_other_failures_refuse(self):
        from factory import config, manage

        for decision in ("CLOSE", "RESOLVE"):
            for stderr, code, expected in [
                ('no pull requests found for branch "agent/7"', 1, True),
                ("authentication failed", 1, False),
                ('no pull requests found for branch "agent/7"', 2, False),
                ('no pull requests found for branch "agent/8"', 1, False),
            ]:
                with self.subTest(decision=decision, stderr=stderr, code=code), tempfile.TemporaryDirectory() as tmp:
                    cfg = config.Config(Path(tmp), "acme/widgets")
                    commands = []

                    def command(cmd, *args, commands=commands, code=code, stderr=stderr):
                        commands.append(cmd)
                        if cmd[1:3] == ["pr", "view"]:
                            raise subprocess.CalledProcessError(code, cmd, stderr=stderr)
                        if cmd[1] == "api":
                            return subprocess.CompletedProcess(cmd, 0, '{"ahead_by": 0}', "")
                        return subprocess.CompletedProcess(cmd, 0, "https://example.test/comment", "")

                    with mock.patch.object(dispatch, "cfg", cfg, create=True), mock.patch.object(dispatch, "REPO", cfg.repo, create=True), mock.patch.object(dispatch, "EVENTS", Path(tmp) / "events", create=True), mock.patch.object(dispatch, "_run", side_effect=command), mock.patch.object(dispatch, "log") as log, mock.patch.object(dispatch, "comment_receipt"), mock.patch.object(dispatch, "record"):
                        if expected:
                            manage.apply(7, {"title": "done"}, decision, "Already shipped.", None, Path("unused"))
                            log.assert_not_called()
                            self.assertTrue(any(cmd[1:3] == ["issue", "comment"] for cmd in commands))
                        else:
                            with self.assertRaises(subprocess.CalledProcessError):
                                manage.apply(7, {"title": "done"}, decision, "Already shipped.", None, Path("unused"))
                            self.assertEqual(len(commands), 1)
                            self.assertIn(f"command failed (exit {code})", log.call_args.args[0])
