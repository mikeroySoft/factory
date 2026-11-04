"""#541: concurrent tickets retain lane evidence and serialized landing."""
from __future__ import annotations

import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle, stats
from tests.test_schedule import REPO, ticket


class DispatchLaneTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(root=Path(temp.name), repo=REPO, max_active=2, max_attempts=1)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=REPO,
            FACTORY=self.cfg.factory, LOGS=self.cfg.factory / "logs", EVENTS=self.events,
            MAX_ACTIVE=2, MAX_ATTEMPTS=1,
        ))

    def test_selected_workers_overlap_and_keep_lane_on_failure(self):
        issues = [ticket(n, f"2026-01-0{n}T00:00:00Z", body="unbound") for n in (1, 2, 3)]
        barrier = threading.Barrier(2, timeout=5)
        finished = threading.Event()
        worktrees = {}

        def worktree(n):
            wt = self.cfg.factory / f"wt-{n}"
            wt.mkdir(parents=True)
            return wt

        def worker(n, wt, *args, **kwargs):
            worktrees[n] = wt
            self.assertTrue(dispatch.lock_held(dispatch.ticket_lock(n)))
            barrier.wait()
            self.assertEqual(sorted(dispatch.active_tickets()), [1, 2])
            with lifecycle.scope(self.events, "worker") as execution:
                # A real child inherits context just as the gate does, not a mock echo.
                child = subprocess.run([
                    sys.executable, "-c",
                    ("from pathlib import Path; from factory import lifecycle; "
                     "scope = lifecycle.scope(Path('/unused'), 'gate'); "
                     "scope.__enter__(); scope.__exit__(None, None, None)"),
                ], env=execution.env(), capture_output=True, text=True, timeout=5, check=False)
                self.assertEqual(child.returncode, 0, child.stderr)
            dispatch.record("attempt", ticket=n, attempt=1, gate="FAIL" if n == 1 else "PASS")
            if n == 1:
                raise RuntimeError("fake worker failed")
            self.assertTrue(finished.wait(5), "failed lane did not finish")
            return True, "PASS", wt / "worker.log", "head"

        def escalate(n, *args, **kwargs):
            dispatch.record("escalate", ticket=n, code=kwargs["code"])
            finished.set()

        def review(wt, n, *args):
            dispatch.record("review", ticket=n, verdict="APPROVE")
            return "APPROVE", "approved", False

        def approve(n, head):
            dispatch.record("approved", ticket=n, head=head)
            return True

        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "1\n", "")

        fresh = {**issues[0], "state": "OPEN", "assignees": [], "comments": []}
        with patch.object(dispatch, "frontier", return_value=issues), \
                patch.object(dispatch, "gh_json", return_value=fresh), \
                patch.object(dispatch, "run", side_effect=run), \
                patch.object(dispatch, "ensure_worktree", side_effect=worktree), \
                patch.object(dispatch, "admit_plan", return_value=None), \
                patch.object(dispatch, "agent_lease", return_value="base"), \
                patch.object(dispatch, "sync_staged", return_value=None), \
                patch.object(dispatch, "worker_round", side_effect=worker), \
                patch.object(dispatch, "escalate", side_effect=escalate), \
                patch.object(dispatch, "push_and_pr", return_value=True), \
                patch.object(dispatch, "record_risk"), \
                patch.object(dispatch, "pr_gate_and_review", side_effect=review), \
                patch.object(dispatch, "pr_comment"), \
                patch.object(dispatch, "approve_pr", side_effect=approve), \
                lifecycle.scope(self.events, "dispatcher", dispatcher=True), \
                self.assertRaisesRegex(RuntimeError, "fake worker failed"):
            dispatch.ticket_pass(1, False)

        self.assertEqual(worktrees, {n: self.cfg.factory / f"wt-{n}" for n in (1, 2)})
        self.assertEqual(dispatch.active_tickets(), [])
        rows = lifecycle.read_events(self.events)
        self.assertEqual([row["ticket"] for row in rows if row["event"] == "approved"], [2])
        for n in (1, 2):
            own = [row for row in rows if row.get("ticket") == n and
                   (row["event"] in {"claimed", "attempt", "review", "approved", "escalate"}
                    or row["event"] == "lifecycle" and row.get("stage") != "scheduling")]
            self.assertEqual({row.get("lane") for row in own}, {n})
            self.assertEqual({row["stage"] for row in own if row["event"] == "lifecycle"},
                             {"ticket", "worker", "gate"})

    def test_landing_skips_contender_then_retries_after_release(self):
        entered, release = threading.Event(), threading.Event()
        landed, errors = [], []

        def merge(dry_run):
            landed.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise RuntimeError("landing was not released")

        def first():
            try:
                dispatch.land_pass(False)
            except RuntimeError as error:
                errors.append(error)

        runner = threading.Thread(target=first, daemon=True)
        with patch.object(dispatch, "sync_pass"), patch.object(dispatch, "merge_pass_locked", side_effect=merge):
            runner.start()
            try:
                self.assertTrue(entered.wait(5))
                dispatch.land_pass(False)
                self.assertEqual(len(landed), 1, "two landings entered merge.lock together")
            finally:
                release.set()
                runner.join(5)
            self.assertFalse(runner.is_alive())
            self.assertEqual(errors, [])
            dispatch.land_pass(False)
        self.assertEqual(len(landed), 2)
        rows = lifecycle.read_events(self.events)
        self.assertTrue(any(row.get("wait", {}).get("reason") == "merge_lock_contended" for row in rows))

    def test_lane_stats_attribute_later_merge_and_reclaim_without_losing_legacy(self):
        rows = [
            {"event": "claimed", "ticket": 1, "lane": 1},
            {"event": "attempt", "ticket": 1, "lane": 1, "attempt": 1, "gate": "FAIL"},
            {"event": "escalate", "ticket": 1, "lane": 1},
            {"event": "claimed", "ticket": 1, "lane": 2},
            {"event": "attempt", "ticket": 1, "lane": 2, "attempt": 1, "gate": "PASS"},
            {"event": "review", "ticket": 1, "lane": 2, "verdict": "APPROVE"},
            {"event": "merged", "ticket": 1},
            {"event": "claimed", "ticket": 9},
            {"event": "attempt", "ticket": 9, "attempt": 1, "gate": "PASS"},
            {"event": "merged", "ticket": 9},
        ]
        for row in rows:
            lifecycle.append(self.events, row)
        out = io.StringIO()
        with patch.object(stats, "resolve", return_value=self.cfg), \
                patch.object(stats, "cfg", create=True), patch.object(stats, "REPOSITORY", create=True), \
                patch.object(stats, "gh", side_effect=AssertionError("lane stats must be local")), \
                redirect_stdout(out):
            self.assertEqual(stats.main(["--by-lane", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), [
            {"lane": 1, "claims": 1, "attempts": 1, "first_pass": 0.0, "reviews": 0, "merges": 0, "escalations": 1},
            {"lane": 2, "claims": 1, "attempts": 1, "first_pass": 1.0, "reviews": 1, "merges": 1, "escalations": 0},
            {"lane": None, "claims": 1, "attempts": 1, "first_pass": 1.0, "reviews": 0, "merges": 1, "escalations": 0},
        ])

    def test_weekly_lane_stats_retain_claim_before_window(self):
        from datetime import UTC, datetime, timedelta

        end = datetime(2026, 11, 3, tzinfo=UTC)
        start = end - timedelta(days=7)
        audit = {7: [
            {"event": "claimed", "lane": 2, "at": "2026-10-01T00:00:00Z"},
            {"event": "attempt", "lane": 2, "attempt": 1, "gate": "PASS", "at": "2026-10-01T00:00:01Z"},
            {"event": "merged", "at": "2026-11-02T00:00:00Z"},
            {"event": "merged", "at": "2026-11-04T00:00:00Z"},
        ]}
        self.assertEqual(stats.lane_metrics(audit, start, end), [
            {"lane": 2, "claims": 0, "attempts": 0, "first_pass": None, "reviews": 0, "merges": 1, "escalations": 0},
        ])

    def test_direct_ticket_defaults_to_lane_one_with_and_without_contention(self):
        issue = ticket(7, "2026-01-01T00:00:00Z")
        for held in (False, True):
            with self.subTest(held=held), dispatch.ticket_lock(7).open("w") as lock:
                if held:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(dispatch, "gh_json", return_value={"state": "CLOSED", "labels": []}):
                    dispatch.process_ticket(issue, 1, False)
                rows = lifecycle.read_events(self.events)
                own = [row for row in rows if row.get("stage") == "ticket"]
                self.assertEqual({row["lane"] for row in own}, {1})
                if held:
                    waits = [row["wait"]["reason"] for row in own if row["kind"] == "wait"]
                    self.assertEqual(waits, ["ticket_lock_contended"])
                else:
                    self.assertEqual(own[-1]["outcome"], "not_admitted")
                self.events.unlink()

    def test_lane_first_pass_is_fraction_of_first_attempts_only(self):
        audit = {n: [
            {"event": "claimed", "lane": 1},
            {"event": "attempt", "lane": 1, "attempt": 0, "gate": "PASS"},
            {"event": "attempt", "lane": 1, "attempt": 1, "gate": gate},
            {"event": "attempt", "lane": 1, "attempt": 2, "gate": "PASS"},
        ] for n, gate in enumerate(("FAIL", "PASS", "FAIL"), 1)}
        self.assertEqual(stats.lane_metrics(audit), [
            {"lane": 1, "claims": 3, "attempts": 9, "first_pass": 1 / 3,
             "reviews": 0, "merges": 0, "escalations": 0},
        ])

    def test_invalid_lanes_are_reported_as_unknown(self):
        for lane in (0, -1, True, "2", 1.5):
            with self.subTest(lane=lane):
                self.assertEqual(stats.lane_metrics({7: [{"event": "claimed", "lane": lane}]}), [
                    {"lane": None, "claims": 1, "attempts": 0, "first_pass": None,
                     "reviews": 0, "merges": 0, "escalations": 0},
                ])

    def test_weekly_lane_window_includes_both_endpoints(self):
        end = datetime(2026, 11, 3, tzinfo=UTC)
        start = end - timedelta(days=7)
        rows = [
            {"event": "claimed", "lane": 2, "at": (start - timedelta(days=1)).isoformat()},
            {"event": "attempt", "attempt": 1, "gate": "PASS", "at": start.isoformat()},
            {"event": "merged", "at": end.isoformat()},
            {"event": "escalate", "at": (end + timedelta(microseconds=1)).isoformat()},
            {"event": "review", "at": (start - timedelta(microseconds=1)).isoformat()},
            {"event": "review", "at": "invalid"},
            {"event": "review"},
        ]
        self.assertEqual(stats.lane_metrics({7: rows}, start, end), [
            {"lane": 2, "claims": 0, "attempts": 1, "first_pass": 1.0,
             "reviews": 0, "merges": 1, "escalations": 0},
        ])

    def test_weekly_lane_cli_reports_rates_and_unknown_lanes_in_text_and_json(self):
        end = datetime(2026, 11, 3, tzinfo=UTC)
        rows = [
            {"event": "claimed", "ticket": 1, "lane": 2, "at": "2026-10-01T00:00:00Z"},
            {"event": "attempt", "ticket": 1, "attempt": 1, "gate": "PASS", "at": "2026-10-28T00:00:00Z"},
            {"event": "attempt", "ticket": 2, "lane": 2, "attempt": 1, "gate": "FAIL", "at": "2026-10-29T00:00:00Z"},
            {"event": "merged", "ticket": 1, "at": "2026-11-02T00:00:00Z"},
            {"event": "claimed", "ticket": 9, "at": "2026-11-02T00:00:00Z"},
            {"event": "merged", "ticket": 10, "lane": 3, "at": "2026-11-04T00:00:00Z"},
        ]
        for row in rows:
            lifecycle.append(self.events, row)
        for json_output in (False, True):
            out = io.StringIO()
            with self.subTest(json=json_output), \
                    patch.object(stats, "resolve", return_value=self.cfg), \
                    patch.object(stats, "cfg", create=True), patch.object(stats, "REPOSITORY", create=True), \
                    patch.object(stats, "_now", return_value=end), \
                    patch.object(stats, "gh", side_effect=AssertionError("lane stats must be local")), \
                    redirect_stdout(out):
                self.assertEqual(stats.main(["--by-lane", "--week", *(["--json"] if json_output else [])]), 0)
            if json_output:
                self.assertEqual(json.loads(out.getvalue()), [
                    {"lane": 2, "claims": 0, "attempts": 2, "first_pass": 0.5,
                     "reviews": 0, "merges": 1, "escalations": 0},
                    {"lane": None, "claims": 1, "attempts": 0, "first_pass": None,
                     "reviews": 0, "merges": 0, "escalations": 0},
                ])
            else:
                self.assertEqual([line.split() for line in out.getvalue().splitlines()[1:]], [
                    ["2", "0", "2", "50.0%", "0", "1", "0"],
                    ["unknown", "1", "0", "n/a", "0", "0", "0"],
                ])


if __name__ == "__main__":
    unittest.main()
