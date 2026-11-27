"""Large survivor payload transport through the real mutation caller witness."""
from __future__ import annotations

import hashlib
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


def large_survivor_step(child, corrupt=None):
    env = child.kwargs["env"]
    scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
    entries = survivors()
    record = mutation.proof_record(
        mode="blocking", status="complete", reason="blocking-survivors", exit_code=1,
        files=sorted(scope.lines), scope=scope.receipt(), planned=200, ran=200,
        survived=200, blocking_survivors=200, survivors=entries,
    )
    mutation.write_proof(env[mutation.PROOF_ENV], record)
    proof = Path(env[mutation.PROOF_ENV])
    core = proof.read_bytes()
    assert len(core) <= gate.MUTATION_PROOF_MAX_BYTES
    assert "survivors_sha256" in json.loads(core)
    if corrupt:
        corrupt(proof.with_suffix(".survivors.json"))
    return "200 survivors\n", 1


def rebind_sidecar(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    proof = path.parent / "proof.json"
    record = json.loads(proof.read_text())
    record["survivors_sha256"] = hashlib.sha256(data).hexdigest()
    proof.write_text(json.dumps(record, sort_keys=True))


class MutationReplayTransportTest(proof_callers.ProofCallerCases):
    def test_large_survivors_reach_durable_replay_and_rework_exactly(self) -> None:
        self.configure()
        result = self.invoke([large_survivor_step])
        durable = self.root / ".factory" / f"mutation-{self.head}.json"
        saved = json.loads(durable.read_text())
        self.assertEqual(saved["survivors"], survivors())
        self.assertNotIn("survivors_sha256", saved)
        command = (
            "uv run --frozen --no-managed-python --group mutation "
            f"python scripts/mutation.py --replay .factory/mutation-{self.head}.json"
        )
        self.assertIn(command, self.review_reports[0])
        with mock.patch.object(dispatch, "unclaim"):
            dispatch._record_rework(witness.N, self.head, result["findings"], deferred=True)
        self.assertIn(command, dispatch.refresh_requeue_context(witness.N))

    def test_invalid_large_survivor_sidecar_fails_closed(self) -> None:
        self.configure()
        result = self.invoke([
            lambda child: large_survivor_step(child, lambda path: path.write_bytes(b"[]"))
        ])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("survivors sidecar digest mismatch", self.review_reports[0])
        self.assertFalse((self.root / ".factory" / f"mutation-{self.head}.json").exists())

    def assert_invalid_sidecar(self, corrupt, reason: str) -> None:
        self.configure()
        result = self.invoke([lambda child: large_survivor_step(child, corrupt)])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn(f"mutation proof: FAIL ({reason}", self.review_reports[0])
        self.assertFalse((self.root / ".factory" / f"mutation-{self.head}.json").exists())

    def test_missing_large_survivor_sidecar_reports_missing_file(self) -> None:
        self.assert_invalid_sidecar(
            lambda path: path.unlink(),
            "invalid survivors sidecar: [Errno 2] No such file or directory",
        )

    def test_symlink_large_survivor_sidecar_reports_nofollow_refusal(self) -> None:
        def symlink(path: Path) -> None:
            path.unlink()
            path.symlink_to(path.parent / "proof.json")

        self.assert_invalid_sidecar(
            symlink, "invalid survivors sidecar: [Errno 40] Too many levels of symbolic links",
        )

    def test_nonregular_large_survivor_sidecar_reports_type_refusal(self) -> None:
        def directory(path: Path) -> None:
            path.unlink()
            path.mkdir()

        self.assert_invalid_sidecar(
            directory, "invalid survivors sidecar: survivors sidecar is not a regular file",
        )

    def test_oversized_large_survivor_sidecar_reports_bound(self) -> None:
        self.assert_invalid_sidecar(
            lambda path: path.write_bytes(b"x" * (16 * 1024 * 1024 + 1)),
            "invalid survivors sidecar: oversized survivors sidecar",
        )

    def test_malformed_large_survivor_json_with_valid_digest_is_rejected(self) -> None:
        self.assert_invalid_sidecar(
            lambda path: rebind_sidecar(path, b"{"),
            "invalid survivors sidecar: Expecting property name enclosed in double quotes",
        )

    def test_invalid_large_survivor_count_with_valid_digest_is_rejected(self) -> None:
        def remove_survivor(path: Path) -> None:
            entries = json.loads(path.read_text())
            rebind_sidecar(path, json.dumps(entries[:-1], sort_keys=True).encode())

        self.assert_invalid_sidecar(
            remove_survivor, "invalid survivors sidecar: malformed survivors sidecar",
        )

    def test_digest_metadata_is_strict_and_exclusive(self) -> None:
        self.configure()
        scope = self.scope()
        base = mutation.proof_record(
            mode="blocking", status="complete", reason="blocking-survivors", exit_code=1,
            files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1,
            survived=1, blocking_survivors=1, survivors=[survivors()[0]],
        )
        cases = {
            "exclusive": {**base, "survivors_sha256": "0" * 64},
            "uppercase": {
                **{key: value for key, value in base.items() if key != "survivors"},
                "survivors_sha256": "A" * 64,
            },
            "short": {
                **{key: value for key, value in base.items() if key != "survivors"},
                "survivors_sha256": "0" * 63,
            },
            "non-string": {
                **{key: value for key, value in base.items() if key != "survivors"},
                "survivors_sha256": 0,
            },
        }
        for name, record in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    gate.read_mutation_proof(json.dumps(record).encode(), 1, scope),
                    ("FAIL", "malformed survivors metadata"),
                )

    def test_core_completion_limit_remains_64_kib(self) -> None:
        self.configure()
        scope = self.scope()
        record = mutation.proof_record(
            mode="blocking", status="complete", reason="no-blocking-survivors", exit_code=0,
            files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1, killed=1,
        )
        record["padding"] = "x" * gate.MUTATION_PROOF_MAX_BYTES
        data = json.dumps(record).encode()
        self.assertEqual(
            gate.read_mutation_proof(data, 0, scope), ("FAIL", "oversized completion record")
        )


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplayTransportTest.__dict__:
        setattr(MutationReplayTransportTest, _name, None)
