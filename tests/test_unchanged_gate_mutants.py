"""Mutation boundaries for unchanged gate evidence."""
from __future__ import annotations

import subprocess
import unittest

from factory import dispatch
from tests import test_actionable_rework as rework_tests
from tests import test_unchanged_round_edges as edge_tests


class WorkerRoundGateCacheMutantsTest(unittest.TestCase):
    def fixture(self):
        fixture = edge_tests.WorkerRoundCacheEdgesTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_flag_order_does_not_change_in_memory_cache_key(self) -> None:
        fixture = self.fixture()
        flags = {"protected_override": False, "add_only_override": True}
        flags_mock = fixture.patch(dispatch, "gate_flags", return_value=flags)
        fixture.gate.return_value = (True, "passing report")
        cache: dict = {}
        self.assertTrue(fixture.run_round(cache)[0])
        flags_mock.return_value = dict(reversed(list(flags.items())))
        fixture.head_values = [edge_tests.HEAD] * 3

        ok, report, _log, _head = fixture.run_round(cache)

        self.assertTrue(ok)
        self.assertEqual(report, "passing report")
        fixture.gate.assert_called_once()

    def test_persisted_cache_rejects_a_different_journal_key(self) -> None:
        fixture = self.fixture()
        report = b"journal report"
        row = fixture.cached_row(report)
        row["gate_key"][1] = "different-base"
        fixture.rows = [row]
        fixture.write_cache(report)

        ok, text, _log, _head = fixture.run_round({})

        self.assertFalse(ok)
        self.assertEqual(text, "fresh gate report")
        fixture.gate.assert_called_once()

    def test_persisted_cache_rejects_a_different_actual_head(self) -> None:
        fixture = self.fixture()
        report = b"journal report"
        row = fixture.cached_row(report)
        row["actual_head"] = "0" * 40
        fixture.rows = [row]
        fixture.write_cache(report)

        ok, text, _log, _head = fixture.run_round({})

        self.assertFalse(ok)
        self.assertEqual(text, "fresh gate report")
        fixture.gate.assert_called_once()

    def test_later_postgate_head_is_not_cached(self) -> None:
        fixture = self.fixture()
        gated_head = "0" * 40
        later_head = "f" * 40
        fixture.gate.return_value = (True, "stale passing report")
        fixture.head_values = [gated_head, gated_head, later_head]
        cache: dict = {}
        cache_report = dispatch.FACTORY / "gate-cache" / "7.md"

        ok, report, _log, _head = fixture.run_round(cache)

        self.assertFalse(ok)
        self.assertIn(f"HEAD changed from {gated_head} to {later_head}", report)
        self.assertEqual(cache, {})
        self.assertFalse(cache_report.exists())


class CarriedReworkMutantsTest(unittest.TestCase):
    def test_earlier_head_does_not_match_carried_rework(self) -> None:
        fixture = rework_tests.ActionableReworkTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = fixture.command_result
        fixture.command.side_effect = lambda args, **kwargs: (
            subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "status"] else original(args, **kwargs)
        )
        carried_head = "f" * 40
        gate_head = "0" * 40
        findings = "carried findings"
        dispatch.record(
            "review-rework", ticket=7, head=carried_head, round=1,
            findings=findings, deferred=True,
        )

        def work(*_args, **_kwargs):
            fixture.heads.append(gate_head)
            fixture.pr["headRefOid"] = gate_head
            dispatch.record(
                "attempt", ticket=7, head=gate_head, actual_head=gate_head,
                gate="PASS", worker_model="worker/model", finished=True, clean=True,
            )
            return True, "- test: PASS\n", fixture.root / "worker.log", gate_head

        fixture.worker.side_effect = work
        review = fixture.patch(
            dispatch, "pr_gate_and_review", return_value=("APPROVE", "approved", False),
        )
        fixture.patch(dispatch, "approve_pr", return_value=True)

        dispatch.process_ticket(fixture.issue, 1, False, forced=True)

        self.assertEqual(fixture.worker.call_count, 1)
        review.assert_called_once()
        fixture.escalate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
