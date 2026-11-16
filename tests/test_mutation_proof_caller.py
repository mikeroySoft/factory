"""mutation-v1 completion records through the actual dispatcher caller chain.

Reuses the approved witness harness (tests/test_mutation_caller_witness.py): real
dispatch.pr_gate_and_review -> run_gate -> cli/gate -> report -> risk-check record ->
_head_evidence_matches; only the processes, reviewer and fixture rows are faked. The
fixture root is a tiny real git repository, so the gate's shared-rule scope, the runner's
and the dispatcher's head/base binding all use real commits. The witness's own cases are
not re-run here.
"""
from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, lifecycle, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests.test_mutation_proof import KILLED, SPEC
from tests.test_mutation_proof import TestOutcome as Outcome
from tests.test_mutation_scope import build_repo, commit, git

COMPLETE = witness.COMPLETE_PASS
NO_PROOF_PASS = ("ran 3 of 3 mutants in 20s: 0 timeouts (included in killed); 3 killed; 0 survived\n", 0)
LEGACY_PASS_REPORT = "# Gate report (pr tier)\n\n## Host toolchain\n\n- mutation: PASS\n"
DRAFT1_PASS_REPORT = "# Gate report (pr tier)\n\nMutation proof: mutation PASS\n\n## Host toolchain\n\n- mutation: PASS\n"


def produced(returncode: int = 0, *, output: str = "runner output\n", **record):
    defaults = {"status": "complete", "reason": "no-blocking-survivors", "planned": 3, "ran": 3, "killed": 3}
    return witness.produced(output, returncode, **{**defaults, **record})


def real_runner(sampled=([SPEC], 1)):
    """The real scripts.mutation.main end to end in the fixture repository: real argv
    parsing, equivalents loading, shared-rule scope (real git) and completion record;
    cosmic-ray (including sample()) and the baseline suite are faked (no mutation trial
    runs); draft3: the copy clone/copytree and the tracked-byte checks run for real."""
    def step(child) -> tuple[str, int]:
        argv = child.args[child.args.index("scripts/mutation.py") + 1:]
        Path("mutation-equivalents.json").write_text("[]\n")

        def process(cmd, **kwargs):
            # draft3: the tracked-byte check and the copy clone run for real (copies verified).
            if cmd[:2] in (["git", "rev-parse"], ["git", "merge-base"], ["git", "diff"],
                           ["git", "ls-tree"], ["git", "clone"]):
                return witness.REAL_RUN(cmd, **kwargs)
            if cmd[:1] == ["env"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected runner process: {cmd}")

        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        modules = {"attrs": attrs, "cosmic_ray": types.ModuleType("cosmic_ray"),
                   "cosmic_ray.mutating": SimpleNamespace(mutate_and_test=lambda *_: KILLED),
                   "cosmic_ray.work_item": SimpleNamespace(TestOutcome=Outcome)}
        out = io.StringIO()
        with mock.patch.dict(os.environ, child.kwargs["env"], clear=True), \
                mock.patch.dict(sys.modules, modules), \
                mock.patch.object(mutation, "WORKERS", 1), \
                mock.patch.object(mutation, "sample", return_value=sampled), \
                mock.patch.object(mutation.subprocess, "run", process), \
                contextlib.redirect_stdout(out):
            code = mutation.main(argv)
        return out.getvalue(), code
    return step


real_runner_complete = real_runner()


class ProofCallerTest(witness.MutationCallerWitness):
    def setUp(self) -> None:
        super().setUp()
        self.gate_cmds: list[list[str]] = []
        self.fixture: tuple[dict, dict] | None = None  # explicit (base, head) file contents

    def commit_fixture(self) -> None:
        if self.fixture is None:
            super().commit_fixture()
        elif self.head is None:
            self.base, self.head = build_repo(self.root, *self.fixture)

    def _dispatch_run(self, cmd, cwd=None, check=True, timeout=None, **kwargs):
        if cmd[:6] == witness.GATE_ARGV:
            self.gate_cmds.append(list(cmd))
        return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)

    def configure_checks(self, checks: list[config.Check], tier: str = "High") -> None:
        self.configure(tier=tier)
        self.cfg.checks = checks
        dispatch.configure(self.cfg)

    def risk_rows(self) -> list[dict]:
        return [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"]

    def child_saw_record_path(self) -> list[bool]:
        return [mutation.PROOF_ENV in child.kwargs["env"] for child in self.children]

    def scope(self) -> mutation_scope.Scope:
        return mutation_scope.compute(self.base, self.root)


# The witness's own cases run once, from their module; this class adds proof cases only.
for _name in [name for name in dir(witness.MutationCallerWitness) if name.startswith("test_")]:
    setattr(ProofCallerTest, _name, None)


class ProofCallerCases(ProofCallerTest):
    def test_real_runner_complete_record_reaches_approve_and_head_acceptance(self) -> None:
        self.configure()
        result = self.invoke([real_runner_complete])
        self.assertIn("--mutation-proof", self.gate_cmds[0])
        self.assertEqual(self.gate_cmds[0][self.gate_cmds[0].index("--base") + 1], "origin/main")
        self.assertEqual(self.child_saw_record_path(), [True])
        # The gate names the caller base it resolved, never the label.
        self.assertEqual(self.children[0].kwargs["env"][mutation.PROOF_BASE_ENV], self.base)
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")],
        })
        scope = self.scope()
        row = self.risk_rows()[-1]
        self.assertEqual((row["mutation_proof"], row["mutation_base"], row["mutation_scope"], row["head"]),
                         (config.MUTATION_PROOF, self.base, scope.fingerprint, self.head))
        self.assertIn(f"Mutation proof: mutation PASS base={self.base} head={self.head} "
                      f"scope={scope.fingerprint}", self.review_reports[0])

    def test_completed_survivor_with_scope_blocks(self) -> None:
        self.configure()
        result = self.invoke([witness.SURVIVOR_EXIT1])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (complete with 1 blocking survivors)", self.review_reports[0])

    def assert_canonical_fail(self, step, why: str | None = None) -> None:
        self.configure()
        result = self.invoke([step])
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertEqual(result["report_status"], "FAIL")
        self.assertIn("Mutation proof: mutation FAIL\n", self.review_reports[0])
        if why:
            self.assertIn(f"mutation proof: FAIL ({why}", self.review_reports[0])
        self.assertEqual(self.risk_rows()[-1]["mutation_proof"], config.MUTATION_PROOF)
        self.assertNotIn("mutation_scope", self.risk_rows()[-1])

    def test_canonical_fail_for_genuine_incomplete_record_exit0(self) -> None:
        self.assert_canonical_fail(produced(status="incomplete", reason="deadline",
                                            ran=1, killed=1, not_started=2))

    def test_canonical_fail_for_contradictory_complete_record(self) -> None:
        self.assert_canonical_fail(produced(ran=1))  # "complete" with 1 of 3 run

    def test_canonical_fail_for_record_exit_that_contradicts_the_child(self) -> None:
        recorded = produced(1)  # record says exit 1; the child exits 0
        self.assert_canonical_fail(lambda child: (recorded(child)[0], 0))

    def test_canonical_fail_for_error_record(self) -> None:
        self.assert_canonical_fail(produced(1, status="error", reason="tool-broken", killed=2, broken=1))

    def test_report_only_record_cannot_stand_in_for_blocking_proof(self) -> None:
        def report_only(child):
            scope = mutation_scope.compute(child.kwargs["env"][mutation.PROOF_BASE_ENV])
            mutation.write_proof(child.kwargs["env"][mutation.PROOF_ENV], mutation.proof_record(
                mode="report-only", status="complete", reason="no-blocking-survivors", exit_code=0,
                files=["factory/example.py"], scope=scope.receipt(), planned=3, ran=3, killed=3))
            return NO_PROOF_PASS

        self.assert_canonical_fail(report_only)

    def test_canonical_fail_for_contradictory_genuine_skip_record(self) -> None:
        # A skip carrying the true scope while that scope has applicable lines: gate exit 1.
        self.assert_canonical_fail(
            produced(status="skip", reason="no-applicable-files", files=[], planned=0, ran=0, killed=0),
            "contradictory skip record (1 applicable changed lines)")

    def test_canonical_fail_for_skip_record_with_an_empty_forged_scope(self) -> None:
        def forged(child):
            real = mutation_scope.compute(child.kwargs["env"][mutation.PROOF_BASE_ENV])
            empty = mutation_scope.Scope(real.base, real.merge_base, real.head, ())
            return produced(status="skip", reason="no-applicable-files", files=[], scope=empty.receipt(),
                            planned=0, ran=0, killed=0)(child)

        self.assert_canonical_fail(forged, "stale or mismatched scope")

    def assert_stale_field_fails(self, field: str) -> None:
        def stale(child):
            receipt = mutation_scope.compute(child.kwargs["env"][mutation.PROOF_BASE_ENV]).receipt()
            return produced(scope={**receipt, field: "0" * len(receipt[field])})(child)

        self.assert_canonical_fail(stale, "stale or mismatched scope")

    def test_canonical_fail_for_stale_base(self) -> None:
        self.assert_stale_field_fails("base")

    def test_canonical_fail_for_stale_head(self) -> None:
        self.assert_stale_field_fails("head")

    def test_canonical_fail_for_mismatched_fingerprint(self) -> None:
        self.assert_stale_field_fails("fingerprint")

    def test_canonical_fail_when_head_moves_during_the_run(self) -> None:
        def moving(child):
            output = produced()(child)  # a true record for the head the run started on
            commit(self.root, {"factory/example.py": "value = 3\n"}, "head moves mid-run")
            return output

        self.assert_canonical_fail(moving, "HEAD or the applicable scope changed during the run")

    def test_unrelated_pr_check_still_passes_while_mutation_lacks_proof(self) -> None:
        self.configure_checks([
            config.Check("lint", ["./lint"], when="pr"),
            config.Check("mutation", list(witness.REPO_MUTATION_RUN), when="pr", proof=config.MUTATION_PROOF),
        ])
        result = self.invoke([("lint ok\n", 0)], [NO_PROOF_PASS])
        self.assertIn("- lint: PASS", self.review_reports[0])
        self.assertEqual(self.child_saw_record_path(), [False, True])  # only the opted-in check
        self.assertEqual(self.summary(result), witness.BLOCKED)

    def test_forged_skip_stdout_with_applicable_change_fails(self) -> None:
        self.configure()
        result = self.invoke([("no mutable files\n", 0)])
        self.assertEqual(result["report_status"], "FAIL")
        self.assertEqual(self.summary(result), witness.BLOCKED)

    # -- shared applicability through the real runner ---------------------------------
    def assert_real_runner(self, changed: str, expected: tuple, **runner) -> dict:
        self.changed = changed
        self.configure()
        result = self.invoke([real_runner(**runner)])
        self.assertEqual((result["report_status"], self.summary(result)), expected)
        return result

    def test_deletion_only_change_is_a_genuine_skip(self) -> None:
        self.assert_real_runner("D\tfactory/example.py\n", ("SKIP", {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("SKIP", "PASS")]}))
        self.assertEqual(self.scope().line_count, 0)

    def test_deletion_only_hunk_in_a_modified_file_is_a_genuine_skip(self) -> None:
        self.fixture = ({"factory/example.py": "value = 1\nextra = 2\n"}, {"factory/example.py": "value = 1\n"})
        self.assert_real_runner(witness.FACTORY_CHANGE, ("SKIP", {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("SKIP", "PASS")]}))
        self.assertEqual(self.scope().files, ())

    def assert_applicable(self, changed: str, module: str) -> None:
        spec = SimpleNamespace(**{**vars(SPEC), "module_path": Path(module)})
        self.assert_real_runner(changed, ("PASS", {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]}),
            sampled=([spec], 1))
        self.assertIn("complete: ran 1 of 1 mutants", self.review_reports[0])

    def test_added_file_is_applicable(self) -> None:
        self.assert_applicable("A\tfactory/example.py\n", "factory/example.py")
        self.assertEqual(self.scope().lines, {"factory/example.py": {1}})

    def test_modified_file_is_applicable(self) -> None:
        self.assert_applicable(witness.FACTORY_CHANGE, "factory/example.py")
        self.assertEqual(self.scope().lines, {"factory/example.py": {1}})

    def test_rename_with_edit_is_applicable_on_every_destination_line(self) -> None:
        body = "".join(f"line_{i} = {i}\n" for i in range(10))
        self.fixture = ({"factory/old.py": body},
                        {"factory/old.py": None, "factory/new.py": body.replace("line_9 = 9", "line_9 = 90")})
        renamed = "D\tfactory/old.py\nA\tfactory/new.py\n"
        self.assert_applicable(renamed, "factory/new.py")
        # git itself calls this a rename with edit; the shared rule (--no-renames) counts
        # every destination line of the new path, exactly as the runner always has.
        self.assertRegex(git(self.root, "diff", "-M", "--name-status", self.base, self.head),
                         r"^R\d+\tfactory/old.py\tfactory/new.py$")
        self.assertEqual(self.scope().lines, {"factory/new.py": set(range(1, 11))})

    def test_docs_only_change_is_a_genuine_skip(self) -> None:
        self.assert_real_runner("M\tdocs/guide.md\n", ("SKIP", {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("SKIP", "PASS")]}))

    def test_zero_candidates_on_applicable_lines_is_complete_not_skip(self) -> None:
        self.assert_real_runner(witness.FACTORY_CHANGE, ("PASS", {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]}),
            sampled=([], 0))
        self.assertIn("- mutation: PASS", self.review_reports[0])
        self.assertIn("complete: 0 mutation candidates enumerated on 1 applicable changed lines (not a skip)",
                      self.review_reports[0])

    # -- dispatcher binding: head and caller base -------------------------------------
    def test_cached_unqualified_pass_is_rerun_with_proof(self) -> None:
        self.configure()
        pr_runs = {self.head: (True, LEGACY_PASS_REPORT)}
        result = self.invoke([witness.INCOMPLETE_EXIT0], pr_runs=pr_runs)
        self.assertEqual(len(self.children), 1)  # the cached PASS was not reused
        self.assertEqual(self.summary(result), witness.BLOCKED)

    def test_cached_unbound_draft_proof_header_is_rerun(self) -> None:
        self.configure()
        pr_runs = {self.head: (True, DRAFT1_PASS_REPORT)}  # proof header without base/head/scope
        result = self.invoke([witness.INCOMPLETE_EXIT0], pr_runs=pr_runs)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.summary(result), witness.BLOCKED)

    def test_cached_proof_report_is_reused_and_stays_qualified(self) -> None:
        self.configure()
        pr_runs: dict = {}
        self.invoke([COMPLETE], pr_runs=pr_runs)
        dispatch._PENDING_REVIEWS.clear()
        dispatch._PENDING_SECOND_REVIEWS.clear()
        second = self.invoke(pr_runs=pr_runs)
        self.assertEqual(len(self.children), 1)
        self.assertEqual((second["verdict"], second["head_accepted"]), ("APPROVE", True))

    def test_cached_proof_bound_to_an_old_base_is_rerun(self) -> None:
        self.configure()
        pr_runs: dict = {}
        self.invoke([COMPLETE], pr_runs=pr_runs)
        git(self.root, "update-ref", "refs/remotes/origin/main", self.head)  # caller base moves
        dispatch._PENDING_REVIEWS.clear()
        dispatch._PENDING_SECOND_REVIEWS.clear()
        second = self.invoke([witness.NO_CHANGE_EXIT0], pr_runs=pr_runs)
        self.assertEqual(len(self.children), 2)  # stale base: not reused
        self.assertEqual(self.children[1].kwargs["env"][mutation.PROOF_BASE_ENV], self.head)
        self.assertEqual((second["report_status"], second["risk"][-1]), ("SKIP", ("SKIP", "PASS")))

    def test_report_bound_to_another_head_never_accepts(self) -> None:
        self.configure()
        other = self.scope()
        report = (f"# Gate report (pr tier)\n\nMutation proof: mutation PASS base={self.base} "
                  f"head={'b' * 40} scope={other.fingerprint}\n\n## Host toolchain\n\n- mutation: PASS\n")
        self.assertEqual(dispatch._mutation_proof(report, self.head, self.base), ("STALE", None))
        self.assertEqual(dispatch._mutation_proof(report, "b" * 40, self.base), ("PASS", other.fingerprint))
        self.assertEqual(dispatch._mutation_proof(report, "b" * 40, "c" * 40), ("STALE", None))
        self.assertEqual(dispatch._mutation_proof(report.replace("- mutation: PASS", "- mutation: FAIL"),
                                                  "b" * 40, self.base), ("FAIL", None))
        result = self.invoke([witness.INCOMPLETE_EXIT0], pr_runs={self.head: (True, report)})
        self.assertEqual(len(self.children), 1)  # rebound to this head by a fresh run
        self.assertEqual(self.summary(result), witness.BLOCKED)

    def test_timeout_after_apparent_completion_still_blocks(self) -> None:
        self.configure()
        timeout = subprocess.TimeoutExpired(witness.REPO_MUTATION_RUN, self.cfg.check_timeout)
        result = self.invoke([timeout, produced()])  # complete exit-0 record as the timeout fires
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertIn("mutation proof: FAIL (check_timeout overrides", self.review_reports[0])

    def test_signal_after_complete_record_still_blocks(self) -> None:
        self.configure()

        def signalled(child):
            produced()(child)  # a complete exit-0 record, then the child dies by signal
            return "partial\n", -signal.SIGTERM

        self.assertEqual(self.summary(self.invoke([signalled])), witness.BLOCKED)

    # -- unopted, Medium and historical rows -------------------------------------------
    def test_custom_unopted_check_never_gets_a_record_path(self) -> None:
        self.configure(check_run=["./custom-mutation-check"], proof=None)
        result = self.invoke([("custom tool: ok\n", 0)])
        self.assertNotIn("--mutation-proof", self.gate_cmds[0])
        self.assertEqual(self.child_saw_record_path(), [False])
        self.assertNotIn(mutation.PROOF_BASE_ENV, self.children[0].kwargs["env"])
        self.assertNotIn("mutation_proof", self.risk_rows()[-1])
        self.assertEqual(self.summary(result)["risk"], [("PASS", "PASS")])
        self.assertTrue(result["head_accepted"])

    def test_opting_in_later_refuses_legacy_risk_rows(self) -> None:
        self.configure(check_run=["./custom-mutation-check"], proof=None)
        legacy = self.invoke([("custom tool: ok\n", 0)])
        self.assertTrue(legacy["head_accepted"])
        self.cfg.checks[0].proof = config.MUTATION_PROOF  # repository now opts in
        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertFalse(dispatch._head_evidence_matches(
            events, witness.N, self.head, changed_paths=["factory/example.py"]))

    def test_draft1_risk_row_without_bound_scope_never_qualifies(self) -> None:
        self.configure()
        self.invoke([COMPLETE])
        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(dispatch._head_evidence_matches(events, witness.N, self.head,
                                                        changed_paths=["factory/example.py"]))
        unbound = [{k: v for k, v in e.items() if k not in {"mutation_scope", "mutation_base"}}
                   if e.get("event") == "risk-check" else e for e in events]
        self.assertFalse(dispatch._head_evidence_matches(unbound, witness.N, self.head,
                                                         changed_paths=["factory/example.py"]))

    def test_medium_opted_repository_stays_report_only_without_risk_row(self) -> None:
        self.configure(tier="Medium")
        result = self.invoke([witness.INCOMPLETE_EXIT0])
        self.assertNotIn("--mutation-proof", self.gate_cmds[0])
        self.assertEqual(self.child_saw_record_path(), [False])
        self.assertEqual(result["risk"], [])
        self.assertEqual((result["verdict"], result["head_accepted"]), ("APPROVE", True))


del ProofCallerTest  # harness base only; its inherited witness cases are disabled above
