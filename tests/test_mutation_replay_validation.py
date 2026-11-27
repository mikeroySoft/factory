"""Strict survivor metadata validation and replay-record persistence."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from factory import gate
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_caller as proof_callers


SURVIVOR = {
    "file": "factory/example.py",
    "line": 1,
    "operator": "NumberReplacer",
    "start_pos": [1, 0],
    "end_pos": [1, 1],
    "original": "1",
    "mutated": "2",
}


class MutationReplayValidationTest(proof_callers.ProofCallerCases):
    def verdict(self, survivors, *, survived=1, blocking=0, returncode=0):
        scope = self.scope()
        record = mutation.proof_record(
            mode="blocking",
            status="complete",
            reason="blocking-survivors" if blocking else "no-blocking-survivors",
            exit_code=returncode,
            files=sorted(scope.lines),
            scope=scope.receipt(),
            planned=survived,
            ran=survived,
            survived=survived,
            blocking_survivors=blocking,
            survivors=survivors,
        )
        if survivors is None:
            record["survivors"] = None
        return gate.read_mutation_proof(json.dumps(record).encode(), returncode, scope)

    def replay_path(self):
        return self.root / ".factory" / f"mutation-{self.head}.json"

    def test_valid_blocking_survivor_metadata_preserves_exact_failure(self) -> None:
        self.configure()
        self.assertEqual(
            self.verdict([SURVIVOR], blocking=1, returncode=1),
            ("FAIL", "complete with 1 blocking survivors"),
        )

    def test_valid_nonblocking_survivor_metadata_passes_at_position_edges(self) -> None:
        self.configure()
        self.assertEqual(
            self.verdict([SURVIVOR]),
            ("PASS", "complete: ran 1 of 1 mutants, 0 killed (0 by timeout), 1 survived"),
        )

    def test_matching_large_line_values_do_not_depend_on_integer_identity(self) -> None:
        self.configure()
        line = int("100000")
        start_line = int("100000")
        survivor = {**SURVIVOR, "line": line, "start_pos": [start_line, 0], "end_pos": [100000, 1]}
        self.assertEqual(
            self.verdict([survivor]),
            ("PASS", "complete: ran 1 of 1 mutants, 0 killed (0 by timeout), 1 survived"),
        )

    def test_valid_survivor_count_above_integer_cache_is_accepted(self) -> None:
        self.configure()
        count = int("257")
        survivors = [copy.deepcopy(SURVIVOR) for _ in range(count)]
        self.assertEqual(
            self.verdict(survivors, survived=count),
            ("PASS", "complete: ran 257 of 257 mutants, 0 killed (0 by timeout), 257 survived"),
        )

    def test_survivor_container_and_count_are_exact(self) -> None:
        self.configure()
        cases = {
            "none": (None, 1),
            "dict": ({"entry": SURVIVOR}, 1),
            "string": ("survivor", 1),
            "too-few": ([SURVIVOR], 2),
            "too-many": ([SURVIVOR, SURVIVOR], 1),
        }
        for name, (survivors, count) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.verdict(survivors, survived=count),
                    ("FAIL", "malformed survivors metadata"),
                )

    def test_survivor_entries_require_exact_object_keys(self) -> None:
        self.configure()
        missing = copy.deepcopy(SURVIVOR)
        del missing["operator"]
        extra = {**SURVIVOR, "unexpected": True}
        for name, entry in (("none", None), ("string", "survivor"),
                            ("missing-key", missing), ("extra-key", extra)):
            with self.subTest(name=name):
                self.assertEqual(
                    self.verdict([entry]),
                    ("FAIL", "malformed survivors metadata"),
                )

    def test_survivor_scalar_fields_are_strict(self) -> None:
        self.configure()
        same_original = "".join(["same", " source"])
        same_mutated = bytearray(b"same source").decode()
        self.assertEqual(same_original, same_mutated)
        self.assertIsNot(same_original, same_mutated)
        cases = {
            "file-mismatch": {"file": "factory/other.py"},
            "line-bool": {"line": True},
            "line-negative": {"line": -1},
            "operator-empty": {"operator": ""},
            "line-zero": {"line": 0},
            "operator-nonstring": {"operator": 1},
            "original-nonstring": {"original": 1},
            "mutated-nonstring": {"mutated": 2},
            "equal-distinct-source": {"original": same_original, "mutated": same_mutated},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.verdict([{**SURVIVOR, **changes}]),
                    ("FAIL", "malformed survivors metadata"),
                )

    def test_each_position_shape_component_and_bound_is_validated(self) -> None:
        self.configure()
        cases = {
            "start-type": {"start_pos": "1:0"},
            "start-short": {"start_pos": [1]},
            "start-long": {"start_pos": [1, 0, 1]},
            "start-row-type": {"start_pos": [True, 0]},
            "start-column-type": {"start_pos": [1, False]},
            "start-row-bound": {"start_pos": [0, 0]},
            "start-column-bound": {"start_pos": [1, -1]},
            "end-type": {"end_pos": "1:1"},
            "end-short": {"end_pos": [1]},
            "end-long": {"end_pos": [1, 1, 2]},
            "end-row-type": {"end_pos": [True, 1]},
            "end-column-type": {"end_pos": [1, False]},
            "end-row-bound": {"end_pos": [0, 1]},
            "end-column-bound": {"end_pos": [1, -1]},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.verdict([{**SURVIVOR, **changes}]),
                    ("FAIL", "malformed survivors metadata"),
                )

    def test_line_must_match_start_and_span_must_be_forward(self) -> None:
        self.configure()
        cases = {
            "line-start-mismatch": {"line": 2},
            "empty-span": {"start_pos": [1, 1], "end_pos": [1, 1]},
            "backward-span": {"line": 2, "start_pos": [2, 0], "end_pos": [1, 1]},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.verdict([{**SURVIVOR, **changes}]),
                    ("FAIL", "malformed survivors metadata"),
                )

    def test_only_survivors_is_an_allowed_optional_completion_key(self) -> None:
        self.configure()
        scope = self.scope()
        record = mutation.proof_record(
            mode="blocking", status="complete", reason="no-blocking-survivors", exit_code=0,
            files=sorted(scope.lines), scope=scope.receipt(), planned=1, ran=1, killed=1,
        )
        self.assertEqual(
            gate.read_mutation_proof(json.dumps(record).encode(), 0, scope),
            ("PASS", "complete: ran 1 of 1 mutants, 1 killed (0 by timeout), 0 survived"),
        )
        record["unexpected"] = True
        self.assertEqual(
            gate.read_mutation_proof(json.dumps(record).encode(), 0, scope),
            ("FAIL", "malformed completion record"),
        )

    def test_pass_without_survivors_is_not_persisted(self) -> None:
        self.configure()
        result = self.invoke([proof_callers.produced()])
        self.assertEqual(result["report_status"], "PASS")
        self.assertFalse(self.replay_path().exists())

    def test_malformed_survivors_fail_and_are_not_persisted(self) -> None:
        self.configure()

        def malformed(child):
            output, returncode = proof_callers.produced(survivors=[SURVIVOR])(child)
            path = child.kwargs["env"][mutation.PROOF_ENV]
            record = json.loads(Path(path).read_text())
            record["survivors"] = "not-a-list"
            mutation.write_proof(path, record)
            return output, returncode

        result = self.invoke([malformed])
        self.assertEqual(result["report_status"], "FAIL")
        self.assertIn("mutation proof: FAIL (malformed survivors metadata)", self.review_reports[0])
        self.assertFalse(self.replay_path().exists())

    def test_unfinished_survivor_record_fails_and_is_not_persisted(self) -> None:
        self.configure()
        result = self.invoke([proof_callers.produced(
            status="incomplete", reason="deadline", planned=3, ran=1, killed=0,
            survived=1, not_started=2, survivors=[SURVIVOR],
        )])
        self.assertEqual(result["report_status"], "FAIL")
        self.assertIn("mutation proof: FAIL (incomplete (deadline))", self.review_reports[0])
        self.assertFalse(self.replay_path().exists())

    def test_valid_pass_with_survivors_is_persisted(self) -> None:
        self.configure()
        result = self.invoke([proof_callers.produced(killed=2, survived=1, survivors=[SURVIVOR])])
        self.assertEqual(result["report_status"], "PASS")
        record = json.loads(self.replay_path().read_text())
        self.assertEqual(record["survivors"], [SURVIVOR])
        self.assertEqual((record["status"], record["reason"]),
                         ("complete", "no-blocking-survivors"))

    def test_valid_multiline_and_positive_column_spans_are_persisted(self) -> None:
        self.configure()
        entries = [
            {**SURVIVOR, "start_pos": [1, 2], "end_pos": [1, 3]},
            {**SURVIVOR, "start_pos": [1, 2], "end_pos": [2, 0]},
            {**SURVIVOR, "line": 2, "start_pos": [2, 0], "end_pos": [2, 1]},
        ]
        result = self.invoke([proof_callers.produced(
            killed=0, survived=3, survivors=entries,
        )])
        self.assertEqual(result["report_status"], "PASS")
        self.assertEqual(json.loads(self.replay_path().read_text())["survivors"], entries)

    def test_start_row_above_line_and_negative_multiline_end_column_are_rejected(self) -> None:
        self.configure()
        for changes in (
            {"line": 1, "start_pos": [2, 0], "end_pos": [2, 1]},
            {"start_pos": [1, 0], "end_pos": [2, -1]},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(
                    self.verdict([{**SURVIVOR, **changes}]),
                    ("FAIL", "malformed survivors metadata"),
                )


for _name in [name for name in dir(proof_callers.ProofCallerCases) if name.startswith("test_")]:
    if _name not in MutationReplayValidationTest.__dict__:
        setattr(MutationReplayValidationTest, _name, None)
