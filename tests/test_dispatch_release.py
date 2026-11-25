"""Assignment lifetime and PR admission regressions for #555."""
from __future__ import annotations

import copy
import subprocess
import unittest
from unittest import mock

from factory import lifecycle, schedule
from tests import test_host_claims as host_claims


class DispatchRelease(unittest.TestCase):
    setUp = host_claims.HostClaimRaceTest.setUp
    dispatcher = host_claims.HostClaimRaceTest.dispatcher
    launch = host_claims.HostClaimRaceTest.launch

    def publish(self):
        self.github.open_prs = [{"number": 70}]
        return True

    def test_approved_open_pr_without_assignee_is_a_hold_at_both_boundaries(self):
        for host in (None, "tank"):
            with self.subTest(host=host):
                self.setUp()
                self.github.open_prs = [{"number": 70}]
                module = self.dispatcher(host)
                module.record("approved", ticket=7, head="a" * 40)
                with mock.patch.object(module, "push_agent") as push, \
                        mock.patch.object(module, "worker_round") as worker:
                    [issue] = module.frontier()
                    self.assertIn("#70", issue.get("held", ""))
                    self.assertEqual(schedule.plan([issue], 1, repo=module.REPO,
                                                   streak=0, experiments=0)["selected"], [])
                    for forced in (False, True):
                        module.process_ticket(issue, 1, False, forced=forced)
                    worker.assert_not_called()
                    push.assert_not_called()
                self.assertEqual(self.github.issue["assignees"], [])
                self.assertEqual(self.github.comments, [])

    def test_closed_unmerged_pr_becomes_eligible(self):
        module = self.dispatcher(None)
        self.github.open_prs = [{"number": 70}]
        self.assertIn("#70", module.frontier()[0].get("held", ""))
        self.github.open_prs.clear()  # --state open no longer returns the closed PR
        [issue] = module.frontier()
        self.assertNotIn("held", issue)
        module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [None])

    def test_every_exit_clears_owned_assignment_and_journals_the_path(self):
        cases = ("done", "review_revise", "gate_failed", "budget", "dispatcher_error",
                 "claim_failed", "worker_unavailable", "interrupted")
        for host in (None, "tank"):
            for path in cases:
                with self.subTest(host=host, exit_path=path):
                    self.setUp()
                    module = self.dispatcher(host)
                    run = module.run

                    def github(cmd, *args, **kwargs):
                        if cmd[:3] == ["gh", "issue", "comment"]:
                            return subprocess.CompletedProcess(cmd, 0, "https://example.invalid/comment/1", "")
                        result = run(cmd, *args, **kwargs)
                        if path == "claim_failed" and "--add-assignee" in cmd:
                            # GitHub applied the edit but the response was lost.
                            raise subprocess.CalledProcessError(1, cmd, stderr="lost response")
                        return result

                    def worker(*args, **kwargs):
                        if {"login": "human"} not in self.github.issue["assignees"]:
                            self.github.issue["assignees"].append({"login": "human"})
                        if path == "dispatcher_error":
                            raise RuntimeError("worker crash")
                        if path == "worker_unavailable":
                            raise FileNotFoundError("missing worker")
                        if path == "interrupted":
                            raise KeyboardInterrupt()
                        return path not in {"gate_failed", "budget"}, "gate result", None, "a" * 40

                    expected_error = (RuntimeError if path == "dispatcher_error" else
                                      KeyboardInterrupt if path == "interrupted" else None)
                    with mock.patch.object(module, "run", side_effect=github), \
                            mock.patch.object(module, "worker_round", side_effect=worker), \
                            mock.patch.object(module, "escalation_packet", return_value=(module.FACTORY / "packet.md", 1)), \
                            mock.patch.object(module, "pr_gate_and_review", return_value=(
                                "REJECT" if path == "review_revise" else "APPROVE", "", False,
                            )):
                        if expected_error:
                            with self.assertRaises(expected_error):
                                module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
                        else:
                            module.process_ticket(copy.deepcopy(self.github.issue), -1 if path == "budget" else 1,
                                                  False, forced=True)
                    self.assertEqual(self.github.issue["assignees"],
                                     [] if path == "claim_failed" else [{"login": "human"}])
                    self.assertFalse(module.lock_held(module.ticket_lock(7)))
                    self.assertFalse(any(c["body"].startswith("Factory claim:") for c in self.github.comments))
                    self.assertFalse(any(row["name"].startswith("host:") for row in self.github.issue["labels"]))
                    cleared = [row for row in lifecycle.read_events(module.EVENTS)
                               if row.get("event") == "assignee-cleared"]
                    self.assertEqual([(row["ticket"], row["login"], row["exit_path"]) for row in cleared],
                                     [(7, "@me", path)])

    def test_preexisting_human_assignments_including_own_login_are_never_cleared(self):
        for login in ("human", "@me"):
            with self.subTest(login=login):
                self.setUp()
                self.github.issue["assignees"] = [{"login": login}]
                module = self.dispatcher(None)
                [issue] = module.frontier()
                self.assertEqual(issue.get("held"), "assigned")
                with mock.patch.object(module, "worker_round") as worker:
                    module.process_ticket(issue, 1, False)
                    worker.assert_not_called()
                # An explicit hand run may work despite a human hold, not erase it.
                module.process_ticket(issue, 1, False, forced=True)
                module.unclaim(7)
                self.assertEqual(self.github.issue["assignees"], [{"login": login}])
                self.assertFalse(any(row.get("event") == "assignee-cleared" and row.get("login") == login
                                     for row in lifecycle.read_events(module.EVENTS)))

    def test_cleanup_failure_surfaces_but_releases_local_lock(self):
        module = self.dispatcher("tank")
        run = module.run

        def github(cmd, *args, **kwargs):
            if "--remove-assignee" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="unassign unavailable")
            return run(cmd, *args, **kwargs)

        with mock.patch.object(module, "run", side_effect=github):
            with self.assertRaises(subprocess.CalledProcessError):
                module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertFalse(module.lock_held(module.ticket_lock(7)))
        self.assertTrue(self.github.comments)  # durable recovery evidence survives failed cleanup
        self.assertFalse(any(row.get("event") == "assignee-cleared" for row in lifecycle.read_events(module.EVENTS)))


if __name__ == "__main__":
    unittest.main()
