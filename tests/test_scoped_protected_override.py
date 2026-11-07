"""Scoped human overrides must survive the real dispatcher-to-gate boundary."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path
from unittest import mock

from factory import binding, config, dispatch, lifecycle, manage


class ScopedProtectedOverrideTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {
            **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        }
        self.env.pop(lifecycle.CONTEXT_ENV, None)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Scoped Override Test")
        self.git("config", "user.email", "scope@example.invalid")
        self.git("remote", "add", "origin", "git@github.com:acme/widgets.git")
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n[gate]\n'
            'protected_paths = [".factory.toml", "**/acceptance/**", "**/*review*prompt*"]\n'
            '[leak_scan]\npattern = ""\n'
        )
        (self.repo / ".gitignore").write_text(".factory/\n")
        (self.repo / "checks/acceptance").mkdir(parents=True)
        (self.repo / "checks/acceptance/oracle.txt").write_text("original\n")
        (self.repo / "checks/review-prompt.txt").write_text("original\n")
        self.commit("base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        dispatch.configure(config.load(self.repo))
        # run_gate deliberately ignores PYTHONPATH; install this revision in an isolated runner.
        runner = self.root / "runner"
        venv.EnvBuilder(with_pip=False).create(runner)
        self.python = runner / "bin/python"
        purelib = subprocess.run(
            [str(self.python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            check=True, capture_output=True, text=True, env=self.env,
        ).stdout.strip()
        shutil.copytree(Path(dispatch.__file__).parent, Path(purelib) / "factory")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def run_gate(self, body, labels=None):
        if labels is None:
            labels = {config.LABEL_PROTECTED_OVERRIDE}
        flags = dispatch.gate_flags(None, labels, body)
        with mock.patch.object(dispatch.sys, "executable", str(self.python)), \
                mock.patch.dict(os.environ, self.env, clear=True):
            return dispatch.run_gate(self.repo, 622, **flags)

    def change_config(self):
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text() + "\n# authorized change\n")
        self.commit("change config")

    def test_protected_override_limits_dispatcher_gate_to_named_paths(self):
        self.change_config()
        body = 'factory-protected-override: [".factory.toml"]'
        passed, report = self.run_gate(body)
        self.assertTrue(passed, report)
        self.assertNotIn("- protected-paths: FAIL", report)
        for path in ("checks/acceptance/oracle.txt", "checks/review-prompt.txt"):
            with self.subTest(path=path):
                (self.repo / path).write_text("unauthorized\n")
                self.commit("change another protected path")
                passed, report = self.run_gate(body)
                self.assertFalse(passed, report)
                self.assertIn("- protected-paths: FAIL", report)
                self.assertIn(path, report)
                self.assertNotIn("diff --git a/.factory.toml", report)

    def test_crlf_protected_override_limits_dispatcher_gate_to_named_paths(self):
        self.change_config()
        body = 'Human scope\r\nfactory-protected-override: [".factory.toml"]\r\nTask\r\n'
        passed, report = self.run_gate(body)
        self.assertTrue(passed, report)
        (self.repo / "checks/acceptance/oracle.txt").write_text("unauthorized\n")
        self.commit("change another protected path")
        passed, report = self.run_gate(body)
        self.assertFalse(passed, report)
        self.assertIn("- protected-paths: FAIL", report)
        self.assertIn("checks/acceptance/oracle.txt", report)
        self.assertNotIn("diff --git a/.factory.toml", report)

    def test_protected_override_requires_label_and_nonempty_scope(self):
        self.change_config()
        for body, labels in (
            ("", {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: []', {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: [".factory.toml", null]', {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: [".factory.toml", ""]', {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: [".factory.toml", "../*"]', {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: .factory.toml', {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: [".factory.toml"]', set()),
            ('factory-protected-override: [".factory.toml"]', {config.LABEL_ADD_ONLY_OVERRIDE}),
        ):
            with self.subTest(body=body, labels=labels):
                passed, report = self.run_gate(body, labels)
                self.assertFalse(passed, report)
                self.assertIn(".factory.toml", report)

    def test_protected_override_scope_is_in_journal_and_escalation(self):
        self.change_config()
        (self.repo / "checks/review-prompt.txt").write_text("unauthorized\n")
        self.commit("out of scope")
        passed, report = self.run_gate('factory-protected-override: [".factory.toml"]')
        self.assertFalse(passed, report)
        events = lifecycle.read_events(self.repo / ".factory/events.jsonl")
        scoped = [event for event in events if event.get("kind") == "protected-override"]
        self.assertTrue(scoped, events)
        self.assertEqual(scoped[-1]["paths"], [".factory.toml"])
        self.assertEqual(scoped[-1]["outside_scope"], ["checks/review-prompt.txt"])
        packet, _ = dispatch.escalation_packet(622, "worker edited protected paths", None, self.repo)
        text = packet.read_text()
        self.assertIn('factory-protected-override: [".factory.toml"]', text)
        self.assertIn("checks/review-prompt.txt", text)

    def test_protected_override_glob_does_not_authorize_sibling_paths(self):
        (self.repo / "checks/acceptance/oracle.txt").write_text("authorized\n")
        self.commit("authorized acceptance change")
        passed, report = self.run_gate('factory-protected-override: ["**/acceptance/**"]')
        self.assertTrue(passed, report)
        (self.repo / "checks/review-prompt.txt").write_text("unauthorized\n")
        self.commit("out of scope")
        passed, report = self.run_gate('factory-protected-override: ["**/acceptance/**"]')
        self.assertFalse(passed, report)
        self.assertIn("checks/review-prompt.txt", report)

    def test_protected_override_keeps_existing_test_protection_outside_scope(self):
        path = self.repo / "tests/test_existing.py"
        path.parent.mkdir()
        path.write_text("def test_existing():\n    assert True\n")
        self.commit("existing test")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.change_config()
        path.write_text("def test_existing():\n    assert False\n")
        self.commit("change existing assertion")
        labels = {"bug", config.LABEL_PROTECTED_OVERRIDE}
        body = ('No repro test: existing-test protection fixture\n'
                'factory-protected-override: [".factory.toml"]')
        passed, report = self.run_gate(body, labels)
        self.assertFalse(passed, report)
        self.assertIn("- protected-tests: FAIL", report)
        self.assertIn("tests/test_existing.py", report)
        body = ('No repro test: existing-test protection fixture\n'
                'factory-protected-override: [".factory.toml", "tests/test_existing.py"]')
        passed, report = self.run_gate(body, labels)
        self.assertTrue(passed, report)

    def test_protected_override_refresh_conflicts_check_each_named_path(self):
        self.git("checkout", "-qb", "agent/622")
        path = self.repo / "checks/acceptance/oracle.txt"
        path.write_text("worker\n")
        self.commit("worker conflict")
        self.git("update-ref", "refs/remotes/origin/agent/622", "HEAD")
        self.git("checkout", "-q", "main")
        path.write_text("main\n")
        self.commit("main conflict")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-q", "agent/622")
        real_run = dispatch.run

        def run(command, *args, **kwargs):
            if command[0] == "gh" or command == ["git", "fetch", "origin"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            return real_run(command, *args, **kwargs)

        for scope, blocked in ((".factory.toml", True), ("**/acceptance/**", False)):
            with self.subTest(scope=scope):
                issue = {
                    "state": "OPEN", "labels": [{"name": config.LABEL_PROTECTED_OVERRIDE}],
                    "body": f'factory-protected-override: ["{scope}"]',
                }
                with mock.patch.object(dispatch, "run", side_effect=run), \
                        mock.patch.object(dispatch, "ensure_worktree", return_value=self.repo), \
                        mock.patch.object(dispatch, "gh_json", side_effect=lambda command, issue=issue: (
                            {"baseRefName": "main"} if command[0] == "pr" else issue
                        )), mock.patch.object(dispatch, "escalate") as escalate:
                    self.assertFalse(dispatch.refresh_pr_branch(622, 700, False))
                events = lifecycle.read_events(dispatch.EVENTS)
                scoped = [event for event in events if event.get("event") == "protected-override"][-1]
                self.assertEqual(scoped["paths"], [scope])
                self.assertEqual(scoped["outside_scope"], ["checks/acceptance/oracle.txt"] if blocked else [])
                self.assertEqual(escalate.call_count, int(blocked))
                if blocked:
                    self.assertIn("checks/acceptance/oracle.txt", escalate.call_args.kwargs["extra"])
                else:
                    self.assertEqual(events[-1]["event"], "conflict-requeue")



class ManagerProtectedOverrideTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        dispatch.configure(config.Config(self.root, "acme/widgets"))
        self.scope = 'factory-protected-override: [".factory.toml"]'

    def test_rewrite_cannot_change_human_protected_scope(self):
        for current, replacement in (
            (self.scope, 'factory-protected-override: ["**"]'),
            (self.scope, self.scope[:-1] + ', "**/acceptance/**"]'),
            (self.scope, "Scope removed"),
            ("No scope", self.scope),
            ("factory-protected-override:", self.scope),
            (self.scope, 'FACTORY-PROTECTED-OVERRIDE: ["**"]\n' + self.scope),
        ):
            with self.subTest(current=current, replacement=replacement), \
                    mock.patch.object(dispatch, "gh_json", return_value={"body": current}), \
                    mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess(
                        [], 0, "", "",
                    )) as run:
                with self.assertRaisesRegex(binding.BindingError, "preserve.*factory-protected-override"):
                    manage.apply(
                        622, {"body": replacement}, "REWRITE", replacement, None,
                        self.root / "packet.md",
                    )
                run.assert_not_called()

    def test_rewrite_preserves_live_scope_while_changing_prose(self):
        current = "Human scope\n" + self.scope
        replacement = "Refined task\n" + self.scope
        with mock.patch.object(dispatch, "gh_json", return_value={"body": current}), \
                mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess(
                    [], 0, "https://github.com/acme/widgets/issues/622#issuecomment-701", "",
                )) as run:
            manage.apply(
                622, {"body": "Stale body without scope"}, "REWRITE", replacement, None,
                self.root / "packet.md",
            )
        edits = [call.args[0] for call in run.call_args_list if "--body" in call.args[0]]
        self.assertEqual(edits[1][-2:], ["--body", replacement])
        self.assertEqual(
            dispatch.gate_flags(None, {config.LABEL_PROTECTED_OVERRIDE}, replacement),
            {"protected_override": False, "protected_override_paths": [".factory.toml"]},
        )

    def test_rewrite_line_endings_cannot_change_effective_protected_scope(self):
        wildcard = 'factory-protected-override: ["**"]'
        expected = {"protected_override": False, "protected_override_paths": [".factory.toml"]}
        for narrow_end, wildcard_end in (("\n", "\r\n"), ("\r\n", "\n")):
            current = self.scope + narrow_end + wildcard + wildcard_end + "Task\n"
            replacement = self.scope + wildcard_end + wildcard + narrow_end + "Refined task\n"
            with self.subTest(narrow_end=narrow_end), \
                    mock.patch.object(dispatch, "gh_json", return_value={"body": current}), \
                    mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess(
                        [], 0, "https://github.com/acme/widgets/issues/622#issuecomment-701", "",
                    )) as run:
                manage.apply(
                    622, {"body": current}, "REWRITE", replacement, None,
                    self.root / "packet.md",
                )
                edits = [call.args[0] for call in run.call_args_list if "--body" in call.args[0]]
                self.assertEqual(edits[1][-2:], ["--body", replacement])
                for body in (current, replacement):
                    self.assertEqual(
                        dispatch.gate_flags(None, {config.LABEL_PROTECTED_OVERRIDE}, body),
                        expected,
                    )


if __name__ == "__main__":
    unittest.main()
