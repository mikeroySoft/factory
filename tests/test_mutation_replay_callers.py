"""Survivor replay records through the gate and dispatcher caller seams."""
from __future__ import annotations

import json
from unittest import mock

from factory import dispatch, gate, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_caller as proof_callers

SURVIVOR = {
    "file": "factory/example.py",
    "line": 1,
    "operator": "NumberReplacer",
    "start_pos": [1, 0],
    "end_pos": [1, 9],
    "original": "value = 1\n",
    "mutated": "value = 2\n",
}


def survivor_step(child):
    env = child.kwargs["env"]
    scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
    record = mutation.proof_record(
        mode="blocking", status="complete", reason="blocking-survivors", exit_code=1,
        files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1, survived=1,
        blocking_survivors=1,
    )
    record["survivors"] = [SURVIVOR]
    mutation.write_proof(env[mutation.PROOF_ENV], record)
    return (
        ("factory/example.py:1: NumberReplacer `value = 1` -> `value = 2`\n"
         "ran 1 of 1 mutants in 1s: 0 timeouts (included in killed); 0 killed; 1 survived\n"),
        1,
    )


class MutationReplayCallerTest(proof_callers.ProofCallerCases):
    def test_optional_survivors_are_strictly_bounded_metadata(self) -> None:
        self.configure()
        scope = self.scope()
        record = mutation.proof_record(
            mode="blocking", status="complete", reason="blocking-survivors", exit_code=1,
            files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1, survived=1,
            blocking_survivors=1,
        )
        record["survivors"] = [SURVIVOR]
        self.assertEqual(gate.read_mutation_proof(json.dumps(record).encode(), 1, scope)[0], "FAIL")
        record["unexpected"] = True
        self.assertEqual(
            gate.read_mutation_proof(json.dumps(record).encode(), 1, scope),
            ("FAIL", "malformed completion record"),
        )

    def test_survivor_record_is_durable_and_rework_names_exact_replay(self) -> None:
        self.configure()
        result = self.invoke([survivor_step])
        path = self.root / ".factory" / f"mutation-{self.head}.json"
        command = (
            "uv run --frozen --no-managed-python --group mutation "
            f"python scripts/mutation.py --replay .factory/mutation-{self.head}.json"
        )
        self.assertEqual(json.loads(path.read_text())["survivors"], [SURVIVOR])
        self.assertIn(command, self.review_reports[0])
        with mock.patch.object(dispatch, "unclaim"):
            dispatch._record_rework(witness.N, self.head, result["findings"], deferred=True)
        brief = dispatch.refresh_requeue_context(witness.N)
        self.assertIn(f".factory/mutation-{self.head}.json", brief)
        self.assertIn(command, brief)

    def test_replay_stdout_without_a_completion_record_fails_fresh_pr_gate(self) -> None:
        self.configure()
        diagnostic = (
            "factory/example.py:1: survived NumberReplacer\n"
            "replayed 1 of 1 applicable survivors (diagnostic only; no proof written)\n"
        )
        result = self.invoke([(diagnostic, 0)])
        self.assertEqual(len(self.children), 1)
        self.assertEqual(result["report_status"], "FAIL")
        self.assertEqual(self.summary(result), witness.BLOCKED)


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplayCallerTest.__dict__:
        setattr(MutationReplayCallerTest, _name, None)
