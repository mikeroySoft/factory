"""mutation-v1 completion records: the runner producer, the gate reader and the opt-in key.

Producer cases run the real scripts.mutation.main in-process inside a tiny isolated git
repository (origin/main = base commit, HEAD = head commit): the shared
factory.mutation_scope rule runs real `git rev-parse`/`merge-base`/`diff` there and decides
applicability. Only cosmic-ray (including sample()) and the baseline suite process are
faked; draft3: the copy clone and copytree run for real so each prepared copy's tracked
bytes are verified. No mutation trial or test suite runs.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, gate, mutation_scope
from scripts import mutation
from tests.test_mutation_scope import REAL_RUN, build_repo, commit, git

FACTORY_BASE = {"factory/example.py": "value = 0\n", "docs/guide.md": "guide\n"}
FACTORY_HEAD = {"factory/example.py": "value = 1\n"}  # line 1 changed: SPEC's line
SCOPE_GIT = (["git", "rev-parse"], ["git", "merge-base"], ["git", "diff"])
NOT_STARTED_CLOCK = [0, 0, 0, 0.1, 0.2, 2, 2]  # baseline fits, no mutant starts (see deadline tests)


class TestOutcome(Enum):
    SURVIVED = "survived"
    INCOMPETENT = "incompetent"
    KILLED = "killed"


class WorkerOutcome(Enum):
    NORMAL = "normal"


def outcome(test_outcome: TestOutcome | None, output: str = "") -> SimpleNamespace:
    return SimpleNamespace(worker_outcome=WorkerOutcome.NORMAL, test_outcome=test_outcome,
                           diff="-value = 1\n+value = 2\n", output=output)


KILLED = outcome(TestOutcome.KILLED)
SURVIVED = outcome(TestOutcome.SURVIVED)
SPEC = SimpleNamespace(module_path=Path("factory/example.py"), operator_name="NumberReplacer",
                       occurrence=0, start_pos=(1, 0), end_pos=(1, 9))


def hidden() -> bool:
    return mutation.PROOF_ENV in os.environ or mutation.PROOF_BASE_ENV in os.environ


@contextlib.contextmanager
def fake_runner(result, *, clock=None, baseline_returncode: int = 0, sampled=([SPEC], 1),
                observed: list | None = None):
    """Patch only the runner's external boundaries; scripts.mutation.main and the shared
    scope rule's git commands run for real in the current (fixture) repository."""
    observed = [] if observed is None else observed

    def process(cmd, **kwargs):
        observed.append((cmd[0], hidden(), cmd[:2]))
        # draft3: the tracked-byte check (`git ls-tree`) and the copy clone run for real too,
        # since proof runs verify each prepared copy's bytes against the pinned head.
        if cmd[:2] in (*SCOPE_GIT, ["git", "ls-tree"], ["git", "clone"]):
            return REAL_RUN(cmd, **kwargs)
        if cmd[:1] == ["env"]:  # the baseline suite command
            return subprocess.CompletedProcess(cmd, baseline_returncode, "baseline output\n", "")
        raise AssertionError(f"unexpected runner process: {cmd}")

    def mutate_and_test(*_args):
        observed.append(("mutant", hidden(), None))
        return result

    attrs = types.ModuleType("attrs")
    attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
    modules = {
        "attrs": attrs, "cosmic_ray": types.ModuleType("cosmic_ray"),
        "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=mutate_and_test),
        "cosmic_ray.work_item": SimpleNamespace(TestOutcome=TestOutcome),
    }
    timer = (mock.patch.object(mutation.time, "monotonic", side_effect=clock) if clock
             else contextlib.nullcontext())
    with mock.patch.dict(sys.modules, modules), timer, \
            mock.patch.object(mutation, "WORKERS", 1), \
            mock.patch.object(mutation, "gate_timeout", return_value=61 if clock else 1200), \
            mock.patch.object(mutation, "sample", return_value=sampled), \
            mock.patch.object(mutation.subprocess, "run", process):
        yield observed


class RunnerHarness(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.base, self.head = build_repo(self.root, FACTORY_BASE, FACTORY_HEAD)
        self.proof = self.root.parent / "proof" / "record.json"
        self.proof.parent.mkdir()
        for patcher in (mock.patch.dict(os.environ), contextlib.chdir(self.root)):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def docs_only_head(self) -> None:
        git(self.root, "checkout", "-q", "--detach", self.base)
        self.head = commit(self.root, {"docs/guide.md": "edited\n"}, "docs only")

    def runner(self, argv: list[str], result=KILLED, *, proof: bool = True, base: str | None = "resolved",
               **fake) -> tuple[int, str, list]:
        if proof:
            os.environ[mutation.PROOF_ENV] = str(self.proof)
            if base is not None:  # what the gate names: its caller base, resolved
                os.environ[mutation.PROOF_BASE_ENV] = self.base if base == "resolved" else base
        out = io.StringIO()
        with fake_runner(result, **fake) as observed, contextlib.redirect_stdout(out):
            code = mutation.main(argv)
        return code, out.getvalue(), observed

    def record(self) -> dict | None:
        return json.loads(self.proof.read_text()) if self.proof.exists() else None

    def verdict(self, code: int) -> str:
        expected = mutation_scope.compute(self.base)  # the gate's own computation
        return gate.read_mutation_proof(self.proof.read_bytes() if self.proof.exists() else None,
                                        code, expected)[0]


class ProducerTest(RunnerHarness):
    def test_complete_blocking_record_matches_exit_and_passes_reader(self) -> None:
        code, _, _ = self.runner(["--blocking"])
        self.assertEqual(code, 0)
        record = self.record()
        self.assertEqual((record["mode"], record["status"], record["reason"], record["exit"]),
                         ("blocking", "complete", "no-blocking-survivors", 0))
        self.assertEqual(record["files"], ["factory/example.py"])
        self.assertEqual(record["counts"], {**dict.fromkeys(mutation.PROOF_COUNTS, 0),
                                            "planned": 1, "ran": 1, "killed": 1})
        self.assertEqual(record["scope"], mutation_scope.compute(self.base).receipt())
        self.assertEqual((record["scope"]["base"], record["scope"]["head"], record["scope"]["lines"]),
                         (self.base, self.head, 1))
        self.assertEqual(self.verdict(code), "PASS")

    def test_timeout_scored_kill_stays_killed_in_the_record(self) -> None:
        code, output, _ = self.runner(["--blocking"], outcome(TestOutcome.KILLED, "timeout"))
        self.assertIn("1 timeouts (included in killed)", output)
        self.assertEqual(self.record()["counts"]["timeouts"], 1)
        self.assertEqual(self.verdict(code), "PASS")  # legacy scoring is unchanged here

    def test_completed_blocking_survivor_exits_one_and_reader_fails(self) -> None:
        code, _, _ = self.runner(["--blocking"], SURVIVED)
        self.assertEqual(code, 1)
        self.assertEqual((self.record()["reason"], self.record()["counts"]["blocking_survivors"]),
                         ("blocking-survivors", 1))
        self.assertEqual(self.verdict(code), "FAIL")

    def test_incomplete_exit_zero_is_recorded_incomplete_and_fails(self) -> None:
        code, output, _ = self.runner(["--blocking"], clock=NOT_STARTED_CLOCK)
        self.assertEqual(code, 0)  # baseline blocking mode ignores incompleteness...
        self.assertIn("incomplete: 1 mutants not started", output)
        self.assertEqual((self.record()["status"], self.record()["counts"]["not_started"]),
                         ("incomplete", 1))
        self.assertEqual(self.verdict(code), "FAIL")  # ...the record does not

    def test_tool_failure_and_baseline_failure_are_errors(self) -> None:
        code, _, _ = self.runner(["--blocking"], outcome(TestOutcome.INCOMPETENT))
        self.assertEqual((code, self.record()["status"], self.record()["reason"]),
                         (1, "error", "tool-broken"))
        self.assertEqual(self.verdict(code), "FAIL")
        self.proof.unlink()
        code, _, _ = self.runner(["--blocking"], baseline_returncode=1)
        self.assertEqual((code, self.record()["reason"]), (1, "baseline-failed"))
        self.assertEqual(self.verdict(code), "FAIL")

    def test_invalid_equivalents_is_an_error_record(self) -> None:
        code, _, _ = self.runner(["--blocking", "--equivalents", "missing.json"])
        self.assertEqual((code, self.record()["status"], self.record()["reason"]),
                         (1, "error", "invalid-equivalents"))

    def test_report_only_record_is_not_blocking_proof(self) -> None:
        code, _, _ = self.runner([])
        self.assertEqual((code, self.record()["mode"], self.record()["status"]),
                         (0, "report-only", "complete"))
        self.assertEqual(self.verdict(code), "FAIL")

    def test_real_applicability_docs_only_writes_genuine_skip(self) -> None:
        self.docs_only_head()
        code, output, observed = self.runner(["--blocking"])
        self.assertEqual((code, output), (0, "no mutable files\n"))
        # Only the shared rule's git commands ran: no clone, baseline or mutant.
        self.assertEqual([argv for _, _, argv in observed],
                         [["git", "rev-parse"], ["git", "rev-parse"], ["git", "merge-base"], ["git", "diff"]])
        self.assertEqual((self.record()["status"], self.record()["files"]), ("skip", []))
        self.assertEqual((self.record()["scope"]["head"], self.record()["scope"]["lines"]), (self.head, 0))
        self.assertEqual(self.verdict(code), gate.SKIP_NO_FACTORY_CODE)

    def test_real_applicability_factory_change_never_yields_skip(self) -> None:
        code, output, _ = self.runner(["--blocking"])
        self.assertEqual(code, 0)
        self.assertNotIn("no mutable files", output)
        self.assertEqual((self.record()["status"], self.record()["files"]),
                         ("complete", ["factory/example.py"]))

    def test_zero_candidates_on_applicable_lines_is_complete_not_skip(self) -> None:
        code, output, _ = self.runner(["--blocking"], sampled=([], 0))
        self.assertNotIn("no mutable files", output)
        record = self.record()
        self.assertEqual((code, record["status"], record["reason"], record["counts"]["planned"]),
                         (0, "complete", "no-mutation-candidates", 0))
        self.assertEqual(record["scope"]["lines"], 1)
        verdict = gate.read_mutation_proof(self.proof.read_bytes(), code, mutation_scope.compute(self.base))
        why = "complete: 0 mutation candidates enumerated on 1 applicable changed lines (not a skip)"
        self.assertEqual(verdict, ("PASS", why))

    def test_record_without_named_base_is_an_error(self) -> None:
        code, _, observed = self.runner(["--blocking"], base=None)
        self.assertEqual((code, self.record()["status"], self.record()["reason"]),
                         (1, "error", "missing-base"))
        self.assertEqual(observed, [])  # nothing ran against a guessed base
        self.assertEqual(self.verdict(code), "FAIL")

    def test_head_moved_after_the_run_is_a_stale_record(self) -> None:
        code, _, _ = self.runner(["--blocking"])
        commit(self.root, {"factory/example.py": "value = 3\n"}, "head moves on")
        self.assertEqual(self.verdict(code), "FAIL")

    def test_record_path_is_hidden_from_every_descendant(self) -> None:
        _, _, observed = self.runner(["--blocking"])
        self.assertEqual({name for name, _, _ in observed}, {"git", "env", "mutant"})
        self.assertEqual([seen for _, seen, _ in observed], [False] * len(observed))
        self.assertFalse(hidden())
        self.proof.unlink()
        os.environ[mutation.PROOF_ENV] = str(self.proof)
        os.environ[mutation.PROOF_BASE_ENV] = self.base
        with mock.patch.object(mutation, "run_tests", return_value=0) as tests:
            self.assertEqual(mutation.main(["--test-module", "factory/example.py"]), 0)
        tests.assert_called_once()
        self.assertIsNone(self.record())  # a test-module child never writes a record

    def test_without_gate_path_no_record_is_written(self) -> None:
        code, _, _ = self.runner(["--blocking"], proof=False)
        self.assertEqual(code, 0)
        self.assertEqual(list(self.proof.parent.iterdir()), [])

    def test_crash_writes_no_record(self) -> None:
        os.environ[mutation.PROOF_ENV] = str(self.proof)
        os.environ[mutation.PROOF_BASE_ENV] = self.base
        # draft3: proof runs take their lines from scope_lines (changed_lines is unopted-only).
        with mock.patch.object(mutation, "scope_lines", side_effect=subprocess.CalledProcessError(128, "git")), \
                self.assertRaises(subprocess.CalledProcessError):
            mutation.main(["--blocking"])
        self.assertEqual(self.verdict(1), "FAIL")


SCOPE = mutation_scope.Scope("b" * 40, "c" * 40, "d" * 40, (("factory/example.py", "e" * 40, (1, 2)),))
EMPTY = mutation_scope.Scope("b" * 40, "c" * 40, "d" * 40, ())


def record(**changes) -> dict:
    base = mutation.proof_record(mode="blocking", status="complete", reason="no-blocking-survivors",
                                 exit_code=0, files=["factory/example.py"], scope=SCOPE.receipt(),
                                 planned=2, ran=2, killed=2)
    counts = changes.pop("counts", {})
    return {**base, **changes, "counts": {**base["counts"], **counts}}


SKIP = {"status": "skip", "reason": "no-applicable-files", "files": [], "scope": EMPTY.receipt(),
        "counts": {"planned": 0, "ran": 0, "killed": 0}}


def read(value, returncode: int = 0, expected: mutation_scope.Scope = SCOPE) -> str:
    data = value if isinstance(value, bytes) or value is None else json.dumps(value).encode()
    return gate.read_mutation_proof(data, returncode, expected)[0]


class ReaderTest(unittest.TestCase):
    def test_valid_complete_and_skip(self) -> None:
        self.assertEqual(read(record()), "PASS")
        self.assertEqual(read(record(**SKIP), expected=EMPTY), gate.SKIP_NO_FACTORY_CODE)

    def test_missing_oversized_and_malformed_fail(self) -> None:
        for data in (None, b"", b"\x00{truncated", b"[]", b"x" * (gate.MUTATION_PROOF_MAX_BYTES + 1),
                     json.dumps({**record(), "extra": 1}).encode()):
            self.assertEqual(read(data), "FAIL", data[:40] if data else data)
        without_scope = record()
        del without_scope["scope"]
        self.assertEqual(read(without_scope), "FAIL")  # a draft-1 record (no scope) never passes

    def test_version_protocol_and_types_fail_closed(self) -> None:
        for bad in (record(version=2), record(version=True), record(protocol="other"),
                    record(exit=False), record(files="factory/example.py"),
                    record(counts={"ran": -1}), record(counts={"ran": 2.0}), record(status=""),
                    record(scope=None), record(scope="fingerprint")):
            self.assertEqual(read(bad), "FAIL", bad)
        broken = record()
        del broken["counts"]["timeouts"]
        self.assertEqual(read(broken), "FAIL")

    def test_exit_must_equal_observed_returncode(self) -> None:
        self.assertEqual(read(record(), 1), "FAIL")
        self.assertEqual(read(record(), 124), "FAIL")  # gate timeout
        self.assertEqual(read(record(), -15), "FAIL")  # signalled child

    def test_non_blocking_and_unfinished_statuses_fail(self) -> None:
        for bad in (record(mode="report-only"), record(status="incomplete", reason="deadline"),
                    record(status="error", reason="tool-broken")):
            self.assertEqual(read(bad), "FAIL", bad)

    def test_contradictory_complete_records_fail(self) -> None:
        for bad in (
            record(counts={"ran": 1}),  # ran < planned, killed+survived != ran
            record(counts={"not_started": 1}), record(counts={"cut_short": 1}),
            record(counts={"broken": 1}), record(counts={"timeouts": 3}),
            record(files=[]), record(reason="blocking-survivors"),
            record(counts={"killed": 1, "survived": 1, "blocking_survivors": 1}),  # exit 0 claimed
            record(counts={"blocking_survivors": 1}),  # more blocking than survivors
            record(reason="no-mutation-candidates"),  # 2 planned
            record(counts={"planned": 0, "ran": 0, "killed": 0}),  # 0 planned, wrong reason
        ):
            self.assertEqual(read(bad), "FAIL", bad)

    def test_completed_blocking_survivor_is_consistent_but_fails(self) -> None:
        survivor = record(exit=1, reason="blocking-survivors",
                          counts={"killed": 1, "survived": 1, "blocking_survivors": 1})
        verdict = gate.read_mutation_proof(json.dumps(survivor).encode(), 1, SCOPE)
        self.assertEqual(verdict, ("FAIL", "complete with 1 blocking survivors"))

    def test_contradictory_skip_records_fail(self) -> None:
        for bad in ({**SKIP, "files": ["factory/example.py"]}, {**SKIP, "reason": "no mutable files"},
                    {**SKIP, "counts": {"planned": 0, "ran": 0, "killed": 0, "not_started": 1}}):
            self.assertEqual(read(record(**bad), expected=EMPTY), "FAIL", bad)
        self.assertEqual(read(record(**SKIP, exit=1), 1, EMPTY), "FAIL")
        # A skip carrying the true, nonempty scope: the gate's scope has applicable lines.
        contradictory = gate.read_mutation_proof(json.dumps(record(**{**SKIP, "scope": SCOPE.receipt()})).encode(),
                                                 0, SCOPE)
        self.assertEqual(contradictory, ("FAIL", "contradictory skip record (2 applicable changed lines)"))
        # A skip carrying an empty scope where the gate finds lines: stale/mismatched scope.
        self.assertEqual(read(record(**SKIP)), "FAIL")

    def test_stale_base_head_or_fingerprint_fail(self) -> None:
        for field in ("base", "merge_base", "head", "fingerprint", "rule", "lines"):
            stale = {**SCOPE.receipt(), field: 0 if field == "lines" else "f" * 64}
            verdict = gate.read_mutation_proof(json.dumps(record(scope=stale)).encode(), 0, SCOPE)
            self.assertEqual(verdict[0], "FAIL", field)
            self.assertIn("stale or mismatched scope", verdict[1])

    def test_zero_candidates_with_applicable_lines_passes_as_complete(self) -> None:
        zero = record(reason="no-mutation-candidates", counts={"planned": 0, "ran": 0, "killed": 0})
        self.assertEqual(read(zero), "PASS")
        self.assertEqual(read({**zero, "scope": EMPTY.receipt(), "files": []}, expected=EMPTY), "FAIL")


class GateBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {mutation.PROOF_ENV: "/forged/by/environment",
                                               mutation.PROOF_BASE_ENV: "f" * 40})
        patcher.start()
        self.addCleanup(patcher.stop)

    def launched_env(self, **kwargs) -> dict:
        seen = {}

        def popen(cmd, **popen_kwargs):
            seen.update(popen_kwargs["env"])
            return SimpleNamespace(pid=1, returncode=0, communicate=lambda timeout=None: ("", None),
                                   poll=lambda: 0)

        with mock.patch.object(gate.lifecycle, "current", return_value=None), \
                mock.patch.object(gate.subprocess, "Popen", popen):
            gate.timed(["check"], **kwargs)
        return seen

    def test_gate_and_runner_share_one_transport_name(self) -> None:
        self.assertEqual(gate.MUTATION_PROOF_ENV, mutation.PROOF_ENV)
        self.assertEqual(gate.MUTATION_PROOF_BASE_ENV, mutation.PROOF_BASE_ENV)
        self.assertEqual(gate.MUTATION_PROOF_COUNTS, mutation.PROOF_COUNTS)
        self.assertEqual(set(mutation.proof_record(mode="m", status="s", reason="r", exit_code=0)),
                         gate.MUTATION_PROOF_KEYS)

    def test_inherited_record_path_and_base_never_reach_a_check(self) -> None:
        plain = self.launched_env()
        self.assertNotIn(mutation.PROOF_ENV, plain)
        self.assertNotIn(mutation.PROOF_BASE_ENV, plain)
        named = self.launched_env(proof_path=Path("/gate/proof.json"), proof_base="a" * 40)
        self.assertEqual((named[mutation.PROOF_ENV], named[mutation.PROOF_BASE_ENV]),
                         ("/gate/proof.json", "a" * 40))

    def proof_run(self, reason: str | None, returncode: int = 0,
                  scope=None) -> tuple[bool, str, str, str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve() / "repo"
        build_repo(root, FACTORY_BASE, FACTORY_HEAD)

        def timed(cmd, proof_path=None, proof_base=None):
            receipt = scope or mutation_scope.compute(proof_base).receipt()
            mutation.write_proof(str(proof_path), record(exit=returncode, scope=receipt))
            return subprocess.CompletedProcess(cmd, returncode, "apparent completion\n", "")

        execution = SimpleNamespace(outcome="unknown" if reason else "completed", reason=reason)
        with contextlib.chdir(root), mock.patch.object(gate, "timed", timed), \
                mock.patch.object(gate, "_trusted_mutation_command", lambda cmd, *_: cmd), \
                mock.patch.object(gate.lifecycle, "current", return_value=execution):
            return gate.run_mutation_proof(["mutation"], "origin/main")

    def test_lifecycle_timeout_or_signal_overrides_a_complete_record(self) -> None:
        passed, _, status, binding = self.proof_run(None)
        self.assertEqual((passed, status), (True, "PASS"))
        self.assertRegex(binding, r"^ base=[0-9a-f]{40} head=[0-9a-f]{40} scope=[0-9a-f]{64}$")
        for reason in ("check_timeout", "check_terminated_by_signal"):
            passed, output, status, binding = self.proof_run(reason)
            self.assertEqual((passed, status, binding), (False, "FAIL", ""))
            self.assertIn(f"mutation proof: FAIL ({reason} overrides", output)

    def test_unresolvable_caller_base_fails_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory), \
                mock.patch.object(gate, "timed", side_effect=AssertionError("ran")), \
                mock.patch.object(gate.lifecycle, "current", return_value=None):
            passed, output, status, binding = gate.run_mutation_proof(["mutation"], "origin/main")
        self.assertEqual((passed, status, binding), (False, "FAIL", ""))
        self.assertIn("has no resolvable scope", output)

    def test_run_keeps_its_two_tuple_contract(self) -> None:
        completed = subprocess.CompletedProcess(["x"], 3, "out", "")
        with mock.patch.object(gate, "timed", return_value=completed), \
                mock.patch.object(gate.lifecycle, "current", return_value=None):
            self.assertEqual(gate.run(["x"]), (False, "out"))


class OptInConfigTest(unittest.TestCase):
    def load(self, check: str) -> config.Config:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as host:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".factory.toml").write_text(
                '[repo]\nslug = "acme/widgets"\n\n[[gate.check]]\nname = "mutation"\n'
                'run = ["runner"]\n' + check,
            )
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": host}):
                return config.load(root)

    def test_proof_key_is_explicit_and_pr_only(self) -> None:
        self.assertIsNone(self.load('when = "pr"\n').checks[0].proof)
        self.assertEqual(self.load('when = "pr"\nproof = "mutation-v1"\n').checks[0].proof,
                         config.MUTATION_PROOF)
        for bad in ('when = "pr"\nproof = "mutation"\n', 'proof = "mutation-v1"\n'):
            with self.assertRaises(config.ConfigError):
                self.load(bad)
        self.assertEqual(config.unknown_keys({"gate": {"check": [{"name": "m", "proof": "x"}]}}), [])


if __name__ == "__main__":
    unittest.main()
