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
            self.assertIsNone(verdict)
            self.assertNotIn(self.head, runs, "a wait outcome must invalidate the raced proof")
        else:
            self.assertEqual(verdict, "APPROVE")
            self.assertEqual(
                dispatch._mutation_proof(runs[self.head][1], self.head, self.new_base)[0],
                "PASS",
            )

    def test_repeatedly_stale_proof_defers_without_worker_rework(self) -> None:
        runs: dict[str, tuple[bool, str]] = {}

        def stale_gate(*_args, **_kwargs):
            git(self.repo, "update-ref", "refs/remotes/origin/main", self.new_base)
            return True, proof("PASS", "f" * 40, self.head)

        with mock.patch.object(dispatch, "run_gate", side_effect=stale_gate) as runner:
            verdict, _, pr_failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertEqual((verdict, pr_failed), (None, False))
        self.assertEqual(runner.call_count, 2)
        self.assertNotIn(self.head, runs)
        self.assertEqual(self.risk_failures(), [])
        self.assertEqual(self.review_roles, [])

        with mock.patch.object(dispatch, "run_gate", return_value=(
            True, proof("PASS", self.new_base, self.head),
        )):
            recovered, _, failed = dispatch.pr_gate_and_review(
                self.repo, 7, "attempt report", self.head, runs,
            )
        self.assertEqual((recovered, failed), ("APPROVE", False))
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


if __name__ == "__main__":
    unittest.main()
