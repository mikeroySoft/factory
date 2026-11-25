"""Lifecycle and trust boundaries for opt-in host claims."""
import copy
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_single_host_unclaim_preserves_preexisting_assignment(self):
        single = self.dispatcher(None)
        self.github.issue["assignees"] = [{"login": "@me"}]

        single.unclaim(
            7, "--remove-label", "ready-for-agent", "--add-label", "ready-for-human",
            check=True,
        )

        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])

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

    def test_stale_releaser_cannot_disturb_live_replacement(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.github.add_claim("old", now - timedelta(hours=3))
        self.github.issue["assignees"] = []
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        for module in (tank, ultron):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.return_value = now
        read = tank.gh_json
        started, release = threading.Event(), threading.Event()
        pool = self.enterContext(ThreadPoolExecutor(max_workers=1))
        replacement = None

        def blocked_worker(*args, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("replacement was not released")
            return self.launch("ultron")

        def replace_after_read(args):
            nonlocal replacement
            result = read(args)
            if replacement is None and not self.github.comments and \
                    {"name": "host:old"} not in self.github.issue["labels"]:
                replacement = pool.submit(
                    ultron.process_ticket, copy.deepcopy(self.github.issue), 1, False,
                )
                self.assertTrue(started.wait(timeout=2))
            return result

        try:
            with mock.patch.object(ultron, "worker_round", side_effect=blocked_worker), \
                    mock.patch.object(tank, "gh_json", side_effect=replace_after_read):
                tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
            self.assertTrue(started.is_set())
            self.assertEqual([row["host"] for row in ultron.host_claims(7)], ["ultron"])
            self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        finally:
            release.set()
        replacement.result(timeout=5)

        self.assertEqual(self.launched, ["ultron"])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.open_prs, [{"number": 70}])

    def test_expired_claim_with_unassigned_open_pr_remains_held(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.github.add_claim("ultron", now - timedelta(hours=3))
        self.github.issue["assignees"] = []
        self.github.open_prs = [{"number": 70}]
        tank = self.dispatcher("tank")
        clock = self.enterContext(mock.patch.object(tank, "datetime", wraps=datetime))
        clock.now.return_value = now

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_two_stale_releasers_dispatch_live_replacement_once(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        self.github.add_claim("old", now - timedelta(hours=3))
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        for module in (tank, ultron):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.return_value = now
        read = tank.gh_json
        claim_reads = 0
        started, release = threading.Event(), threading.Event()
        pool = self.enterContext(ThreadPoolExecutor(max_workers=1))
        replacement = None

        def blocked_worker(*args, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("replacement was not released")
            return self.launch("ultron")

        def replace_after_stale_recheck(args):
            nonlocal claim_reads, replacement
            result = read(args)
            if "--slurp" in args:
                claim_reads += 1
                if claim_reads == 2:
                    replacement = pool.submit(
                        ultron.process_ticket, copy.deepcopy(self.github.issue), 1, False,
                    )
                    self.assertTrue(started.wait(timeout=2))
            return result

        try:
            with mock.patch.object(ultron, "worker_round", side_effect=blocked_worker), \
                    mock.patch.object(tank, "gh_json", side_effect=replace_after_stale_recheck):
                tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
            self.assertEqual([row["host"] for row in ultron.host_claims(7)], ["ultron"])
        finally:
            release.set()
        replacement.result(timeout=5)

        self.assertEqual(self.launched, ["ultron"])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.open_prs, [{"number": 70}])

    def test_completed_worker_with_open_pr_remains_held(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        ultron = self.dispatcher("ultron", claim_ttl="1s")
        for module in (tank, ultron):
            with self.subTest(host=module.cfg.host_name):
                [issue] = module.frontier()
                self.assertEqual(issue.get("held"), "open agent PR #70")
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
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])

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
                self.assertEqual(self.github.comments, [old] if failing_arg == "--color" else [])
                self.assertNotIn("held", tank.frontier()[0])
        tank.process_ticket(tank.frontier()[0], 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_requeue_marker_does_not_bypass_open_pr_hold(self):
        tank = self.dispatcher("tank")
        old = self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        nonce = old["body"].split("nonce=", 1)[1].split()[0]
        self.github.comments.append({
            "id": 100,
            "body": f"Factory requeue: nonce={nonce}\nRebase PR #70 and resolve shared.py",
            "created_at": datetime.now(UTC).isoformat(),
            "user": {"login": "@me"},
        })
        self.github.open_prs = [{"number": 70}]

        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)

        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

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
        self.assertEqual(issue.get("held"), "open agent PR #70")
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
        self.assertEqual(issue.get("held"), "open agent PR #70")
        before = copy.deepcopy((self.github.issue, self.github.comments))
        tank.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def escalate_with_fake_github(self, module):
        run = module.run

        def post_comment(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "comment"]:
                self.github.json(module.cfg.host_name, [
                    "api", "repos/local/claims/issues/7/comments", "--method", "POST",
                    "-f", f"body={cmd[cmd.index('--body') + 1]}",
                ])
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return run(cmd, *args, **kwargs)

        with mock.patch.object(module, "run", side_effect=post_comment):
            module.escalate(7, "CI failed on PR #70", None, code="ci_failed")

    def test_human_relabel_does_not_bypass_open_pr_hold(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.github.add_claim("tank", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        self.escalate_with_fake_github(ultron)
        self.github.run("human", [
            "gh", "issue", "edit", "7", "--remove-label", "ready-for-human",
            "--add-label", "ready-for-agent",
        ])
        after_ttl = datetime.now(UTC) + timedelta(hours=3)
        for module in (tank, ultron):
            with mock.patch.object(module, "datetime", wraps=datetime) as clock:
                clock.now.return_value = after_ttl
                [issue] = module.frontier()
                self.assertEqual(issue.get("held"), "open agent PR #70")
                module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [])

    def test_preexisting_authenticated_assignment_is_preserved_without_owned_claim(self):
        tank = self.dispatcher("tank")
        self.github.issue["assignees"] = [{"login": "@me"}]
        before = copy.deepcopy((self.github.issue, self.github.comments))

        tank.unclaim(7, "--add-label", "ready-for-agent")

        self.assertEqual((self.github.issue, self.github.comments), before)
        [issue] = tank.frontier()
        self.assertEqual(issue.get("held"), "assigned")
        tank.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [])

    def test_owner_refresh_requeue_shares_reports_after_claim_deletion(self):
        from factory import dispatch

        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.enterContext(mock.patch.object(
            tank, "refresh_requeue_context",
            type(dispatch.refresh_requeue_context)(
                dispatch.refresh_requeue_context.__code__, tank.__dict__),
        ))
        self.github.add_claim("tank", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        tank.record("gate-requeue", ticket=7, pr=70, head="a" * 40, round=1,
                    reports=["test: FAIL first", "test: FAIL again"], unrelated=True)
        tank.unclaim(7, "--remove-label", "ready-for-human",
                     "--add-label", "ready-for-agent", assignee_last=True)
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertFalse(any(row["name"].startswith("host:") for row in self.github.issue["labels"]))
        self.assertFalse(any(row["body"].startswith("Factory claim:") for row in self.github.comments))
        context = []

        def worker(*args, **kwargs):
            context.append(args[4])
            return self.launch("ultron")

        with mock.patch.object(ultron, "worker_round", side_effect=worker):
            ultron.process_ticket(ultron.frontier()[0], 1, False)
        self.assertEqual(self.launched, ["ultron"])
        self.assertIn("PR #70", context[0])
        self.assertIn("test: FAIL first", context[0])
        self.assertIn("test: FAIL again", context[0])
        self.assertIn("unrelated", context[0])
        self.assertEqual(ultron.frontier()[0].get("held"), "open agent PR #70")

    def test_expired_transition_context_targets_latest_claim(self):
        from factory import dispatch

        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.enterContext(mock.patch.object(
            tank, "refresh_requeue_context",
            type(dispatch.refresh_requeue_context)(
                dispatch.refresh_requeue_context.__code__, tank.__dict__),
        ))
        for prior_claim in (False, True):
            with self.subTest(prior_claim=prior_claim):
                self.github.comments.clear()
                self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                if prior_claim:
                    old = self.github.add_claim("thor", datetime.now(UTC) - timedelta(hours=6))
                    nonce = old["body"].split("nonce=", 1)[1].split()[0]
                    self.github.json("thor", [
                        "api", "repos/local/claims/issues/7/comments", "--method", "POST",
                        "-f", f"body=Factory requeue: nonce={nonce}\nObsolete refresh instructions",
                    ])
                self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
                self.github.open_prs = [{"number": 70}]
                tank.record("conflict-requeue", ticket=7, pr=70, head="a" * 40,
                            verb="rebase", files=["latest.py"], round=1)
                tank.unclaim(7, "--add-label", "ready-for-agent")
                expected = "latest.py"
                [issue] = ultron.frontier()
                self.assertNotIn("held", issue)
                context = []

                def worker(*args, context=context, **kwargs):
                    context.append(args[4])
                    return self.launch("ultron")

                with mock.patch.object(ultron, "worker_round", side_effect=worker):
                    ultron.process_ticket(issue, 1, False)
                self.assertIn(expected, context[0])
                self.assertNotIn("Obsolete refresh instructions", context[0])
        self.assertEqual(self.launched, ["ultron", "ultron"])

    def test_overlapping_stale_recovery_cleans_old_claim_before_winner_launch(self):
        from factory import lifecycle

        old = self.github.add_claim("old", datetime.now(UTC) - timedelta(hours=3))
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        issues = [module.frontier()[0] for module in (tank, ultron)]
        self.assertTrue(all("held" not in issue for issue in issues))
        self.github.fresh_barrier = threading.Barrier(2)
        self.github.comments_barrier = threading.Barrier(2)
        posting = threading.Barrier(2)
        tank_posted, ultron_posted, tank_finished = (threading.Event() for _ in range(3))
        tank_json, ultron_json, ultron_run = tank.gh_json, ultron.gh_json, ultron.run
        at_launch = []

        def tank_read(args):
            if "--method" in args and "POST" in args:
                posting.wait(timeout=10)
                result = tank_json(args)
                tank_posted.set()
                self.assertTrue(ultron_posted.wait(timeout=10))
                return result
            return tank_json(args)

        def ultron_read(args):
            if "--method" in args and "POST" in args:
                posting.wait(timeout=10)
                self.assertTrue(tank_posted.wait(timeout=10))
                result = ultron_json(args)
                ultron_posted.set()
                return result
            return ultron_json(args)

        def delay_loser_cleanup(cmd, *args, **kwargs):
            if "DELETE" in cmd:
                self.assertTrue(tank_finished.wait(timeout=10))
            return ultron_run(cmd, *args, **kwargs)

        def winner_pipeline():
            try:
                tank.process_ticket(issues[0], 1, False)
            finally:
                tank_finished.set()

        def worker(*args, **kwargs):
            at_launch.append(copy.deepcopy((self.github.issue, self.github.comments)))
            return self.launch("tank")

        with mock.patch.object(tank, "gh_json", side_effect=tank_read), \
                mock.patch.object(ultron, "gh_json", side_effect=ultron_read), \
                mock.patch.object(ultron, "run", side_effect=delay_loser_cleanup), \
                mock.patch.object(tank, "worker_round", side_effect=worker), \
                ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(winner_pipeline)
            loser = pool.submit(ultron.process_ticket, issues[1], 1, False)
            winner.result(timeout=20)
            loser.result(timeout=20)

        self.assertEqual(self.launched, ["tank"])
        issue, comments = at_launch[0]
        self.assertNotIn(old, comments)
        self.assertNotIn({"name": "host:old"}, issue["labels"])
        self.assertEqual(issue["assignees"], [{"login": "@me"}])
        released = [row for row in lifecycle.read_events(tank.EVENTS)
                    if row.get("event") == "claim-released"]
        self.assertEqual([(row["host"], row["last_heartbeat"]) for row in released],
                         [("old", old["created_at"])])
        self.assertTrue(any(row.get("event") == "claim_lost"
                            for row in lifecycle.read_events(ultron.EVENTS)))
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.comments, [])
        [issue] = ultron.frontier()
        self.assertEqual(issue.get("held"), "open agent PR #70")
        ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_owner_transitions_remove_older_stale_claim_without_releasing_newer_work(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        for transition in ("requeue", "escalation"):
            with self.subTest(transition=transition):
                self.github.comments.clear()
                self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                tank._HOST_CLAIMS.clear()
                old = self.github.add_claim("old", datetime.now(UTC) - timedelta(hours=3))
                self.github.add_claim("tank", datetime.now(UTC))
                self.github.open_prs = [{"number": 70}]
                if transition == "requeue":
                    tank.unclaim(7, "--add-label", "ready-for-agent")
                else:
                    self.escalate_with_fake_github(tank)
                    self.github.run("human", [
                        "gh", "issue", "edit", "7", "--remove-label", "ready-for-human",
                        "--add-label", "ready-for-agent",
                    ])
                self.assertNotIn(old, self.github.comments)
                self.assertEqual(self.github.issue["assignees"], [])
                self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
                [issue] = ultron.frontier()
                if transition == "requeue":
                    self.assertNotIn("held", issue)
                    ultron.process_ticket(issue, 1, False)
                else:
                    self.assertEqual(issue.get("held"), "open agent PR #70")
                    ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["ultron"])


class HostClaimOwnershipTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def test_expired_owner_heartbeat_does_not_revive_after_replacement(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        claim = tank.acquire_host_claim(7, copy.deepcopy(self.github.issue), [])
        self.addCleanup(claim.stop)
        with self.github.lock:
            old = self.github.comments[0]
            old["body"] = (
                f"Factory claim: host=tank nonce={claim.comment['nonce']} "
                f"at={(datetime.now(UTC) - timedelta(hours=3)).isoformat()}"
            )
            replacement = self.github.add_claim("ultron", datetime.now(UTC))
            before = copy.deepcopy(self.github.comments)

        claim.thread.join(timeout=2)

        self.assertEqual(self.github.comments, before)
        self.assertFalse(claim.thread.is_alive(), "displaced owner must stop heartbeating")
        self.assertEqual(self.github.patch_count, 0)
        tank.unclaim(7)
        self.assertIn(replacement, self.github.comments)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])

    def test_invalid_claim_dates_do_not_hide_later_live_claims(self):
        tank = self.dispatcher("tank")
        for field in ("body", "created_at"):
            with self.subTest(field=field):
                self.github.comments.clear()
                malformed = self.github.add_claim("broken", datetime.now(UTC))
                malformed[field] = (
                    "Factory claim: host=broken nonce=broken at=invalid"
                    if field == "body" else "invalid"
                )
                self.github.add_claim("ultron", datetime.now(UTC))
                self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                self.github.issue["assignees"] = []
                before = copy.deepcopy((self.github.issue, self.github.comments))

                [issue] = tank.frontier()
                self.assertEqual(issue.get("held"), "host claim (ultron)")
                tank.process_ticket(issue, 1, False)

                self.assertEqual((self.github.issue, self.github.comments), before)
                self.assertEqual(self.launched, [])

    def test_replacement_retires_all_expired_comments_with_serialized_ids(self):
        import json

        from factory import lifecycle

        tank = self.dispatcher("tank")
        for host in ("old", "ultron", "old"):
            self.github.add_claim(host, datetime.now(UTC) - timedelta(hours=3))
        self.github.next_id = 1000
        read = tank.gh_json

        def serialized_read(args):
            return json.loads(json.dumps(read(args)))

        # Real API reads deserialize numeric IDs into distinct Python objects.
        with mock.patch.object(tank, "gh_json", side_effect=serialized_read):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.open_prs, [{"number": 70}])
        releases = [row["host"] for row in lifecycle.read_events(tank.EVENTS)
                    if row.get("event") == "claim-released"]
        self.assertEqual(releases, ["old", "ultron", "old"])

    def test_expired_replacement_cleanup_preserves_own_equal_numeric_id(self):
        import json

        tank = self.dispatcher("tank")
        self.github.next_id = 1000
        replacement = self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=4))
        self.github.add_claim("old", datetime.now(UTC) - timedelta(hours=3))
        read = tank.gh_json
        with mock.patch.object(tank, "gh_json", side_effect=lambda args: json.loads(json.dumps(read(args)))):
            tank.release_stale_claims(
                7, copy.deepcopy(self.github.issue), tank.host_claims(7),
                replacement=replacement["id"],
            )

        self.assertEqual(self.github.comments, [replacement])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:tank"}])

    def test_ready_label_removed_during_election_prevents_worker_launch(self):
        tank = self.dispatcher("tank")
        read = tank.gh_json

        def escalate_after_post(args):
            result = read(args)
            if "--method" in args and args[args.index("--method") + 1] == "POST":
                self.github.issue["labels"] = [
                    row for row in self.github.issue["labels"] if row["name"] != "ready-for-agent"
                ] + [{"name": "ready-for-human"}]
            return result

        with mock.patch.object(tank, "gh_json", side_effect=escalate_after_post):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(self.launched, [])
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])

    def test_election_loss_names_earliest_live_winner(self):
        from factory import lifecycle

        tank = self.dispatcher("tank")
        read = tank.gh_json

        def contenders_before_post(args):
            if "--method" in args and args[args.index("--method") + 1] == "POST":
                self.github.add_claim("ultron", datetime.now(UTC) - timedelta(seconds=2))
                self.github.add_claim("thor", datetime.now(UTC) - timedelta(seconds=1))
            return read(args)

        with mock.patch.object(tank, "gh_json", side_effect=contenders_before_post):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        losses = [row for row in lifecycle.read_events(tank.EVENTS)
                  if row.get("event") == "claim_lost"]
        self.assertEqual([row.get("winner") for row in losses], ["ultron"])
        self.assertEqual(self.launched, [])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}, {"name": "host:thor"}])
        self.assertEqual(len(self.github.comments), 2)

    def test_failed_post_preserves_label_of_another_same_host_claim(self):
        tank = self.dispatcher("tank")
        read = tank.gh_json

        def concurrent_claim_before_failure(args):
            if "--method" in args and args[args.index("--method") + 1] == "POST":
                self.github.add_claim("tank", datetime.now(UTC))
                raise subprocess.CalledProcessError(1, args, stderr="comment unavailable")
            return read(args)

        with mock.patch.object(tank, "gh_json", side_effect=concurrent_claim_before_failure), \
                self.assertRaises(subprocess.CalledProcessError):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(self.launched, [])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:tank"}])
        self.assertEqual(len(self.github.comments), 1)

    def test_non_requeue_transition_does_not_publish_requeue_instructions(self):
        tank = self.dispatcher("tank")
        claim = self.github.add_claim("ultron", datetime.now(UTC))
        for args in (("--remove-label", "ready-for-agent"),
                     ("--remove-label", "ready-for-human", "--add-label", "ready-for-human")):
            with self.subTest(args=args):
                tank.unclaim(7, *args)
                self.assertEqual(self.github.comments, [claim])
                self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])

    def test_failed_post_removes_only_own_label_while_foreign_claim_survives(self):
        tank = self.dispatcher("tank")
        read = tank.gh_json

        def foreign_claim_before_failure(args):
            if "--method" in args and args[args.index("--method") + 1] == "POST":
                self.github.add_claim("ultron", datetime.now(UTC))
                raise subprocess.CalledProcessError(1, args, stderr="comment unavailable")
            return read(args)

        with mock.patch.object(tank, "gh_json", side_effect=foreign_claim_before_failure), \
                self.assertRaises(subprocess.CalledProcessError):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(self.launched, [])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}])
        self.assertEqual(len(self.github.comments), 1)


# Avoid collecting the imported harness class as a second copy of its suite.
del Harness
