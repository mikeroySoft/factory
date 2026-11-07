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

from factory import config, dispatch, lifecycle


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

    def test_protected_override_requires_label_and_nonempty_scope(self):
        self.change_config()
        for body, labels in (
            ("", {config.LABEL_PROTECTED_OVERRIDE}),
            ('factory-protected-override: []', {config.LABEL_PROTECTED_OVERRIDE}),
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
        scoped = [event for event in events if event.get("event") == "protected-override"]
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


if __name__ == "__main__":
    unittest.main()
