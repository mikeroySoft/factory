"""Caller-level witness: High-risk blocking mutation through the real dispatcher chain.

Path exercised for real: dispatch.pr_gate_and_review -> dispatch.run_gate ->
factory.cli.main(["gate", ...]) -> gate.main/execute/timed/_check_status -> gate report
-> dispatch._mutation_status / risk-check dispatch.record -> dispatch._head_evidence_matches.

Faked boundaries only (see WITNESS-NOTES.md):
- dispatch.run: `git rev-parse`, `git diff --name-status`, and the `python -P -E -m factory
  gate` child, which is run in-process via factory.cli.main instead of a subprocess.
- gate.subprocess: the mutation check child (fake Popen with scripted output/returncode,
  TimeoutExpired) and the toolchain probes; os.killpg is a recorder. No real child
  process is created or signalled. A child running one of the repository `.factory.toml`'s
  non-mutation checks (e.g. `test-pr-tier`, which the real config spawns before `mutation`)
  exits 0 with no output and consumes no scripted step. Proof-draft fixture change: the
  complete/survivor positives write their completion record with the real scripts.mutation producer
  (proof_record/write_proof) at the path the gate handed the child, and the no-change
  skip runs the real scripts.mutation.main in-process against a fake empty `git diff`
  (no mutation trial is ever run). Every other step still writes no record.
- The fixture Config models the repository-owned opt-in (`proof = "mutation-v1"`); the
  custom-check case is configured without it. (tests/test_mutation_proof_config.py covers
  the real config.load of the repository's own .factory.toml.)
- Proof-draft2 fixture change: the fixture root is a tiny real git repository built from
  `self.changed` (origin/main = base commit, HEAD = head commit), so the head is a real
  commit and the shared factory.mutation_scope rule (run by the gate, and by the runner
  steps) uses real git there. dispatch.run forwards only `git rev-parse` to real git; the
  producer-backed steps record the scope the shared rule computes for the gate's base.
- dispatch.review: the reviewer boundary; it journals the same `review` row shape as
  dispatch.review with real dispatch.record.
- gate.config.load returns the fixture Config; risk/attempt rows are fixture journal rows.

Several cases below assert the DESIRED fail-closed behaviour and are expected to FAIL
(RED) on baseline 235025aa; they are witnesses, not regressions to xfail or skip.
"""
from __future__ import annotations

import collections
import contextlib
import hashlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, gate, lifecycle, mutation_scope
from scripts import mutation
from tests.test_mutation_scope import REAL_RUN, build_repo

N = 7
FAKE_PID = 999_999  # above the Darwin pid limit; never signalled (killpg is a recorder)
CHECKOUT = Path(__file__).resolve().parents[1]
REPO_CHECKS = tomllib.loads((CHECKOUT / ".factory.toml").read_text())["gate"]["check"]
REPO_MUTATION_RUN = next(check["run"] for check in REPO_CHECKS if check["name"] == "mutation")
# Real-config children that are not the mutation runner: auto-succeeded, never scripted.
REPO_UNSCRIPTED_RUNS = [check["run"] for check in REPO_CHECKS if "scripts/mutation.py" not in check["run"]]
GATE_ARGV = [sys.executable, "-P", "-E", "-m", "factory", "gate"]

# Output shapes copied from scripts/mutation.py print statements (lines 262-264, 288-289,
# 324-326, 336-340, 229). Exit codes are what baseline main() returns for each shape.
HEADER = (
    "files: factory/example.py\n"
    "mutants: 3 sampled (seed 197, cap 200), 3 on changed lines first, 3 workers\n"
    "baseline: 5s, per-mutant timeout 60s, 1000s left before the deadline\n"
)
def produced(output: str, returncode: int, **record):
    """Child step whose completion record is written by the real scripts.mutation producer
    at the path the gate gave this child (proof-draft fixture change; output unchanged)."""
    def step(child: FakeChild) -> tuple[str, int]:
        mode = "blocking" if "--blocking" in child.args else "report-only"
        env = child.kwargs["env"]
        # As the real runner does: the shared rule's scope for the base the gate named.
        scope = mutation_scope.compute(env[mutation.PROOF_BASE_ENV])
        mutation.write_proof(env[mutation.PROOF_ENV], mutation.proof_record(
            mode=mode, exit_code=returncode,
            **{"files": sorted(scope.lines), "scope": scope.receipt(), **record},
        ))
        return output, returncode
    return step


def runner_git_only(cmd, **kwargs):
    """The runner's own processes: only the shared scope rule's git commands, for real."""
    if cmd[:2] not in (["git", "rev-parse"], ["git", "merge-base"], ["git", "diff"]):
        raise AssertionError(f"unexpected runner process: {cmd}")
    return REAL_RUN(cmd, **kwargs)


def real_runner_without_changes(child: FakeChild) -> tuple[str, int]:
    """The real scripts.mutation.main, in-process, with the child's environment and argv
    in the fixture repository: its own (shared) applicability rule yields the skip record."""
    argv = child.args[child.args.index("scripts/mutation.py") + 1:]
    Path("mutation-equivalents.json").write_text("[]\n")  # cwd is the fixture root
    out = io.StringIO()
    with mock.patch.dict(os.environ, child.kwargs["env"], clear=True), \
            mock.patch.object(mutation.subprocess, "run", runner_git_only), \
            contextlib.redirect_stdout(out):
        code = mutation.main(argv)
    return out.getvalue(), code


COMPLETE_PASS = produced(HEADER + "ran 3 of 3 mutants in 20s: 0 timeouts (included in killed); "
                         "3 killed; 0 survived\n", 0,
                         status="complete", reason="no-blocking-survivors", planned=3, ran=3, killed=3)
INCOMPLETE_EXIT0 = (HEADER + "ran 1 of 3 mutants in 1140s: 0 timeouts (included in killed); "
                    "1 killed; 0 survived\n"
                    "incomplete: 2 mutants not started and 0 cut short by the 1140s deadline\n", 0)
SURVIVOR_EXIT1 = produced(HEADER + "factory/example.py:2: core/NumberReplacer `x = 1` -> `x = 2`\n"
                          "ran 3 of 3 mutants in 20s: 0 timeouts (included in killed); "
                          "2 killed; 1 survived\n", 1,
                          status="complete", reason="blocking-survivors", planned=3, ran=3,
                          killed=2, survived=1, blocking_survivors=1)
MISSING_PROOF_EXIT0 = ("", 0)
MALFORMED_PROOF_EXIT0 = ("ran ? of ? mutants: proof=\x00{truncated\n", 0)
NO_CHANGE_EXIT0 = real_runner_without_changes
FACTORY_CHANGE = "M\tfactory/example.py\n"
# Desired High-risk outcome: no APPROVE, no exact-head acceptance, gate exit 1 and the
# emitted risk-check row recording mutation FAIL + pr_gate FAIL.
BLOCKED = {"approve": False, "head_accepted": False, "gate_exit": 1, "risk": [("FAIL", "FAIL")]}

DISPATCH_REBOUND = ("cfg", "ROOT", "REPO", "UPSTREAM", "UPSTREAM_REPO", "FACTORY", "LOGS",
                    "SYNC_LOG", "EVENTS", "MAX_ACTIVE", "MAX_ATTEMPTS", "host_claim_login")
DISPATCH_CONTAINERS = ("_PENDING_REVIEWS", "_PENDING_SECOND_REVIEWS", "_PENDING_RISK",
                       "_LOW_FALLBACK", "UNAVAILABLE_WORKERS", "VERIFIED_WORKERS", "_ESCALATED")
GATE_GLOBALS = ("cfg", "GPU_LOCK", "LEAK_RE", "GPU_CHECKS", "CHECK_TIMEOUT")
_UNSET = object()


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class FakeChild:
    """Mutation-check child: scripted communicate() steps, never a real process."""

    def __init__(self, steps: list, argv: list[str], kwargs: dict) -> None:
        self.steps, self.args, self.kwargs = list(steps), argv, kwargs
        self.pid, self.returncode, self.timeouts = FAKE_PID, None, []

    def communicate(self, timeout=None):
        self.timeouts.append(timeout)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):  # proof-draft fixture change: producer-backed step
            step = step(self)
        output, self.returncode = step
        return output, None

    def poll(self):
        return self.returncode


class MutationCallerWitness(unittest.TestCase):
    def setUp(self) -> None:
        checkout_journal = CHECKOUT / ".factory" / "events.jsonl"
        before = _digest(checkout_journal)
        self.addCleanup(lambda: self.assertEqual(_digest(checkout_journal), before))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self._isolate_globals()
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.root)
        self.scripts: collections.deque = collections.deque()
        self.children: list[FakeChild] = []  # children that consumed a scripted step
        self.unscripted_children: list[FakeChild] = []  # REPO_UNSCRIPTED_RUNS children
        self.gate_exits: list[int] = []
        self.killpg_calls: list[tuple] = []
        self.review_reports: list[str] = []
        self.changed = FACTORY_CHANGE
        self.head: str | None = None  # set by commit_fixture(), from configure()
        fake_subprocess = types.SimpleNamespace(
            Popen=self._popen, run=self._toolchain_probe, PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired,
            CompletedProcess=subprocess.CompletedProcess,
        )
        for patcher in (
            mock.patch.dict(os.environ),
            mock.patch.object(dispatch, "run", self._dispatch_run),
            mock.patch.object(dispatch, "review", self._review),
            mock.patch.object(gate, "subprocess", fake_subprocess),
            mock.patch.object(gate.os, "killpg", lambda *a: self.killpg_calls.append(a)),
            mock.patch.object(gate.config, "load", lambda *a, **k: self.cfg),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _isolate_globals(self) -> None:
        saved = {(dispatch, name): getattr(dispatch, name, _UNSET)
                 for name in (*DISPATCH_REBOUND, *DISPATCH_CONTAINERS)}
        saved.update({(gate, name): getattr(gate, name, _UNSET) for name in GATE_GLOBALS})

        def restore() -> None:
            for (module, name), value in saved.items():
                if value is _UNSET:
                    if hasattr(module, name):
                        delattr(module, name)
                else:
                    setattr(module, name, value)

        self.addCleanup(restore)
        for name in DISPATCH_CONTAINERS:  # configure() clears these; never the originals
            setattr(dispatch, name, type(saved[(dispatch, name)])())
        dispatch.host_claim_login = mock.Mock()  # configure() calls cache_clear() only

    # -- fixture context -------------------------------------------------------------
    def commit_fixture(self) -> None:
        """proof-draft2: a real base/head commit pair in the root whose diff is `self.changed`
        (`<status>\\t<path>` lines: A adds, M edits, D deletes a one-line file)."""
        if self.head is not None:
            return
        base, head = {}, {}
        for line in self.changed.splitlines():
            status, path = line.split("\t")[0], line.split("\t")[-1]
            if status != "A":
                base[path] = "value = 1\n"
            head[path] = None if status == "D" else "value = 2\n"
        self.base, self.head = build_repo(self.root, base or {"README.md": "r\n"}, head)

    def configure(self, tier: str = "High", check_run: list[str] = REPO_MUTATION_RUN,
                  proof: str | None = config.MUTATION_PROOF) -> None:
        # proof-draft fixture change: `proof` models the repository-owned opt-in.
        self.commit_fixture()
        self.cfg = config.Config(
            self.root, "acme/widgets",
            reviewer=["fake-reviewer", "--model", "primary/model"],
            second_reviewer=["fake-second", "--model", "second/model"],
            checks=[config.Check("mutation", list(check_run), when="pr", proof=proof)],
            lock=self.root / "factory.lock",
        )
        dispatch.configure(self.cfg)
        self.record_ticket(tier)

    def record_ticket(self, tier: str) -> None:
        dispatch.record("risk", ticket=N, head=self.head, tier=tier, matched_rules=[f"{tier.lower()}:fixture"])
        dispatch.record("attempt", ticket=N, attempt=1, gate="PASS", head=self.head, actual_head=self.head,
                        worker_model="worker/model")

    # -- faked boundaries ------------------------------------------------------------
    def _dispatch_run(self, cmd, cwd=None, check=True, timeout=None, **_kwargs):
        if cmd[:2] == ["git", "rev-parse"]:  # proof-draft2: the fixture's real commits
            return REAL_RUN(cmd, cwd=cwd, capture_output=True, text=True, check=check)
        if cmd[:3] == ["git", "diff", "--name-status"]:
            return subprocess.CompletedProcess(cmd, 0, self.changed, "")
        if cmd[:6] == GATE_ARGV:
            self.assertEqual(Path(cwd).resolve(), self.root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(cmd[5:])  # real `python -m factory gate ...`, in-process
            self.gate_exits.append(code)
            return subprocess.CompletedProcess(cmd, code, out.getvalue(), "")
        raise AssertionError(f"unexpected dispatcher process: {cmd}")

    def _popen(self, argv, **kwargs) -> FakeChild:
        if argv in REPO_UNSCRIPTED_RUNS:
            child = FakeChild([("", 0)], argv, kwargs)
            self.unscripted_children.append(child)
            return child
        if not self.scripts:
            raise AssertionError(f"no scripted step left for child {argv!r}")
        child = FakeChild(self.scripts.popleft(), argv, kwargs)
        self.children.append(child)
        return child

    def assert_steps_consumed(self, spawned: int) -> None:
        """Every scripted step must reach a scripted child. Exempt only a run that spawned no
        child at all (the gate refused before any check; those cases assert children == [])."""
        if len(self.children) + len(self.unscripted_children) > spawned:
            self.assertFalse(self.scripts, "scripted steps never reached a scripted child; "
                             f"unscripted children: {[c.args for c in self.unscripted_children]}")

    @staticmethod
    def _toolchain_probe(command, **_kwargs):
        raise FileNotFoundError(command[0])  # gate.toolchain reports "unavailable"

    def _review(self, wt, n, report, head, *, role="primary", reviewer=None):
        self.review_reports.append(report)
        model = config.command_model(reviewer or dispatch.cfg.reviewer)
        dispatch.record("review", ticket=n, verdict="APPROVE", parsed=True, accepted=True,
                        head=head, actual_head=head, counts={}, reason=None, role=role,
                        model=model, outside_diff_findings=0)
        target = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
        target[n] = {"verdict": "APPROVE", "accepted": True, "head": head, "role": role,
                     "model": model, "findings": f"{role} ok"}
        return "APPROVE", f"{role} ok"

    # -- invocation ------------------------------------------------------------------
    def invoke(self, *steps, pr_runs: dict | None = None) -> dict:
        self.scripts.extend(steps)
        spawned = len(self.children) + len(self.unscripted_children)
        pr_runs = {} if pr_runs is None else pr_runs
        head = self.head
        verdict, findings, _ = dispatch.pr_gate_and_review(self.root, N, "attempt report", head, pr_runs)
        self.assert_steps_consumed(spawned)
        events = lifecycle.read_events(dispatch.EVENTS)
        risk = [e for e in events if e.get("event") == "risk-check" and e.get("head") == head]
        paths = [line.split("\t")[-1] for line in self.changed.splitlines()]
        return {
            "verdict": verdict,
            "findings": findings,
            "gate_exit": self.gate_exits[-1] if self.gate_exits else None,
            "report_status": dispatch._mutation_status(pr_runs[head][1]) if head in pr_runs else None,
            "risk": [(e.get("mutation"), e.get("pr_gate")) for e in risk],
            "head_accepted": dispatch._head_evidence_matches(events, N, head, changed_paths=paths),
        }

    @staticmethod
    def summary(result: dict) -> dict:
        return {
            "approve": result["verdict"] == "APPROVE",
            "head_accepted": result["head_accepted"],
            "gate_exit": result["gate_exit"],
            "risk": result["risk"][-1:],
        }

    # -- cases -----------------------------------------------------------------------
    def test_complete_pass_positive_control_reaches_approve_and_head_acceptance(self) -> None:
        self.configure()
        result = self.invoke([COMPLETE_PASS])
        self.assertEqual([c.args for c in self.children], [[*REPO_MUTATION_RUN, "--blocking"]])
        self.assertEqual(result["report_status"], "PASS")
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")],
        })

    def test_incomplete_blocking_exit0_must_not_approve_expected_red(self) -> None:
        self.configure()
        result = self.invoke([INCOMPLETE_EXIT0])
        self.assertEqual([c.args for c in self.children], [[*REPO_MUTATION_RUN, "--blocking"]])
        self.assertEqual(self.summary(result), BLOCKED)

    def test_completed_survivor_exit1_blocks_positive_safety_control(self) -> None:
        self.configure()
        result = self.invoke([SURVIVOR_EXIT1])
        self.assertEqual(self.summary(result), BLOCKED)
        self.assertEqual(result["verdict"], "REVISE")

    def test_missing_proof_exit0_fails_closed_expected_red(self) -> None:
        self.configure()
        self.assertEqual(self.summary(self.invoke([MISSING_PROOF_EXIT0])), BLOCKED)

    def test_malformed_proof_exit0_fails_closed_expected_red(self) -> None:
        self.configure()
        self.assertEqual(self.summary(self.invoke([MALFORMED_PROOF_EXIT0])), BLOCKED)

    def test_no_change_skip_is_exemption_not_completed_proof(self) -> None:
        self.changed = "M\tdocs/guide.md\n"  # proof-draft2: before configure() commits it
        self.configure()
        result = self.invoke([NO_CHANGE_EXIT0])
        self.assertEqual(result["report_status"], "SKIP")
        self.assertIn("- mutation: SKIP (no changed factory code)", self.review_reports[0])
        self.assertNotIn("PASS", [mutation for mutation, _ in result["risk"]])
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("SKIP", "PASS")],
        })

    def test_custom_mutation_check_without_opt_in_keeps_exit0_behaviour(self) -> None:
        custom = ["./custom-mutation-check", "--strict"]
        self.configure(check_run=custom, proof=None)  # proof-draft: no repository opt-in
        result = self.invoke([("custom tool: ok\n", 0)])
        self.assertEqual([c.args for c in self.children], [[*custom, "--blocking"]])
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")],
        })

    def test_medium_report_only_uses_non_high_head_evidence(self) -> None:
        self.configure(tier="Medium")
        result = self.invoke([INCOMPLETE_EXIT0])
        self.assertEqual([c.args for c in self.children], [list(REPO_MUTATION_RUN)])
        self.assertEqual(len(self.review_reports), 1)  # primary only; no second reviewer
        self.assertIn("incomplete: 2 mutants not started", self.review_reports[0])
        self.assertEqual(result["risk"], [])
        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual([e["tier"] for e in events if e.get("event") == "risk"], ["Medium"])
        self.assertEqual((result["verdict"], result["head_accepted"]), ("APPROVE", True))

    def test_cached_pr_run_is_reused_for_same_head_observed_behaviour(self) -> None:
        self.configure()
        pr_runs: dict = {}
        first = self.invoke([COMPLETE_PASS], pr_runs=pr_runs)
        dispatch._PENDING_REVIEWS.clear()
        dispatch._PENDING_SECOND_REVIEWS.clear()
        second = self.invoke(pr_runs=pr_runs)  # a new incomplete outcome would never be observed
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.gate_exits, [0])
        self.assertEqual((first["verdict"], second["verdict"]), ("APPROVE", "APPROVE"))
        self.assertEqual(second["risk"], [("PASS", "PASS"), ("PASS", "PASS")])

    def test_outer_timeout_kills_fake_group_and_blocks(self) -> None:
        self.configure()
        timeout = subprocess.TimeoutExpired(REPO_MUTATION_RUN, self.cfg.check_timeout)
        result = self.invoke([timeout, ("partial output\n", -signal.SIGKILL)])
        self.assertEqual(self.killpg_calls, [(FAKE_PID, signal.SIGKILL)])
        self.assertEqual(self.children[0].timeouts, [self.cfg.check_timeout, None])
        self.assertIn("check timed out after 1200s", self.review_reports[0])
        self.assertEqual(self.summary(result), BLOCKED)

    def test_signalled_child_negative_returncode_blocks(self) -> None:
        self.configure()
        result = self.invoke([("partial output\n", -signal.SIGTERM)])
        self.assertEqual(self.killpg_calls, [])
        self.assertEqual(self.summary(result), BLOCKED)


if __name__ == "__main__":
    unittest.main()
