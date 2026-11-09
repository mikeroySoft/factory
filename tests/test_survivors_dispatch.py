"""Survivor kills for factory.dispatch: unbound FAIL proof headers and the High risk-check
mutation status for a change with no factory code."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch

HEAD = "a" * 40
BASE = "c" * 40


class MutationProofHeaderTest(unittest.TestCase):
    def test_unbound_fail_header_stays_fail(self) -> None:
        report = "- mutation: FAIL\nMutation proof: mutation FAIL\n"
        self.assertEqual(dispatch._mutation_proof(report, HEAD, BASE), ("FAIL", None))

    def test_unbound_pass_header_is_stale(self) -> None:
        report = "- mutation: PASS\nMutation proof: mutation PASS\n"
        self.assertEqual(dispatch._mutation_proof(report, HEAD, BASE), ("STALE", None))


class HighRiskCheckMutationTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        cfg = config.Config(self.root, "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(cfg)
        for pending in (dispatch._PENDING_REVIEWS, dispatch._PENDING_SECOND_REVIEWS,
                        dispatch._PENDING_RISK):
            self.addCleanup(pending.pop, 7, None)
        # Real journal rows: risk_for_head reads the High tier and _worker_model_for_head the
        # worker model for this head from the events journal in the temp root.
        dispatch.record("risk", ticket=7, head=HEAD, tier="High", matched_rules=["high:risk policy"])
        dispatch.record("attempt", ticket=7, head=HEAD, worker_model="worker/m")

    def risk_check(self, report: str, changed: str) -> dict:
        def review(*_args, role="primary", **_kwargs):
            second = role == "second"
            pending = dispatch._PENDING_SECOND_REVIEWS if second else dispatch._PENDING_REVIEWS
            pending[7] = {"verdict": "APPROVE", "model": f"{role}/model"}
            return "APPROVE", "ok"

        # Process boundaries only: the reviewer CLI, the gate subprocess and git.
        with mock.patch.object(dispatch, "run_gate", return_value=(True, report)), \
                mock.patch.object(dispatch, "review", side_effect=review), \
                mock.patch.object(dispatch, "run", return_value=mock.Mock(stdout=changed)):
            dispatch.pr_gate_and_review(self.root, 7, "attempt", HEAD, {})
        rows = dispatch.lifecycle.read_events(dispatch.EVENTS)
        return next(row for row in rows if row.get("event") == "risk-check")

    def test_pass_without_factory_code_is_recorded_as_pass(self) -> None:
        row = self.risk_check("- mutation: PASS\n", "M\tdocs/guide.md\n")
        self.assertEqual(row["mutation"], "PASS")

    def test_missing_without_factory_code_is_recorded_as_skip(self) -> None:
        row = self.risk_check("", "M\tdocs/guide.md\n")
        self.assertEqual(row["mutation"], "SKIP")


if __name__ == "__main__":
    unittest.main()
