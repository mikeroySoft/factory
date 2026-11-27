"""Durable mutation replay records through the real caller boundary."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from factory import dispatch, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_caller as proof_callers
from tests import test_mutation_replay_transport as transport


def sidecar_completion(records: list[dict], *, blocking: bool):
    def produce(child):
        env = child.kwargs["env"]
        scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
        entries = transport.survivors()
        count = len(entries)
        record = mutation.proof_record(
            mode="blocking",
            status="complete",
            reason="blocking-survivors" if blocking else "no-blocking-survivors",
            exit_code=int(blocking),
            files=sorted(scope.lines),
            scope=scope.receipt(),
            planned=count,
            ran=count,
            survived=count,
            blocking_survivors=count if blocking else 0,
            survivors=entries,
        )
        records.append(record)
        mutation.write_proof(env[mutation.PROOF_ENV], record)
        assert "survivors_sha256" in json.loads(Path(env[mutation.PROOF_ENV]).read_text())
        return "survivor diagnostics\n", int(blocking)

    return produce


class MutationReplayPersistenceTest(proof_callers.ProofCallerCases):
    def replay_path(self) -> Path:
        return self.root / ".factory" / f"mutation-{self.head}.json"

    def test_passing_sidecar_persists_the_exact_producer_record(self) -> None:
        self.configure()
        records: list[dict] = []
        result = self.invoke([sidecar_completion(records, blocking=False)])

        self.assertEqual(result["report_status"], "PASS")
        saved = json.loads(self.replay_path().read_text())
        self.assertEqual(saved, records[0])
        self.assertEqual(
            (saved["protocol"], saved["version"]),
            (mutation.PROOF_PROTOCOL, mutation.PROOF_VERSION),
        )
        self.assertIn("survivors", saved)
        self.assertNotIn("survivors_sha256", saved)

    def test_blocking_sidecar_persists_an_exact_rework_command(self) -> None:
        self.configure()
        records: list[dict] = []
        result = self.invoke([sidecar_completion(records, blocking=True)])

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
                killed=2,
                survived=1,
                survivors=[transport.survivors()[0]],
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


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplayPersistenceTest.__dict__:
        setattr(MutationReplayPersistenceTest, _name, None)
