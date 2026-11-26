"""#947: add-only gate failures ask the manager before entering the human queue."""
from __future__ import annotations

import itertools
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage


class ManagerAuthorizationRequestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = config.Config(
            self.root,
            "acme/widgets",
            manager=["manager", "{prompt}"],
        )
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir()
        self.issue = {
            "number": 7,
            "title": "Update the authorization behavior",
            "body": "Keep the add-only guard in place.",
            "labels": [{"name": config.LABEL_AGENT}],
        }
        self.paths = ["tests/test_policy.py:41", "tests/test_policy.py:57"]
        self.report = (
            "# Gate report\n\n"
            "- add-only: FAIL\n\n"
            "FAILED tests.test_policy.PolicyTest.test_existing_rule\n"
            "FAILED tests.test_policy.PolicyTest.test_obsolete_wording\n"
        )
        self.logfile = self.cfg.factory / "logs" / "7-1.log"
        self.manager_calls: list[list[str]] = []
        self.commands: list[list[str]] = []
        self.manager_output = (
            'DECISION: AUTHORIZE\n'
            '{"justification":"The ticket requires the existing policy change.",'
            '"guidance":"Change only the named assertions."}'
        )
        self.comment = 100

    def command_result(self, command: list[str], *args, **kwargs) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if command[0] == "manager":
            self.manager_calls.append(command)
            return subprocess.CompletedProcess(command, 0, self.manager_output, "")
        if command[:3] == ["gh", "issue", "comment"]:
            self.comment += 1
            return subprocess.CompletedProcess(
                command, 0,
                f"https://github.com/acme/widgets/issues/7#issuecomment-{self.comment}\n", "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def events(self) -> list[dict]:
        return lifecycle.read_events(dispatch.EVENTS)

    def request(self) -> None:
        with mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            self.assertIsNone(manage.request_add_only_authorization(
                7, self.issue, self.paths, self.report, self.logfile,
            ))

    def test_authorize_runs_one_manager_command_with_scope_and_records_without_escalation(self) -> None:
        self.request()

        self.assertEqual(len(self.manager_calls), 1)
        prompt = Path(self.manager_calls[0][1]).read_text()
        for value in [*self.paths,
                      "tests.test_policy.PolicyTest.test_existing_rule",
                      "tests.test_policy.PolicyTest.test_obsolete_wording"]:
            self.assertIn(value, prompt)
        rows = self.events()
        request = next(row for row in rows if row.get("event") == "manager-request")
        self.assertEqual(request["code"], "add_only_authorization")
        self.assertEqual(request["paths"], self.paths)
        self.assertEqual(next(row["decision"] for row in rows if row.get("event") == "manage"), "AUTHORIZE")
        self.assertFalse(any(row.get("event") == "escalate" for row in rows))
        edits = [command for command in self.commands if command[:3] == ["gh", "issue", "edit"]]
        self.assertTrue(any(config.LABEL_ADD_ONLY_OVERRIDE in command for command in edits))
        self.assertFalse(any(
            ("--add-label", config.LABEL_HUMAN) in itertools.pairwise(command) for command in edits
        ))

    def test_repeated_request_spends_round_budget_without_running_manager_twice(self) -> None:
        self.request()
        self.request()

        rows = self.events()
        requests = [row for row in rows if row.get("event") == "manager-request"]
        self.assertEqual([row["round"] for row in requests], [1, 2])
        self.assertEqual(len(self.manager_calls), 1)
        escalation = next(row for row in rows if row.get("event") == "escalate")
        self.assertEqual(escalation["round"], 2)
        self.assertEqual(escalation["code"], "add_only_authorization")
        self.assertFalse(manage.pending_add_only_request(7))

    def test_decline_enters_standard_human_escalation(self) -> None:
        self.manager_output = "DECISION: HUMAN\nThe requested mutation is not justified."
        self.request()

        rows = self.events()
        escalation = next(row for row in rows if row.get("event") == "escalate")
        self.assertEqual(escalation["code"], "add_only_authorization")
        self.assertTrue(any(
            command[:3] == ["gh", "issue", "edit"]
            and "--add-label" in command
            and config.LABEL_HUMAN in command
            for command in self.commands
        ))

    def test_no_manager_leaves_durable_pending_request_without_human_label(self) -> None:
        self.cfg.manager = None
        self.request()

        rows = self.events()
        request = next(row for row in rows if row.get("event") == "manager-request")
        packet = Path(request["packet"])
        self.assertTrue(packet.is_file())
        self.assertIn(self.report, packet.read_text())
        self.assertFalse(any(row.get("event") in {"manage", "escalate"} for row in rows))
        self.assertTrue(any(
            row.get("event") == "comment" and row.get("kind") == "escalation"
            for row in rows
        ))
        self.assertFalse(any(
            command[:3] == ["gh", "issue", "edit"] and config.LABEL_HUMAN in command
            for command in self.commands
        ))



class ManagerFixNoProgressTest(unittest.TestCase):
    def test_unchanged_worker_head_records_unresolved_findings_without_reviewing_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(root, "acme/widgets")
            dispatch.configure(cfg)
            wt = cfg.factory / "wt-7"
            wt.mkdir(parents=True)
            packet = cfg.factory / "manager-packet.md"
            packet.write_text("Unresolved review finding: keep the authorization scope narrow.")
            issue = {
                "number": 7,
                "title": "Fix review findings",
                "body": "Resolve the current-head review.",
                "labels": [{"name": config.LABEL_HUMAN}],
            }
            lease = "a" * 40
            dispatch.record("review", ticket=7, head=lease, actual_head=lease,
                            accepted=True, verdict="REVISE")
            pr = {
                "state": "OPEN",
                "headRefName": "agent/7",
                "headRefOid": lease,
                "baseRefName": "main",
                "reviewDecision": None,
            }

            def git(command: list[str], *args, **kwargs) -> subprocess.CompletedProcess:
                output = "agent/7\n" if command[:3] == ["git", "branch", "--show-current"] else lease + "\n"
                return subprocess.CompletedProcess(command, 0, output, "")

            with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                    mock.patch.object(dispatch, "run", side_effect=git), \
                    mock.patch.object(
                        dispatch, "worker_round",
                        return_value=(True, "attempt gate passed", cfg.factory / "fix.log", lease),
                    ), \
                    mock.patch.object(dispatch, "push_agent") as push, \
                    mock.patch.object(dispatch, "pr_gate_and_review") as review:
                manage.apply(7, issue, "FIX", "", {
                    "worker": "default",
                    "guidance": "Resolve the supplied findings.",
                }, packet)

            push.assert_not_called()
            review.assert_not_called()
            outcome = next(
                row for row in reversed(lifecycle.read_events(dispatch.EVENTS))
                if row.get("event") == "decision" and row.get("chosen") == "FIX_FAILED"
            )
            self.assertIn("review_findings_unresolved", outcome["rationale"])
            self.assertIn("keep the authorization scope narrow", outcome["rationale"])

if __name__ == "__main__":
    unittest.main()
