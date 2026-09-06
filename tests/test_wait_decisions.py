"""Known waits describe existing decisions without changing locks or queries."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from factory import dispatch, lifecycle
from factory.config import Config


class WaitDecisions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(root=Path(temp.name), repo="local/waits", max_active=1)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
            EVENTS=self.events, MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.issue = {"number": 7, "title": "local ticket", "labels": []}

    def rows(self):
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == "lifecycle"]

    def hold(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.enterContext(path.open("w"))
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle

    def test_capacity_uses_existing_live_ticket_count_without_frontier_query(self):
        self.hold(dispatch.ticket_lock(7))
        with patch.object(dispatch.config, "load", return_value=self.cfg), \
                patch.object(dispatch, "sync_pass") as sync, \
                patch.object(dispatch, "merge_pass_locked") as merge, \
                patch.object(dispatch, "frontier") as frontier, \
                patch.object(dispatch, "process_ticket") as process:
            self.assertEqual(dispatch.main([]), 0)
        sync.assert_called_once_with(False)
        merge.assert_called_once_with(False)
        frontier.assert_not_called()
        process.assert_not_called()
        waits = [row["wait"] for row in self.rows() if row["kind"] == "wait"]
        self.assertEqual([(wait["reason"], wait["mode"]) for wait in waits],
                         [("capacity_reached", "admission")])
        self.assertEqual(waits[0]["details"], {"active": 1, "max_active": 1})
        self.assertTrue(dispatch.lock_held(dispatch.ticket_lock(7)))

    def test_merge_lock_miss_skips_work_and_next_call_retries_nonblocking(self):
        lock = self.cfg.factory / "locks" / "merge.lock"
        holder = self.hold(lock)
        with patch.object(dispatch, "sync_pass") as sync, \
                patch.object(dispatch, "merge_pass_locked") as merge:
            dispatch.land_pass(False)
            sync.assert_not_called()
            merge.assert_not_called()
            first = self.rows()
            wait = next(row for row in first if row["kind"] == "wait")
            request = next(row for row in first if row["kind"] == "resource_requested")
            self.assertEqual((wait["wait"]["reason"], wait["wait"]["mode"]),
                             ("merge_lock_contended", "retry_next_pass"))
            self.assertEqual(wait["wait"]["resource"], request["resource"])
            self.assertFalse(request["blocking"])
            self.assertFalse(any(row["kind"] == "lock_acquired" for row in first))
            self.assertTrue(dispatch.lock_held(lock))
            fcntl.flock(holder, fcntl.LOCK_UN)
            dispatch.land_pass(False)
        sync.assert_called_once_with(False)
        merge.assert_called_once_with(False)
        self.assertFalse(dispatch.lock_held(lock))
        acquired = next(row for row in self.rows() if row["kind"] == "lock_acquired")
        self.assertEqual(acquired["resource"]["id"], request["resource"]["id"])
        self.assertNotEqual(acquired["execution_id"], request["execution_id"])

    def test_landing_holds_merge_resource_through_sync_and_merge_then_releases_on_error(self):
        lock = self.cfg.factory / "locks" / "merge.lock"
        stages = []

        def work(name):
            stages.append(name)
            self.assertTrue(dispatch.lock_held(lock))
            current = lifecycle.current()
            acquired = next(row for row in self.rows() if row["kind"] == "lock_acquired")
            self.assertEqual(acquired["execution_id"], current.execution_id)
            self.assertFalse(any(row["kind"] == "exit" for row in self.rows()))
            if name == "merge":
                raise RuntimeError("local merge failure")

        with patch.object(dispatch, "sync_pass", side_effect=lambda dry: work("sync")), \
                patch.object(dispatch, "merge_pass_locked", side_effect=lambda dry: work("merge")):
            with self.assertRaisesRegex(RuntimeError, "local merge failure"):
                dispatch.land_pass(False)
        self.assertEqual(stages, ["sync", "merge"])
        self.assertFalse(dispatch.lock_held(lock))
        rows = self.rows()
        kinds = [row["kind"] for row in rows]
        self.assertLess(kinds.index("resource_requested"), kinds.index("lock_acquired"))
        self.assertLess(kinds.index("lock_released"), kinds.index("exit"))
        acquired = next(row for row in rows if row["kind"] == "lock_acquired")
        released = next(row for row in rows if row["kind"] == "lock_released")
        self.assertEqual(released["acquisition_id"], acquired["acquisition_id"])
        self.assertEqual(released["execution_id"], acquired["execution_id"])

    def test_ticket_contention_and_lost_race_never_claim_or_acquire(self):
        lock = dispatch.ticket_lock(7)
        self.hold(lock)
        with patch.object(dispatch, "gh_json") as query, patch.object(dispatch, "run") as run:
            dispatch.process_ticket(self.issue, 1, False)
            # Force only the existing preflight probe to miss a real held lock.
            # The actual flock still rejects admission without blocking.
            with patch.object(dispatch, "lock_held", return_value=False):
                dispatch.process_ticket(self.issue, 1, False)
        query.assert_not_called()
        run.assert_not_called()
        rows = self.rows()
        waits = [row for row in rows if row["kind"] == "wait"]
        self.assertEqual([(row["wait"]["reason"], row["wait"]["mode"]) for row in waits],
                         [("ticket_lock_contended", "retry_next_pass")] * 2)
        self.assertEqual(len({row["execution_id"] for row in waits}), 2)
        self.assertFalse(any(row["kind"] in {"lock_acquired", "lock_released"} for row in rows))
        self.assertTrue(dispatch.lock_held(lock))

    def test_ticket_state_recheck_preserves_outcome_and_releases_before_exit(self):
        with patch.object(dispatch, "gh_json", return_value={
            "state": "CLOSED", "labels": [], "assignees": []
        }) as query, patch.object(dispatch, "run") as run:
            dispatch.process_ticket(self.issue, 1, False)
        query.assert_called_once()
        run.assert_not_called()
        rows = self.rows()
        exit_row = next(row for row in rows if row["kind"] == "exit")
        self.assertEqual((exit_row["outcome"], exit_row["reason"]), ("not_admitted", "state_changed"))
        self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))
        acquired = next(row for row in rows if row["kind"] == "lock_acquired")
        released = next(row for row in rows if row["kind"] == "lock_released")
        self.assertEqual(acquired["execution_id"], exit_row["execution_id"])
        self.assertEqual(released["acquisition_id"], acquired["acquisition_id"])
        self.assertLess(released["sequence"], exit_row["sequence"])
        self.assertFalse(any(row["kind"] == "wait" for row in rows))

    def test_ci_waits_reuse_single_query_per_candidate_and_preserve_eligibility(self):
        prs = [{"number": n * 10, "headRefName": f"agent/{n}", "isDraft": False,
                "labels": [{"name": "factory-approved"}], "reviewDecision": "APPROVED"}
               for n in (7, 8)]
        results = [subprocess.CompletedProcess([], 8, '[{"name":"ci","bucket":"pending"}]'),
                   subprocess.CompletedProcess([], 0, '[]')]
        with patch.object(dispatch, "gh_json", return_value=prs) as query, \
                patch.object(dispatch, "run", side_effect=results) as run, \
                patch.object(dispatch, "refresh_pr_branch") as refresh:
            dispatch.merge_pass_locked(False)
        query.assert_called_once()
        self.assertEqual(run.call_count, 2)
        self.assertEqual([call.args[0][:4] for call in run.call_args_list],
                         [["gh", "pr", "checks", "70"], ["gh", "pr", "checks", "80"]])
        refresh.assert_not_called()
        rows = self.rows()
        self.assertEqual([(row["ticket"], row["outcome"], row["reason"])
                          for row in rows if row["kind"] == "exit"],
                         [(7, "not_eligible", "ci_pending"), (8, "not_eligible", "no_passing_ci")])
        self.assertEqual([(row["wait"]["reason"], row["wait"]["mode"], row["wait"]["details"])
                          for row in rows if row["kind"] == "wait"],
                         [("ci_pending", "eligibility", {"pr": 70}),
                          ("no_passing_ci", "eligibility", {"pr": 80})])
        self.assertFalse(any(row["kind"] == "lock_acquired" for row in rows))

    def test_dry_run_does_not_create_journal_or_lock_directory(self):
        with patch.object(dispatch.config, "load", return_value=self.cfg), \
                patch.object(dispatch, "sync_pass") as sync, \
                patch.object(dispatch, "merge_pass_locked") as merge, \
                patch.object(dispatch, "frontier", return_value=[]):
            self.assertEqual(dispatch.main(["--dry-run"]), 0)
            dispatch.process_ticket(self.issue, 1, True)
        sync.assert_called_once_with(True)
        merge.assert_called_once_with(True)
        self.assertFalse(self.cfg.factory.exists())


if __name__ == "__main__":
    unittest.main()
