"""#594: fairness cannot displace claimable NOW work or survive a timer/engine restart."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle, schedule
from tests.test_schedule import REPO, ticket


class FairnessPolicyTest(unittest.TestCase):
    def test_two_slots_with_streak_three_both_go_to_ready_now(self):
        rows = [ticket(1, "2026-01-01", body="unbound"),
                ticket(3, "2026-01-03"), ticket(5, "2026-01-02")]
        decided = schedule.plan(rows, 2, repo=REPO, streak=3, experiments=0)
        self.assertEqual([row["number"] for row in decided["selected"]], [5, 3])

    def test_fairness_applies_without_claimable_now(self):
        for now in ([], [ticket(3, "2026-01-03", held="already claimed")]):
            with self.subTest(now=bool(now)):
                rows = now + [ticket(2, "2026-01-02"), ticket(1, "2026-01-01", body="unbound")]
                decided = schedule.plan(rows, 2, repo=REPO, streak=3, experiments=0)
                self.assertEqual([row["number"] for row in decided["selected"]], [1, 2])

    def test_fairness_never_overrides_the_experiment_limit(self):
        rows = [ticket(5, "2026-01-02", labels=("ready-for-agent", "experiment")),
                ticket(2, "2026-01-03"), ticket(1, "2026-01-01", body="unbound")]
        decided = schedule.plan(rows, 2, repo=REPO, streak=3, experiments=1)
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 2])


class FairnessSessionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = config.Config(root=Path(temp.name), repo=REPO, max_active=3)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=REPO,
            FACTORY=self.cfg.factory, EVENTS=self.events, MAX_ACTIVE=3,
        ))
        self.engine = self.enterContext(patch.object(config, "engine_commit", return_value="engine-a"))
        self.timer = self.enterContext(patch.object(
            dispatch.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "a" * 32 + "\n", ""),
        ))
        self.claimed = []

    def run_pass(self, rows, capacity):
        self.claimed = []

        def claim(issue, *args):
            self.claimed.append(issue["number"])
            tier, _ = schedule.priority(issue["number"], issue["body"], REPO)
            dispatch.record("claimed", ticket=issue["number"], priority=tier, experiment=False)

        with patch.object(dispatch, "MAX_ACTIVE", capacity), \
                patch.object(dispatch, "frontier", return_value=rows), \
                patch.object(dispatch, "process_ticket", side_effect=claim):
            dispatch.ticket_pass(1, False)
        return self.claimed

    def seed_streak(self):
        self.assertEqual(self.run_pass([ticket(2, "2026-01-01"), ticket(3, "2026-01-02"),
                                        ticket(5, "2026-01-03")], 3), [3, 5, 2])

    def competing(self):
        return [ticket(2, "2026-01-02"), ticket(1, "2026-01-01", body="unbound")]

    def test_streak_survives_ordinary_passes_with_the_same_active_timer(self):
        self.seed_streak()
        self.assertEqual(self.run_pass(self.competing(), 1), [1])

    def test_streak_resets_after_timer_pause_resume(self):
        self.seed_streak()
        # systemd gives the timer a new InvocationID when it is activated again.
        self.timer.return_value = subprocess.CompletedProcess([], 0, "b" * 32 + "\n", "")
        self.assertEqual(self.run_pass(self.competing(), 1), [2])
        # A subsequent pass counts only this session's claim, not the old three.
        self.assertEqual(self.run_pass(self.competing(), 1), [2])

    def test_streak_resets_after_engine_upgrade_restart(self):
        self.seed_streak()
        self.engine.return_value = "engine-b"
        self.assertEqual(self.run_pass(self.competing(), 1), [2])

    def test_streak_resets_after_version_upgrade_without_a_commit(self):
        self.engine.return_value = None
        self.seed_streak()
        with patch.object(dispatch, "__version__", "new-version"):
            self.assertEqual(self.run_pass(self.competing(), 1), [2])

    def test_unobservable_timer_does_not_reuse_historical_streak(self):
        for result in (subprocess.CompletedProcess([], 1, "", "no timer"),
                       subprocess.CompletedProcess([], 0, "\n", ""),
                       FileNotFoundError("systemctl"), subprocess.TimeoutExpired("systemctl", 3)):
            with self.subTest(result=result):
                self.timer.side_effect = None
                self.timer.return_value = subprocess.CompletedProcess([], 0, "a" * 32 + "\n", "")
                self.seed_streak()
                if isinstance(result, Exception):
                    self.timer.side_effect = result
                else:
                    self.timer.side_effect = None
                    self.timer.return_value = result
                self.assertEqual(self.run_pass(self.competing(), 1), [2])


if __name__ == "__main__":
    unittest.main()
