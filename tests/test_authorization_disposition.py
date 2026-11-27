"""Disposition and claim ownership regressions for add-only authorization."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_authorization_request as authorization_tests
from tests import test_unchanged_rounds as round_tests


class AuthorizationClaimDispositionTest(unittest.TestCase):
    setUp = authorization_tests.ManagerAuthorizationRequestTest.setUp
    command_result = authorization_tests.ManagerAuthorizationRequestTest.command_result
    events = authorization_tests.ManagerAuthorizationRequestTest.events

    def request_with(self, runner, **kwargs) -> None:
        with mock.patch.object(dispatch, "run", side_effect=runner), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            manage.request_add_only_authorization(
                7, self.issue, self.paths, self.report, self.logfile, **kwargs,
            )

    def release_commands(self) -> list[list[str]]:
        return [
            command for command in self.commands
            if command[:4] == ["gh", "issue", "edit", "7"]
            and "--remove-assignee" in command
        ]

    def assert_released_for_agent(self) -> None:
        [release] = self.release_commands()
        self.assertIn(("--remove-assignee", "@me"), pairwise(release))
        self.assertIn(("--add-label", config.LABEL_AGENT), pairwise(release))
        self.assertNotIn(config.LABEL_HUMAN, release)

    def test_no_manager_releases_claim_while_durable_request_blocks_workers(self) -> None:
        self.cfg.manager = None
        rework = {"head": "a" * 40, "findings": "Unresolved review finding", "pr_failed": False}

        self.request_with(self.command_result, rework=rework)

        request = next(row for row in self.events() if row.get("event") == "manager-request")
        self.assertEqual(request["rework"], rework)
        self.assertTrue(manage.pending_add_only_request(7))
        self.assert_released_for_agent()
        self.assertFalse(any(row.get("event") == "escalate" for row in self.events()))

    def test_failed_manager_queue_releases_claim_without_human_disposition(self) -> None:
        def unavailable(command, *args, **kwargs):
            if command[0] == "manager":
                raise OSError("manager unavailable")
            return self.command_result(command, *args, **kwargs)

        self.request_with(unavailable)

        self.assertTrue(manage.pending_add_only_request(7))
        self.assert_released_for_agent()
        self.assertFalse(any(row.get("event") in {"manage", "escalate"} for row in self.events()))

    def test_direct_override_completes_old_request_without_clearing_foreign_claim(self) -> None:
        packet = self.cfg.factory / "request.md"
        packet.write_text("authorization evidence")
        dispatch.record(
            "manager-request", ticket=7, code="add_only_authorization", paths=self.paths,
            packet=str(packet), round=1, exhausted=False,
        )
        self.cfg.host_name = "ultron"
        now = datetime.now(UTC)
        foreign = {
            "id": 41, "host": "tank", "nonce": "foreign", "heartbeat": now,
            "created": now, "at": now.isoformat(), "login": "factory",
        }

        with mock.patch.object(dispatch, "host_claims", return_value=[foreign]), \
                mock.patch.object(dispatch, "gh_json", return_value={}), \
                mock.patch.object(dispatch, "run", side_effect=self.command_result), \
                mock.patch.object(dispatch, "delete_host_claim") as delete:
            self.assertFalse(manage.pending_add_only_request(
                7, labels={config.LABEL_ADD_ONLY_OVERRIDE},
            ))

        [edit] = [command for command in self.commands if command[:4] == ["gh", "issue", "edit", "7"]]
        self.assertIn(("--add-label", config.LABEL_AGENT), pairwise(edit))
        self.assertNotIn("--remove-assignee", edit)
        delete.assert_not_called()
        completed = [row for row in self.events() if row.get("event") == "manager-request-complete"]
        self.assertEqual([(row["packet"], row["round"]) for row in completed], [(str(packet), 1)])

    def run_terminal(self, output: str) -> list[list[str]]:
        self.manager_output = output

        def command(args, *pos, **kwargs):
            if args[:3] == ["gh", "issue", "create"]:
                self.commands.append(args)
                return subprocess.CompletedProcess(args, 0, "https://github.com/acme/widgets/issues/8\n", "")
            return self.command_result(args, *pos, **kwargs)

        def github(args):
            if args[:2] == ["issue", "view"]:
                return {"title": self.issue["title"], "body": self.issue["body"]}
            if args[:2] == ["pr", "view"]:
                return {"state": "CLOSED", "baseRefName": self.cfg.main}
            raise AssertionError(args)

        with mock.patch.object(dispatch, "run", side_effect=command), \
                mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(manage, "selectable_workers", return_value={"default": []}), \
                mock.patch.object(manage, "human_activity", return_value=False):
            manage.request_add_only_authorization(
                7, self.issue, self.paths, self.report, self.logfile,
            )
        return self.commands

    def terminal_transition(self, commands: list[list[str]]) -> int:
        return next(index for index, command in enumerate(commands) if
                    command[:4] == ["gh", "issue", "edit", "7"]
                    and ("--remove-label", config.LABEL_AGENT) in pairwise(command)
                    and ("--add-label", config.LABEL_HUMAN) in pairwise(command))

    def test_close_moves_parent_to_human_before_real_apply(self) -> None:
        commands = self.run_terminal("DECISION: CLOSE\nDuplicate of shipped work")

        transition = self.terminal_transition(commands)
        proposal = next(index for index, command in enumerate(commands)
                        if ("--add-label", config.LABEL_WONTFIX) in pairwise(command))
        self.assertLess(transition, proposal)

    def test_split_moves_parent_to_human_before_real_apply(self) -> None:
        children = [{"title": "Child", "body": "Child acceptance", "blocked_by": []}]
        commands = self.run_terminal("DECISION: SPLIT\n" + json.dumps(children))

        transition = self.terminal_transition(commands)
        create = next(index for index, command in enumerate(commands)
                      if command[:3] == ["gh", "issue", "create"])
        self.assertLess(transition, create)
        self.assertTrue(any(
            any("Blocked by: #8" in argument for argument in command)
            for command in commands
        ))


class OrdinaryFrontierAuthorizationTest(unittest.TestCase):
    setUp = round_tests.UnchangedRoundsTest.setUp
    git = round_tests.UnchangedRoundsTest.git
    write_config = round_tests.UnchangedRoundsTest.write_config
    commit_config = round_tests.UnchangedRoundsTest.commit_config
    cli = round_tests.UnchangedRoundsTest.cli
    failed_gate = round_tests.UnchangedRoundsTest.failed_gate

    def test_released_pending_request_reenters_ordinary_frontier_after_override(self) -> None:
        self.failed_gate(needs=True)
        gh = self.bin / "gh"
        script = gh.read_text()
        script = script.replace(
            '    s["labels"] = sorted(labels)\n',
            '    if "--add-assignee" in a: s["assigned"] = True\n'
            '    if "--remove-assignee" in a: s["assigned"] = False\n'
            '    s["labels"] = sorted(labels)\n',
        ).replace(
            'issue["labels"] = [{"name": value} for value in s.get("labels", ["ready-for-agent"])]\n',
            'issue["labels"] = [{"name": value} for value in s.get("labels", ["ready-for-agent"])]\n'
            'issue["assignees"] = [{"login": "factory"}] if s.get("assigned") else []\n',
        ).replace(
            '    print(json.dumps([issue]) if s.get("sync") and not s.get("merged") else "[]")\n',
            '    print(json.dumps([issue]))\n',
        )
        gh.write_text(script)
        allowed = self.base / "allowed"
        self.write_config(check=[sys.executable, "-c", (
            "from pathlib import Path; "
            "print('FAIL: test_contract (test_contract.ContractTest.test_contract)'); "
            f"raise SystemExit(0 if Path({str(allowed)!r}).exists() else 1)"
        )])
        self.commit_config()

        self.cli("dispatch", "--budget-min", "1")
        state = json.loads(self.state.read_text())
        self.assertFalse(state.get("assigned"))
        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run"])

        state["labels"].append(config.LABEL_ADD_ONLY_OVERRIDE)
        self.state.write_text(json.dumps(state))
        allowed.touch()
        self.cli("dispatch", "--budget-min", "1")

        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run", "run"])
        rows = lifecycle.read_events(self.events)
        self.assertEqual(sum(row.get("event") == "manager-request" for row in rows), 1)
        self.assertEqual(sum(row.get("event") == "manager-request-complete" for row in rows), 1)


class FixDispositionTest(unittest.TestCase):
    def test_unchanged_fix_escalates_without_recording_terminal_failure_before_human_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(root, "acme/widgets")
            dispatch.configure(cfg)
            wt = cfg.factory / "wt-7"
            wt.mkdir(parents=True)
            packet = cfg.factory / "manager-packet.md"
            packet.write_text("Unresolved review finding")
            issue = {
                "number": 7, "title": "Fix review findings", "body": "Resolve review.",
                "labels": [{"name": config.LABEL_AGENT}],
            }
            lease = "a" * 40
            dispatch.record("review", ticket=7, head=lease, actual_head=lease,
                            accepted=True, verdict="REVISE")
            pr = {
                "state": "OPEN", "headRefName": "agent/7", "headRefOid": lease,
                "baseRefName": "main", "reviewDecision": None,
            }

            def git(command, *args, **kwargs):
                output = "agent/7\n" if command[:3] == ["git", "branch", "--show-current"] else lease + "\n"
                return subprocess.CompletedProcess(command, 0, output, "")

            with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                    mock.patch.object(dispatch, "run", side_effect=git), \
                    mock.patch.object(dispatch, "worker_round", return_value=(True, "passed", None, lease)), \
                    mock.patch.object(dispatch, "escalate") as escalate:
                manage.apply(7, issue, "FIX", "", {
                    "worker": "default", "guidance": "Resolve the supplied findings.",
                }, packet)

            escalate.assert_called_once()
            self.assertEqual(escalate.call_args.kwargs["code"], "review_findings_unresolved")
            self.assertFalse(any(
                row.get("event") == "decision" and row.get("chosen") == "FIX_FAILED"
                for row in lifecycle.read_events(dispatch.EVENTS)
            ))


if __name__ == "__main__":
    unittest.main()
