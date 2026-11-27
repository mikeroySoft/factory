"""Durable mutation replay records through the real caller boundary."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from factory import dispatch, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_caller as proof_callers
from tests.test_mutation_replay_callers import SURVIVOR


def inline_completion(records: list[dict], *, blocking: bool):
    def produce(child):
        env = child.kwargs["env"]
        scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
        record = mutation.proof_record(
            mode="blocking",
            status="complete",
            reason="blocking-survivors" if blocking else "no-blocking-survivors",
            exit_code=int(blocking),
            files=sorted(scope.lines),
            scope=scope.receipt(),
            planned=1,
            ran=1,
            survived=1,
            blocking_survivors=int(blocking),
            survivors=[SURVIVOR],
        )
        records.append(record)
        mutation.write_proof(env[mutation.PROOF_ENV], record)
        assert json.loads(Path(env[mutation.PROOF_ENV]).read_text()) == record
        return "survivor diagnostics\n", int(blocking)

    return produce


class MutationReplayPersistenceTest(proof_callers.ProofCallerCases):
    def replay_path(self) -> Path:
        return self.root / ".factory" / f"mutation-{self.head}.json"

    def test_passing_inline_survivors_persist_the_exact_producer_record(self) -> None:
        self.configure()
        records: list[dict] = []
        result = self.invoke([inline_completion(records, blocking=False)])

        self.assertEqual(result["report_status"], "PASS")
        self.assertEqual(json.loads(self.replay_path().read_text()), records[0])

    def test_blocking_inline_survivors_persist_an_exact_rework_command(self) -> None:
        self.configure()
        records: list[dict] = []
        result = self.invoke([inline_completion(records, blocking=True)])

        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertEqual(json.loads(self.replay_path().read_text()), records[0])
        command = (
            "uv run --frozen --no-managed-python --group mutation "
            f"python scripts/mutation.py --replay .factory/mutation-{self.head}.json"
        )
        self.assertIn(f"mutation replay: {command}\n", self.review_reports[0])
        with mock.patch.object(dispatch, "unclaim"):
            dispatch._record_rework(witness.N, self.head, result["findings"], deferred=True)
        self.assertIn(f"mutation replay: {command}\n", dispatch.refresh_requeue_context(witness.N))

    def test_tracked_edit_after_a_valid_record_fails_without_persisting_it(self) -> None:
        self.configure()

        def edit_tracked_input(child):
            output = proof_callers.produced(
                killed=2, survived=1, survivors=[SURVIVOR],
            )(child)
            (self.root / "factory/example.py").write_text("value = 3\n")
            return output

        result = self.invoke([edit_tracked_input])

        self.assertEqual(result["report_status"], "FAIL")
        self.assertIn(
            "mutation proof: FAIL (tracked inputs changed during the run: factory/example.py)",
            self.review_reports[0],
        )
        self.assertFalse(self.replay_path().exists())

    def test_invalid_completed_record_is_not_persisted(self) -> None:
        self.configure()

        def invalid(child):
            output, returncode = proof_callers.produced()(child)
            path = Path(child.kwargs["env"][mutation.PROOF_ENV])
            record = json.loads(path.read_text())
            record["unexpected"] = True
            path.write_text(json.dumps(record))
            return output, returncode

        result = self.invoke([invalid])

        self.assertEqual(result["report_status"], "FAIL")
        self.assertFalse(self.replay_path().exists())

    def test_uncompleted_record_is_not_persisted(self) -> None:
        self.configure()
        result = self.invoke([
            proof_callers.produced(
                status="incomplete", reason="deadline", ran=1, killed=1, not_started=2,
            )
        ])

        self.assertEqual(result["report_status"], "FAIL")
        self.assertFalse(self.replay_path().exists())

    def test_finished_zero_survivor_record_is_persisted_exactly(self) -> None:
        self.configure()
        result = self.invoke([proof_callers.produced(survivors=[])])

        self.assertEqual(result["report_status"], "PASS")
        saved = json.loads(self.replay_path().read_text())
        self.assertEqual(saved["counts"]["survived"], 0)
        self.assertEqual(saved["survivors"], [])
        self.assertEqual((saved["status"], saved["reason"]), ("complete", "no-blocking-survivors"))


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplayPersistenceTest.__dict__:
        setattr(MutationReplayPersistenceTest, _name, None)
