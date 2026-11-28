"""Regression coverage for reachable dispatch comparison survivors on issue #947."""
from __future__ import annotations

import unittest

from factory import dispatch
from tests import test_authorization_request as authorization_fixture
from tests import test_unchanged_round_edges as cache_fixture


class RefreshContextComparisonTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp

    def test_manager_request_with_producer_rework_supplies_refresh_context(self) -> None:
        dispatch.record(
            "manager-request", ticket=7, code="add_only_authorization",
            paths=self.paths, packet=str(self.cfg.factory / "escalations" / "7.md"),
            log=str(self.logfile), round=2, exhausted=False,
            rework={"head": "a" * 40, "findings": "Keep the policy assertion.", "pr_failed": False},
        )

        context = dispatch.refresh_requeue_context(7)

        self.assertIn("Reviewer findings, round 2", context)
        self.assertIn("Keep the policy assertion.", context)

    def test_lexically_prior_producer_event_does_not_replace_refresh_context(self) -> None:
        dispatch.record(
            "conflict-requeue", ticket=7, pr=70, head="a" * 40,
            verb="rebase", files=["factory/dispatch.py"], round=1,
        )
        dispatch.record(
            "llm-usage", ticket=7, stage="worker", attempt=1, local=False,
            worker="default", worker_version=None, worker_model=None, model=None,
            prompt_tokens=1, completion_tokens=1, cached_tokens=0, cost=0,
        )

        context = dispatch.refresh_requeue_context(7)

        self.assertIn("Refresh conflict, round 1", context)
        self.assertIn("factory/dispatch.py", context)



class DispatchComparisonRegressionTest(unittest.TestCase):
    def test_persisted_cache_reuses_exact_actual_head(self) -> None:
        fixture = cache_fixture.WorkerRoundCacheEdgesTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        report = b"recorded report"
        fixture.rows = [fixture.cached_row(report)]
        fixture.write_cache(report)

        ok, text, _log, _head = fixture.run_round({})

        self.assertTrue(ok)
        self.assertEqual(text, report.decode())
        fixture.gate.assert_not_called()

    def test_persisted_cache_rejects_lexically_greater_actual_head(self) -> None:
        fixture = cache_fixture.WorkerRoundCacheEdgesTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        head = "1" * 40
        actual_head = "e" * 40
        report = b"tampered persisted report"
        row = fixture.cached_row(report)
        row.update(
            head=head,
            actual_head=actual_head,
            gate_key=list(fixture.gate_key(head)),
        )
        fixture.rows = [row]
        fixture.head_values = [head, head, head]
        fixture.write_cache(report)

        ok, text, _log, _head = fixture.run_round({})

        self.assertFalse(ok)
        self.assertEqual(text, "fresh gate report")
        fixture.gate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
