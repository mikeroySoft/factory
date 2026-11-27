"""#828: bulk admission reserves more than the single-ticket API floor."""
import unittest

from factory import lifecycle
from tests import test_dispatch_preflight as preflight


class BulkBudgetTest(unittest.TestCase):
    setUp = preflight.DispatchPreflightTest.setUp
    dispatcher = preflight.DispatchPreflightTest.dispatcher
    launch = preflight.DispatchPreflightTest.launch
    run_pass = preflight.DispatchPreflightTest.run_pass
    assert_no_claim = preflight.DispatchPreflightTest.assert_no_claim

    def test_bulk_pass_defers_before_large_pr_listing_below_graphql_floor(self):
        module, result, output, health, land = self.run_pass(
            host="ultron", rest=5000, graphql=999, argv=[],
        )
        self.assertEqual(result, 0)
        self.assert_no_claim()
        health.assert_not_called()
        land.assert_not_called()
        self.assertEqual(self.github.calls, [])
        self.assertIn("graphql has 999 remaining (floor 1000)", output)
        rows = lifecycle.read_events(module.EVENTS)
        [skip] = [row for row in rows if row.get("event") == "pass-skipped"]
        self.assertEqual(skip["code"], "rate_limited")
        [backoff] = [row for row in rows if row.get("event") == "gh-read-backoff"]
        self.assertEqual(backoff["reset"], 1800000000)

    def test_bulk_budget_at_floor_reaches_landing(self):
        _, result, _, health, land = self.run_pass(
            host="ultron", rest=100, graphql=1000, argv=[],
        )
        self.assertEqual(result, 0)
        health.assert_called_once_with()
        land.assert_called_once_with(False)

    def test_bulk_dry_run_reports_floor_without_persisting_backoff(self):
        module, result, output, _, land = self.run_pass(
            graphql=999, argv=["--dry-run"],
        )
        self.assertEqual(result, 0)
        self.assertIn("floor 1000", output)
        land.assert_not_called()
        self.assertEqual(lifecycle.read_events(module.EVENTS), [])
