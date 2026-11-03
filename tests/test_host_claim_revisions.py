"""Lifecycle and trust boundaries for opt-in host claims."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests.test_host_claims import HostClaimRaceTest as Harness


class HostClaimRevisionTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def test_foreign_and_expired_claims_preserve_lifecycle_transitions(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        now = datetime(2026, 11, 2, tzinfo=UTC)
        clock = self.enterContext(mock.patch.object(tank, "datetime", wraps=datetime))
        clock.now.return_value = now
        for host, age, assignee_last in (
            ("ultron", 0, False), ("ultron", 0, True),
            ("tank", 5, False), ("tank", 5, True),
        ):
            with self.subTest(host=host, assignee_last=assignee_last):
                self.github.comments.clear()
                self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                claim = self.github.add_claim(host, now - timedelta(seconds=age))

                tank.unclaim(7, "--remove-label", "ready-for-agent", "--add-label", "ready-for-human",
                             assignee_last=assignee_last)

                self.assertIn({"name": "ready-for-human"}, self.github.issue["labels"])
                self.assertNotIn({"name": "ready-for-agent"}, self.github.issue["labels"])
                if host == "ultron":
                    self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
                    self.assertEqual(self.github.comments, [claim])
                    self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
                else:
                    self.assertEqual(self.github.issue["assignees"], [])
                    self.assertEqual(self.github.comments, [])
                    self.assertNotIn({"name": "host:tank"}, self.github.issue["labels"])

                tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent",
                             assignee_last=True)
                self.assertIn({"name": "ready-for-agent"}, self.github.issue["labels"])
                self.assertNotIn({"name": "ready-for-human"}, self.github.issue["labels"])

    def test_foreign_unclaim_without_transitions_does_not_edit_issue(self):
        tank = self.dispatcher("tank")
        self.github.add_claim("ultron", datetime.now(UTC))
        before = copy.deepcopy((self.github.issue, self.github.comments))
        for assignee_last in (False, True):
            with self.subTest(assignee_last=assignee_last):
                self.github.calls.clear()
                tank.unclaim(7, assignee_last=assignee_last)
                self.assertEqual((self.github.issue, self.github.comments), before)
                self.assertFalse(any(args[:3] == ["gh", "issue", "edit"]
                                     for _, args in self.github.calls), self.github.calls)

    def test_unclaim_with_old_local_nonce_preserves_elected_claim(self):
        tank = self.dispatcher("tank")
        now = datetime.now(UTC)
        local = tank.HostClaim(7, {"nonce": "previous-process"})
        tank._HOST_CLAIMS[7] = local
        claim = self.github.add_claim("tank", now)
        for assignee_last in (False, True):
            with self.subTest(assignee_last=assignee_last):
                self.github.issue["labels"] = [{"name": "host:tank"}, {"name": "ready-for-agent"}]
                tank.unclaim(7, "--remove-label", "ready-for-agent", "--add-label", "ready-for-human",
                             assignee_last=assignee_last)
                self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
                self.assertEqual(self.github.comments, [claim])
                self.assertEqual(self.github.issue["labels"],
                                 [{"name": "host:tank"}, {"name": "ready-for-human"}])

    def test_single_host_unclaim_keeps_original_argument_order(self):
        single = self.dispatcher(None)
        transition = ["--remove-label", "ready-for-agent", "--add-label", "ready-for-human"]
        for assignee_last in (False, True):
            with self.subTest(assignee_last=assignee_last):
                self.github.calls.clear()
                single.unclaim(7, *transition, check=True, assignee_last=assignee_last)
                assignment = ["--remove-assignee", "@me"]
                args = transition + assignment if assignee_last else assignment + transition
                self.assertEqual(self.github.calls, [
                    (None, ["gh", "issue", "edit", "7", "--repo", "local/claims", *args]),
                ])

    def test_expired_local_owner_with_matching_nonce_can_unclaim(self):
        tank = self.dispatcher("tank")
        for assignee_last in (False, True):
            with self.subTest(assignee_last=assignee_last):
                claim = self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
                nonce = claim["body"].split("nonce=", 1)[1].split()[0]
                tank._HOST_CLAIMS[7] = tank.HostClaim(7, {**claim, "nonce": nonce})
                tank.unclaim(7, assignee_last=assignee_last)
                self.assertEqual(self.github.issue["assignees"], [])
                self.assertEqual(self.github.comments, [])
                self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])

    def test_foreign_label_without_comment_stays_held_during_stale_recovery(self):
        self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        self.github.issue["labels"].append({"name": "host:thor"})
        tank = self.dispatcher("tank")
        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "host label (thor)")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_failed_claim_delete_preserves_ownership_and_surfaces_error(self):
        self.github.add_claim("tank", datetime.now(UTC))
        tank = self.dispatcher("tank")
        before = copy.deepcopy((self.github.issue["labels"], self.github.comments))
        run = tank.run

        def fail_delete(cmd, *args, **kwargs):
            if "DELETE" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "permission denied")
            return run(cmd, *args, **kwargs)

        with mock.patch.object(tank, "run", side_effect=fail_delete), \
                self.assertRaises(subprocess.CalledProcessError):
            tank.unclaim(7)
        self.assertEqual((self.github.issue["labels"], self.github.comments), before)

    def test_stale_releaser_cannot_unassign_replacement_after_empty_claim_read(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.github.add_claim("old", now - timedelta(hours=3))
        # An abandoned claim can already be unassigned. The replacement must
        # remain assigned even if another stale releaser is still in flight.
        self.github.issue["assignees"] = []
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        for module in (tank, ultron):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.return_value = now
        read = tank.gh_json
        replaced = False

        def replace_after_read(args):
            nonlocal replaced
            result = read(args)
            if not replaced and not self.github.comments and \
                    {"name": "host:old"} not in self.github.issue["labels"]:
                # The response was captured before the replacement claimed.
                # On the broken code this is the final empty live-claim read,
                # immediately before its shared-assignee removal.
                replaced = True
                ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
            return result

        with mock.patch.object(tank, "gh_json", side_effect=replace_after_read):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertTrue(replaced)
        self.assertEqual(self.launched, ["ultron"])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}])
        self.assertEqual(len(self.github.comments), 1)
        self.assertIn("host=ultron ", self.github.comments[0]["body"])
        self.assertFalse(any("--remove-assignee" in args
                             for host, args in self.github.calls if host == "tank"))

    def test_expired_claim_with_unassigned_open_pr_remains_held(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.github.add_claim("ultron", now - timedelta(hours=3))
        self.github.issue["assignees"] = []
        self.github.open_prs = [{"number": 70}]
        tank = self.dispatcher("tank")
        clock = self.enterContext(mock.patch.object(tank, "datetime", wraps=datetime))
        clock.now.return_value = now

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open PR")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_two_stale_releasers_keep_replacement_assigned_and_dispatch_once(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.github.add_claim("old", now - timedelta(hours=3))
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        for module in (tank, ultron):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.return_value = now
        read = tank.gh_json
        claim_reads = 0

        def replace_after_stale_recheck(args):
            nonlocal claim_reads
            result = read(args)
            if "--slurp" in args:
                claim_reads += 1
                if claim_reads == 2:
                    # Both releasers saw the same stale claim; ultron completes
                    # its reclaim while tank still holds that old snapshot.
                    ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
            return result

        with mock.patch.object(tank, "gh_json", side_effect=replace_after_stale_recheck):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(self.launched, ["ultron"])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}])
        self.assertEqual(len(self.github.comments), 1)
        self.assertIn("host=ultron ", self.github.comments[0]["body"])

    def test_completed_worker_with_open_pr_remains_held_after_ttl(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.github.open_prs = [{"number": 70}]
        ultron = self.dispatcher("ultron", claim_ttl="1s")
        after_ttl = datetime.now(UTC) + timedelta(seconds=5)
        for module in (tank, ultron):
            with self.subTest(host=module.cfg.host_name), \
                    mock.patch.object(module, "datetime", wraps=datetime) as clock:
                clock.now.return_value = after_ttl
                [issue] = module.frontier()
                self.assertIn("held", issue)
                before = copy.deepcopy((self.github.issue, self.github.comments))
                module.process_ticket(issue, 1, False)
                self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, ["tank"])

    def test_human_assignee_remains_frontier_hold_with_stale_claim(self):
        self.github.add_claim("ultron", datetime.now(UTC) - timedelta(seconds=5))
        self.github.issue["assignees"].append({"login": "human"})
        tank = self.dispatcher("tank", claim_ttl="1s")
        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "assigned")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_heartbeat_waits_before_refreshing_and_stays_within_ttl(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        claim = tank.acquire_host_claim(7, copy.deepcopy(self.github.issue), [])
        self.addCleanup(claim.stop)
        self.assertFalse(self.github.comment_patched.wait(timeout=0.05))
        self.assertTrue(self.github.comment_patched.wait(timeout=0.7))

    def test_untrusted_claim_comment_cannot_hold_or_be_deleted(self):
        forged = self.github.add_claim("intruder", datetime(2099, 1, 1, tzinfo=UTC))
        forged["user"] = {"login": "outside-commenter"}
        self.github.issue["labels"] = [{"name": "ready-for-agent"}]
        self.github.issue["assignees"] = []
        tank = self.dispatcher("tank")

        [issue] = tank.frontier()
        self.assertNotIn("held", issue)
        tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertIn(forged, self.github.comments)
        tank.unclaim(7)
        self.assertEqual(self.github.comments, [forged])

    def test_stale_assignment_survives_admission_refusal_with_recoverable_claim(self):
        from factory.binding import BindingError

        tank = self.dispatcher("tank")
        old = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        for refusal in ("state", "worker", "plan"):
            with self.subTest(refusal=refusal):
                issue = copy.deepcopy(self.github.issue)
                with mock.patch.object(tank.shutil, "which", return_value=None if refusal == "worker" else "worker"), \
                        mock.patch.object(tank, "admit_plan",
                                          side_effect=BindingError("baseline changed") if refusal == "plan" else None):
                    if refusal == "state":
                        self.github.issue["state"] = "CLOSED"
                    tank.process_ticket(issue, 1, False)
                self.github.issue["state"] = "OPEN"
                self.assertEqual(self.launched, [])
                self.assertEqual(self.github.comments, [old])
                self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
                self.assertNotIn("held", tank.frontier()[0])
        tank.process_ticket(tank.frontier()[0], 1, False)
        self.assertEqual(self.launched, ["tank"])
        self.assertEqual([row["name"] for row in self.github.issue["labels"]],
                         ["ready-for-agent", "host:tank"])

    def test_stale_assignment_remains_recoverable_when_claim_creation_or_assignment_fails(self):
        tank = self.dispatcher("tank")
        old = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        run = tank.run
        for failing_arg in ("--color", "--add-assignee"):
            with self.subTest(failing_arg=failing_arg):
                def fail_claim(cmd, *args, failing_arg=failing_arg, **kwargs):
                    if failing_arg in cmd:
                        raise subprocess.CalledProcessError(1, cmd, stderr="permission denied")
                    return run(cmd, *args, **kwargs)

                with mock.patch.object(tank, "run", side_effect=fail_claim):
                    if failing_arg == "--color":
                        with self.assertRaises(subprocess.CalledProcessError):
                            tank.process_ticket(tank.frontier()[0], 1, False)
                    else:
                        tank.process_ticket(tank.frontier()[0], 1, False)
                self.assertEqual(self.launched, [])
                self.assertEqual(self.github.comments, [old])
                self.assertNotIn("held", tank.frontier()[0])
        tank.process_ticket(tank.frontier()[0], 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_foreign_host_requeue_resumes_open_pr_after_ttl(self):
        from factory import dispatch, lifecycle

        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        # Use the real context reader with each host's separate journal.
        for module in (tank, ultron):
            self.enterContext(mock.patch.object(
                module, "refresh_requeue_context",
                type(dispatch.refresh_requeue_context)(
                    dispatch.refresh_requeue_context.__code__, module.__dict__),
            ))
        old = self.github.add_claim("tank", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        ultron.record("conflict-requeue", ticket=7, pr=70, head="a" * 40,
                      verb="rebase", files=["shared.py"], round=1)
        # This is the transition shared by all refresh requeue paths.
        ultron.unclaim(7, "--remove-label", "ready-for-human",
                       "--add-label", "ready-for-agent", assignee_last=True)
        self.assertIn(old, self.github.comments)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertIn("held", tank.frontier()[0])

        clock = self.enterContext(mock.patch.object(tank, "datetime", wraps=datetime))
        clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
        [issue] = tank.frontier()
        self.assertNotIn("held", issue)
        context = []
        def worker(*args, **kwargs):
            context.append(args[4])
            return self.launch("tank")

        with mock.patch.object(tank, "worker_round", side_effect=worker):
            tank.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank"])
        self.assertIn("shared.py", context[0])
        self.assertIn("PR #70", context[0])
        self.assertTrue(any(row.get("event") == "claim-released"
                            for row in lifecycle.read_events(tank.EVENTS)))
        # The previous requeue must not authorize another run after this worker finishes.
        clock.now.return_value += timedelta(hours=3)
        self.assertEqual(tank.frontier()[0].get("held"), "open PR")

    def test_untrusted_requeue_cannot_release_expired_open_pr_hold(self):
        old = self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        nonce = old["body"].split("nonce=", 1)[1].split()[0]
        self.github.comments.append({
            "id": 100, "body": f"Factory requeue: nonce={nonce}\nContinue work",
            "created_at": datetime.now(UTC).isoformat(),
            "user": {"login": "outside-commenter"},
        })
        self.github.open_prs = [{"number": 70}]
        tank = self.dispatcher("tank")

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open PR")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_old_requeue_cannot_release_newer_expired_claim_when_cleanup_was_deferred(self):
        old = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=6))
        nonce = old["body"].split("nonce=", 1)[1].split()[0]
        self.github.comments.append({
            "id": 100, "body": f"Factory requeue: nonce={nonce}\nOld refresh",
            "created_at": datetime.now(UTC).isoformat(), "user": {"login": "@me"},
        })
        self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        self.github.open_prs = [{"number": 70}]
        tank = self.dispatcher("tank")

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open PR")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])


# Avoid collecting the imported harness class as a second copy of its suite.
del Harness
