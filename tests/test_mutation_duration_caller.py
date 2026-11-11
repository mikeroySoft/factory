"""Explicit mutation duration through the actual dispatcher caller chain (tests first, v2).

Reuses the approved witness harness (tests/test_mutation_caller_witness.py): real
dispatch.pr_gate_and_review -> dispatch.run_gate -> factory.cli.main(["gate", ...]) ->
gate.main (parser) / execute / timed / run_mutation_proof -> report -> risk-check row ->
dispatch._head_evidence_matches. Faked boundaries only: the gate child process (FakeChild,
scripted communicate()), os.killpg (recorder), toolchain probes, the reviewer, config.load
(fixture Config modelling the repository-owned `proof = "mutation-v1"` opt-in) and fixture
journal rows. The in-process gate's SystemExit is turned into the exit code a real
`python -m factory gate` child would return (the process boundary). The fixture root is a
tiny real git repository. "Clocked" mutation children run the real scripts.mutation.main
in-process with a fake monotonic clock, faked baseline suite, faked enumeration cost and
faked cosmic-ray; the shared scope rule, copy clone (real git, derived timeout recorded
only) and record are real.

Invocation under test: an explicit, manual Python API keyword (absent/None = legacy):
  dispatch.pr_gate_and_review(..., mutation_duration=SECONDS)
    # forwarded only when the head is High and the repository opted in (proof run);
    # Medium, Low, unopted and custom same-name checks ignore it, valid or not
  -> dispatch.run_gate(..., mutation_proof=True, mutation_duration=SECONDS)
    # forwards the value as-is; returns (passed, text) as today, never raises for it
  -> `factory gate ... --mutation-proof --mutation-duration SECONDS`
    # the one validation point: finite and > the 60s reserve, else usage exit 2
  -> the opted proof check alone: enclosing timeout SECONDS, runner argv `--duration SECONDS`;
     gate.CHECK_TIMEOUT and every other check's timeout untouched; no outer run_gate timeout.
Existing production call sites pass nothing, so no daemon, CLI dispatch flag, Config
setting or policy adopts a duration automatically.

An invalid explicit value on the opted path reaches the real gate parser, which exits 2
without writing a report or launching any check; run_gate's existing "did not write" path
returns (False, text) and the caller records risk-check mutation FAIL + pr_gate FAIL and
returns REVISE: no review, no APPROVE, no head acceptance, no mutant child.

Warm cache: with a qualified complete proof for the same head/base already in the caller's
real pr_runs mapping (primed through the same caller with approving reviewers), an invalid
explicit value still fails closed for the new attempt (REVISE, risk-check FAIL + FAIL, no
review, no APPROVE, no head acceptance, no new mutant child); it never returns the cached
approval. Where it is rejected (gate parser, or safely earlier) is not pinned. Absent/None
keeps the existing cached reuse.

Feature cases are expected to FAIL (RED) on baseline aeee1ce (no such keyword/option); a
missing keyword or option is trapped into an explicit failure message. Controls pass there.
"""
from __future__ import annotations

import contextlib
import io
import math
import os
import signal
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import cli, config, dispatch, gate, lifecycle, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_proof_config as real_config
from tests.test_mutation_proof import KILLED, SPEC
from tests.test_mutation_proof import TestOutcome as Outcome
from tests.test_mutation_scope import REAL_RUN, git

N = witness.N
REPO = list(witness.REPO_MUTATION_RUN)
LINT = config.Check("lint", ["./lint"], when="pr")
INVALID = (0, -1, 59.99, 60, 60.0, math.nan, math.inf, -math.inf)
TIMED_OUT = r"check timed out after 1800(\.0)?s"


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def clocked_runner(seen: dict, *, sample_cost: float = 0.0, clone_cost: float = 0.0,
                   baseline_cost: float = 0.0):
    """The real scripts.mutation.main for the child the gate launched (its argv and env),
    with a fake clock; records the clone/baseline/mutant budgets the runner derived."""
    def step(child) -> tuple[str, int]:
        argv = child.args[child.args.index("scripts/mutation.py") + 1:]
        seen.setdefault("argv", []).append(argv)
        Path("mutation-equivalents.json").write_text("[]\n")
        clock = Clock()

        def process(cmd, **kwargs):
            if cmd[:2] == ["git", "clone"]:
                seen.setdefault("clone", []).append(kwargs.get("timeout"))
                done = REAL_RUN(cmd, **{k: v for k, v in kwargs.items() if k != "timeout"})
                clock.advance(clone_cost)
                return done
            if cmd[:2] in (["git", "rev-parse"], ["git", "merge-base"], ["git", "diff"],
                           ["git", "ls-tree"]):
                return REAL_RUN(cmd, **kwargs)
            if cmd[:1] == ["env"]:
                seen.setdefault("baseline", []).append(kwargs.get("timeout"))
                clock.advance(baseline_cost)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected runner process: {cmd}")

        def sample(_lines, _scratch):
            clock.advance(sample_cost)
            return [SPEC], 1

        def mutate_and_test(_mutations, _command, timeout):
            seen.setdefault("mutant", []).append(timeout)
            return KILLED

        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        modules = {"attrs": attrs, "cosmic_ray": types.ModuleType("cosmic_ray"),
                   "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=mutate_and_test),
                   "cosmic_ray.work_item": SimpleNamespace(TestOutcome=Outcome)}
        out = io.StringIO()
        with mock.patch.dict(os.environ, child.kwargs["env"], clear=True), \
                mock.patch.dict(sys.modules, modules), \
                mock.patch.object(mutation.time, "monotonic", clock), \
                mock.patch.object(mutation, "WORKERS", 1), \
                mock.patch.object(mutation, "sample", sample), \
                mock.patch.object(mutation.subprocess, "run", process), \
                contextlib.redirect_stdout(out):
            code = mutation.main(argv)
        return out.getvalue(), code
    return step


def flag_value(argv: list[str], flag: str) -> tuple[str, int] | None:
    """`(value, tokens)` for `flag VALUE` or `flag=VALUE` in argv, else None."""
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return argv[index + 1], 2
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1], 1
    return None


class _DurationHarness(witness.MutationCallerWitness):
    def setUp(self) -> None:
        super().setUp()
        self.gate_cmds: list[list[str]] = []
        self.gate_outer_timeouts: list = []
        self.seen: dict = {}

    def _dispatch_run(self, cmd, cwd=None, check=True, timeout=None, **kwargs):
        if cmd[:6] != witness.GATE_ARGV:
            return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)
        self.gate_cmds.append(list(cmd))
        self.gate_outer_timeouts.append(timeout)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)
        except SystemExit as exc:  # a real gate child process exits with this code
            code = exc.code if isinstance(exc.code, int) else 1
            self.gate_exits.append(code)
            return subprocess.CompletedProcess(cmd, code, "", err.getvalue())

    def configure_checks(self, checks: list[config.Check], tier: str = "High") -> None:
        self.configure(tier=tier)
        self.cfg.checks = checks
        dispatch.configure(self.cfg)

    def trap(self, exc: TypeError, where: str) -> None:
        if "mutation_duration" in str(exc):
            self.fail(f"{where} has no mutation_duration parameter yet: {exc}")
        raise exc

    def invoke_with(self, *steps, pr_runs: dict | None = None, **kwargs) -> dict:
        """witness.invoke, except the real PR caller receives `kwargs`."""
        self.scripts.extend(steps)
        spawned = len(self.children) + len(self.unscripted_children)
        pr_runs = {} if pr_runs is None else pr_runs
        head = self.head
        try:
            returned = dispatch.pr_gate_and_review(
                self.root, N, "attempt report", head, pr_runs, **kwargs)
        except TypeError as exc:
            self.trap(exc, "dispatch.pr_gate_and_review")
        self.assert_steps_consumed(spawned)
        self.assertEqual((type(returned), len(returned)), (tuple, 3))  # existing contract
        verdict, findings, _ = returned
        events = lifecycle.read_events(dispatch.EVENTS)
        risk = [e for e in events if e.get("event") == "risk-check" and e.get("head") == head]
        paths = [line.split("\t")[-1] for line in self.changed.splitlines()]
        return {
            "returned": returned, "verdict": verdict, "findings": findings,
            "gate_exit": self.gate_exits[-1] if self.gate_exits else None,
            "report_status": dispatch._mutation_status(pr_runs[head][1]) if head in pr_runs else None,
            "risk": [(e.get("mutation"), e.get("pr_gate")) for e in risk],
            "head_accepted": dispatch._head_evidence_matches(events, N, head, changed_paths=paths),
        }

    def call_run_gate(self, **kwargs) -> tuple[bool, str]:
        try:
            returned = dispatch.run_gate(self.root, f"{N}-pr", tier="pr", blocking_mutation=True,
                                         base="origin/main", **kwargs)
        except TypeError as exc:
            self.trap(exc, "dispatch.run_gate")
        ok, text = returned
        self.assertEqual((type(ok), type(text)), (bool, str))  # existing (passed, output)
        return ok, text

    def gate_cli(self, *extra: str) -> int:
        argv = ["gate", "--base", "origin/main", "--report", ".factory/duration.md",
                "--tier", "pr", "--blocking-mutation", "--mutation-proof", *extra]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                return cli.main(argv)
        except SystemExit as exc:
            self.fail(f"gate parser refused {extra!r} (exit {exc.code}): {err.getvalue().strip()}")

    def duration_of(self, argv: list[str], flag: str) -> float:
        found = flag_value(argv, flag)
        self.assertIsNotNone(found, f"{flag} not in {argv!r}")
        return float(found[0])

    def assert_same_duration(self, argv: list[str], flag: str, value: float) -> None:
        got = self.duration_of(argv, flag)
        if math.isnan(value):
            self.assertTrue(math.isnan(got), got)
        else:
            self.assertEqual(got, value)

    def assert_runner_argv(self, child, value: float) -> None:
        """Exactly the legacy argv plus one duration specification, nothing else."""
        self.assertEqual(child.args[:len(REPO) + 1], [*REPO, "--blocking"])
        rest = child.args[len(REPO) + 1:]
        found = flag_value(rest, "--duration")
        self.assertIsNotNone(found, f"--duration not in {child.args!r}")
        self.assertEqual(len(rest), found[1])
        self.assertEqual(float(found[0]), value)

    def risk_rows(self) -> list[dict]:
        return [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"]

    def assert_legacy_gate_command(self) -> None:
        for cmd in self.gate_cmds:
            self.assertIsNone(flag_value(cmd, "--mutation-duration"), cmd)
            self.assertIsNone(flag_value(cmd, "--check-timeout"), cmd)


# The witness's own cases run once, from their module; this harness adds duration cases only.
for _name in [name for name in dir(witness.MutationCallerWitness) if name.startswith("test_")]:
    setattr(_DurationHarness, _name, None)


class DurationCallerCases(_DurationHarness):
    # -- controls: no explicit duration is the legacy route ---------------------------
    def test_control_opted_high_without_duration_is_unchanged(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen)])
        self.assertIn("--mutation-proof", self.gate_cmds[0])
        self.assert_legacy_gate_command()
        self.assertEqual(self.gate_outer_timeouts, [None])
        self.assertEqual(self.children[0].args, [*REPO, "--blocking"])
        self.assertEqual(self.children[0].timeouts, [self.cfg.check_timeout])
        self.assertEqual(self.cfg.check_timeout, 1200)
        self.assertEqual((self.seen["clone"], self.seen["baseline"]), ([1140.0], [1140.0]))
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_control_run_gate_without_duration_emits_no_flag(self) -> None:
        self.configure()
        self.scripts.append([witness.COMPLETE_PASS])
        ok, text = dispatch.run_gate(self.root, f"{N}-pr", tier="pr", blocking_mutation=True,
                                     base="origin/main", mutation_proof=True)
        self.assertEqual((type(ok), type(text), ok), (bool, str, True))
        self.assert_legacy_gate_command()
        self.assertEqual(self.children[0].timeouts, [1200])

    def test_control_gate_check_timeout_still_bounds_the_proof_check(self) -> None:
        self.configure_checks([LINT, config.Check("mutation", REPO, when="pr",
                                                  proof=config.MUTATION_PROOF)])
        self.scripts.extend([[("lint ok\n", 0)], [witness.COMPLETE_PASS]])
        self.assertEqual(self.gate_cli("--check-timeout", "300"), 0)
        self.assertEqual([c.timeouts for c in self.children], [[300], [300]])

    # -- feature: the opted PR caller forwards one explicit total ---------------------
    def test_explicit_duration_reaches_runner_and_only_the_proof_check_timeout(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen)], mutation_duration=1800)
        cmd = self.gate_cmds[0]
        self.assertIn("--mutation-proof", cmd)
        self.assert_same_duration(cmd, "--mutation-duration", 1800)
        self.assertIsNone(flag_value(cmd, "--check-timeout"))  # never a global budget change
        self.assertEqual(self.gate_outer_timeouts, [None])  # no new outer run_gate timeout
        child = self.children[0]
        self.assert_runner_argv(child, 1800)
        self.assertEqual(child.timeouts, [1800])  # the enclosing timeout of this check only
        self.assertEqual((self.seen["clone"], self.seen["baseline"], self.seen["mutant"]),
                         ([1740.0], [1740.0], [60.0]))
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        scope = mutation_scope.compute(self.base, self.root)
        row = self.risk_rows()[-1]
        self.assertEqual((row["mutation_proof"], row["mutation_base"], row["mutation_scope"]),
                         (config.MUTATION_PROOF, self.base, scope.fingerprint))
        self.assertIn(f"Mutation proof: mutation PASS base={self.base} head={self.head} "
                      f"scope={scope.fingerprint}", self.review_reports[0])
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_explicit_none_is_the_legacy_route(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen)], mutation_duration=None)
        self.assert_legacy_gate_command()
        self.assertEqual((self.children[0].args, self.children[0].timeouts),
                         ([*REPO, "--blocking"], [1200]))
        self.assertEqual(self.seen["clone"], [1140.0])
        self.assertEqual(self.summary(result)["approve"], True)

    def test_fractional_duration_is_honoured_exactly_end_to_end(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen)], mutation_duration=60.25)
        self.assert_same_duration(self.gate_cmds[0], "--mutation-duration", 60.25)
        self.assert_runner_argv(self.children[0], 60.25)
        self.assertEqual(self.children[0].timeouts, [60.25])
        # Exactly total - 60: never clamped up to the legacy 1s, never a new minimum.
        self.assertEqual((self.seen["clone"], self.seen["baseline"], self.seen["mutant"]),
                         ([0.25], [0.25], [0.0625]))
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_explicit_duration_never_reaches_other_pr_checks(self) -> None:
        self.configure_checks([LINT, config.Check("mutation", REPO, when="pr",
                                                  proof=config.MUTATION_PROOF)])
        result = self.invoke_with([("lint ok\n", 0)], [clocked_runner(self.seen)],
                                  mutation_duration=1800)
        lint, proved = self.children
        self.assertEqual((lint.args, lint.timeouts), (["./lint"], [1200]))
        self.assertEqual(proved.timeouts, [1800])
        self.assert_runner_argv(proved, 1800)
        self.assertIn("- lint: PASS", self.review_reports[0])
        self.assertEqual(self.summary(result)["approve"], True)
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_gate_duration_and_check_timeout_are_independent(self) -> None:
        self.configure_checks([LINT, config.Check("mutation", REPO, when="pr",
                                                  proof=config.MUTATION_PROOF)])
        self.scripts.extend([[("lint ok\n", 0)], [witness.COMPLETE_PASS]])
        code = self.gate_cli("--mutation-duration", "1800", "--check-timeout", "300")
        self.assertEqual(code, 0)
        self.assertEqual([c.timeouts for c in self.children], [[300], [1800]])
        self.assertEqual(self.children[0].args, ["./lint"])
        self.assertEqual(gate.CHECK_TIMEOUT, 300)  # existing --check-timeout semantics only
        report = (self.root / ".factory/duration.md").read_text()
        self.assertIn(f"Mutation proof: mutation PASS base={self.base} head={self.head}", report)

    def test_gate_duration_never_reaches_an_unopted_same_name_check(self) -> None:
        self.configure_checks([LINT, config.Check("mutation", REPO, when="pr", proof=None)])
        self.scripts.extend([[("lint ok\n", 0)], [("mutation ok\n", 0)]])
        self.assertEqual(self.gate_cli("--mutation-duration", "1800"), 0)
        self.assertEqual([c.timeouts for c in self.children], [[1200], [1200]])
        self.assertEqual([c.args for c in self.children], [["./lint"], [*REPO, "--blocking"]])
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_run_gate_forwards_valid_duration_with_proof(self) -> None:
        self.configure()
        self.scripts.append([witness.COMPLETE_PASS])
        ok, _ = self.call_run_gate(mutation_proof=True, mutation_duration=1800)
        self.assertTrue(ok)
        self.assert_same_duration(self.gate_cmds[0], "--mutation-duration", 1800)
        self.assertEqual(self.children[0].timeouts, [1800])
        self.assertEqual(self.gate_outer_timeouts, [None])

    # -- feature: incomplete / timeout under an explicit total still blocks -----------
    def test_runner_expiry_under_explicit_duration_blocks_despite_approving_reviewers(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen, baseline_cost=50)], mutation_duration=100)
        self.assertEqual(self.children[0].timeouts, [100])
        self.assertEqual(self.seen["baseline"], [40.0])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertNotEqual(result["verdict"], "APPROVE")
        self.assertIn("mutation proof: FAIL (incomplete (baseline-deadline))", self.review_reports[0])
        self.assertEqual(self.risk_rows()[-1]["mutation_proof"], config.MUTATION_PROOF)
        self.assertNotIn("mutation_scope", self.risk_rows()[-1])

    def test_preparation_exhausting_a_fractional_window_blocks(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen, clone_cost=0.5)], mutation_duration=60.5)
        self.assertEqual(self.children[0].timeouts, [60.5])
        self.assertEqual((self.seen["clone"], self.seen.get("baseline"), self.seen.get("mutant")),
                         ([0.5], None, None))
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (incomplete (baseline-deadline))", self.review_reports[0])
        self.assertNotIn("mutation_scope", self.risk_rows()[-1])

    def test_enumeration_exhausting_a_fractional_window_blocks(self) -> None:
        self.configure()
        result = self.invoke_with([clocked_runner(self.seen, sample_cost=0.5)], mutation_duration=60.5)
        self.assertEqual((self.seen.get("clone"), self.seen.get("baseline")), (None, None))
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (incomplete (baseline-deadline))", self.review_reports[0])

    def test_incomplete_exit0_record_under_explicit_duration_blocks(self) -> None:
        self.configure()
        incomplete = witness.produced("ran 1 of 3 mutants\n", 0, status="incomplete", reason="deadline",
                                      planned=3, ran=1, killed=1, not_started=2)
        result = self.invoke_with([incomplete], mutation_duration=1800)
        self.assertEqual(self.children[0].timeouts, [1800])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (incomplete (deadline))", self.review_reports[0])

    def test_enclosing_timeout_uses_explicit_duration_and_blocks(self) -> None:
        self.configure()
        timeout = subprocess.TimeoutExpired(REPO, 1800)
        result = self.invoke_with([timeout, witness.COMPLETE_PASS], mutation_duration=1800)
        self.assertEqual(self.children[0].timeouts, [1800, None])
        self.assertEqual(self.killpg_calls, [(witness.FAKE_PID, signal.SIGKILL)])
        self.assertRegex(self.review_reports[0], TIMED_OUT)
        self.assertIn("mutation proof: FAIL (check_timeout overrides", self.review_reports[0])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    # -- feature: unopted, Medium and custom runs ignore the explicit total -----------
    def test_medium_opted_repository_ignores_duration_and_stays_report_only(self) -> None:
        for value in (1800, math.nan):  # ignored even when it would be invalid
            with self.subTest(value=value):
                self.setUp_fresh(tier="Medium")
                result = self.invoke_with([witness.INCOMPLETE_EXIT0], mutation_duration=value)
                self.assertNotIn("--mutation-proof", self.gate_cmds[0])
                self.assert_legacy_gate_command()
                self.assertEqual((self.children[0].args, self.children[0].timeouts), (REPO, [1200]))
                self.assertNotIn("Mutation proof:", self.review_reports[0])
                self.assertEqual(result["risk"], [])
                # Report-only permission (Medium evidence), never a completed proof.
                self.assertEqual((result["verdict"], result["head_accepted"]), ("APPROVE", True))

    def test_unopted_custom_check_ignores_duration(self) -> None:
        custom = ["./custom-mutation-check", "--strict"]
        for value in (1800, math.nan):
            with self.subTest(value=value):
                self.setUp_fresh(check_run=custom, proof=None)
                result = self.invoke_with([("custom tool: ok\n", 0)], mutation_duration=value)
                self.assertNotIn("--mutation-proof", self.gate_cmds[0])
                self.assert_legacy_gate_command()
                self.assertEqual((self.children[0].args, self.children[0].timeouts),
                                 ([*custom, "--blocking"], [1200]))
                self.assertNotIn("mutation_proof", self.risk_rows()[-1])
                self.assertEqual(self.summary(result), {
                    "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})

    def test_unopted_same_name_repository_runner_ignores_duration(self) -> None:
        self.configure(proof=None)  # the repository's own runner command, without the opt-in
        result = self.invoke_with([("mutation ok\n", 0)], mutation_duration=1800)
        self.assertNotIn("--mutation-proof", self.gate_cmds[0])
        self.assert_legacy_gate_command()
        self.assertEqual((self.children[0].args, self.children[0].timeouts),
                         ([*REPO, "--blocking"], [1200]))
        self.assertNotIn("mutation_proof", self.risk_rows()[-1])
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)
        self.assertEqual(self.summary(result)["gate_exit"], 0)

    # -- feature: invalid explicit totals fail closed, contracts intact ---------------
    def test_invalid_duration_on_the_opted_path_fails_closed_through_the_real_parser(self) -> None:
        for value in INVALID:
            with self.subTest(value=value):
                self.setUp_fresh()
                result = self.invoke_with(mutation_duration=value)
                cmd = self.gate_cmds[-1]
                self.assertIn("--mutation-proof", cmd)
                self.assert_same_duration(cmd, "--mutation-duration", value)
                self.assertEqual(result["gate_exit"], 2)  # the gate's usage rejection
                self.assertEqual((self.children, self.review_reports), ([], []))
                self.assertEqual(result["returned"][0], "REVISE")
                self.assertIs(result["returned"][2], True)
                self.assertIn("(exit 2)", result["findings"])
                self.assertIn("--mutation-duration", result["findings"])
                self.assertEqual(self.summary(result), {
                    "approve": False, "head_accepted": False, "gate_exit": 2,
                    "risk": [("FAIL", "FAIL")]})
                row = self.risk_rows()[-1]
                self.assertEqual(row["mutation_proof"], config.MUTATION_PROOF)
                self.assertNotIn("mutation_scope", row)
                self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_invalid_duration_never_reuses_a_cached_qualified_approval(self) -> None:
        # Bounded: legacy and valid-explicit primes, two invalid values; one fixture repo.
        for prime in ({}, {"mutation_duration": 1800}):
            for value in (math.nan, 60):
                with self.subTest(prime=prime, value=value):
                    self.setUp_fresh()
                    head = self.head
                    pr_runs: dict = {}
                    # 1. Prime a qualified complete proof through the actual caller.
                    primed = self.invoke_with([clocked_runner({})], pr_runs=pr_runs, **prime)
                    self.assertEqual(self.summary(primed), {
                        "approve": True, "head_accepted": True, "gate_exit": 0,
                        "risk": [("PASS", "PASS")]})
                    scope = mutation_scope.compute(self.base, self.root)
                    self.assertIs(pr_runs[head][0], True)
                    self.assertEqual(dispatch._mutation_proof(pr_runs[head][1], head, self.base),
                                     ("PASS", scope.fingerprint))
                    self.assertEqual(len(self.children), 1)

                    # 2. Control: absent (None) explicit value keeps the existing cached reuse.
                    dispatch._PENDING_REVIEWS.clear()
                    dispatch._PENDING_SECOND_REVIEWS.clear()
                    cmds = len(self.gate_cmds)
                    reused = self.invoke_with(pr_runs=pr_runs, mutation_duration=None)
                    self.assertEqual((len(self.gate_cmds), len(self.children)), (cmds, 1))
                    self.assertEqual((reused["verdict"], reused["head_accepted"]), ("APPROVE", True))
                    self.assertEqual(reused["risk"][-1], ("PASS", "PASS"))

                    # 3. Same cache, same opted High caller, invalid explicit value.
                    dispatch._PENDING_REVIEWS.clear()
                    dispatch._PENDING_SECOND_REVIEWS.clear()
                    cmds, exits = len(self.gate_cmds), len(self.gate_exits)
                    reviews, rows = len(self.review_reports), len(self.risk_rows())
                    result = self.invoke_with(pr_runs=pr_runs, mutation_duration=value)
                    self.assertEqual(result["returned"][0], "REVISE")  # never the cached APPROVE
                    self.assertIs(result["returned"][2], True)
                    self.assertRegex(result["findings"], r"mutation[-_]duration")
                    self.assertEqual(len(self.children), 1)  # no new mutation child
                    self.assertEqual(len(self.review_reports), reviews)  # no review run
                    for cmd in self.gate_cmds[cmds:]:  # if the gate was reached, as requested
                        self.assertIn("--mutation-proof", cmd)
                        self.assert_same_duration(cmd, "--mutation-duration", value)
                    for code in self.gate_exits[exits:]:
                        self.assertNotEqual(code, 0)
                    new_rows = self.risk_rows()[rows:]
                    self.assertEqual([(r.get("mutation"), r.get("pr_gate")) for r in new_rows],
                                     [("FAIL", "FAIL")])
                    self.assertEqual(new_rows[0]["mutation_proof"], config.MUTATION_PROOF)
                    self.assertNotIn("mutation_scope", new_rows[0])
                    self.assertEqual(result["risk"][-1], ("FAIL", "FAIL"))
                    self.assertIs(result["head_accepted"], False)
                    self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_run_gate_with_duration_but_no_proof_returns_failure(self) -> None:
        self.configure()
        ok, text = self.call_run_gate(mutation_proof=False, mutation_duration=1800)
        self.assertFalse(ok)
        self.assertEqual((self.gate_exits, self.children), ([2], []))
        self.assertIn("--mutation-duration", text)
        self.assertIn("--mutation-proof", text)

    def test_run_gate_with_invalid_duration_returns_failure(self) -> None:
        self.configure()
        for value in INVALID:
            with self.subTest(value=value):
                ok, text = self.call_run_gate(mutation_proof=True, mutation_duration=value)
                self.assertFalse(ok)
                self.assertEqual(self.gate_exits[-1], 2)
                self.assertIn("--mutation-duration", text)
        self.assertEqual(self.children, [])

    # -- helpers ---------------------------------------------------------------------
    def setUp_fresh(self, **configure) -> None:
        """A clean per-subTest slate in the same fixture repository and journal."""
        self.gate_cmds.clear()
        self.gate_outer_timeouts.clear()
        self.children.clear()
        self.review_reports.clear()
        for name in witness.DISPATCH_CONTAINERS:
            getattr(dispatch, name).clear()
        self.configure(**configure)


class RealConfigDurationCases(real_config.RealConfigCallerTest):
    """The same explicit-duration route, but through the existing REAL config.load harness
    (tests/test_mutation_proof_config.py): the dispatcher and the in-process gate both load
    the MAIN checkout's `.factory.toml` pinned at the fixture origin/main; the ticket head is
    a linked worktree. Nothing reconstructs Config or stubs load here. Faked boundaries are
    the harness's own (processes, reviewer, journal rows) plus clocked_runner's fake clock."""

    # Borrowed unchanged from the fixture-config duration harness (plain helper methods).
    invoke_with = _DurationHarness.invoke_with
    trap = _DurationHarness.trap
    duration_of = _DurationHarness.duration_of
    assert_same_duration = _DurationHarness.assert_same_duration
    assert_runner_argv = _DurationHarness.assert_runner_argv
    assert_legacy_gate_command = _DurationHarness.assert_legacy_gate_command
    risk_rows = _DurationHarness.risk_rows

    def setUp(self) -> None:
        super().setUp()
        self.gate_outer_timeouts: list = []
        self.seen: dict = {}

    def _dispatch_run(self, cmd, cwd=None, check=True, timeout=None, **kwargs):
        if cmd[:6] == witness.GATE_ARGV:  # the parent already records the gate argv
            self.gate_outer_timeouts.append(timeout)
        return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)

    def test_real_config_opted_caller_forwards_explicit_duration_to_the_proof_check(self) -> None:
        self.build(main_config=real_config.CANDIDATE, worktree_config=real_config.CANDIDATE)
        self.configure()
        # Real loaded context: main checkout at origin/main owns the opt-in; candidate untouched.
        self.assertEqual(self.cfg.root, self.main.resolve())
        self.assertEqual(git(self.main, "rev-parse", "HEAD"), git(self.main, "rev-parse", "origin/main"))
        self.assertEqual((self.main / config.CONFIG_NAME).read_text(), real_config.CANDIDATE)
        self.assertEqual((self.root / config.CONFIG_NAME).read_text(), real_config.CANDIDATE)
        check = self.mutation_check()
        self.assertEqual((check.when, check.proof, check.run), ("pr", config.MUTATION_PROOF, REPO))
        self.assertEqual(self.cfg.check_timeout, 1200)

        result = self.invoke_with([clocked_runner(self.seen)], mutation_duration=1800)
        argv = self.gate_cmds[0]
        self.assertEqual(argv[:6], witness.GATE_ARGV)
        for flag in ("--mutation-proof", "--blocking-mutation"):
            self.assertIn(flag, argv)
        self.assertEqual((argv[argv.index("--tier") + 1], argv[argv.index("--base") + 1]),
                         ("pr", "origin/main"))
        self.assert_same_duration(argv, "--mutation-duration", 1800)
        self.assertIsNone(flag_value(argv, "--check-timeout"))
        self.assertEqual(self.gate_outer_timeouts, [None])
        self.assertEqual(self.loaded_roots, [self.main.resolve()])  # the gate's real load
        self.assertEqual(len(self.children), 1)
        child = self.children[0]
        self.assert_runner_argv(child, 1800)
        self.assertEqual(child.timeouts, [1800])
        env = child.kwargs["env"]
        self.assertEqual((env[mutation.PROOF_BASE_ENV], mutation.PROOF_ENV in env), (self.base, True))
        self.assertEqual((self.seen["clone"], self.seen["baseline"], self.seen["mutant"]),
                         ([1740.0], [1740.0], [60.0]))
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        scope = mutation_scope.compute(self.base, self.root)
        row = self.risk_rows()[-1]
        self.assertEqual((row["mutation_proof"], row["mutation_base"], row["mutation_scope"]),
                         (config.MUTATION_PROOF, self.base, scope.fingerprint))
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)

    def test_real_config_worktree_only_opt_in_ignores_even_an_invalid_duration(self) -> None:
        self.changed = "M\t.factory.toml\nM\tfactory/example.py\n"
        self.build(main_config=real_config.BEFORE, worktree_config=real_config.CANDIDATE)
        self.configure()
        self.assertEqual((self.root / config.CONFIG_NAME).read_text(), real_config.CANDIDATE)
        self.assertEqual((self.main / config.CONFIG_NAME).read_text(), real_config.BEFORE)
        self.assertEqual(self.cfg.root, self.main.resolve())
        self.assertIsNone(self.mutation_check().proof)  # origin/main is unopted

        result = self.invoke_with([real_config.NO_PROOF_PASS], mutation_duration=math.nan)
        argv = self.gate_cmds[0]
        self.assertNotIn("--mutation-proof", argv)
        self.assertIn("--blocking-mutation", argv)  # High stays exit-status blocking
        self.assert_legacy_gate_command()  # no duration, no check-timeout
        self.assertEqual(self.gate_outer_timeouts, [None])
        self.assertEqual(self.loaded_roots, [self.main.resolve()])
        self.assertEqual([(c.args, c.timeouts) for c in self.children],
                         [([*REPO, "--blocking"], [self.cfg.check_timeout])])
        self.assertEqual(self.cfg.check_timeout, 1200)
        self.assertNotIn(mutation.PROOF_ENV, self.children[0].kwargs["env"])
        self.assertNotIn("mutation_proof", self.risk_rows()[-1])
        self.assertEqual(result["gate_exit"], 0)
        self.assertEqual(result["risk"], [("PASS", "PASS")])
        self.assertEqual(gate.CHECK_TIMEOUT, 1200)


# Only the two duration cases above run here; the parent's own cases run from their module.
for _name in [name for name in dir(real_config.RealConfigCallerTest) if name.startswith("test_")]:
    if _name not in vars(RealConfigDurationCases):
        setattr(RealConfigDurationCases, _name, None)


del _DurationHarness  # harness base only; its inherited witness cases are disabled above
