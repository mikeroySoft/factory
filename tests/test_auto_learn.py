"""`factory learn` runs at the end of a dispatcher pass after every 10 finished tickets (#212)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from factory import dispatch, learn, lifecycle
from factory.config import Config


class AutoLearnTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(root=Path(temp.name), repo="acme/widgets", manager=["true"])
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory, LOGS=self.cfg.factory / "logs",
            SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl", EVENTS=self.events,
            MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.enterContext(mock.patch("factory.stats.gh", return_value=[]))
        self.open_prs: list[dict] = []
        self.gh = self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=lambda _: self.open_prs))
        self.propose = self.enterContext(mock.patch.object(
            learn, "propose", return_value=(["Run `make test` before the gate."], None)))
        self.chore_pr = self.enterContext(mock.patch.object(learn, "chore_pr"))
        self.log = self.enterContext(mock.patch.object(dispatch, "log"))

    def finish(self, tickets: range, year: int = 2000) -> None:
        """Journal `merged` rows; year 2000 precedes any real `learn` row, 2999 follows it."""
        with self.events.open("a") as f:
            for n in tickets:
                f.write(json.dumps({"event": "claimed", "ticket": n, "title": f"t{n}", "at": f"{year}-01-01T00:00:00Z"}) + "\n")
                f.write(json.dumps({"event": "merged", "ticket": n, "at": f"{year}-01-01T00:{n % 60:02d}:00Z"}) + "\n")

    def learns(self) -> list[dict]:
        return [e for e in lifecycle.read_events(self.events) if e.get("event") == "learn"]

    def logged(self) -> list[str]:
        return [c.args[0] for c in self.log.call_args_list]

    def test_runs_once_after_ten_finished_tickets(self) -> None:
        self.finish(range(1, 10))
        learn.learn_pass()
        self.propose.assert_not_called()
        self.assertEqual(self.learns(), [])
        self.finish(range(10, 11))
        learn.learn_pass()
        self.propose.assert_called_once()
        self.chore_pr.assert_called_once()
        [event] = self.learns()
        self.assertEqual(event["trigger"], "auto")
        self.assertEqual(event["tickets"], list(range(1, 11)))
        self.assertEqual(event["branch"], f"agent/lessons-{date.today().isoformat()}-t10")
        learn.learn_pass()  # the count restarts after the recorded run
        self.propose.assert_called_once()

    def test_open_lessons_pr_skips_and_keeps_the_count(self) -> None:
        self.finish(range(1, 11))
        self.open_prs = [{"number": 7, "headRefName": "agent/31"},
                         {"number": 42, "headRefName": "agent/curate-2026-10-01-t9"}]
        learn.learn_pass()
        self.propose.assert_not_called()
        self.assertIn("learn: skipped (open lessons PR #42)", self.logged())
        self.assertEqual(self.learns(), [])
        self.assertEqual(learn.finished_since_learn(), 10)
        self.open_prs = [{"number": 7, "headRefName": "agent/31"}]
        learn.learn_pass()
        self.propose.assert_called_once()
        self.assertEqual([e["trigger"] for e in self.learns()], ["auto"])

    def test_no_manager_skips_and_records_nothing(self) -> None:
        self.cfg.manager = None
        self.finish(range(1, 11))
        learn.learn_pass()
        self.assertEqual(self.logged(), ["learn: skipped (no manager)"])
        self.propose.assert_not_called()
        self.gh.assert_not_called()
        self.assertFalse((self.cfg.root / ".factory-lessons.md").exists())
        self.assertEqual(self.learns(), [])

    def test_failure_never_fails_the_pass_and_the_next_pass_retries(self) -> None:
        self.finish(range(1, 11))
        self.propose.side_effect = ValueError("model returned unparseable JSON twice")
        with mock.patch.object(dispatch.config, "load", return_value=self.cfg), \
                mock.patch.object(dispatch, "land_pass"), \
                mock.patch.object(dispatch, "review_intake_pass"), \
                mock.patch("factory.manage.manage_pass"), \
                mock.patch.object(dispatch, "frontier", return_value=[]):
            self.assertEqual(dispatch.main([]), 0)
            [failed] = self.learns()
            self.assertEqual((failed["status"], failed["trigger"]), ("failed", "auto"))
            self.assertIn("unparseable JSON", failed["reason"])
            self.assertEqual(learn.finished_since_learn(), 10)
            self.propose.side_effect = None
            self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.propose.call_count, 2)
        self.assertNotIn("status", self.learns()[-1])
        self.assertEqual(learn.finished_since_learn(), 0)

    def test_same_day_runs_get_distinct_branches_the_merge_stage_ignores(self) -> None:
        self.finish(range(1, 11))
        learn.learn_pass()
        self.finish(range(11, 21), year=2999)
        learn.learn_pass()
        branches = [e["branch"] for e in self.learns()]
        today = date.today().isoformat()
        self.assertEqual(branches, [f"agent/lessons-{today}-t10", f"agent/lessons-{today}-t20"])
        self.assertEqual(learn.branch_name("curate", [3, 20]), f"agent/curate-{today}-t20")
        self.open_prs = [{"number": i, "headRefName": b, "headRefOid": "x", "baseRefName": "main",
                          "isDraft": False, "labels": [{"name": dispatch.FACTORY_APPROVED}],
                          "reviewDecision": ""} for i, b in enumerate([*branches, learn.branch_name("curate", [20])])]
        with mock.patch.object(dispatch, "initiative_kind") as candidate:
            dispatch.merge_pass_locked(dry_run=True)
        candidate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
