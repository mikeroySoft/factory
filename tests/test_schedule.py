"""#102: the dispatcher admits eligible work by accepted priority, not issue-list order."""
from __future__ import annotations

import os
import tempfile
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
        tier, why = schedule.priority(3, BOUND.replace("Initiative: #50", "Initiative: #51"), REPO)
        self.assertIsNone(tier)
        self.assertIn("invalid Plan baseline", why)

    def test_controlled_queue_ranks_after_eligibility_capacity_and_the_experiment_slot(self):
        decided = schedule.plan(QUEUE, 3, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [5, 3, 2])
        self.assertEqual(decisions(decided), {5: "selected", 3: "selected", 2: "selected", 7: "deferred",
                                              4: "deferred", 1: "deferred", 6: "held"})
        by = {row["ticket"]: row for row in decided["explained"]}
        self.assertIn("highest accepted priority NOW, oldest first", by[5]["reason"])
        self.assertTrue(by[5]["experiment"])
        self.assertIn("experiment slot full (1 of 1 active)", by[7]["reason"])
        self.assertIn("accepted priority LATER ranks after", by[4]["reason"])
        self.assertIn("unknown priority ranks after explicit priorities (unknown priority: no Plan baseline", by[1]["reason"])
        self.assertEqual(by[6], {"ticket": 6, "decision": "held", "reason": "blocked by #9"})

        # One experiment already active: the NOW experiment is deferred, production fills capacity.
        decided = schedule.plan(QUEUE, 3, repo=REPO, streak=0, experiments=1)
        self.assertEqual([row["number"] for row in decided["selected"]], [3, 2, 4])

        # Capacity beyond three explicit admissions reaches the oldest unknown-priority ticket.
        decided = schedule.plan(QUEUE, 4, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [5, 3, 2, 1])
        self.assertIn("fairness after 3 consecutive explicit-priority admissions",
                      next(row["reason"] for row in decided["explained"] if row["ticket"] == 1))

    def test_bounded_starvation_carries_over_from_the_journal_and_never_bypasses_the_slot(self):
        # Three explicit admissions in earlier passes: the oldest unknown-priority ticket goes first now.
        decided = schedule.plan(QUEUE, 2, repo=REPO, streak=3, experiments=1)
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 3])
        # #7 is the oldest unknown-priority ticket by age but the experiment slot holds; #1 is next oldest.
        self.assertEqual(decisions(decided)[7], "deferred")
        # Without any explicit-priority candidate, unknown-priority work is admitted oldest first.
        decided = schedule.plan([QUEUE[0], QUEUE[2]], 2, repo=REPO, streak=0, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 7])
        self.assertIn("no explicit priority is eligible", decided["explained"][0]["reason"])
        # Nothing eligible but a second experiment: no admission, the reason says why.
        decided = schedule.plan([QUEUE[2]], 2, repo=REPO, streak=0, experiments=1)
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

    def test_ticket_pass_claims_in_policy_order_and_journals_the_explanation(self):
        for n in (10, 11, 12):
            dispatch.record("claimed", ticket=n, priority="NOW")
        with patch.object(dispatch, "frontier", return_value=QUEUE), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(1, False)
        self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [1, 5, 3])
        row = next(e for e in lifecycle.read_events(self.events) if e.get("event") == "scheduling")
        self.assertEqual((row["capacity"], row["active"]), (3, []))
        self.assertEqual([(e["ticket"], e["decision"]) for e in row["explained"]],
                         [(1, "selected"), (5, "selected"), (3, "selected"), (2, "deferred"), (4, "deferred"),
                          (7, "deferred"), (6, "held")])
        self.assertIn("fairness after 3 consecutive", row["explained"][0]["reason"])

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


if __name__ == "__main__":
    unittest.main()
