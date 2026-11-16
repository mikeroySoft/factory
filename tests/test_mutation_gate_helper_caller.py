"""Regression: the GATE's scope helper missing or incompatible, or a gate that writes no
report, never approves or accepts a High head through the real caller.

Real: dispatch.pr_gate_and_review -> dispatch.run_gate (including its existing no-report
branch, which writes `gate runner did not write ...` and fails) -> risk-check row ->
dispatch._head_evidence_matches. For the missing and incompatible cases the in-process gate
child is the real `factory.cli.main(["gate", ...])`: with `factory.mutation_scope`
unimportable, re-importing factory.gate raises ImportError; with a draft2-era helper (no
`tracked_mismatches`) bound into the gate, `_proof_run` raises AttributeError.

Synthetic (disclosed): the gate child is in-process (witness harness), so an uncaught
exception is mapped to what `python -m factory gate` does on one: exit 1, traceback on
stderr, no report. The no-report case is a gate child that exits 0 without writing one.
No mutation check child is started in any case. This is existing fail-closed behaviour;
no product change is made or needed for it.
"""
from __future__ import annotations

import subprocess
import sys
import traceback
import types
import unittest
from unittest import mock

from factory import config, dispatch, gate, lifecycle, mutation_scope
from tests import test_mutation_caller_witness as witness
from tests import test_mutation_input_integrity as integrity


class GateHelperCallerTest(witness.MutationCallerWitness):
    def setUp(self) -> None:
        super().setUp()
        self.gate_child = None  # None: the real in-process gate; else a replacement result

    commit_fixture = integrity.IntegrityCallerTest.commit_fixture

    def _dispatch_run(self, cmd, cwd=None, check=True, timeout=None, **kwargs):
        if cmd[:6] == witness.GATE_ARGV:
            if self.gate_child is not None:
                self.gate_exits.append(self.gate_child.returncode)
                return self.gate_child
            try:
                return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)
            except (ImportError, AttributeError):  # uncaught in `python -m`: exit 1, no report
                self.gate_exits.append(1)
                return subprocess.CompletedProcess(cmd, 1, "", traceback.format_exc())
        return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)

    def assert_rejected(self, result: dict, gate_exit: int, detail: str) -> None:
        self.assertEqual(result["verdict"], "REVISE")
        self.assertEqual(self.summary(result), {**witness.BLOCKED, "gate_exit": gate_exit})
        self.assertEqual(result["report_status"], "MISSING")
        self.assertIn(f"did not write .factory/gate-report-{witness.N}-pr.md (exit {gate_exit})", result["findings"])
        self.assertIn(detail, result["findings"])
        self.assertEqual(self.review_reports, [])  # no reviewer was asked: no APPROVE
        self.assertEqual(self.children, [])  # the mutation check never started
        row = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"][-1]
        self.assertEqual((row["mutation"], row["pr_gate"], row["mutation_proof"]),
                         ("FAIL", "FAIL", config.MUTATION_PROOF))
        self.assertNotIn("mutation_scope", row)

    def test_missing_gate_helper_rejects(self) -> None:
        self.configure()
        with integrity.unimportable_scope(), mock.patch.dict(sys.modules):
            del sys.modules["factory.gate"]  # the child imports factory.gate afresh
            result = self.invoke([witness.COMPLETE_PASS])  # a true record would pass
        self.assertIs(sys.modules["factory.gate"], gate)
        self.assert_rejected(result, 1, "ModuleNotFoundError: import of factory.mutation_scope halted")

    def test_incompatible_gate_helper_rejects(self) -> None:
        self.configure()
        old = types.ModuleType("factory.mutation_scope")  # draft2-era helper: no byte check
        old.__dict__.update({k: v for k, v in vars(mutation_scope).items()
                             if k not in {"tracked_mismatches", "__name__"}})
        with mock.patch.object(gate, "mutation_scope", old):
            result = self.invoke([witness.COMPLETE_PASS])
        self.assert_rejected(result, 1, "AttributeError")

    def test_gate_exit0_without_a_report_rejects(self) -> None:
        self.configure()
        self.gate_child = subprocess.CompletedProcess(witness.GATE_ARGV, 0, "", "")
        self.assert_rejected(self.invoke([witness.COMPLETE_PASS]), 0, "(exit 0)")


for _name in [name for name in dir(witness.MutationCallerWitness) if name.startswith("test_")]:
    setattr(GateHelperCallerTest, _name, None)


if __name__ == "__main__":
    unittest.main()
