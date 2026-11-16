from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from factory import config, dispatch

SCOPE = "a" * 64


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def proof(status: str, base: str, head: str) -> str:
    return (
        f"- mutation: {status}\n"
        f"Mutation proof: mutation {status} base={base} head={head} scope={SCOPE}\n"
    )


class MutationIntegrityDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Factory Test")
        git(self.repo, "config", "user.email", "factory@example.test")
        (self.repo / "README.md").write_text("base\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "base")
        self.old_base = git(self.repo, "rev-parse", "HEAD")

        git(self.repo, "checkout", "-qb", "agent/7")
        (self.repo / "factory").mkdir()
        (self.repo / "factory" / "example.py").write_text("value = 1\n")
        git(self.repo, "add", "factory/example.py")
        git(self.repo, "commit", "-qm", "agent")
        self.head = git(self.repo, "rev-parse", "HEAD")

        git(self.repo, "checkout", "-q", "main")
        (self.repo / "README.md").write_text("moved main\n")
        git(self.repo, "commit", "-qam", "move main")
        self.new_base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "agent/7")
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.old_base)

        self.cfg = config.Config(
            self.repo,
            "acme/widgets",
            reviewer=["reviewer", "--model", "primary/model"],
            second_reviewer=["reviewer", "--model", "second/model"],
            checks=[config.Check(
                "mutation", ["mutation-check"], when="pr", proof=config.MUTATION_PROOF,
            )],
        )
        self.records: list[tuple[str, dict]] = []
        self.review_roles: list[str] = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(dispatch, "cfg", self.cfg, create=True))
        self.stack.enter_context(mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:fixture"])))
        self.stack.enter_context(mock.patch.object(dispatch, "_mutation_proof_opted", return_value=True))
        self.stack.enter_context(mock.patch.object(dispatch, "_worker_model_for_head", return_value="worker/model"))
        self.stack.enter_context(mock.patch.object(dispatch, "record", side_effect=self._record))
        self.stack.enter_context(mock.patch.object(dispatch, "review", side_effect=self._review))
        self.stack.enter_context(mock.patch.object(dispatch, "_PENDING_REVIEWS", {}))
        self.stack.enter_context(mock.patch.object(dispatch, "_PENDING_SECOND_REVIEWS", {}))
        self.stack.enter_context(mock.patch.object(dispatch, "_PENDING_RISK", {}))
        self.stack.enter_context(mock.patch.object(dispatch, "_LOW_FALLBACK", set()))

    def _record(self, event: str, **fields) -> None:
        self.records.append((event, fields))

    def _review(self, _wt, n, _report, head, *, role="primary", reviewer=None):
        self.review_roles.append(role)
        model = config.command_model(reviewer or self.cfg.reviewer)
        row = {"verdict": "APPROVE", "model": model, "head": head, "role": role}
        target = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
        target[n] = row
        return "APPROVE", f"{role} approved"

    def risk_failures(self) -> list[dict]:
        return [
            fields for event, fields in self.records
            if event == "risk-check" and fields.get("pr_gate") == "FAIL"
        ]

    def test_main_moving_during_pr_gate_never_becomes_revise_or_gate_failure(self) -> None:
        runs: dict[str, tuple[bool, str]] = {}
        calls = 0

        def moving_gate(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            git(self.repo, "update-ref", "refs/remotes/origin/main", self.new_base)
            return True, proof("PASS", self.new_base, self.head)

        with mock.patch.object(dispatch, "run_gate", side_effect=moving_gate):
            verdict, _findings, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )

        self.assertNotEqual(verdict, "REVISE")
        self.assertFalse(pr_failed)
        self.assertEqual(self.risk_failures(), [])
        self.assertLessEqual(calls, 2, "base movement may cause at most one bounded rerun")
        if calls == 1:
            self.assertEqual(verdict, "WAIT")
            self.assertNotIn(self.head, runs, "a wait outcome must invalidate the raced proof")
        else:
            self.assertEqual(verdict, "APPROVE")
            self.assertEqual(
                dispatch._mutation_proof(runs[self.head][1], self.head, self.new_base)[0],
                "PASS",
            )

    def test_twice_moved_base_defers_without_worker_rework(self) -> None:
        runs: dict[str, tuple[bool, str]] = {}
        third_base = git(
            self.repo, "commit-tree", f"{self.new_base}^{{tree}}",
            "-p", self.new_base, "-m", "move main again",
        )
        moved_bases = iter((self.new_base, third_base))

        def stale_gate(*_args, **_kwargs):
            moved = next(moved_bases)
            git(self.repo, "update-ref", "refs/remotes/origin/main", moved)
            return True, proof("PASS", moved, self.head)

        with mock.patch.object(dispatch, "run_gate", side_effect=stale_gate) as runner:
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertEqual((verdict, pr_failed), ("WAIT", False))
        self.assertEqual(runner.call_count, 2)
        self.assertNotIn(self.head, runs)
        self.assertEqual(self.risk_failures(), [])
        self.assertEqual(self.review_roles, [])

        with mock.patch.object(dispatch, "run_gate", return_value=(
            True, proof("PASS", third_base, self.head),
        )):
            recovered, _, failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertEqual((recovered, failed), ("APPROVE", False))
        self.assertEqual(self.review_roles, ["primary", "second"])

    def test_twice_moved_base_wait_compares_stale_verdict_by_equality(self) -> None:
        # Eq->Is at the second STALE check would miss a non-interned "STALE" string
        # (the literal returned by _mutation_proof is interned, so it does not kill).
        stale = (" " + "STALE").lstrip()
        self.assertEqual(stale, "STALE")
        self.assertIsNot(stale, "STALE")
        real = dispatch._mutation_proof

        def copy_stale(report: str, head: str, base: str | None):
            result = real(report, head, base)
            if result and result[0] == "STALE":
                return stale, result[1]
            return result

        runs: dict[str, tuple[bool, str]] = {}
        third_base = git(
            self.repo, "commit-tree", f"{self.new_base}^{{tree}}",
            "-p", self.new_base, "-m", "move main again",
        )
        moved_bases = iter((self.new_base, third_base))

        def stale_gate(*_args, **_kwargs):
            moved = next(moved_bases)
            git(self.repo, "update-ref", "refs/remotes/origin/main", moved)
            return True, proof("PASS", moved, self.head)

        with mock.patch.object(dispatch, "_mutation_proof", side_effect=copy_stale), \
                mock.patch.object(dispatch, "run_gate", side_effect=stale_gate) as runner:
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertEqual((verdict, pr_failed), ("WAIT", False))
        self.assertEqual(runner.call_count, 2)
        self.assertNotIn(self.head, runs)

    def test_proof_bound_to_intermediate_base_retries_then_accepts(self) -> None:
        """Main moved before the producer bound its base, then again during the run."""
        third_base = git(
            self.repo, "commit-tree", f"{self.new_base}^{{tree}}",
            "-p", self.new_base, "-m", "move main again",
        )
        reports = iter((
            proof("PASS", self.new_base, self.head),
            proof("PASS", third_base, self.head),
        ))

        def mid_gate(*_args, **_kwargs):
            git(self.repo, "update-ref", "refs/remotes/origin/main", third_base)
            return True, next(reports)

        with mock.patch.object(dispatch, "run_gate", side_effect=mid_gate) as runner:
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, {},
            )

        self.assertEqual((verdict, pr_failed), ("APPROVE", False))
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(self.risk_failures(), [])
        self.assertEqual(self.review_roles, ["primary", "second"])

    def test_stale_cached_proof_recovers_for_same_head_and_keeps_both_reviews(self) -> None:
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.new_base)
        runs = {self.head: (True, proof("PASS", self.old_base, self.head))}

        with mock.patch.object(
            dispatch, "run_gate", return_value=(True, proof("PASS", self.new_base, self.head)),
        ) as gate:
            first = dispatch.pr_gate_and_review(self.repo, 7, "attempt report", self.head, runs)
            dispatch._PENDING_REVIEWS.clear()
            dispatch._PENDING_SECOND_REVIEWS.clear()
            second = dispatch.pr_gate_and_review(self.repo, 7, "attempt report", self.head, runs)

        self.assertEqual((first[0], second[0]), ("APPROVE", "APPROVE"))
        self.assertEqual(self.review_roles, ["primary", "second", "primary", "second"])
        self.assertEqual(gate.call_count, 1, "the recovered same-head proof remains cacheable")
        self.assertEqual(self.risk_failures(), [])

    def test_stale_proof_without_base_move_fails_closed(self) -> None:
        report = proof("PASS", self.old_base, "f" * 40)
        with mock.patch.object(dispatch, "run_gate", return_value=(True, report)) as runner:
            verdict, findings, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, {},
            )

        self.assertEqual((verdict, pr_failed), ("REVISE", True))
        self.assertEqual(findings, report)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(self.risk_failures()), 1)
        self.assertEqual(self.review_roles, [])

    def test_unbound_proof_fails_closed(self) -> None:
        report = "- mutation: PASS\n"
        with mock.patch.object(dispatch, "run_gate", return_value=(True, report)):
            verdict, findings, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, {},
            )

        self.assertEqual((verdict, pr_failed), ("REVISE", True))
        self.assertEqual(findings, report)
        self.assertEqual(len(self.risk_failures()), 1)
        self.assertEqual(self.review_roles, [])

    def test_base_movement_does_not_defer_a_head_mismatched_proof(self) -> None:
        third_base = git(
            self.repo, "commit-tree", f"{self.new_base}^{{tree}}",
            "-p", self.new_base, "-m", "move main again",
        )
        moved_bases = iter((self.new_base, third_base))

        def wrong_head_gate(*_args, **_kwargs):
            moved = next(moved_bases)
            git(self.repo, "update-ref", "refs/remotes/origin/main", moved)
            proof_head = self.head if moved == self.new_base else "f" * 40
            return True, proof("PASS", moved, proof_head)

        with mock.patch.object(dispatch, "run_gate", side_effect=wrong_head_gate):
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, {},
            )
        self.assertEqual((verdict, pr_failed), ("REVISE", True))
        self.assertEqual(len(self.risk_failures()), 1)
        self.assertEqual(self.review_roles, [])

    def test_real_pr_gate_failure_still_returns_revise_and_records_failure(self) -> None:
        report = proof("PASS", self.old_base, self.head) + "- protected-paths: FAIL\n"
        with mock.patch.object(dispatch, "run_gate", return_value=(False, report)):
            verdict, findings, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, {},
            )

        self.assertEqual((verdict, pr_failed), ("REVISE", True))
        self.assertIn("- protected-paths: FAIL", findings)
        self.assertEqual(len(self.risk_failures()), 1)
        self.assertEqual(self.review_roles, [])


    def descendant_oid(self, parent: str, *, greater: bool) -> str:
        for i in range(20_000):
            oid = git(
                self.repo, "commit-tree", f"{parent}^{{tree}}", "-p", parent,
                "-m", f"move {i} {'gt' if greater else 'lt'}",
            )
            if oid != parent and (oid > parent) is greater:
                return oid
        self.fail(f"could not synthesize a descendant oid {'>' if greater else '<'} {parent}")

    def test_merge_base_failure_is_not_ancestry(self) -> None:
        # proof_base is between snapshot and current so the move path has to
        # ask git; a signal or git error must not count as ancestry.
        mid = git(
            self.repo, "commit-tree", f"{self.old_base}^{{tree}}",
            "-p", self.old_base, "-m", "intermediate",
        )
        tip = git(
            self.repo, "commit-tree", f"{mid}^{{tree}}", "-p", mid, "-m", "tip",
        )
        report = proof("PASS", mid, self.head)
        for code in (-9, 128):
            failed = subprocess.CompletedProcess(["git", "merge-base"], code, "", "")
            with mock.patch.object(dispatch, "run", return_value=failed):
                self.assertFalse(dispatch._is_ancestor(self.repo, self.old_base, tip))
                self.assertFalse(dispatch._moved_main_proof(
                    self.repo, report, self.head, self.old_base, tip,
                ))

    def test_moved_main_proof_is_false_when_snapshot_or_current_is_missing_or_equal(self) -> None:
        report = proof("PASS", self.new_base, self.head)
        self.assertFalse(dispatch._moved_main_proof(
            self.repo, report, self.head, None, self.new_base,
        ))
        self.assertFalse(dispatch._moved_main_proof(
            self.repo, report, self.head, self.old_base, None,
        ))
        self.assertFalse(dispatch._moved_main_proof(
            self.repo, report, self.head, self.new_base, self.new_base,
        ))

    def test_moved_main_proof_requires_exactly_one_bound_proof(self) -> None:
        # A missing or duplicated proof line is not a moved-main proof, even when
        # snapshot and current actually differ. return False -> True at the
        # len(proofs) != 1 guard would report a move.
        self.assertFalse(dispatch._moved_main_proof(
            self.repo, "no mutation proof in this report\n", self.head,
            self.old_base, self.new_base,
        ))
        doubled = proof("PASS", self.new_base, self.head) + proof("PASS", self.old_base, self.head)
        self.assertFalse(dispatch._moved_main_proof(
            self.repo, doubled, self.head, self.old_base, self.new_base,
        ))

    def test_moved_main_proof_requires_base_on_the_snapshot_to_current_path(self) -> None:
        off_path = git(
            self.repo, "commit-tree", f"{self.new_base}^{{tree}}",
            "-p", self.new_base, "-m", "descendant of current, not between snapshot and current",
        )
        report = proof("PASS", off_path, self.head)
        self.assertFalse(dispatch._moved_main_proof(
            self.repo, report, self.head, self.old_base, self.new_base,
        ))

    def test_moved_main_reruns_when_the_new_base_oid_is_lexicographically_greater(self) -> None:
        greater = self.descendant_oid(self.old_base, greater=True)
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.old_base)
        runs: dict[str, tuple[bool, str]] = {}

        def moving_gate(*_args, **_kwargs):
            git(self.repo, "update-ref", "refs/remotes/origin/main", greater)
            return True, proof("PASS", greater, self.head)

        with mock.patch.object(dispatch, "run_gate", side_effect=moving_gate) as runner:
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertFalse(pr_failed)
        self.assertNotEqual(verdict, "REVISE")
        self.assertGreaterEqual(runner.call_count, 1)
        if runner.call_count == 1:
            self.assertEqual(verdict, "WAIT")
        else:
            self.assertEqual(verdict, "APPROVE")

    def test_twice_moved_base_defers_when_second_oid_is_lexicographically_greater(self) -> None:
        first = self.descendant_oid(self.old_base, greater=True)
        second = self.descendant_oid(first, greater=True)
        self._assert_twice_moved_defers(first, second)

    def test_twice_moved_base_defers_when_second_oid_is_lexicographically_lesser(self) -> None:
        first = self.descendant_oid(self.old_base, greater=True)
        second = self.descendant_oid(first, greater=False)
        self._assert_twice_moved_defers(first, second)

    def _assert_twice_moved_defers(self, first: str, second: str) -> None:
        runs: dict[str, tuple[bool, str]] = {}
        moved_bases = iter((first, second))

        def stale_gate(*_args, **_kwargs):
            moved = next(moved_bases)
            git(self.repo, "update-ref", "refs/remotes/origin/main", moved)
            return True, proof("PASS", moved, self.head)

        with mock.patch.object(dispatch, "run_gate", side_effect=stale_gate) as runner:
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertEqual((verdict, pr_failed), ("WAIT", False))
        self.assertEqual(runner.call_count, 2)
        self.assertNotIn(self.head, runs)
        self.assertEqual(self.review_roles, [])

    def test_stale_proof_without_a_second_move_fails_closed_by_equality(self) -> None:
        # After a single base move and rerun that is still STALE, equality (not
        # identity) must turn the leftover STALE into a fail-closed proof.
        stale = (" " + "STALE").lstrip()
        self.assertEqual(stale, "STALE")
        self.assertIsNot(stale, "STALE")
        real = dispatch._mutation_proof

        def copy_stale(report: str, head: str, base: str | None):
            result = real(report, head, base)
            if result and result[0] == "STALE":
                return stale, result[1]
            return result

        git(self.repo, "update-ref", "refs/remotes/origin/main", self.old_base)
        with mock.patch.object(dispatch, "_mutation_proof", side_effect=copy_stale),                 mock.patch.object(
                    dispatch, "run_gate",
                    return_value=(True, proof("PASS", self.old_base, "f" * 40)),
                ):
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, {},
            )
        self.assertEqual((verdict, pr_failed), ("REVISE", True))
        self.assertEqual(self.review_roles, [])


if __name__ == "__main__":
    unittest.main()
