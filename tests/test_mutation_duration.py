"""Explicit total duration for the mutation runner and the gate parser (tests first, v2).

Runner cases run the real scripts.mutation.main in-process, as proof runs, inside a tiny
isolated git repository (origin/main = base commit, HEAD = head commit): argv parsing, the
shared factory.mutation_scope rule, the copy clone and tracked-byte checks and the
completion record are real. Only these boundaries are faked: the monotonic clock (a
callable that moves only when a faked boundary "takes" time), the baseline suite process,
cosmic-ray's mutate_and_test and sample() (the enumeration boundary). `git clone` runs for
real but without the derived timeout (that value is recorded, never enforced on the host),
so sub-second windows cannot flake. No test suite, mutation trial or sleep runs.

Contract under test (absent duration = legacy, unchanged):
- no `--duration`: deadline = anchor + max(1, `[gate].timeout` - 60), i.e. 1140 seconds by
  default, with the existing 1s clamp (`[gate].timeout = 30` still gives 1s);
- `--duration SECONDS`: the same one deadline, anchored at the same point (after scope
  computation), becomes anchor + (SECONDS - 60) exactly: no clamp, no minimum work window,
  `[gate].timeout` not consulted. 60.25 gives 0.25s, 60.5 gives 0.5s, 61 gives 1s, 1800
  gives 1740s. The 60s cleanup reserve, preparation accounting (enumeration and clones count
  against the window), sampling/order/cap/seed, the per-mutant timeout rule and timeout
  scoring are unchanged;
- SECONDS must be finite and strictly greater than the existing 60s reserve: 0, negatives,
  59.99, 60, 60.0, NaN, infinities and non-numbers are refused before any process starts,
  with a nonzero exit (argparse's 2 or a returned error code) and never a complete or skip
  record;
- the gate's `--mutation-duration` is refused (exit 2, config never loaded) without
  `--mutation-proof` and for the same invalid values; valid values (60.25, 61, 1800) parse.

Cases marked "control" pin legacy behaviour and already pass on baseline aeee1ce; the rest
are feature cases expected to FAIL (RED) there, which has neither option.
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
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import gate
from scripts import mutation
from tests.test_mutation_proof import FACTORY_BASE, FACTORY_HEAD, KILLED, SPEC, TestOutcome, outcome
from tests.test_mutation_scope import REAL_RUN, build_repo

SPEC2 = SimpleNamespace(**{**vars(SPEC), "occurrence": 1})  # a second changed-line mutant
INVALID = ("0", "-1", "59.99", "60", "60.0", "nan", "inf", "-inf", "abc", "")
VALID = ("60.25", "60.5", "61", "1800")
TOML_300 = "[gate]\ntimeout = 300\n"
TOML_30 = "[gate]\ntimeout = 30\n"  # legacy clamps 30 - 60 up to the existing 1s window
REAL_GIT = (["git", "rev-parse"], ["git", "diff"], ["git", "ls-tree"])


class Clock:
    """Fake time.monotonic: constant until a faked boundary advances it."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def unbounded(kwargs: dict) -> dict:
    """Real git, without the runner-derived timeout (recorded by the caller instead)."""
    return {key: value for key, value in kwargs.items() if key != "timeout"}


class RunnerDurationTest(unittest.TestCase):
    def build(self, toml: str | None = None) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        base = {**FACTORY_BASE, **({".factory.toml": toml} if toml else {})}
        self.base, self.head = build_repo(self.root, base, FACTORY_HEAD)
        self.proof = self.root.parent / "record.json"
        for patcher in (mock.patch.dict(os.environ), contextlib.chdir(self.root)):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def run_runner(self, argv: list[str], *, result=KILLED, sampled=([SPEC], 1),
                   scope_cost: float = 0.0, sample_cost: float = 0.0, clone_cost: float = 0.0,
                   baseline_cost: float = 0.0, mutant_cost: float = 0.0):
        """`(code, stdout, stderr, seen, record)`; code is `("exit", n)` on SystemExit."""
        self.proof.unlink(missing_ok=True)
        os.environ[mutation.PROOF_ENV] = str(self.proof)
        os.environ[mutation.PROOF_BASE_ENV] = self.base
        clock = Clock()
        seen: dict[str, list] = {"processes": [], "sample": [], "clone": [], "baseline": [],
                                 "mutant": []}
        merge_bases: list = []

        def process(cmd, **kwargs):
            seen["processes"].append(cmd[:2])
            if cmd[:2] == ["git", "merge-base"]:  # first call: scope computation (startup)
                merge_bases.append(cmd)
                if len(merge_bases) == 1:
                    clock.advance(scope_cost)
                return REAL_RUN(cmd, **kwargs)
            if cmd[:2] == ["git", "clone"]:  # preparation
                seen["clone"].append(kwargs.get("timeout"))
                done = REAL_RUN(cmd, **unbounded(kwargs))
                clock.advance(clone_cost)
                return done
            if cmd[:2] in REAL_GIT:
                return REAL_RUN(cmd, **kwargs)
            if cmd[:1] == ["env"]:  # the baseline suite command
                seen["baseline"].append(kwargs.get("timeout"))
                clock.advance(baseline_cost)
                return subprocess.CompletedProcess(cmd, 0, "baseline output\n", "")
            raise AssertionError(f"unexpected runner process: {cmd}")

        def sample(_lines, _scratch):  # enumeration boundary (preparation, after the anchor)
            seen["sample"].append(clock())
            clock.advance(sample_cost)
            return sampled

        def mutate_and_test(_mutations, _command, timeout):
            seen["mutant"].append(timeout)
            clock.advance(mutant_cost)
            return result

        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        modules = {
            "attrs": attrs, "cosmic_ray": types.ModuleType("cosmic_ray"),
            "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=mutate_and_test),
            "cosmic_ray.work_item": SimpleNamespace(TestOutcome=TestOutcome),
        }
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(mutation.time, "monotonic", clock), \
                mock.patch.object(mutation, "WORKERS", 1), \
                mock.patch.object(mutation, "sample", sample), \
                mock.patch.object(mutation.subprocess, "run", process), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = mutation.main(argv)
            except SystemExit as exc:
                code = ("exit", exc.code)
        record = json.loads(self.proof.read_text()) if self.proof.exists() else None
        return code, out.getvalue(), err.getvalue(), seen, record

    def explicit(self, argv: list[str], **fake):
        """run_runner for argv naming --duration; baseline's argparse rejection is RED."""
        code, out, err, seen, record = self.run_runner(argv, **fake)
        if isinstance(code, tuple) and "unrecognized arguments" in err:
            self.fail(f"scripts/mutation.py does not accept --duration yet: {err.strip()}")
        return code, out, err, seen, record

    def assert_budgets(self, seen: dict, clone: list, baseline: list, mutant: list) -> None:
        self.assertEqual((seen["clone"], seen["baseline"], seen["mutant"]), (clone, baseline, mutant))

    # -- controls: absent duration is the legacy budget, exactly ----------------------
    def test_control_absent_duration_keeps_default_1200_and_deadline_1140(self) -> None:
        self.build()
        code, out, _, seen, record = self.run_runner(["--blocking"])
        self.assertEqual(code, 0)
        self.assert_budgets(seen, [1140.0], [1140.0], [60.0])
        self.assertIn("1140s left before the deadline", out)
        self.assertEqual((record["mode"], record["status"], record["exit"]), ("blocking", "complete", 0))

    def test_control_absent_duration_keeps_repository_gate_timeout(self) -> None:
        self.build(TOML_300)
        code, _, _, seen, record = self.run_runner(["--blocking"])
        self.assertEqual(code, 0)
        self.assertEqual((seen["clone"], seen["baseline"]), ([240.0], [240.0]))
        self.assertEqual(record["status"], "complete")

    def test_control_absent_duration_keeps_the_legacy_one_second_clamp(self) -> None:
        self.build(TOML_30)  # max(1, 30 - 60) == 1: legacy only, never applied to explicit totals
        code, _, _, seen, record = self.run_runner(["--blocking"])
        self.assertEqual(code, 0)
        self.assert_budgets(seen, [1.0], [1.0], [0.25])
        self.assertEqual(record["status"], "complete")

    def test_control_startup_is_not_counted_and_enumeration_is(self) -> None:
        self.build()
        _, _, _, startup, _ = self.run_runner(["--blocking"], scope_cost=5000)
        self.assertEqual(startup["clone"], [1140.0])  # anchor follows scope computation
        _, _, _, enumerated, _ = self.run_runner(["--blocking"], sample_cost=40)
        self.assertEqual(enumerated["clone"], [1100.0])  # enumeration follows the anchor

    # -- feature: one deadline, explicit total ----------------------------------------
    def test_explicit_duration_sets_the_one_deadline_and_ignores_gate_timeout(self) -> None:
        self.build(TOML_300)  # would give 240s if [gate].timeout were consulted
        code, out, _, seen, record = self.explicit(["--blocking", "--duration", "1800"])
        self.assertEqual(code, 0)
        self.assert_budgets(seen, [1740.0], [1740.0], [60.0])
        self.assertIn("1740s left before the deadline", out)
        self.assertEqual((record["mode"], record["status"], record["exit"]), ("blocking", "complete", 0))
        # Sampling, cap and seed disclosure unchanged.
        self.assertIn(f"(seed {mutation.SEED}, cap {mutation.CAP})", out)
        self.assertEqual((mutation.SEED, mutation.CAP), (197, 200))

    def test_explicit_fractional_totals_keep_exactly_total_minus_reserve(self) -> None:
        # No clamp to 1s and no invented minimum: the legacy clamp would give 1.0 here.
        self.build(TOML_30)
        for total, window in ((60.25, 0.25), (60.5, 0.5)):
            with self.subTest(total=total):
                code, _, _, seen, record = self.explicit(["--blocking", "--duration", str(total)])
                self.assertEqual(code, 0)
                # Per-mutant timeout: the unchanged min(max(60, 3 * baseline), remaining / 4).
                self.assert_budgets(seen, [window], [window], [window / 4])
                self.assertEqual((record["mode"], record["status"], record["exit"]),
                                 ("blocking", "complete", 0))

    def test_explicit_61_is_an_ordinary_one_second_window_not_a_minimum(self) -> None:
        self.build()
        code, _, _, seen, record = self.explicit(["--blocking", "--duration", "61"])
        self.assertEqual(code, 0)
        self.assert_budgets(seen, [1.0], [1.0], [0.25])
        self.assertEqual((record["mode"], record["status"]), ("blocking", "complete"))

    def test_startup_before_the_existing_anchor_is_unchanged(self) -> None:
        # The deadline stays anchored where it is today (after scope computation).
        self.build()
        _, _, _, legacy, _ = self.run_runner(["--blocking"], scope_cost=5)
        _, _, _, seen, _ = self.explicit(["--blocking", "--duration", "1800"], scope_cost=5)
        _, _, _, tiny, record = self.explicit(["--blocking", "--duration", "60.5"], scope_cost=5000)
        self.assertEqual(legacy["clone"], [1140.0])
        self.assertEqual(seen["clone"], [1740.0])
        self.assertEqual((tiny["clone"], record["status"]), ([0.5], "complete"))

    def test_enumeration_exhausting_a_fractional_window_never_starts_a_clone(self) -> None:
        self.build()
        code, _, _, seen, record = self.explicit(["--blocking", "--duration", "60.5"], sample_cost=0.5)
        self.assertEqual(len(seen["sample"]), 1)
        self.assert_budgets(seen, [], [], [])
        self.assertEqual(code, 1)
        self.assertEqual((record["status"], record["reason"], record["exit"]),
                         ("incomplete", "baseline-deadline", 1))
        self.assertEqual((record["counts"]["planned"], record["counts"]["not_started"],
                          record["counts"]["ran"]), (1, 1, 0))

    def test_clone_exhausting_a_fractional_window_never_starts_the_baseline(self) -> None:
        self.build()
        code, _, _, seen, record = self.explicit(["--blocking", "--duration", "60.5"], clone_cost=0.5)
        self.assert_budgets(seen, [0.5], [], [])
        self.assertEqual(code, 1)
        self.assertEqual((record["status"], record["reason"], record["exit"]),
                         ("incomplete", "baseline-deadline", 1))

    def test_preparation_time_counts_against_the_explicit_window(self) -> None:
        self.build()
        code, out, _, seen, record = self.explicit(
            ["--blocking", "--duration", "100"], clone_cost=39.5, baseline_cost=1)
        self.assert_budgets(seen, [40.0], [0.5], [])
        self.assertEqual(code, 1)
        self.assertIn("baseline suite did not finish before the 40s deadline", out)
        self.assertEqual((record["status"], record["reason"], record["exit"]),
                         ("incomplete", "baseline-deadline", 1))
        self.assertEqual((record["counts"]["planned"], record["counts"]["not_started"],
                          record["counts"]["ran"]), (1, 1, 0))

    def test_control_same_preparation_cost_fits_the_legacy_window(self) -> None:
        self.build()
        code, _, _, seen, record = self.run_runner(["--blocking"], clone_cost=39.5, baseline_cost=1)
        self.assertEqual((code, seen["baseline"], record["status"]), (0, [1100.5], "complete"))

    def test_expiry_during_mutants_under_explicit_duration_is_incomplete(self) -> None:
        self.build()
        code, _, _, seen, record = self.explicit(
            ["--blocking", "--duration", "100"], sampled=([SPEC, SPEC2], 2), mutant_cost=41)
        self.assertEqual(seen["mutant"], [10.0])  # min(max(60, baseline), 40 / 4): unchanged rule
        self.assertEqual((record["status"], record["reason"], record["exit"]), ("incomplete", "deadline", code))
        self.assertEqual((record["counts"]["ran"], record["counts"]["not_started"]), (1, 1))

    def test_control_same_mutant_cost_completes_in_the_legacy_window(self) -> None:
        self.build()
        code, _, _, seen, record = self.run_runner(
            ["--blocking"], sampled=([SPEC, SPEC2], 2), mutant_cost=41)
        self.assertEqual((code, seen["mutant"], record["status"]), (0, [60.0, 60.0], "complete"))

    def test_timeout_scored_kill_counting_is_unchanged_under_explicit_duration(self) -> None:
        # Preservation only: timeout-counted kills remain the runner's existing, explicitly
        # UNRESOLVED scoring limitation; this asserts it is not altered, not that it is proof.
        self.build()
        timed_out = outcome(TestOutcome.KILLED, "timeout")
        _, _, _, _, legacy = self.run_runner(["--blocking"], result=timed_out)
        _, _, _, _, record = self.explicit(["--blocking", "--duration", "1800"], result=timed_out)
        for row in (legacy, record):
            self.assertEqual((row["status"], row["counts"]["killed"], row["counts"]["timeouts"]),
                             ("complete", 1, 1))

    # -- feature: invalid explicit totals never start work ----------------------------
    def test_invalid_explicit_duration_is_refused_before_any_process(self) -> None:
        self.build()
        for value in INVALID:
            with self.subTest(value=value):
                code, _, err, seen, record = self.run_runner(["--blocking", "--duration", value])
                self.assertNotIn("unrecognized arguments", err, "runner lacks --duration")
                # A process exit code either way: argparse's 2, or a returned nonzero int.
                self.assertTrue(code == ("exit", 2) or (type(code) is int and code != 0), code)
                self.assertEqual(seen["processes"], [])
                self.assertEqual(seen["sample"], [])
                self.assert_budgets(seen, [], [], [])
                self.assertTrue(record is None or record["status"] == "error", record)


class GateDurationParserTest(unittest.TestCase):
    """gate.main decides at parse time: an invalid value never loads config or launches."""

    class Reached(Exception):
        """Raised by the faked config.load: parsing accepted the argv."""

    def parse(self, argv: list[str]) -> tuple[object, str]:
        err = io.StringIO()
        refused = AssertionError(f"gate accepted {argv!r} and went on to load config")
        with mock.patch.object(gate.config, "load", side_effect=refused), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            gate.main(argv)
        return raised.exception.code, err.getvalue()

    def test_duration_without_mutation_proof_is_refused(self) -> None:
        code, err = self.parse(["--tier", "pr", "--blocking-mutation", "--mutation-duration", "1800"])
        self.assertNotIn("unrecognized arguments", err, "gate lacks --mutation-duration")
        self.assertEqual(code, 2)
        self.assertIn("--mutation-duration", err)
        self.assertIn("--mutation-proof", err)

    def test_invalid_duration_is_refused_with_mutation_proof(self) -> None:
        for value in INVALID:
            with self.subTest(value=value):
                code, err = self.parse(["--tier", "pr", "--blocking-mutation", "--mutation-proof",
                                        "--mutation-duration", value])
                self.assertNotIn("unrecognized arguments", err, "gate lacks --mutation-duration")
                self.assertEqual(code, 2)
                self.assertIn("--mutation-duration", err)

    def test_valid_fractional_and_integral_durations_are_accepted(self) -> None:
        for value in VALID:
            with self.subTest(value=value):
                err = io.StringIO()
                with mock.patch.object(gate.config, "load", side_effect=self.Reached), \
                        contextlib.redirect_stderr(err):
                    try:
                        gate.main(["--tier", "pr", "--blocking-mutation", "--mutation-proof",
                                   "--mutation-duration", value])
                    except self.Reached:
                        continue
                    except SystemExit as exc:
                        self.fail(f"gate refused {value!r} (exit {exc.code}): {err.getvalue().strip()}")
                self.fail("gate returned without loading config")


if __name__ == "__main__":
    unittest.main()
