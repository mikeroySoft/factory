"""#517: waiting pull requests keep pace with main without entering an active round."""
from __future__ import annotations

import fcntl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests.test_refresh_conflict import FakeGitHub, build_fork, git


class WaitingGitHub(FakeGitHub):
    def __init__(self, origin: Path, real_run) -> None:
        super().__init__(origin, real_run)
        self.issue_labels = {config.LABEL_HUMAN}
        self.assignees = []
        self.pr_labels = set()


class WaitingRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.root, self.origin = build_fork(self.tmp)
        git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "feature.txt").write_text("feature\n")
        git(self.origin, "add", "feature.txt")
        git(self.origin, "commit", "-qm", "agent change")
        self.old_head = git(self.origin, "rev-parse", "HEAD")
        git(self.origin, "checkout", "-q", "main")
        self.cfg = config.Config(
            root=self.root,
            repo="acme/widgets",
            main="main",
            review_rounds=3,
            manager=["unused-manager"],
            manager_review="all",
            reviewer=[
                sys.executable,
                "-c",
                "print('```json\\n{\"verdict\":\"APPROVE\",\"findings\":[]}\\n```\\nVERDICT: APPROVE')",
            ],
        )
        dispatch.configure(self.cfg)
        self.github = WaitingGitHub(self.origin, dispatch.run)

    def patched(self):
        return mock.patch.multiple(dispatch, gh_json=self.github.gh_json, run=self.github.run)

    def events(self, name: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == name]

    def move_main(self, path: str = "main.txt", text: str = "main moved\n") -> str:
        (self.origin / path).write_text(text)
        git(self.origin, "add", path)
        git(self.origin, "commit", "-qm", "main moved")
        return git(self.origin, "rev-parse", "main")

    def assert_clean_waiting_refresh(self) -> None:
        initial_labels = set(self.github.issue_labels)
        initial_assignees = list(self.github.assignees)
        initial_escalations = len(self.events("escalate"))
        initial_manager_rounds = len(self.events("manage"))
        gated_heads: list[str] = []

        def gate(wt: Path, *_args, **_kwargs):
            gated_heads.append(git(wt, "rev-parse", "HEAD"))
            return True, "gate passed"

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=gate):
            dispatch.merge_pass_locked(False)

        refreshed_head = self.github.head()
        self.assertNotEqual(refreshed_head, self.old_head)
        self.assertEqual(git(self.origin, "merge-base", "main", refreshed_head), git(self.origin, "rev-parse", "main"))
        self.assertEqual(gated_heads, [refreshed_head])
        self.assertEqual(self.github.issue()["state"], "OPEN")
        self.assertEqual(self.github.issue_labels, initial_labels)
        self.assertEqual(self.github.assignees, initial_assignees)
        self.assertEqual(len(self.events("escalate")), initial_escalations)
        self.assertEqual(len(self.events("manage")), initial_manager_rounds)
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertFalse(any(row.get("event") == "approved" and row.get("head") == refreshed_head
                             for row in lifecycle.read_events(dispatch.EVENTS)))
        self.assertEqual([row["head"] for row in self.events("refreshed")], [refreshed_head])
        self.assertFalse(self.github.merged)

    def test_ready_for_human_unapproved_pr_refreshes_on_the_next_merge_pass(self) -> None:
        self.move_main()
        dispatch.record("manage", ticket=9, pr=102, head=self.old_head, decision="APPROVE", round=1)

        self.assert_clean_waiting_refresh()

    def test_gate_parked_pr_refreshes_without_an_escalation_or_manager_round(self) -> None:
        parked_main = self.move_main()
        dispatch.record(
            "escalate",
            ticket=9,
            pr=102,
            code="gate_failed",
            round=1,
            parked_test={"test": "tests/test_feature.py::test_feature", "file": "tests/test_feature.py", "main": parked_main},
        )

        self.assert_clean_waiting_refresh()

    def test_up_to_date_waiting_pr_is_not_refreshed(self) -> None:
        with self.patched(), mock.patch.object(dispatch, "refresh_pr_branch") as refresh, \
                mock.patch.object(dispatch, "run_gate") as gate:
            dispatch.merge_pass_locked(False)

        refresh.assert_not_called()
        gate.assert_not_called()
        self.assertEqual(self.github.head(), self.old_head)

    def test_active_ticket_lock_prevents_waiting_refresh_race(self) -> None:
        self.move_main()
        dispatch.ticket_lock(9).parent.mkdir(parents=True, exist_ok=True)
        with dispatch.ticket_lock(9).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.patched(), mock.patch.object(dispatch, "refresh_pr_branch") as refresh:
                dispatch.merge_pass_locked(False)

        refresh.assert_not_called()
        self.assertEqual(self.github.head(), self.old_head)

    def test_waiting_refresh_conflict_reuses_worker_requeue(self) -> None:
        self.move_main("feature.txt", "main version\n")

        with self.patched(), mock.patch.object(dispatch, "run_gate") as gate:
            dispatch.merge_pass_locked(False)

        gate.assert_not_called()
        self.assertEqual(self.events("escalate"), [])
        [requeue] = self.events("conflict-requeue")
        self.assertEqual((requeue["pr"], requeue["files"], requeue["round"]), (102, ["feature.txt"], 1))
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)

    def test_waiting_refresh_gate_failure_reuses_worker_requeue(self) -> None:
        self.move_main()
        reports = [(False, "first failure"), (False, "second failure")]

        with self.patched(), mock.patch.object(dispatch, "run_gate", side_effect=reports) as gate:
            dispatch.merge_pass_locked(False)

        self.assertEqual(gate.call_count, 2)
        self.assertEqual(self.events("escalate"), [])
        [requeue] = self.events("gate-requeue")
        self.assertEqual((requeue["pr"], requeue["reports"], requeue["round"]), (102, ["first failure", "second failure"], 1))
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)

    def test_clean_waiting_refresh_does_not_review_or_escalate_revise(self) -> None:
        self.move_main()
        self.cfg.reviewer = [
            sys.executable,
            "-c",
            "print('```json\\n{\"verdict\":\"REVISE\",\"findings\":[\"change it\"]}\\n```\\nVERDICT: REVISE')",
        ]
        initial_labels = set(self.github.issue_labels)

        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(True, "gate passed")
        ):
            dispatch.merge_pass_locked(False)

        self.assertNotEqual(self.github.head(), self.old_head)
        self.assertEqual(self.github.issue_labels, initial_labels)
        self.assertEqual(self.events("review"), [])
        self.assertEqual(self.events("escalate"), [])



if __name__ == "__main__":
    unittest.main()
