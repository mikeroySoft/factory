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

FLAKY_TEST = "tests.test_flaky.FlakyTest.test_status"
FLAKY_SOURCE = (
    "import unittest\n"
    "class FlakyTest(unittest.TestCase):\n"
    "    def test_status(self):\n"
    "        self.fail('known failure')\n"
)
FLAKY_REPORT = f"""# Gate report
- conflict-markers: PASS
- test: FAIL
- ruff: PASS

## test failure
```
FAIL: test_status ({FLAKY_TEST})
Traceback (most recent call last):
  File "tests/test_flaky.py", line 4, in test_status
    self.fail("known failure")
AssertionError: known failure
Ran 1 test in 0.001s

FAILED (failures=1)
```
"""


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

    def test_exhausted_waiting_refresh_escalates_once_per_pr_and_main_heads(self) -> None:
        self.cfg.review_rounds = 0
        self.move_main()

        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(False, "persistent failure")
        ) as gate:
            dispatch.merge_pass_locked(False)
            first_escalations = len(self.events("escalate"))
            first_comments = len([
                call for call in self.github.calls if call[1:3] == ["issue", "comment"]
            ])

            dispatch.merge_pass_locked(False)

            self.assertEqual(gate.call_count, 2)
            self.assertEqual(len(self.events("escalate")), first_escalations)
            self.assertEqual(
                len([call for call in self.github.calls if call[1:3] == ["issue", "comment"]]),
                first_comments,
            )

            self.move_main("main-again.txt", "main moved again\n")
            dispatch.merge_pass_locked(False)

        self.assertEqual(gate.call_count, 4)
        self.assertEqual(len(self.events("escalate")), first_escalations + 1)

    def test_unpublished_waiting_head_is_retained_while_remote_refreshes_and_marker_stays_parked(
        self,
    ) -> None:
        (self.origin / "tests").mkdir()
        (self.origin / "tests/test_flaky.py").write_text(FLAKY_SOURCE)
        git(self.origin, "add", "tests/test_flaky.py")
        git(self.origin, "commit", "-qm", "known failing test")
        baseline = git(self.origin, "rev-parse", "main")
        dispatch.record(
            "escalate",
            ticket=9,
            pr=102,
            code="gate_failed",
            round=1,
            parked_test={
                "test": FLAKY_TEST,
                "file": "tests/test_flaky.py",
                "main": baseline,
            },
        )
        wt = dispatch.ensure_worktree(9)
        (wt / "review-bounce.txt").write_text("unpushed\n")
        git(wt, "add", "review-bounce.txt")
        git(wt, "commit", "-qm", "unpublished review bounce")
        kept_head = git(wt, "rev-parse", "HEAD")
        evidence_ref = f"refs/factory-evidence/9-refreshed-{kept_head[:12]}"

        self.move_main("unrelated.txt", "unrelated\n")
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(True, "gate passed")
        ) as gate:
            dispatch.merge_pass_locked(False)
            dispatch.requeue_parked_tests(False)

        refreshed_head = self.github.head()
        gate.assert_called_once()
        self.assertNotEqual(refreshed_head, self.old_head)
        self.assertNotEqual(refreshed_head, kept_head)
        self.assertEqual(
            git(self.origin, "merge-base", "main", refreshed_head),
            git(self.origin, "rev-parse", "main"),
        )
        self.assertNotIn(
            "review-bounce.txt",
            git(self.origin, "ls-tree", "-r", "--name-only", refreshed_head).splitlines(),
        )
        self.assertEqual(git(wt, "rev-parse", evidence_ref), kept_head)
        self.assertEqual(git(wt, "rev-parse", "HEAD"), refreshed_head)
        self.assertEqual(self.github.issue()["state"], "OPEN")
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.github.assignees, [])
        self.assertEqual([event["round"] for event in self.events("escalate")], [1])
        self.assertEqual(self.events("manage"), [])
        self.assertEqual(self.events("gate-requeue"), [])
        self.assertEqual(self.events("test-requeue"), [])

        changed = self.move_main(
            "tests/test_flaky.py",
            FLAKY_SOURCE.replace("self.fail('known failure')", "pass"),
        )
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(True, "gate passed")
        ):
            dispatch.merge_pass_locked(False)
            dispatch.requeue_parked_tests(False)

        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertEqual(git(wt, "rev-parse", evidence_ref), kept_head)
        [requeue] = self.events("test-requeue")
        self.assertEqual((requeue["test"], requeue["main"]), (FLAKY_TEST, changed))
        self.assertEqual([event["round"] for event in self.events("escalate")], [1])

    def test_failed_waiting_refresh_parks_test_from_gated_revision_then_main_fix_requeues(
        self,
    ) -> None:
        self.cfg.review_rounds = 0
        (self.origin / "tests").mkdir()
        gated_main = self.move_main("tests/test_flaky.py", FLAKY_SOURCE)

        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(False, FLAKY_REPORT)
        ) as gate:
            dispatch.merge_pass_locked(False)

        self.assertEqual(gate.call_count, 2)
        wt = dispatch.FACTORY / "wt-9"
        self.assertEqual(self.github.head(), self.old_head)
        self.assertEqual(git(wt, "rev-parse", "HEAD"), self.old_head)
        [escalation] = self.events("escalate")
        self.assertEqual(escalation["code"], "gate_failed")
        self.assertEqual(
            escalation["parked_test"],
            {"test": FLAKY_TEST, "file": "tests/test_flaky.py", "main": gated_main},
        )
        self.assertNotEqual(escalation["refreshed_head"], self.old_head)
        self.assertEqual(
            git(wt, "rev-parse", escalation["evidence_ref"]),
            escalation["refreshed_head"],
        )
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("manage"), [])

        changed = self.move_main(
            "tests/test_flaky.py",
            FLAKY_SOURCE.replace("self.fail('known failure')", "pass"),
        )
        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(True, "gate passed")
        ):
            dispatch.merge_pass_locked(False)
            dispatch.requeue_parked_tests(False)

        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertEqual(len(self.events("escalate")), 1)
        [requeue] = self.events("test-requeue")
        self.assertEqual((requeue["test"], requeue["main"]), (FLAKY_TEST, changed))

    def test_up_to_date_waiting_pr_is_not_refreshed(self) -> None:
        with self.patched(), mock.patch.object(dispatch, "refresh_pr_branch") as refresh, \
                mock.patch.object(dispatch, "run_gate") as gate:
            dispatch.merge_pass_locked(False)

        refresh.assert_not_called()
        gate.assert_not_called()
        self.assertEqual(self.github.head(), self.old_head)

    def test_cross_repository_agent_branch_is_not_refreshed(self) -> None:
        self.move_main()
        real_json = self.github.gh_json

        def github_json(args):
            result = real_json(args)
            if args[:2] in (["pr", "list"], ["pr", "view"]):
                rows = result if isinstance(result, list) else [result]
                if "isCrossRepository" in args[args.index("--json") + 1].split(","):
                    for row in rows:
                        row["isCrossRepository"] = True
            return result

        with self.patched(), mock.patch.object(dispatch, "gh_json", side_effect=github_json), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "gate passed")) as gate:
            dispatch.merge_pass_locked(False)

        self.assertEqual(self.github.head(), self.old_head)
        gate.assert_not_called()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("refreshed"), [])
        self.assertEqual(self.events("escalate"), [])
        self.assertEqual(self.events("manage"), [])

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



    def test_waiting_pr_refreshes_even_when_an_approved_pr_also_needs_refresh(self):
        self.move_main()
        git(self.origin, "branch", "agent/10", self.old_head)
        approved = {
            **self.github.pr(), "number": 101, "headRefName": "agent/10",
            "labels": [{"name": config.LABEL_APPROVED}],
        }
        real_json = self.github.gh_json
        real_refresh = dispatch.refresh_pr_branch

        def github_json(args):
            if args[:2] == ["pr", "list"]:
                return [approved, self.github.pr()]
            if args[:3] == ["pr", "view", "101"]:
                return approved
            return real_json(args)

        def refresh(n, *args, **kwargs):
            return True if n == 10 else real_refresh(n, *args, **kwargs)

        with self.patched(), mock.patch.object(dispatch, "gh_json", side_effect=github_json), \
                mock.patch.object(dispatch, "refresh_pr_branch", side_effect=refresh), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "gate passed")):
            dispatch.merge_pass_locked(False)

        self.assertNotEqual(self.github.head(), self.old_head)
        self.assertEqual(git(self.origin, "merge-base", "main", self.github.head()), git(self.origin, "rev-parse", "main"))
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})

    def test_dirty_unpublished_waiting_worktree_is_preserved_before_restore(self):
        wt = dispatch.ensure_worktree(9)
        (wt / "unpublished.txt").write_text("kept commit\n")
        git(wt, "add", "unpublished.txt")
        git(wt, "commit", "-qm", "unpublished work")
        kept_head = git(wt, "rev-parse", "HEAD")
        (wt / "feature.txt").write_text("staged forensic edit\n")
        git(wt, "add", "feature.txt")
        (wt / "feature.txt").write_text("unstaged forensic edit\n")
        (wt / "untracked.txt").write_text("untracked forensic evidence\n")
        status = git(wt, "status", "--porcelain")
        self.move_main()

        with self.patched(), mock.patch.object(
            dispatch, "run_gate", return_value=(True, "gate passed"),
        ) as gate:
            dispatch.merge_pass_locked(False)

        self.assertEqual(git(wt, "rev-parse", "HEAD"), kept_head)
        self.assertEqual(git(wt, "status", "--porcelain"), status)
        self.assertEqual(git(wt, "show", ":feature.txt"), "staged forensic edit")
        self.assertEqual((wt / "feature.txt").read_text(), "unstaged forensic edit\n")
        self.assertEqual((wt / "untracked.txt").read_text(), "untracked forensic evidence\n")
        self.assertEqual(self.github.head(), self.old_head)
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("escalate"), [])
        self.assertEqual(self.events("manage"), [])
        gate.assert_not_called()

    def test_main_file_change_releases_before_exhausted_waiting_refresh(self):
        from contextlib import ExitStack

        self.cfg.review_rounds = 0
        (self.origin / "tests").mkdir()
        baseline = self.move_main("tests/test_flaky.py", FLAKY_SOURCE)
        dispatch.record(
            "escalate", ticket=9, code="gate_failed", round=1,
            parked_test={"test": FLAKY_TEST, "file": "tests/test_flaky.py", "main": baseline},
        )
        changed = self.move_main("tests/test_flaky.py", FLAKY_SOURCE + "# still fails\n")
        git(self.root, "fetch", "origin")
        with ExitStack() as stack:
            stack.enter_context(self.patched())
            stack.enter_context(mock.patch.object(config, "load", return_value=self.cfg))
            stack.enter_context(mock.patch.object(dispatch, "pin_root", return_value=[]))
            for name in ("sync_pass", "review_intake_pass", "ticket_pass"):
                stack.enter_context(mock.patch.object(dispatch, name))
            for name in ("factory.manage.manage_pass", "factory.learn.learn_pass"):
                stack.enter_context(mock.patch(name))
            stack.enter_context(mock.patch("factory.results.prune", return_value={"status": "complete"}))
            gate = stack.enter_context(mock.patch.object(
                dispatch, "run_gate", return_value=(False, FLAKY_REPORT),
            ))
            self.assertEqual(dispatch.main([]), 0)

        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        [requeue] = self.events("test-requeue")
        self.assertEqual((requeue["test"], requeue["main"]), (FLAKY_TEST, changed))
        self.assertEqual([event["round"] for event in self.events("escalate")], [1])
        self.assertEqual(self.events("manage"), [])
        self.assertEqual(self.events("gate-requeue"), [])
        comments = [call[call.index("--body") + 1] for call in self.github.calls
                    if call[1:3] == ["issue", "comment"]]
        self.assertIn(FLAKY_TEST, comments[-1])
        self.assertIn(changed, comments[-1])
        gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
