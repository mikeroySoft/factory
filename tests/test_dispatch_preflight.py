"""#732: refuse unsafe multi-host admission and exhausted GitHub budgets."""
from __future__ import annotations

import copy
import io
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import UTC, datetime
from unittest import mock

from factory import lifecycle
from tests import test_host_claims as host_fixture


class DispatchPreflightTest(unittest.TestCase):
    setUp = host_fixture.HostClaimRaceTest.setUp
    dispatcher = host_fixture.HostClaimRaceTest.dispatcher
    launch = host_fixture.HostClaimRaceTest.launch

    def run_pass(
        self, *, host=None, labels=(), comments=(), rest=5000, graphql=5000,
        error=None, repo_error=False, rates=None, argv=None,
    ):
        module = self.dispatcher(host)
        read = module.gh_json

        def github(args):
            if args == ["api", "rate_limit"]:
                if error:
                    raise error
                if rates is not None:
                    return rates
                return {"resources": {"core": {"remaining": rest, "reset": 1800000000},
                                      "graphql": {"remaining": graphql, "reset": 1800000000}}}
            if args[0] == "api" and any(arg.endswith("/labels?per_page=100") for arg in args):
                return [[{"name": name} for name in labels]]
            if args[0] == "api" and any(arg.endswith("/issues/comments?per_page=100") for arg in args):
                if repo_error:
                    raise OSError("repository unavailable")
                return [[], list(comments)]
            return read(args)

        output = io.StringIO()
        with (
            mock.patch.object(module, "gh_json", side_effect=github),
            mock.patch.object(module, "pin_root", return_value=[]),
            mock.patch.object(module.config, "load", return_value=module.cfg),
            mock.patch.object(module.config, "engine_commit", return_value=None),
            mock.patch.object(module.config, "engine_target", return_value=None),
            mock.patch.object(module, "_sweep_stale_factory_tempdirs"),
            mock.patch.object(module, "worker_health_pass") as health,
            mock.patch.object(module, "land_pass") as land,
            mock.patch.object(module, "review_intake_pass"),
            mock.patch("factory.results.prune", return_value={"status": "complete"}),
            mock.patch("factory.manage.manage_pass"),
            mock.patch("factory.learn.learn_pass"),
            redirect_stdout(output),
        ):
            result = module.main(["--ticket", "7"] if argv is None else argv)
        return module, result, output.getvalue(), health, land

    def assert_no_claim(self):
        self.assertEqual(self.launched, [])
        self.assertFalse(any("--add-assignee" in args or "POST" in args
                             for _, args in self.github.calls), self.github.calls)

    def test_unnamed_host_refuses_repository_host_label_before_assignment(self):
        _, result, output, health, _ = self.run_pass(labels=["host:ultron"])
        self.assert_no_claim()
        self.assertNotEqual(result, 0)
        self.assertIn("host_name", output)
        health.assert_not_called()

    def test_unnamed_host_refuses_claim_on_another_closed_ticket(self):
        claim = {"body": f"Factory claim: host=ultron nonce=old at={datetime.now(UTC).isoformat()}",
                 "user": {"login": "@me"}, "issue_url": "repos/local/claims/issues/99"}
        _, result, output, _, _ = self.run_pass(comments=[claim])
        self.assert_no_claim()
        self.assertNotEqual(result, 0)
        self.assertIn("host_name", output)

    def test_different_repository_login_refuses_before_claim(self):
        claim = {"body": f"Factory claim: host=tank nonce=old at={datetime.now(UTC).isoformat()}",
                 "user": {"login": "different-account"}}
        _, result, output, _, _ = self.run_pass(host="ultron", comments=[claim])
        self.assert_no_claim()
        self.assertNotEqual(result, 0)
        self.assertIn("login", output)

    def test_exhausted_rest_or_graphql_skips_whole_pass(self):
        for resource in ("rest", "graphql"):
            with self.subTest(resource=resource):
                module, result, output, health, land = self.run_pass(host=resource, **{resource: 0}, argv=[])
                self.assert_no_claim()
                self.assertEqual(result, 0)
                self.assertIn("rate", output.lower())
                health.assert_not_called()
                land.assert_not_called()
                rows = lifecycle.read_events(module.EVENTS)
                self.assertTrue(any(row.get("event") == "pass-skipped" and
                                    row.get("reason") == "github_rate_limit" for row in rows), rows)
                self.assertFalse(any(row.get("event") == "escalate" for row in rows))

    def test_below_floor_skips_before_ticket_reads(self):
        _, result, output, _, _ = self.run_pass(rest=99)
        self.assert_no_claim()
        self.assertEqual(result, 0)
        self.assertIn("floor 100", output)
        self.assertEqual(self.github.calls, [])

    def test_budget_at_floor_allows_single_host_dispatch(self):
        _, result, _, _, _ = self.run_pass(rest=100, graphql=100)
        self.assertEqual(result, 0)
        self.assertEqual(self.launched, [None])

    def test_unreadable_repository_refuses_without_claim(self):
        _, result, output, _, _ = self.run_pass(repo_error=True)
        self.assert_no_claim()
        self.assertEqual(result, 1)
        self.assertIn("repository unavailable", output)

    def test_unparseable_rate_budget_skips_without_claim(self):
        _, result, output, _, _ = self.run_pass(rates={})
        self.assert_no_claim()
        self.assertEqual(result, 0)
        self.assertIn("rate", output.lower())

    def test_failed_rate_read_skips_without_claim_or_escalation(self):
        error = subprocess.CalledProcessError(1, ["gh", "api", "rate_limit"], stderr="unavailable")
        _, result, output, _, _ = self.run_pass(error=error)
        self.assert_no_claim()
        self.assertEqual(result, 0)
        self.assertIn("rate", output.lower())

    def test_ordinary_single_host_still_dispatches(self):
        _, result, _, _, _ = self.run_pass()
        self.assertEqual(result, 0)
        self.assertEqual(self.launched, [None])

    def test_dry_run_budget_skip_writes_no_events(self):
        module, result, output, _, land = self.run_pass(graphql=0, argv=["--dry-run"])
        self.assertEqual(result, 0)
        self.assertIn("rate", output.lower())
        land.assert_not_called()
        self.assertEqual(lifecycle.read_events(module.EVENTS), [])

    def test_different_logins_racing_after_empty_reads_cannot_both_win(self):
        modules = [self.dispatcher(host) for host in ("tank", "ultron")]
        issues = [copy.deepcopy(self.github.issue) for _ in modules]
        self.github.fresh_barrier = threading.Barrier(2)
        self.github.comments_barrier = threading.Barrier(2)
        self.github.post_barrier = threading.Barrier(2)
        authors_ready = threading.Barrier(2)
        elections_ready = threading.Barrier(2)
        for module in modules:
            original = module.gh_json
            login = module.cfg.host_name + "-login"
            stage = [0]

            def github(args, original=original, login=login, stage=stage):
                if args == ["api", "user"]:
                    return {"login": login}
                value = original(args)
                if "POST" in args:
                    with self.github.lock:
                        value["user"] = {"login": login}
                    authors_ready.wait(timeout=10)
                    stage[0] = 1
                elif stage[0] == 1 and args[0] == "api" and "--slurp" in args:
                    stage[0] = 2
                    elections_ready.wait(timeout=10)
                return value

            self.enterContext(mock.patch.object(module, "gh_json", side_effect=github))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(module.process_ticket, issue, 1, False)
                       for module, issue in zip(modules, issues)]
            for future in futures:
                future.result(timeout=20)
        self.assertLessEqual(len(self.launched), 1, self.launched)
        self.assertFalse(any("--add-assignee" in args for _, args in self.github.calls))
        self.assertEqual(self.github.comments, [])
        self.assertFalse(any(row.get("event") == "escalate"
                             for module in modules for row in lifecycle.read_events(module.EVENTS)))
