"""Survivor sidecar size boundaries through the gate and dispatcher."""
from __future__ import annotations

import json
from pathlib import Path

from factory import mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_caller as proof_callers
from tests.test_mutation_replay_callers import SURVIVOR

MIB = 1024 * 1024
SIDECAR_LIMIT = 16 * MIB


def survivor_step(original: str, sizes: list[int]):
    def produce(child):
        env = child.kwargs["env"]
        scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
        record = mutation.proof_record(
            mode="blocking", status="complete", reason="blocking-survivors", exit_code=1,
            files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1,
            survived=1, blocking_survivors=1,
            survivors=[{**SURVIVOR, "original": original}],
        )
        mutation.write_proof(env[mutation.PROOF_ENV], record)
        proof = Path(env[mutation.PROOF_ENV])
        sizes.append(proof.with_suffix(".survivors.json").stat().st_size)
        return "1 survivor\n", 1

    return produce


class MutationReplaySidecarBoundsTest(proof_callers.ProofCallerCases):
    def test_valid_sidecar_between_fifteen_and_sixteen_mib_reaches_replay(self) -> None:
        self.configure()
        original = "o" * (15 * MIB)
        sizes: list[int] = []

        result = self.invoke([survivor_step(original, sizes)])

        self.assertGreater(sizes[0], 15 * MIB)
        self.assertLessEqual(sizes[0], SIDECAR_LIMIT)
        self.assertEqual(self.summary(result), witness.BLOCKED)
        durable = self.root / ".factory" / f"mutation-{self.head}.json"
        self.assertEqual(json.loads(durable.read_text())["survivors"][0]["original"], original)

    def test_valid_json_sidecar_over_sixteen_mib_fails_closed(self) -> None:
        self.configure()
        sizes: list[int] = []

        result = self.invoke([survivor_step("o" * SIDECAR_LIMIT, sizes)])

        self.assertGreater(sizes[0], SIDECAR_LIMIT)
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn(
            "invalid survivors sidecar: oversized survivors sidecar",
            self.review_reports[0],
        )
        self.assertFalse(
            (self.root / ".factory" / f"mutation-{self.head}.json").exists()
        )

    def test_valid_sidecar_exactly_sixteen_mib_reaches_replay(self) -> None:
        self.configure()
        overhead = len(json.dumps([{**SURVIVOR, "original": ""}], sort_keys=True).encode())
        original = "o" * (SIDECAR_LIMIT - overhead)
        sizes: list[int] = []

        result = self.invoke([survivor_step(original, sizes)])

        self.assertEqual(sizes, [SIDECAR_LIMIT])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        durable = self.root / ".factory" / f"mutation-{self.head}.json"
        self.assertEqual(json.loads(durable.read_text())["survivors"][0]["original"], original)

    def test_sidecar_one_byte_over_sixteen_mib_is_not_persisted(self) -> None:
        self.configure()
        overhead = len(json.dumps([{**SURVIVOR, "original": ""}], sort_keys=True).encode())
        sizes: list[int] = []

        result = self.invoke([survivor_step("o" * (SIDECAR_LIMIT - overhead + 1), sizes)])

        self.assertEqual(sizes, [SIDECAR_LIMIT + 1])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("invalid survivors sidecar: oversized survivors sidecar", self.review_reports[0])
        self.assertFalse((self.root / ".factory" / f"mutation-{self.head}.json").exists())


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplaySidecarBoundsTest.__dict__:
        setattr(MutationReplaySidecarBoundsTest, _name, None)
