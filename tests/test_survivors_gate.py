"""factory.gate: behaviour pinned by surviving mutants of the mutation-proof reader and the gate.

Reader cases call the real gate.read_mutation_proof / _read_proof_file on literal records and
real temp files. _proof_run and run_mutation_proof replace only the check process (gate.timed)
and the lifecycle scope; the scope rule runs real git in a tiny fixture repository.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest import mock

from factory import gate, mutation_scope
from tests.test_gate import gate as run_gate
from tests.test_gate import repo_with_test
from tests.test_mutation_scope import build_repo, commit

SCOPE = mutation_scope.Scope("b" * 40, "c" * 40, "d" * 40,
                             (("factory/example.py", "e" * 40, (1, 2)),))
MALFORMED = ("FAIL", "malformed or unsupported completion record")
CONTRADICTORY = ("FAIL", "contradictory complete record")
PASSED = ("PASS", "complete: ran 2 of 2 mutants, 2 killed (0 by timeout), 0 survived")


def record(**changes) -> dict:
    """A consistent complete blocking record for SCOPE (2 planned, 2 ran, 2 killed)."""
    counts = {**dict.fromkeys(gate.MUTATION_PROOF_COUNTS, 0), "planned": 2, "ran": 2, "killed": 2,
              **changes.pop("counts", {})}
    return {"protocol": "factory-mutation-proof", "version": 1, "mode": "blocking",
            "status": "complete", "reason": "no-blocking-survivors", "exit": 0,
            "files": ["factory/example.py"], "counts": counts, "scope": SCOPE.receipt(), **changes}


def padded(value: dict, size: int) -> bytes:
    """`value` as JSON followed by insignificant whitespace, exactly `size` bytes long."""
    data = json.dumps(value).encode()
    assert len(data) <= size, (len(data), size)
    data += b" " * (size - len(data))
    assert len(data) == size, (len(data), size)
    return data


def read(value: dict | bytes, returncode: int = 0) -> tuple[str, str]:
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    return gate.read_mutation_proof(data, returncode, SCOPE)


class ProofSizeLimitTest(unittest.TestCase):
    def test_limit_is_64_kib(self) -> None:
        self.assertEqual(gate.MUTATION_PROOF_MAX_BYTES, 65536)

    def test_valid_record_of_a_few_kib_is_accepted(self) -> None:
        self.assertEqual(read(padded(record(), 4096)), PASSED)

    def test_valid_record_of_exactly_the_limit_is_accepted(self) -> None:
        self.assertEqual(read(padded(record(), gate.MUTATION_PROOF_MAX_BYTES)), PASSED)

    def test_valid_record_one_byte_over_the_limit_is_oversized(self) -> None:
        data = padded(record(), gate.MUTATION_PROOF_MAX_BYTES + 1)
        self.assertEqual(read(data), ("FAIL", "oversized completion record"))


class ProofCountsTest(unittest.TestCase):
    def test_negative_count_is_malformed(self) -> None:
        counts = {**record()["counts"], "timeouts": -1}
        self.assertIsNone(gate._proof_counts(counts))
        self.assertEqual(read(record(counts={"timeouts": -1})), MALFORMED)

    def test_non_string_or_empty_mode_status_reason_is_malformed(self) -> None:
        for bad in (record(status=["complete"]), record(reason=["no-blocking-survivors"]),
                    record(mode=["blocking"]), record(reason="")):
            with self.subTest(bad=bad):
                self.assertEqual(read(bad), MALFORMED)

    def test_more_ran_than_planned_is_contradictory(self) -> None:
        self.assertEqual(read(record(counts={"ran": 3, "killed": 3})), CONTRADICTORY)

    def test_non_blocking_survivors_below_survived_pass(self) -> None:
        tolerated = record(counts={"killed": 0, "survived": 2, "blocking_survivors": 0})
        self.assertEqual(read(tolerated),
                         ("PASS", "complete: ran 2 of 2 mutants, 0 killed (0 by timeout), 2 survived"))

    def test_exit_one_without_blocking_survivors_is_contradictory(self) -> None:
        self.assertEqual(read(record(exit=1), 1), CONTRADICTORY)

    def test_exit_status_false_is_compared_by_value_like_zero(self) -> None:
        self.assertEqual(read(record(), False), PASSED)


class ReadProofFileTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_file_of_a_few_kib_is_read_whole_and_accepted(self) -> None:
        path = self.directory / "proof.json"
        path.write_bytes(padded(record(), 4096))
        data = gate._read_proof_file(path)
        self.assertEqual(len(data), 4096)
        self.assertEqual(read(data), PASSED)

    def test_symlinked_record_path_is_not_followed_and_fails_closed(self) -> None:
        target = self.directory / "elsewhere.json"
        target.write_bytes(json.dumps(record()).encode())
        link = self.directory / "proof.json"
        link.symlink_to(target)
        data = gate._read_proof_file(link)
        self.assertTrue(data.startswith(b"unreadable: "), data[:80])
        self.assertEqual(read(data), ("FAIL", "malformed completion record"))

    def test_bounded_read_of_a_larger_file_returns_exactly_limit_plus_one_bytes(self) -> None:
        path = self.directory / "proof.json"
        path.write_bytes(b" " * (3 * gate.MUTATION_PROOF_MAX_BYTES))
        data = gate._read_proof_file(path)
        self.assertEqual(len(data), gate.MUTATION_PROOF_MAX_BYTES + 1)
        self.assertEqual(read(data), ("FAIL", "oversized completion record"))


class ProofRunTest(unittest.TestCase):
    def test_failed_record_keeps_its_reason_when_head_also_moves(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve() / "repo"
        build_repo(root, {"factory/example.py": "value = 0\n"}, {"factory/example.py": "value = 1\n"})

        def timed(cmd, proof_path=None, proof_base=None):  # writes no record; moves HEAD
            commit(root, {"factory/example.py": "value = 3\n"}, "head moves on")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with contextlib.chdir(root), mock.patch.object(gate, "timed", timed):
            _, status, why, _ = gate._proof_run(["mutation"], "origin/main")
        self.assertEqual((status, why), ("FAIL", "missing completion record"))


class RunMutationProofLifecycleTest(unittest.TestCase):
    def fails_before_running(self, execution: SimpleNamespace) -> tuple[bool, str, str, str]:
        """run_mutation_proof from a temp cwd, where the proof fails before any check runs.

        Whether the scope fails to resolve (not a checkout) or resolves and then fails on its
        tracked inputs, the proof FAILs without starting the check (timed raises if called).
        lifecycle.current (a context variable, not an OS boundary) is replaced by a stand-in
        with only outcome/reason: a real Execution comes from lifecycle.scope, which writes
        journal rows and inherits identity from FACTORY_LIFECYCLE_CONTEXT in the environment.
        These tests observe only how the failed proof rewrites outcome and reason.
        """
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                mock.patch.object(gate, "timed", side_effect=AssertionError("ran")), \
                mock.patch.object(gate.lifecycle, "current", return_value=execution):
            return gate.run_mutation_proof(["mutation"], "origin/main")

    def test_failed_proof_under_completed_execution_is_product_feedback(self) -> None:
        execution = SimpleNamespace(outcome="completed", reason=None)
        passed, _, status, _ = self.fails_before_running(execution)
        self.assertEqual((passed, status), (False, "FAIL"))
        self.assertEqual((execution.outcome, execution.reason),
                         ("product_feedback", "configured_check_failed"))

    def test_failed_proof_keeps_an_earlier_non_completed_outcome(self) -> None:
        execution = SimpleNamespace(outcome="unknown", reason="git_diff_failed")
        passed, _, status, _ = self.fails_before_running(execution)
        self.assertEqual((passed, status), (False, "FAIL"))
        self.assertEqual((execution.outcome, execution.reason), ("unknown", "git_diff_failed"))


class VerifyTransitionTest(unittest.TestCase):
    RAW: ClassVar[dict[str, tuple[str, str, str]]] = {gate.CONFIG_NAME: ("100644", "100644", "M")}

    def refusal(self, base_blob: str, head_blob: str) -> str:
        blobs = {f"{gate.REVIEWED_TRANSITION_BASE}:{gate.CONFIG_NAME}": base_blob,
                 f"HEAD:{gate.CONFIG_NAME}": head_blob}

        def timed(cmd):  # the git boundary: every ref resolves to the reviewed base
            ref = cmd[-1]
            out = blobs.get(ref, gate.REVIEWED_TRANSITION_BASE)
            return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

        with mock.patch.object(gate, "timed", timed):
            return gate._verify_transition("origin/main", gate.REVIEWED_TRANSITION,
                                           gate.REVIEWED_TRANSITION_BASE, self.RAW)

    def test_refusal_names_committed_base_blob_then_head_blob(self) -> None:
        path, pre, post = gate.REVIEWED_TRANSITION
        prefix = (f"protected transition {path}={pre}:{post} "
                  f"(base {gate.REVIEWED_TRANSITION_BASE}) refused: ")
        self.assertEqual(self.refusal("a" * 40, "f" * 40),
                         prefix + f"committed {'a' * 40} -> {'f' * 40}")
        self.assertEqual(self.refusal("a" * 40, ""), prefix + f"committed {'a' * 40} -> -")


class TierHeaderTest(unittest.TestCase):
    def test_explicit_attempt_tier_on_the_command_line_writes_the_plain_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory))
            code, out, report = run_gate(repo, "--tier", "attempt",
                                         "--skip", "conflict-markers,leak-scan")
        self.assertEqual(code, 0, report + out)
        self.assertEqual(report.splitlines()[0], "# Gate report")


if __name__ == "__main__":
    unittest.main()
