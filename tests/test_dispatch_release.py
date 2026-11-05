"""#555: assignment ownership ends with dispatch; an open PR is an explicit hold."""
from __future__ import annotations

import copy
import subprocess
from datetime import UTC, datetime, timedelta
from types import FunctionType
import unittest
from unittest import mock

from factory import dispatch, lifecycle
from tests import test_host_claims


class DispatchReleaseTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    launch = test_host_claims.HostClaimRaceTest.launch

    def dispatcher(self, host):
        root = self.root
        self.root = root / str(len(list(root.iterdir())))
        self.root.mkdir()
        try:
            return test_host_claims.HostClaimRaceTest.dispatcher(self, host)
        finally:
            self.root = root

    def run_exit(self, path, *, host=None, forced=False, assignees=()):
        module = self.dispatcher(host)
        module.MAX_ATTEMPTS = 1
        self.github.issue["assignees"] = [{"login": login} for login in assignees]
        run = module.run

        def external(cmd, *args, **kwargs):
            module.raise_if_interrupted()
            if cmd[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(cmd, 0, "https://example.invalid/comment/1", "")
            result = run(cmd, *args, **kwargs)
            if path == "claim_failed" and "--add-assignee" in cmd:
                # GitHub accepted the edit, but its response was lost.
                raise subprocess.CalledProcessError(1, cmd, stderr="response lost")
            return result

        def worker(*args, **kwargs):
            # A human can take a hold while the worker runs; cleanup must preserve it.
            self.github.issue["assignees"].append({"login": "alice"})
            if path == "crash":
                raise RuntimeError("worker crashed")
            if path == "interrupt":
                module._CANCEL.set()
                raise KeyboardInterrupt
            if path == "worker_unavailable":
                raise FileNotFoundError("worker vanished")
            return path not in {"gate_failed", "budget"}, "gate failed", None, "a" * 40

        def approve(*args):
            self.github.open_prs = [{"number": 70, "headRefName": "agent/7",
                                     "headRefOid": "a" * 40,
                                     "labels": [{"name": "factory-approved"}]}]
            return True

        with mock.patch.object(module, "run", side_effect=external), \
                mock.patch.object(module, "worker_round", side_effect=worker), \
                mock.patch.object(module, "approve_pr", side_effect=approve), \
                mock.patch.object(module, "escalation_packet", return_value=(self.root / "packet.md", 1)), \
                mock.patch.object(module, "pr_gate_and_review", return_value=(
                    "REJECT" if path == "escalation" else "APPROVE", "", False)):
            if path in {"crash", "interrupt"}:
                with self.assertRaises(RuntimeError if path == "crash" else KeyboardInterrupt):
                    module.process_ticket(copy.deepcopy(self.github.issue), 0 if path == "budget" else 1,
                                          False, forced=forced)
            else:
                module.process_ticket(copy.deepcopy(self.github.issue), 0 if path == "budget" else 1,
                                      False, forced=forced)
        return module

    def test_done_releases_assignment_and_following_pass_never_pushes(self):
        module = self.run_exit("done")
        self.assertEqual(self.github.issue["assignees"], [{"login": "alice"}])
        self.github.issue["assignees"] = []
        [issue] = module.frontier()
        self.assertIn("#70", issue.get("held", ""))
        self.assertIn("approved", issue["held"])
        with mock.patch.object(module, "worker_round") as worker, \
                mock.patch.object(module, "push_agent") as push:
            module.ticket_pass(1, False)
            # Also protect stale frontier reads and hand dispatch of an approved PR.
            module.process_ticket({**issue, "held": None}, 1, False, forced=True)
        worker.assert_not_called()
        push.assert_not_called()

    def test_each_exit_releases_only_dispatch_assignment_and_journals(self):
        for host in (None, "tank"):
            for path in ("done", "escalation", "gate_failed", "budget", "crash",
                         "claim_failed", "worker_unavailable", "interrupt"):
                with self.subTest(host=host, path=path):
                    self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                    self.github.comments.clear()
                    self.github.open_prs.clear()
                    module = self.run_exit(path, host=host, forced=True)
                    expected = [] if path == "claim_failed" else [{"login": "alice"}]
                    self.assertEqual(self.github.issue["assignees"], expected)
                    rows = [row for row in lifecycle.read_events(module.EVENTS)
                            if row.get("event") == "assignment-cleared"]
                    self.assertEqual(len(rows), 1, rows)
                    exit_path = {"escalation": "review_revise", "crash": "dispatcher_error"}.get(path, path)
                    self.assertEqual((rows[0]["ticket"], rows[0]["login"], rows[0]["exit_path"]),
                                     (7, "@me", exit_path))
                    self.assertFalse(module.lock_held(module.ticket_lock(7)))
                    self.assertFalse(module.host_claims(7), "a completed run is not a live host claim")

    def test_closed_agent_pr_becomes_eligible_again(self):
        module = self.dispatcher(None)
        self.github.open_prs = [{"number": 70, "headRefName": "agent/7", "labels": []}]
        self.assertIn("#70", module.frontier()[0].get("held", ""))
        self.github.open_prs.clear()  # GitHub --state open excludes closed, unmerged PRs.
        self.assertNotIn("held", module.frontier()[0])
        module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, [None])

    def test_human_hold_is_respected_and_never_cleared_even_on_hand_run(self):
        for login in ("alice", "@me"):
            with self.subTest(login=login):
                module = self.dispatcher(None)
                self.github.issue["assignees"] = [{"login": login}]
                self.assertEqual(module.frontier()[0].get("held"), "assigned")
                module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
                self.assertEqual(self.launched, [])
                module = self.run_exit("done", forced=True, assignees=(login,))
                self.assertIn({"login": login}, self.github.issue["assignees"])
                if login == "@me":
                    self.assertFalse(any(row.get("event") == "assignment-cleared"
                                         for row in lifecycle.read_events(module.EVENTS)))
                self.github.open_prs.clear()

    def test_preexisting_own_login_is_preserved_on_failure(self):
        for path in ("escalation", "gate_failed", "budget", "crash",
                     "worker_unavailable", "interrupt"):
            for host in (None, "tank"):
                with self.subTest(path=path, host=host):
                    self.github.comments.clear()
                    self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                    module = self.run_exit(path, host=host, forced=True, assignees=("@me",))
                    self.assertIn({"login": "@me"}, self.github.issue["assignees"])
                    self.assertFalse(any(row.get("event") == "assignment-cleared"
                                         for row in lifecycle.read_events(module.EVENTS)))

    def test_success_consumes_shared_rework_without_retaining_a_claim(self):
        module = self.dispatcher("tank")
        self.github.comments.append({
            "id": 100, "user": {"login": "@me"},
            "body": "Factory requeue: nonce=old\nFix the previous review findings",
        })
        module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(module.host_claim_state(7), ([], ""))
        self.github.open_prs = [{"number": 70, "labels": []}]
        self.assertIn("#70", module.frontier()[0].get("held", ""))

    def test_refresh_rework_after_success_reaches_another_host_without_a_claim(self):
        tank = self.dispatcher("tank")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(tank.host_claims(7), [])
        self.github.open_prs = [{"number": 70, "labels": [{"name": "factory-approved"}]}]
        context = FunctionType(dispatch.refresh_requeue_context.__code__, tank.__dict__)
        with mock.patch.object(tank, "refresh_requeue_context", context):
            tank._record_rework(7, "a" * 40, "Resolve shared.py conflict", deferred=True)
        ultron = self.dispatcher("ultron")
        received = []

        def worker(*args, **kwargs):
            received.append(args[4])
            return self.launch("ultron")

        with mock.patch.object(ultron, "worker_round", side_effect=worker):
            ultron.process_ticket(ultron.frontier()[0], 1, False)
        self.assertEqual(self.launched, ["tank", "ultron"])
        self.assertEqual(len(received), 1)
        self.assertIn("Resolve shared.py conflict", received[0])
        self.assertEqual(ultron.host_claim_state(7), ([], ""))

    def test_claim_free_rework_survives_a_crashed_admission_on_another_host(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        context = FunctionType(dispatch.refresh_requeue_context.__code__, tank.__dict__)
        with mock.patch.object(tank, "refresh_requeue_context", context):
            tank._record_rework(7, "a" * 40, "Resolve shared.py conflict", deferred=True)
        # A host reads the shared findings, then dies after claiming, before any attempt.
        self.github.add_claim("crashed", datetime.now(UTC) - timedelta(hours=3))
        received = []

        def worker(*args, **kwargs):
            received.append(args[4])
            return self.launch("ultron")

        with mock.patch.object(ultron, "worker_round", side_effect=worker):
            ultron.process_ticket(ultron.frontier()[0], 1, False)
        self.assertEqual(self.launched, ["ultron"])
        self.assertEqual(len(received), 1)
        self.assertIn("Resolve shared.py conflict", received[0])
        self.assertEqual(ultron.host_claim_state(7), ([], ""))

    def test_requeue_context_cannot_bypass_an_open_unapproved_pr(self):
        for host in (None, "tank"):
            with self.subTest(host=host):
                module = self.dispatcher(host)
                self.github.open_prs = [{"number": 70, "labels": []}]
                self.github.comments.append({
                    "id": 100, "user": {"login": "@me"},
                    "body": "Factory requeue: nonce=old\nPrevious review findings",
                })
                with mock.patch.object(module, "refresh_requeue_context",
                                       return_value="Previous local rework"), \
                        mock.patch.object(module, "worker_round") as worker, \
                        mock.patch.object(module, "push_agent") as push:
                    [issue] = module.frontier()
                    self.assertEqual(issue.get("held"),
                                     "open agent PR #70 (awaiting merge or closure)")
                    module.ticket_pass(1, False)
                    module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
                worker.assert_not_called()
                push.assert_not_called()
                self.assertEqual(self.github.issue["assignees"], [])

    def test_approved_pr_is_a_hold_without_a_live_claim_or_lock(self):
        module = self.dispatcher("tank")
        self.github.open_prs = [{"number": 70, "labels": [{"name": "factory-approved"}]}]
        self.github.comments.append({
            "id": 100, "user": {"login": "@me"},
            "body": "Factory requeue: nonce=old\nEarlier unapproved rework",
        })
        self.assertFalse(module.lock_held(module.ticket_lock(7)))
        self.assertEqual(module.host_claims(7), [])
        [issue] = module.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70 (approved, awaiting merge)")
        with mock.patch.object(module, "worker_round") as worker:
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
            module.process_ticket(copy.deepcopy(self.github.issue), 1, True, forced=True)
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertFalse(any(row.get("event") == "claimed"
                             for row in lifecycle.read_events(module.EVENTS)))
        worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
