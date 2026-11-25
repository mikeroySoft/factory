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

    def test_requeue_context_cannot_bypass_an_unapproved_open_pr(self):
        module = self.dispatcher("tank")
        self.github.open_prs = [{"number": 70}]
        with mock.patch.object(module, "refresh_requeue_context", return_value="retry work"), \
                mock.patch.object(module, "worker_round") as worker:
            [issue] = module.frontier()
            self.assertIn("#70", issue.get("held", ""))
            module.process_ticket(issue, 1, False, forced=True)
            worker.assert_not_called()
        self.assertEqual(self.github.issue["assignees"], [])

    def test_preexisting_human_assignments_including_own_login_are_never_cleared(self):
        for host, login in ((None, "human"), (None, "@me"), ("tank", "human"), ("tank", "@me")):
            with self.subTest(host=host, login=login):
                self.setUp()
                self.github.issue["assignees"] = [{"login": login}]
                module = self.dispatcher(host)
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

    def test_cleanup_failure_preserves_the_original_crash(self):
        module = self.dispatcher("tank")
        run = module.run

        def github(cmd, *args, **kwargs):
            if "--remove-assignee" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="unassign unavailable")
            return run(cmd, *args, **kwargs)

        with mock.patch.object(module, "run", side_effect=github), \
                mock.patch.object(module, "worker_round", side_effect=RuntimeError("original crash")), \
                mock.patch.object(module, "escalation_packet", return_value=(module.FACTORY / "packet.md", 1)):
            with self.assertRaisesRegex(RuntimeError, "original crash") as error:
                module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertTrue(any("Assignment cleanup also failed" in note for note in error.exception.__notes__))
        self.assertFalse(module.lock_held(module.ticket_lock(7)))
        self.assertTrue(self.github.comments)

    def test_replacement_after_cleanup_issue_read_keeps_its_assignment(self):
        module = self.dispatcher("tank")
        github = module.gh_json
        replacement = None

        def read(args):
            nonlocal replacement
            result = github(args)
            if args[:2] == ["issue", "view"] and args[-1] == "labels,assignees" and replacement is None:
                self.github.comments.clear()
                self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                replacement = self.github.add_claim("ultron", module.datetime.now(module.UTC))
            return result

        with mock.patch.object(module, "gh_json", side_effect=read):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.comments, [replacement])
        self.assertFalse(any(row.get("event") == "assignee-cleared" for row in lifecycle.read_events(module.EVENTS)))

    def test_pr_hold_reads_later_pages_and_matches_the_exact_branch(self):
        module = self.dispatcher(None)
        github = module.gh_json

        def read(args):
            if args[-1].endswith("/pulls?state=open&per_page=100"):
                return [[{"number": 700, "head": {"ref": "agent/70"}}],
                        [{"number": 70, "head": {"ref": "agent/7"}}]]
            return github(args)

        with mock.patch.object(module, "gh_json", side_effect=read):
            self.assertEqual(module.frontier()[0]["held"], "open agent PR #70")

    def test_requeue_close_failure_rechecks_whether_the_pr_is_still_open(self):
        for concurrently_closed in (False, True):
            with self.subTest(concurrently_closed=concurrently_closed):
                self.setUp()
                module = self.dispatcher(None)
                self.github.open_prs = [{"number": 70}]

                def close(cmd, *args, **kwargs):
                    if concurrently_closed:
                        self.github.open_prs.clear()
                    return subprocess.CompletedProcess(cmd, 1, "", "close failed")

                with mock.patch.object(module, "run", side_effect=close):
                    if concurrently_closed:
                        module.close_requeue_pr(7)
                        self.assertNotIn("held", module.frontier()[0])
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            module.close_requeue_pr(7)
                        self.assertIn("#70", module.frontier()[0]["held"])


if __name__ == "__main__":
    unittest.main()
