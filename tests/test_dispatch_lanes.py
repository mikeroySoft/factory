"""#823: lane attribution must not change pipeline isolation or landing locks."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import config, dispatch, lifecycle
from tests.test_schedule import ticket

WAIT = 10


class DispatchLanesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), "owner/repo", max_active=2)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            FACTORY=self.cfg.factory, LOGS=self.cfg.factory / "logs", EVENTS=self.events,
            MAX_ACTIVE=2, MAX_ATTEMPTS=1,
        ))

    def test_selected_workers_overlap_and_failure_preserves_other_lane(self):
        issues = [ticket(n, f"2026-01-0{n}T00:00:00Z", body="unbound") for n in (1, 2, 3)]
        barrier = threading.Barrier(2, timeout=WAIT)
        workers, approved, failures = {}, [], []

        def fresh(args):
            n = int(args[2])
            return {**issues[n - 1], "state": "OPEN", "comments": []}

        def worktree(n):
            wt = self.cfg.factory / f"wt-{n}"
            wt.mkdir(parents=True, exist_ok=True)
            return wt

        def worker(n, wt, *_args, **_kwargs):
            workers[n] = (wt, sorted(dispatch.active_tickets()))
            barrier.wait()
            if n == 1:
                raise RuntimeError("failed worker lane")
            return True, "gate passed", None, "head"

        def approve(n, _head):
            approved.append(n)
            dispatch.record("approved", ticket=n)
            return True

        def escalate(n, _reason, *_args, **_kwargs):
            failures.append(n)
            dispatch.record("escalate", ticket=n)

        replacements = {
            "frontier": lambda: issues,
            "fairness_session": dict,
            "gh_json": fresh,
            "observe_owner_requeue": lambda *_: None,
            "fallback_worker": lambda *_: "default",
            "worker_missing": lambda *_: None,
            "record_worker_selection": lambda *_, **__: "default",
            "record_claim_decision": lambda *_, **__: None,
            "admit_plan": lambda *_: None,
            "ensure_worktree": worktree,
            "run": lambda *_args, **_kwargs: SimpleNamespace(stdout="head", returncode=0),
            "agent_lease": lambda *_: None,
            "refresh_requeue_context": lambda *_: "",
            "sync_staged": lambda *_: None,
            "worker_round": worker,
            "push_and_pr": lambda *_, **__: True,
            "record_risk": lambda *_: None,
            "pr_gate_and_review": lambda *_: ("APPROVE", "review passed", False),
            "pr_comment": lambda *_: None,
            "_review_rounds_used": lambda *_: 0,
            "approve_pr": approve,
            "escalate": escalate,
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(dispatch.runner, "preflight", return_value=None))
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(dispatch, name, replacement))
            with self.assertRaisesRegex(RuntimeError, "failed worker lane"):
                dispatch.ticket_pass(1, False)

        self.assertEqual(set(workers), {1, 2}, "max_active must bound the selected pipelines")
        self.assertEqual(approved, [2], "the other lane must finish after one worker fails")
        self.assertEqual(failures, [1])
        self.assertEqual({value[0] for value in workers.values()},
                         {self.cfg.factory / "wt-1", self.cfg.factory / "wt-2"})
        self.assertTrue(all(n in active for n, (_, active) in workers.items()))
        self.assertEqual(dispatch.active_tickets(), [])
        rows = lifecycle.read_events(self.events)
        for n in (1, 2):
            own = [row for row in rows if row.get("ticket") == n and row.get("event") != "decision"]
            self.assertTrue(own)
            self.assertEqual({row.get("lane") for row in own}, {n})

    def test_landing_lock_serializes_simultaneous_passes(self):
        entered, release = threading.Event(), threading.Event()
        merges = []

        def merge(_dry_run):
            merges.append(threading.get_ident())
            entered.set()
            if not release.wait(WAIT):
                raise TimeoutError("landing was not released")

        with patch.object(dispatch, "sync_pass"), patch.object(dispatch, "merge_pass_locked", merge), \
                ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(dispatch.land_pass, False)
            try:
                self.assertTrue(entered.wait(WAIT))
                dispatch.land_pass(False)
                self.assertEqual(len(merges), 1, "a contender must not enter the merge stage")
            finally:
                release.set()
            first.result(timeout=WAIT)
            dispatch.land_pass(False)
            self.assertEqual(len(merges), 2, "the merge lock must be released for the next pass")
        rows = lifecycle.read_events(self.events)
        self.assertTrue(any(row.get("wait", {}).get("reason") == "merge_lock_contended" for row in rows))
