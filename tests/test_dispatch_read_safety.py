"""Dispatch-managed stages keep remote read failures item-local and fail closed."""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import binding, config, dispatch, learn, lifecycle, reads, release


class DispatchReadSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(
            self.root,
            "acme/widgets",
            workers={"default": [sys.executable, "{prompt}"]},
        )
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        dispatch.configure(self.cfg)
        self.journal = self.cfg.factory / "events.jsonl"

    @staticmethod
    def failure(command: list[str] | None = None) -> reads.ReadFailure:
        command = command or ["gh", "issue", "view", "7"]
        return reads.ReadFailure(
            "gh_read_failed",
            subprocess.CompletedProcess(command, 1, "", "HTTP 500: provider unavailable"),
        )

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.journal) if row.get("event") == event]

    def test_binding_read_failure_is_a_journaled_refusal_before_claim(self) -> None:
        issue = {
            "number": 7,
            "title": "Bounded work",
            "body": "Initiative: #50",
            "state": "OPEN",
            "labels": [{"name": config.LABEL_AGENT}],
            "assignees": [],
            "comments": [],
        }
        output = io.StringIO()
        with mock.patch.object(dispatch, "gh_json", return_value=issue), \
                mock.patch.object(binding, "admit", side_effect=self.failure()), \
                reads.pass_scope(self.journal, "dispatch"), redirect_stdout(output):
            dispatch.process_ticket(issue, 1, False, forced=True)

        self.assertIn("refused", output.getvalue())
        self.assertIn("unavailable", output.getvalue())
        [skip] = self.rows("read-skip")
        self.assertEqual((skip["ticket"], skip["code"]), (7, "gh_read_failed"))
        decision = next(row for row in self.rows("decision") if row.get("stage") == "binding")
        self.assertEqual(decision["chosen"], "refused")
        self.assertEqual(self.rows("claimed") + self.rows("escalate"), [])

    def test_direct_ticket_read_failure_has_ticket_identity(self) -> None:
        args = SimpleNamespace(ticket=7, budget_min=1, dry_run=False)
        with mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(config, "load", return_value=self.cfg), \
                mock.patch.object(config, "engine_commit", return_value=None), \
                mock.patch.object(config, "engine_target", return_value=None), \
                mock.patch.object(dispatch, "_sweep_stale_factory_tempdirs"), \
                mock.patch("factory.results.prune", return_value={"status": "complete"}), \
                mock.patch.object(dispatch, "issue_is_open", side_effect=self.failure()), \
                reads.pass_scope(self.journal, "dispatch"):
            self.assertEqual(dispatch._dispatch(args), 0)

        [skip] = self.rows("read-skip")
        self.assertEqual((skip["ticket"], skip["code"]), (7, "gh_read_failed"))

    def test_failed_promotion_read_skips_one_pr_and_continues(self) -> None:
        prs = [
            {"number": 10, "headRefName": "agent/lessons-a", "isDraft": False,
             "baseRefName": "main"},
            {"number": 11, "headRefName": "agent/lessons-b", "isDraft": False,
             "baseRefName": "main"},
        ]
        with mock.patch.object(learn, "_reconcile_human_merges"), \
                mock.patch.object(learn, "promote", side_effect=[self.failure(), None]) as promote, \
                reads.pass_scope(self.journal, "dispatch"):
            learn.promote_pass(prs)

        self.assertEqual(promote.call_count, 2)
        [skip] = self.rows("read-skip")
        self.assertEqual((skip["pr"], skip["operation"]), (10, "promotion"))

    def test_learn_read_failure_is_not_recorded_as_a_learning_failure(self) -> None:
        with mock.patch.object(learn.score, "run"), \
                mock.patch.object(learn, "auto_learn", side_effect=self.failure()), \
                mock.patch.object(learn, "decide_promotions") as decide, \
                reads.pass_scope(self.journal, "dispatch"), reads.item(operation="learn"):
            learn.learn_pass()

        decide.assert_not_called()
        [skip] = self.rows("read-skip")
        self.assertEqual(skip["operation"], "learn")
        self.assertFalse(any(row.get("event") == "learn" and row.get("status") == "failed"
                             for row in lifecycle.read_events(self.journal)))

    def test_release_read_failure_is_not_converted_to_publish_failed(self) -> None:
        with mock.patch.object(release, "_gh_release", side_effect=self.failure()), \
                self.assertRaises(reads.ReadFailure):
            release._publish(self.cfg, "v1.2.3")
        self.assertEqual(self.rows("release"), [])


if __name__ == "__main__":
    unittest.main()
