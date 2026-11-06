from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, dispatch


class PushAndPrReopenTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        dispatch.configure(config.Config(root=self.root, repo="acme/widgets"))
        dispatch.FACTORY.mkdir()

    @staticmethod
    def result(cmd: list[str], returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, returncode, stdout, "reopen refused" if returncode else "")

    def test_deleted_head_reopen_refusal_pushes_safely_then_creates_pr(self) -> None:
        actions: list[str] = []

        def github(args: list[str]) -> list[dict[str, object]]:
            if "--state" not in args:
                return []
            return [{"number": 22, "baseRefName": "main", "state": "CLOSED",
                     "isCrossRepository": False}]

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            if cmd[:3] == ["git", "rev-list", "--count"]:
                return self.result(cmd, stdout="1\n")
            if cmd[:3] == ["gh", "pr", "reopen"]:
                actions.append("reopen")
                result = self.result(cmd, returncode=1)
                if kwargs.get("check", True):
                    result.check_returncode()
                return result
            if cmd[:3] == ["gh", "pr", "create"]:
                actions.append("create")
                return self.result(cmd, stdout="https://github.com/acme/widgets/pull/23\n")
            return self.result(cmd)

        def push(*_args: object, **_kwargs: object) -> str:
            actions.append("push")
            return "new-head"

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "push_agent", side_effect=push) as push_agent, \
                mock.patch.object(dispatch, "record") as record:
            self.assertTrue(dispatch.push_and_pr(
                self.root, "agent/7", "feature", "body", ticket=7, lease="old-head",
            ))

        push_agent.assert_called_once_with(self.root, 7, "old-head")
        self.assertEqual(actions, ["push", "reopen", "create"])
        record.assert_called_once_with("pr-opened", ticket=7, pr=23)

    def test_foreign_candidates_are_ignored_and_local_closed_pr_is_reused(self) -> None:
        actions: list[str] = []
        foreign_open = {"number": 11, "baseRefName": "main", "isCrossRepository": True}
        foreign_closed = {"number": 12, "baseRefName": "main", "state": "CLOSED",
                          "isCrossRepository": True}
        local_closed = {"number": 22, "baseRefName": "main", "state": "CLOSED",
                        "isCrossRepository": False, "reviewDecision": "CHANGES_REQUESTED"}

        def github(args: list[str]) -> list[dict[str, object]]:
            if "--state" not in args:
                return [foreign_open]
            candidates = [foreign_closed, local_closed]
            if "--limit" in args:
                candidates = candidates[:int(args[args.index("--limit") + 1])]
            return candidates

        def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
            if cmd[:3] == ["git", "rev-list", "--count"]:
                return self.result(cmd, stdout="1\n")
            if cmd[:3] == ["gh", "pr", "edit"]:
                actions.append(f"edit:{cmd[3]}")
            elif cmd[:3] == ["gh", "pr", "reopen"]:
                actions.append(f"reopen:{cmd[3]}")
            elif cmd[:3] == ["gh", "pr", "create"]:
                actions.append("create")
            return self.result(cmd)

        def push(*_args: object, **_kwargs: object) -> str:
            actions.append("push")
            return "new-head"

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "push_agent", side_effect=push) as push_agent, \
                mock.patch.object(dispatch, "record") as record:
            self.assertTrue(dispatch.push_and_pr(
                self.root, "agent/7", "feature", "body", ticket=7, lease="old-head",
            ))

        push_agent.assert_called_once_with(self.root, 7, "old-head")
        self.assertEqual(actions, ["push", "edit:22", "reopen:22"])
        self.assertNotIn("create", actions)
        record.assert_called_once_with("pr-opened", ticket=7, pr=22)

    def test_wrong_base_local_closed_pr_is_not_mutated_or_pushed(self) -> None:
        def github(args: list[str]) -> list[dict[str, object]]:
            if "--state" not in args:
                return []
            return [{"number": 22, "baseRefName": "release", "state": "CLOSED",
                     "isCrossRepository": False}]

        def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
            stdout = "1\n" if cmd[:3] == ["git", "rev-list", "--count"] else ""
            return self.result(cmd, stdout=stdout)

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "run", side_effect=run) as run, \
                mock.patch.object(dispatch, "push_agent") as push_agent:
            self.assertFalse(dispatch.push_and_pr(
                self.root, "agent/7", "feature", "body", ticket=7, lease="old-head",
            ))

        push_agent.assert_not_called()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(cmd[:3] in (["gh", "pr", "edit"], ["gh", "pr", "reopen"],
                                         ["gh", "pr", "create"]) for cmd in commands))


if __name__ == "__main__":
    unittest.main()
