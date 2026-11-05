"""#555: assignment ownership ends with dispatch; an open PR is an explicit hold."""
from __future__ import annotations

import copy
import subprocess
import unittest
from unittest import mock

from factory import lifecycle
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
                    self.assertEqual((rows[-1]["ticket"], rows[-1]["login"], rows[-1]["exit_path"]),
                                     (7, "@me", path))
                    self.assertFalse(module.lock_held(module.ticket_lock(7)))
                    self.assertFalse(self.github.comments, "a completed run is not a live host claim")

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
                self.run_exit("done", forced=True, assignees=(login,))
                self.assertIn({"login": login}, self.github.issue["assignees"])
                if login == "@me":
                    self.assertFalse(any(row.get("event") == "assignment-cleared"
                                         for row in lifecycle.read_events(module.EVENTS)))
                self.github.open_prs.clear()


if __name__ == "__main__":
    unittest.main()
