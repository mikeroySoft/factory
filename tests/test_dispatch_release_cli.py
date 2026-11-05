"""Run actual dispatch and filing commands against a local GitHub stand-in."""
from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as fixtures


class DispatchReleaseCLI(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config
    cli = fixtures.LocalCLI.cli

    def install_ownership(self):
        source = fixtures.GH
        source = source.replace('if s.get("sync") and not s.get("merged") else "[]"',
                                'if not s.get("merged") else "[]"')
        (self.bin / "gh").write_text(source)

    def test_approved_exit_zero_releases_and_next_pass_does_not_run_worker(self):
        self.install_ownership()
        self.state.write_text(json.dumps({"reviews": 1}))
        result = self.cli("dispatch", "--ticket", "7")
        state = json.loads(self.state.read_text())
        self.assertEqual(state["assignees"], [])
        self.assertTrue(state.get("pr"), result.stdout + result.stderr)
        wt = self.repo / ".factory" / "wt-7"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, env=self.env,
                              check=True, capture_output=True, text=True).stdout
        first = lifecycle.read_events(self.events)
        cleared = [r for r in first if r.get("event") == "assignment-cleared"]
        self.assertEqual([(r["ticket"], r["login"], r["exit_path"]) for r in cleared],
                         [(7, "factory-bot", "done")])
        approved = [r for r in first if r.get("event") == "approved"]
        self.assertEqual([(r["pr"], r["hold"]) for r in approved],
                         [(70, "approved, awaiting merge")])
        result = self.cli("dispatch")
        self.assertIn("open agent PR #70 (approved, awaiting merge)", result.stdout)
        self.assertEqual(len([r for r in lifecycle.read_events(self.events)
                              if r.get("event") == "claimed"]), 1)
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, env=self.env,
                                        check=True, capture_output=True, text=True).stdout, head)

    def test_newly_filed_sync_issue_has_no_assignee(self):
        self.install_ownership()

        module_config = config.load(self.repo)
        module_config.factory.mkdir(exist_ok=True)
        with mock.patch.multiple(dispatch, create=True, cfg=module_config,
                                 FACTORY=module_config.factory, REPO=module_config.repo,
                                 UPSTREAM_REPO="local/upstream"), \
                mock.patch.dict("os.environ", self.env):
            number = dispatch.sync_issue("a" * 40, "1", "is staged", "local evidence")
        self.assertEqual(number, 7)
        self.assertEqual(json.loads(self.state.read_text())["filed_assignees"], [])


if __name__ == "__main__":
    unittest.main()
