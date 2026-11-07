"""#102: the dispatcher admits eligible work by accepted priority, not issue-list order."""
from __future__ import annotations

import fcntl
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import binding, config, dispatch, lifecycle, schedule
from tests.test_roadmap import REPO, initiative_body, issue

PLAN = "NOW #3 and #5\nNEXT #2\nLATER #4\nNOW #8"
PARENT = issue(50, initiative_body(PLAN), labels=("initiative",), title="Roadmap")
BOUND = binding.render(binding.from_issue(config.Config(Path("/nonexistent"), REPO), 50, PARENT))


def ticket(number: int, created: str, *, body: str = BOUND, labels=("ready-for-agent",), held: str | None = None) -> dict:
    row = {"number": number, "title": f"t{number}", "body": body, "createdAt": created,
           "labels": [{"name": name} for name in labels], "assignees": []}
    if held:
        row["held"] = held
    return row


# Issue-list order is deliberately not priority order.
QUEUE = [
    ticket(8, "2025-12-31T00:00:00Z", body="Initiative: #50\n\nno baseline"),  # admission would refuse it
    ticket(1, "2026-01-01T00:00:00Z", body="Scope\n\nNOW #1 says the brief"),  # unknown: no baseline
    ticket(3, "2026-01-03T00:00:00Z"),                                        # NOW, newer than #5
    ticket(7, "2026-01-02T00:00:00Z", body="unbound", labels=("ready-for-agent", "experiment")),
    ticket(4, "2026-01-04T00:00:00Z"),                                        # LATER
    ticket(5, "2026-01-02T00:00:00Z", labels=("ready-for-agent", "experiment")),  # NOW experiment
    ticket(2, "2026-01-05T00:00:00Z"),                                        # NEXT
    ticket(6, "2026-01-01T00:00:00Z", held="blocked by #9"),
]


def decisions(decided: dict) -> dict[int, str]:
    return {row["ticket"]: row["decision"] for row in decided["explained"]}


class PolicyTest(unittest.TestCase):
    def test_priority_comes_only_from_the_pinned_plan_baseline(self):
        self.assertEqual(schedule.priority(3, BOUND, REPO)[0], "NOW")
        self.assertEqual(schedule.priority(4, BOUND, REPO), ("LATER", "LATER in initiative #50 Plan baseline"))
        tier, why = schedule.priority(1, "Triage says NOW #1\nNOW #1", REPO)  # a recommendation, not a binding
        self.assertEqual((tier, why), (None, "unknown priority: no Plan baseline (ticket is not bound to an initiative)"))
        tier, why = schedule.priority(9, BOUND, REPO)
        self.assertIsNone(tier)
        self.assertIn("initiative #50 Plan baseline names no tier for #9", why)
        with self.assertRaisesRegex(binding.BindingError, "initiative #50 is missing a Plan baseline"):
            schedule.priority(8, QUEUE[0]["body"], REPO)
        with self.assertRaises(binding.BindingError):
            schedule.priority(3, BOUND.replace("Initiative: #50", "Initiative: #51"), REPO)

    def test_controlled_queue_ranks_after_eligibility_capacity_and_the_experiment_slot(self):
        decided = schedule.plan(QUEUE, 3, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [5, 3, 2])
        self.assertEqual(decisions(decided), {5: "selected", 3: "selected", 2: "selected", 7: "deferred",
                                              4: "deferred", 1: "deferred", 8: "held", 6: "held"})
        by = {row["ticket"]: row for row in decided["explained"]}
        self.assertIn("highest accepted priority NOW, oldest first", by[5]["reason"])
        self.assertTrue(by[5]["experiment"])
        self.assertIn("experiment slot full (1 of 1 active)", by[7]["reason"])
        self.assertIn("accepted priority LATER ranks after", by[4]["reason"])
        self.assertIn("unknown priority ranks after explicit priorities (unknown priority: no Plan baseline", by[1]["reason"])
        self.assertEqual(by[6], {"ticket": 6, "decision": "held", "reason": "blocked by #9"})
        self.assertEqual(by[8]["reason"], "Plan binding is invalid; admission would refuse it: "
                                          "linked ticket for initiative #50 is missing a Plan baseline")

        # One experiment already active: the NOW experiment is deferred, production fills capacity.
        decided = schedule.plan(QUEUE, 3, repo=REPO, streak=0, experiments=1)
        self.assertEqual([row["number"] for row in decided["selected"]], [3, 2, 4])

        # Capacity beyond three explicit admissions reaches the oldest unknown-priority ticket.
        decided = schedule.plan(QUEUE, 4, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [5, 3, 2, 1])
        self.assertIn("fairness after 3 consecutive explicit-priority admissions",
                      next(row["reason"] for row in decided["explained"] if row["ticket"] == 1))

    def test_bounded_starvation_carries_over_from_the_journal_and_never_bypasses_the_slot(self):
        # No claimable NOW: #3 is already claimed, #5 is held by the experiment slot.
        # Three explicit admissions let the oldest eligible unknown-priority ticket go first.
        # #8 is older still, but admission would refuse it, so fairness never picks it.
        rows = [{**row, "held": "already claimed"} if row["number"] == 3 else row for row in QUEUE]
        decided = schedule.plan(rows, 2, repo=REPO, streak=3, experiments=1)
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 2])
        self.assertEqual(decisions(decided)[3], "held")
        self.assertEqual(decisions(decided)[5], "deferred")
        self.assertEqual(decisions(decided)[8], "held")
        # #7 is an unknown-priority experiment, so fairness cannot bypass its full slot either.
        self.assertEqual(decisions(decided)[7], "deferred")
        # Without any explicit-priority candidate, unknown-priority work is admitted oldest first.
        decided = schedule.plan([QUEUE[1], QUEUE[3]], 2, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 7])
        self.assertIn("no explicit priority is eligible", decided["explained"][0]["reason"])
        # Nothing eligible but a second experiment: no admission, the reason says why.
        decided = schedule.plan([QUEUE[3]], 2, repo=REPO, streak=0, experiments=1)
        self.assertEqual(decided["selected"], [])
        self.assertEqual(decisions(decided), {7: "deferred"})

    def test_streak_and_active_experiments_read_claimed_rows(self):
        rows = [
            {"event": "claimed", "ticket": 1, "priority": None},
            {"event": "claimed", "ticket": 2, "priority": "NOW", "experiment": True},
            {"event": "attempt", "ticket": 2},
            {"event": "claimed", "ticket": 3, "priority": "LATER"},
            {"event": "claimed", "ticket": 2, "priority": "NOW", "experiment": False},
        ]
        self.assertEqual(schedule.explicit_streak(rows), 3)
        self.assertEqual(schedule.explicit_streak(rows[:1]), 0)
        self.assertEqual(schedule.explicit_streak(rows[:2] + [{"event": "claimed", "ticket": 9}]), 0)
        self.assertEqual(schedule.active_experiments(rows[:3], {2, 3}), 1)
        self.assertEqual(schedule.active_experiments(rows, {2, 3}), 0)  # the latest claim of #2 is not an experiment


class DispatcherPassTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = config.Config(root=Path(temp.name), repo=REPO, max_active=3)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", EVENTS=self.events, MAX_ACTIVE=3,
        ))
        self.enterContext(patch("factory.handoff.timeline", return_value=[]))

    def test_ticket_pass_claims_in_policy_order_and_journals_the_explanation(self):
        # Keep the fairness/experiment scenario, but with NEXT rather than claimable NOW work.
        parent = issue(50, initiative_body(PLAN.replace("NOW #3 and #5", "NEXT #3 and #5")),
                       labels=("initiative",), title="Roadmap")
        bound = binding.render(binding.from_issue(self.cfg, 50, parent))
        rows = [{**row, "body": bound} if row["number"] in (3, 5) else row for row in QUEUE]
        session = {"timer_invocation": "a" * 32, "engine_version": dispatch.__version__, "engine_commit": "engine-a"}
        dispatch.record("scheduling", fairness_session=session)
        for n in (10, 11, 12):
            dispatch.record("claimed", ticket=n, priority="NOW")
        with patch.object(dispatch, "fairness_session", return_value=session), \
                patch.object(dispatch, "frontier", return_value=rows), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(1, False)
        self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [1, 5, 3])
        row = next(e for e in lifecycle.read_events(self.events) if e.get("event") == "scheduling" and "explained" in e)
        self.assertEqual((row["capacity"], row["active"]), (3, []))
        self.assertEqual([(e["ticket"], e["decision"]) for e in row["explained"]],
                         [(1, "selected"), (5, "selected"), (3, "selected"), (2, "deferred"), (4, "deferred"),
                          (7, "deferred"), (8, "held"), (6, "held")])
        self.assertIn("fairness after 3 consecutive", row["explained"][0]["reason"])
        by = {e["ticket"]: e for e in row["explained"]}
        self.assertIn("admission would refuse it", by[8]["reason"])
        self.assertIn("experiment slot full", by[7]["reason"])

    def test_dry_run_explains_without_journaling_and_held_only_frontier_is_idle(self):
        with patch.object(dispatch, "frontier", return_value=QUEUE), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(1, True)
        self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [5, 3, 2])
        self.assertFalse(self.events.exists())
        with patch.object(dispatch, "frontier", return_value=[QUEUE[-1]]), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(1, False)
        process.assert_not_called()
        self.assertFalse(self.events.exists())

    def test_frontier_sees_more_than_30_ready_issues(self):
        # #577: gh returns the 30 newest issues unless told otherwise; the oldest NOW ticket must still be seen.
        issues = [ticket(3, "2025-01-01T00:00:00Z")] + [
            ticket(100 + i, f"2026-01-{i + 1:02d}T00:00:00Z", body="unbound") for i in range(30)]

        def fake_run(cmd, cwd=None, check=True, stdout=None, stderr=None, env=None, timeout=None):
            args = cmd[1:]
            if args[:2] == ["issue", "list"]:
                limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 30
                self.assertGreaterEqual(limit, 1000, "frontier() must pass an explicit --limit >= 1000")
                served = sorted(issues, key=lambda row: row["createdAt"], reverse=True)[:limit]
                return subprocess.CompletedProcess(cmd, 0, json.dumps(served), "")
            if args[0] == "api" and args[1].endswith("/dependencies/blocked_by"):
                return subprocess.CompletedProcess(cmd, 1, "", "HTTP 404")
            self.fail(f"unexpected gh call: {cmd}")

        with patch.object(dispatch, "run", side_effect=fake_run):
            rows = dispatch.frontier()
        oldest = next((row for row in rows if row["number"] == 3), None)
        self.assertIsNotNone(oldest, "the oldest ready issue is missing from frontier()")
        self.assertNotIn("held", oldest)
        decided = schedule.plan(rows, 1, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [3])

    def test_pass_reranks_after_bind_change_mid_pass(self):
        def bound(plan: str) -> str:
            parent = issue(50, initiative_body(plan), labels=("initiative",), title="Roadmap")
            return binding.render(binding.from_issue(config.Config(Path("/nonexistent"), REPO), 50, parent))

        before, after = bound("NOW #21\nNEXT #22"), bound("NEXT #22\nNOW #23")
        stale = [ticket(21, "2026-01-01T00:00:00Z", body=before), ticket(22, "2026-01-02T00:00:00Z", body=before)]
        # The Plan baseline changed while #21 was worked: #22 is re-bound, #23 is new and NOW, #21 is claimed.
        fresh = [ticket(22, "2026-01-02T00:00:00Z", body=after), ticket(23, "2026-01-03T00:00:00Z", body=after)]
        with patch.object(dispatch, "MAX_ACTIVE", 2), patch.object(dispatch, "process_ticket") as process, \
                patch.object(dispatch, "frontier", side_effect=lambda: fresh if process.called else stale):
            dispatch.ticket_pass(1, False)
        self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [21, 23])
        admissions = [e for e in lifecycle.read_events(self.events)
                      if e.get("event") == "decision" and e.get("stage") == "admission"]
        baselines = {e["ticket"]: next(i["summary"] for i in e["inputs"] if i["kind"] == "plan_baseline")
                     for e in admissions}
        shas = binding.baseline(before, REPO)["sha256"], binding.baseline(after, REPO)["sha256"]
        self.assertNotEqual(*shas)
        self.assertEqual(baselines, {21: shas[0], 23: shas[1]})

    def test_pass_runs_selected_tickets_concurrently_up_to_max_active(self):
        # #579: a pass works its picks at once, up to max_active counting tickets already in flight.
        pair = [ticket(3, "2026-01-03T00:00:00Z"), ticket(5, "2026-01-02T00:00:00Z")]
        for max_active, held, expected in ((2, None, 2), (1, None, 1), (2, 9, 1)):
            with self.subTest(max_active=max_active, held=held):
                started, release, errors = queue.Queue(), threading.Event(), []

                def fake_process(issue, budget_min, dry_run, *args, started=started, release=release, **kwargs):
                    with dispatch.ticket_lock(issue["number"]).open("w") as fd:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # models the real ticket lock
                        started.put(issue["number"])
                        if admitted := issue.get("_factory_admitted"):
                            admitted.set()
                        if not release.wait(10):
                            raise AssertionError(f"#{issue['number']} was never released")
                        fcntl.flock(fd, fcntl.LOCK_UN)

                def run_pass(errors=errors):
                    try:
                        dispatch.ticket_pass(1, False)
                    except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                        errors.append(exc)

                holder = None
                if held is not None:
                    holder = dispatch.ticket_lock(held).open("w")
                    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(dispatch, "MAX_ACTIVE", max_active), \
                        patch.object(dispatch, "frontier", return_value=pair), \
                        patch.object(dispatch, "process_ticket", side_effect=fake_process) as process:
                    worker = threading.Thread(target=run_pass, daemon=True)
                    worker.start()
                    try:
                        running = []
                        for _ in range(expected):
                            try:
                                running.append(started.get(timeout=10))
                            except queue.Empty:
                                self.fail(f"max_active={max_active}: only {running} in flight; "
                                          "the pass works its picks one at a time")
                        with self.assertRaises(queue.Empty, msg=f"more than {expected} ticket(s) in flight"):
                            started.get(timeout=0.5)
                        self.assertEqual(sorted(dispatch.active_tickets()),
                                         sorted(running + ([held] if held is not None else [])))
                    finally:
                        release.set()
                        worker.join(10)
                        if holder is not None:
                            holder.close()
                    self.assertFalse(worker.is_alive(), "ticket_pass did not finish after release")
                self.assertEqual(errors, [])
                self.assertEqual(process.call_count, expected)

    def test_process_ticket_signals_admission_after_the_claimed_row(self):
        # #579: the pass ranks its next pick once this one is claimed; the real pipeline says when.
        class Stop(BaseException):
            pass

        fresh = {**ticket(7, "2026-01-01T00:00:00Z"), "state": "OPEN", "comments": []}
        wt = self.cfg.factory / "wt-7"
        seen = {}

        def claimed() -> list[int]:
            return [e["ticket"] for e in lifecycle.read_events(self.events) if e.get("event") == "claimed"]

        def worktree(n):
            seen["worktree"] = admitted.is_set()
            wt.mkdir(parents=True, exist_ok=True)
            return wt

        def lease(wt, n):
            seen["lease"] = admitted.is_set()
            return ""

        def first_round(*args, **kwargs):
            seen["round"] = (admitted.is_set(), claimed())
            raise Stop

        workers = config.Config(root=self.cfg.root, repo=REPO, workers={"default": [sys.executable, "{prompt}"]})
        done = subprocess.CompletedProcess([], 0, "abc123\n", "")
        with patch.multiple(
            dispatch, create=True, cfg=workers, MAX_ATTEMPTS=1, admit_plan=lambda n, fresh: None,
            run=lambda cmd, *args, **kwargs: done, ensure_worktree=worktree, agent_lease=lease,
            sync_staged=lambda n, wt: None, worker_round=first_round,
        ):
            admitted = threading.Event()
            issue = {**ticket(7, "2026-01-01T00:00:00Z"), "_factory_admitted": admitted}
            with patch.object(dispatch, "gh_json", return_value={**fresh, "state": "CLOSED"}):
                dispatch.process_ticket(issue, 30, False)
            self.assertFalse(admitted.is_set(), "a ticket skipped before its claim must not signal admission")
            self.assertEqual(claimed(), [])

            with patch.object(dispatch, "gh_json", return_value=fresh), self.assertRaises(Stop):
                dispatch.process_ticket(issue, 30, False)
        self.assertEqual(seen, {"worktree": False, "lease": True, "round": (True, [7])})


if __name__ == "__main__":
    unittest.main()
