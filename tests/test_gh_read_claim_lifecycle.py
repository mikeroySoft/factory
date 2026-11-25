"""Claim ownership survives deferred reads without abandoning renewal or cleanup."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from types import FunctionType
from unittest import mock

from factory import dispatch, gh_reads, lifecycle
from tests import test_host_claims


class ClaimReadLifecycleTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def connected_dispatcher(self, host):
        module = self.dispatcher(host)
        self.failure = None
        self.reset = 4102444800
        self.remaining = 5000
        self.commands = []
        for name in ("gh_json", "run"):
            original = getattr(dispatch, name)
            function = FunctionType(original.__code__, module.__dict__, argdefs=original.__defaults__)
            self.enterContext(mock.patch.object(module, name, function))

        def command(cmd, *args):
            self.commands.append(list(cmd))
            if self.failure is not None:
                failure = self.failure(cmd)
                if failure:
                    raise subprocess.CalledProcessError(1, cmd, "", failure)
            if gh_reads.is_read(cmd) or (
                cmd[:2] == ["gh", "api"] and "--method" in cmd
                and cmd[cmd.index("--method") + 1] == "POST"
            ):
                return subprocess.CompletedProcess(cmd, 0, json.dumps(self.github.json(host, cmd[1:])), "")
            if cmd[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/local/claims/issues/7#issuecomment-99", "")
            return self.github.run(host, cmd)

        def probe(cmd, **kwargs):
            self.assertEqual(cmd[:3], ["gh", "api", "rate_limit"])
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": {
                name: {"remaining": self.remaining, "reset": self.reset}
                for name in ("core", "graphql")
            }}), "")

        self.enterContext(mock.patch.object(module, "_run", side_effect=command))
        self.enterContext(mock.patch.object(module.runner, "preflight", return_value=None))
        self.enterContext(mock.patch.object(module.config, "engine_commit", return_value=None))
        self.enterContext(mock.patch("factory.gh_reads.subprocess.run", side_effect=probe))
        return module

    def fail_next_claim_read(self, message="connection refused"):
        failed = False

        def failure(cmd):
            nonlocal failed
            if not failed and gh_reads.is_read(cmd) and any(arg.endswith("/comments") for arg in cmd):
                failed = True
                return message
            return None

        self.failure = failure

    def heartbeat(self, module):
        self.github.add_claim("tank", datetime.now(UTC))
        [comment] = module.host_claims(7)
        return module.HostClaim(7, comment)

    def test_active_heartbeat_ignores_unrelated_admission_backoff(self):
        module = self.connected_dispatcher("tank")
        heartbeat = self.heartbeat(module)
        lifecycle.append(module.EVENTS, {
            "event": "gh-read-backoff", "stage": "dispatch", "reset": self.reset,
        })
        with mock.patch.object(heartbeat.stopped, "wait", side_effect=[False, True]):
            heartbeat.heartbeat()
        self.assertEqual(self.github.patch_count, 1)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual([row["host"] for row in module.host_claims(7)], ["tank"])

    def test_heartbeat_revalidates_ownership_after_transient_read_failure(self):
        module = self.connected_dispatcher("tank")
        heartbeat = self.heartbeat(module)
        self.fail_next_claim_read()
        with mock.patch.object(heartbeat.stopped, "wait", side_effect=[False, False, True]):
            heartbeat.heartbeat()
        self.assertEqual(self.github.patch_count, 1)
        skips = [row for row in lifecycle.read_events(module.EVENTS) if row["event"] == "gh-read-skipped"]
        self.assertEqual([(row["stage"], row["ticket"]) for row in skips], [("claim-heartbeat", 7)])

    def test_heartbeat_honors_its_own_reset_then_resumes_renewal(self):
        module = self.connected_dispatcher("tank")
        heartbeat = self.heartbeat(module)
        self.reset, self.remaining = 200, 0
        self.fail_next_claim_read("API rate limit exceeded")
        clock = [100]
        ticks = iter([100, 101, 200, None])

        def wait(_interval):
            tick = next(ticks)
            if tick is None:
                return True
            clock[0] = tick
            return False

        self.commands.clear()
        with mock.patch.object(heartbeat.stopped, "wait", side_effect=wait), \
                mock.patch("factory.gh_reads.time.time", side_effect=lambda: clock[0]):
            heartbeat.heartbeat()
        ownership_reads = [cmd for cmd in self.commands if gh_reads.is_read(cmd)
                           and any(arg.endswith("/comments") for arg in cmd)]
        self.assertEqual(len(ownership_reads), 2, "renewal must not read again before its own reset")
        self.assertEqual(self.github.patch_count, 1, "renewal must resume when its own reset expires")

    def test_heartbeat_does_not_renew_a_claim_lost_during_read_failure(self):
        module = self.connected_dispatcher("tank")
        heartbeat = self.heartbeat(module)
        self.fail_next_claim_read()
        ticks = iter([0, 1, 2])

        def wait(_interval):
            tick = next(ticks)
            if tick == 1:
                self.github.comments.clear()
                self.github.add_claim("ultron", datetime.now(UTC))
            return tick == 2

        with mock.patch.object(heartbeat.stopped, "wait", side_effect=wait):
            heartbeat.heartbeat()
        self.assertEqual(self.github.patch_count, 0)
        self.assertEqual([row["host"] for row in module.host_claims(7)], ["ultron"])

    def test_claimed_read_failure_is_unchanged_and_retryable_on_next_pass(self):
        module = self.connected_dispatcher(None)
        before = copy.deepcopy(self.github.issue)

        def work(*args, **kwargs):
            cmd = ["gh", "issue", "view", "7", "--repo", module.REPO, "--json", "body"]
            return gh_reads.run(cmd, lambda: subprocess.CompletedProcess(cmd, 1, "", "connection refused"))

        with mock.patch.object(module, "worker_round", side_effect=work):
            module.process_ticket(copy.deepcopy(before), 1, False)
        self.assertEqual(self.github.issue, {**before, "assignees": [{"login": "@me"}]})
        rows = lifecycle.read_events(module.EVENTS)
        self.assertEqual([row["ticket"] for row in rows if row["event"] == "ticket-read-deferred"], [7])
        self.assertFalse(any(row["event"] == "escalate" for row in rows))
        [retry] = module.frontier()
        self.assertNotIn("held", retry)
        module.process_ticket(retry, 1, False)
        self.assertEqual(self.launched, [None])
        self.assertFalse(module.lock_held(module.ticket_lock(7)))

    def test_read_failure_during_exceptional_release_keeps_retry_evidence(self):
        module = self.connected_dispatcher("tank")

        def missing_worker(*args, **kwargs):
            self.fail_next_claim_read()
            raise FileNotFoundError("worker disappeared")

        with mock.patch.object(module, "worker_round", side_effect=missing_worker):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        rows = lifecycle.read_events(module.EVENTS)
        self.assertEqual([row["ticket"] for row in rows if row["event"] == "ticket-read-deferred"], [7])
        self.assertFalse(any(row["event"] == "escalate" for row in rows))
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual([row["host"] for row in module.host_claims(7)], ["tank"])
        with mock.patch.object(module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [retry] = module.frontier()
            self.assertNotIn("held", retry)
            module.process_ticket(retry, 1, False)
        self.assertEqual(self.launched, ["tank"])
        self.assertFalse(module.lock_held(module.ticket_lock(7)))

    def test_deferred_exceptional_escalation_finishes_once_without_worker_replay(self):
        module = self.connected_dispatcher("tank")

        def crash(*args, **kwargs):
            self.fail_next_claim_read()
            raise RuntimeError("dispatcher crashed")

        with mock.patch.object(module, "worker_round", side_effect=crash):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertIn({"name": "ready-for-agent"}, self.github.issue["labels"])
        [retry] = module.frontier()
        self.assertNotIn("held", retry, "pending escalation cleanup must not wait for claim expiry")
        module.process_ticket(retry, 1, False)
        module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": module.LABEL_HUMAN}])
        self.assertEqual(self.github.comments, [])
        rows = lifecycle.read_events(module.EVENTS)
        self.assertEqual(len([row for row in rows if row["event"] == "escalate"]), 1)
        self.assertEqual(len([row for row in rows if row["event"] == "ticket-read-deferred"]), 1)
        posted = [cmd for cmd in self.commands if cmd[:3] == ["gh", "issue", "comment"]]
        self.assertEqual(len(posted), 1)
        self.assertIn("dispatcher crashed", posted[0][-1])

    def pending_escalation(self, host):
        module = self.connected_dispatcher(host)
        if host:
            self.github.add_claim(host, datetime.now(UTC))
        else:
            self.github.issue["assignees"] = [{"login": "@me"}]
        with lifecycle.scope(module.EVENTS, "ticket", ticket=7):
            module.record(
                "escalate", ticket=7, code="dispatcher_error", reason="dispatcher crashed",
                packet=str(module.FACTORY / "escalations" / "7.md"), round=1, log=None,
            )
            module.record("ticket-read-deferred", ticket=7, code="gh_read_failed")
        return module

    def assert_pending_refused(self, module, *, held=None):
        before = copy.deepcopy((self.github.issue, self.github.comments))
        issue = copy.deepcopy(self.github.issue)
        if held:
            issue["held"] = held
        module.process_ticket(issue, 1, False, forced=True)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])
        self.assertFalse(any(cmd[:3] == ["gh", "issue", "comment"] for cmd in self.commands))
        self.assertFalse(any(row["event"] == "escalation-recovered"
                             for row in lifecycle.read_events(module.EVENTS)))

    def test_pending_escalation_preserves_single_host_human_reassignment(self):
        module = self.pending_escalation(None)
        self.github.issue["assignees"] = [{"login": "human-owner"}]
        self.assert_pending_refused(module)

    def test_pending_escalation_preserves_named_host_human_reassignment(self):
        module = self.pending_escalation("tank")
        self.github.issue["assignees"] = [{"login": "human-owner"}]
        self.assert_pending_refused(module)

    def test_pending_escalation_does_not_mutate_closed_ticket(self):
        module = self.pending_escalation("tank")
        self.github.issue["state"] = "CLOSED"
        self.assert_pending_refused(module)

    def test_pending_escalation_does_not_override_held_ticket(self):
        module = self.pending_escalation("tank")
        self.assert_pending_refused(module, held="blocked by #8")

    def test_pending_escalation_preserves_removed_ready_label(self):
        module = self.pending_escalation("tank")
        self.github.issue["labels"] = [{"name": "host:tank"}, {"name": module.LABEL_HUMAN}]
        self.assert_pending_refused(module)
