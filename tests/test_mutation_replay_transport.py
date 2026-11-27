"""Bounded survivor transport through the real mutation caller witness."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from factory import dispatch, gate, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_caller as proof_callers


def survivors() -> list[dict]:
    return [
        {
            "file": "factory/example.py",
            "line": 1,
            "operator": f"LongOperator{i}",
            "start_pos": [1, 0],
            "end_pos": [1, 9],
            "original": f"original-{i}-" + "o" * 400,
            "mutated": f"mutated-{i}-" + "m" * 400,
        }
        for i in range(200)
    ]


def large_survivor_step(child):
    env = child.kwargs["env"]
    scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
    entries = survivors()
    mutation.write_proof(
        env[mutation.PROOF_ENV],
        mutation.proof_record(
            mode="blocking", status="complete", reason="blocking-survivors", exit_code=1,
            files=sorted(scope.lines), scope=scope.receipt(), planned=len(entries),
            ran=len(entries), survived=len(entries), blocking_survivors=len(entries),
            survivors=entries,
        ),
    )
    proof = Path(env[mutation.PROOF_ENV])
    record = json.loads(proof.read_bytes())
    assert proof.stat().st_size <= gate.MUTATION_PROOF_MAX_BYTES
    assert record["counts"]["survived"] == len(entries)
    assert record["counts"]["blocking_survivors"] == len(entries)
    assert record["survivors_truncated"] is True
    assert "survivors" not in record
    assert not proof.with_suffix(".survivors.json").exists()
    return "200 survivors\n", 1


class MutationReplayTransportTest(proof_callers.ProofCallerCases):
    def replay_path(self) -> Path:
        return self.root / ".factory" / f"mutation-{self.head}.json"

    def test_oversized_survivors_persist_bounded_record_and_exact_rework_command(self) -> None:
        self.configure()
        produced: list[bytes] = []

        def produce(child):
            outcome = large_survivor_step(child)
            produced.append(Path(child.kwargs["env"][mutation.PROOF_ENV]).read_bytes())
            return outcome

        result = self.invoke([produce])
        saved_bytes = self.replay_path().read_bytes()
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertEqual(saved_bytes, produced[0])
        saved = json.loads(saved_bytes)
        self.assertLessEqual(len(saved_bytes), gate.MUTATION_PROOF_MAX_BYTES)
        self.assertEqual(saved["counts"]["survived"], len(survivors()))
        self.assertEqual(saved["counts"]["blocking_survivors"], len(survivors()))
        self.assertIs(saved["survivors_truncated"], True)
        self.assertNotIn("survivors", saved)
        self.assertFalse(self.replay_path().with_suffix(".survivors.json").exists())
        command = (
            "uv run --frozen --no-managed-python --group mutation "
            f"python scripts/mutation.py --replay .factory/mutation-{self.head}.json"
        )
        self.assertIn(f"mutation replay: {command}\n", self.review_reports[0])
        with mock.patch.object(dispatch, "unclaim"):
            dispatch._record_rework(witness.N, self.head, result["findings"], deferred=True)
        self.assertIn(f"mutation replay: {command}\n", dispatch.refresh_requeue_context(witness.N))

    def assert_tampered_marker_rejected(self, changes: dict) -> None:
        self.configure()

        def produce(child):
            large_survivor_step(child)
            path = Path(child.kwargs["env"][mutation.PROOF_ENV])
            record = json.loads(path.read_text())
            record.update(changes)
            path.write_text(json.dumps(record, sort_keys=True))
            return "tampered survivor record\n", 1

        result = self.invoke([produce])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (malformed survivors metadata", self.review_reports[0])
        self.assertFalse(self.replay_path().exists())

    def test_false_truncation_marker_is_rejected_without_persistence(self) -> None:
        self.assert_tampered_marker_rejected({"survivors_truncated": False})

    def test_nonboolean_truncation_marker_is_rejected_without_persistence(self) -> None:
        self.assert_tampered_marker_rejected({"survivors_truncated": 1})

    def test_survivor_list_and_truncation_marker_are_rejected_without_persistence(self) -> None:
        self.assert_tampered_marker_rejected({
            "survivors_truncated": True,
            "survivors": [survivors()[0]],
        })

    def test_zero_survivor_truncation_marker_is_rejected_without_persistence(self) -> None:
        self.configure()

        def produce(child):
            env = child.kwargs["env"]
            scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
            record = mutation.proof_record(
                mode="blocking", status="complete", reason="no-blocking-survivors",
                exit_code=0, files=sorted(scope.lines), scope=scope.receipt(),
                planned=1, ran=1, killed=1,
            )
            record["survivors_truncated"] = True
            mutation.write_proof(env[mutation.PROOF_ENV], record)
            return "no survivors\n", 0

        result = self.invoke([produce])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (malformed survivors metadata", self.review_reports[0])
        self.assertFalse(self.replay_path().exists())

    def test_core_completion_limit_remains_64_kib(self) -> None:
        self.configure()
        scope = self.scope()
        record = mutation.proof_record(
            mode="blocking", status="complete", reason="no-blocking-survivors", exit_code=0,
            files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1, killed=1,
        )
        record["padding"] = "x" * gate.MUTATION_PROOF_MAX_BYTES
        self.assertEqual(
            gate.read_mutation_proof(json.dumps(record).encode(), 0, scope),
            ("FAIL", "oversized completion record"),
        )


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplayTransportTest.__dict__:
        setattr(MutationReplayTransportTest, _name, None)
