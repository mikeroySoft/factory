"""Real CLI regression for assignment release and open-PR admission holds."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from contextlib import chdir
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as support


class DispatchReleaseCLI(unittest.TestCase):
    setUp = support.LocalCLI.setUp
    git = support.LocalCLI.git
    write_config = support.LocalCLI.write_config
    cli = support.LocalCLI.cli

    def test_sync_issue_files_an_unassigned_issue(self):
        with chdir(self.repo), mock.patch.dict(os.environ, {**self.env, lifecycle.CONTEXT_ENV: ""}):
            dispatch.configure(config.load(self.repo))
            dispatch.FACTORY.mkdir(exist_ok=True)
            self.assertEqual(dispatch.sync_issue("a" * 40, "1", "is staged", "evidence"), 7)
            created = dispatch.gh_json([
                "issue", "view", "7", "--repo", "local/smoke", "--json", "assignees",
            ])

        self.assertEqual(created["assignees"], [])
        self.assertEqual(
            json.loads(self.state.read_text())["created_issue"],
            {"number": 7, "assignees": []},
        )

    def test_approval_releases_assignment_and_open_pr_holds_the_next_forced_pass(self):
        self.worker.write_text(self.worker.read_text() + (
            '\np = Path(".factory/handoff-7.md")\n'
            'p.parent.mkdir(exist_ok=True)\n'
            'p.write_text("## Changed\\nDone.\\n## Still unverified\\nNone.\\n")\n'
            'import subprocess\n'
            'subprocess.run(["git", "add", "change.txt"], check=True)\n'
            'subprocess.run(["git", "commit", "-qm", "local change"], check=True)\n'
        ))
        clock = [0]
        original_review = dispatch.review

        def expire_after_review(*args, **kwargs):
            result = original_review(*args, **kwargs)
            clock[0] = 120
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, {**self.env, lifecycle.CONTEXT_ENV: ""}), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "review", side_effect=expire_after_review), \
                mock.patch.object(dispatch.risk, "classify", return_value=("Medium", ["medium:fixture"])):
            deferred = dispatch.main(["--ticket", "7", "--budget-min", "1"])

        deferred_state = json.loads(self.state.read_text())
        self.assertEqual(deferred, 0)
        self.assertEqual(deferred_state["assignees"], [])
        self.assertEqual(deferred_state["pr_state"], "CLOSED")
        self.assertIn(["pr", "close", "70", "--repo", "local/smoke"], deferred_state["gh"])

        first = self.cli("dispatch", "--ticket", "7")
        state = json.loads(self.state.read_text())
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(state["assignees"], [])
        self.assertTrue(state["pr"])
        self.assertTrue(state["approved"])
        calls = state["gh"]
        self.assertEqual(sum(call[:2] == ["pr", "create"] for call in calls), 1)
        close = calls.index(["pr", "close", "70", "--repo", "local/smoke"])
        remove_approval = next(
            i for i, call in enumerate(calls)
            if call[:3] == ["pr", "edit", "70"]
            and call[-2:] == ["--remove-label", "factory-approved"]
        )
        reopen = calls.index(["pr", "reopen", "70", "--repo", "local/smoke"])
        self.assertLess(close, remove_approval)
        self.assertLess(remove_approval, reopen)

        cleared = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "assignee-cleared"
        ]
        self.assertTrue(cleared)
        self.assertTrue(all(
            (row["ticket"], row["login"]) == (7, "runner") for row in cleared
        ))
        self.assertEqual(cleared[-1]["exit_path"], "done")
        wt = self.repo / ".factory" / "wt-7"
        head = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            env=self.env, check=True, capture_output=True, text=True,
        ).stdout.strip()
        attempts = sum(
            row.get("event") == "attempt" for row in lifecycle.read_events(self.events)
        )

        second = self.cli("dispatch", "--ticket", "7")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        self.assertIn("open agent PR #70", second.stdout)
        self.assertEqual(json.loads(self.state.read_text())["assignees"], [])
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                env=self.env, check=True, capture_output=True, text=True,
            ).stdout.strip(),
            head,
        )
        self.assertEqual(
            sum(row.get("event") == "attempt" for row in lifecycle.read_events(self.events)),
            attempts,
        )


if __name__ == "__main__":
    unittest.main()
