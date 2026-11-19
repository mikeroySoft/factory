"""#855: a source dispatcher shares existing approvals without reapproval."""
from __future__ import annotations

import fcntl
import json
import unittest
from unittest import mock

from factory import decisions, dispatch, lifecycle
from tests import test_cross_host_decisions as cross


class ApprovalBackfillTest(unittest.TestCase):
    def setUp(self):
        self.case = cross.CrossHostDecisionTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.share()
        self.comment_metadata = {key: value for key, value in self.case.comments[0].items() if key != "body"}
        self.case.comments = []
        self.commands = []
        self.pr = {**self.case.driver.pr, "files": []}
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        self.enterContext(mock.patch.object(dispatch.config, "load", return_value=self.case.source))
        self.enterContext(mock.patch.object(dispatch.config, "engine_commit", return_value=None))
        self.enterContext(mock.patch.object(dispatch.config, "engine_target", return_value=None))
        self.enterContext(mock.patch.object(dispatch, "pin_root", return_value=[]))
        self.enterContext(mock.patch.object(dispatch, "github_preflight", return_value=None))
        for stage in ("_sweep_stale_factory_tempdirs", "worker_health_pass", "land_pass",
                      "review_intake_pass", "ticket_pass"):
            self.enterContext(mock.patch.object(dispatch, stage))
        self.enterContext(mock.patch("factory.manage.manage_pass"))
        self.enterContext(mock.patch("factory.learn.learn_pass"))
        self.enterContext(mock.patch("factory.results.prune", return_value={"status": "complete"}))
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.read_provider))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.publish))
        self.enterContext(mock.patch.object(dispatch, "approve_pr", side_effect=AssertionError("reapproval")))

    def read_provider(self, argv, **kwargs):
        if argv[:2] == ["pr", "list"]:
            return [self.pr]
        if argv[:2] == ["pr", "view"]:
            return self.pr
        raise AssertionError(argv)

    def publish(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[:3] != ["gh", "pr", "comment"]:
            raise AssertionError(argv)
        body = argv[argv.index("--body") + 1]
        at = json.loads(body.partition("\n")[2])["at"]
        self.case.comments.append({**self.comment_metadata, "body": body,
                                   "created_at": at, "updated_at": at})
        return self.case.driver.completed(argv)

    def test_existing_remote_journal_approval_reaches_dashboard_without_reapproval(self):
        self.assertEqual(lifecycle.read_events(self.case.cfg.factory / "events.jsonl"), [])
        self.assertEqual(dispatch.main([]), 0)
        preview = self.case.prepare()
        self.assertEqual(preview["targets"][0]["head"], self.case.head)
        self.assertEqual(self.case.driver.pr["labels"], [])  # Sharing does not add approval.
        self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(len(self.commands), 1)  # No receipt spam on subsequent passes.
        self.assertTrue(self.case.driver.apply(preview)["ok"])

    def test_invalid_existing_approval_is_not_published(self):
        for defect in ("head", "gate_head", "review_head", "gate", "review", "veto", "draft", "base"):
            with self.subTest(defect=defect):
                path = self.case.source.factory / "events.jsonl"
                path.unlink()
                self.case.share()
                self.case.comments = []
                self.commands.clear()
                self.pr = {**self.case.driver.pr, "files": []}
                if defect in {"head", "gate_head", "review_head"}:
                    lifecycle.append(path, {"event": "approved", "ticket": 7, "pr": 17,
                                           "head": self.case.head, "gate_head": self.case.head,
                                           "review_head": self.case.head, defect: "b" * 40})
                elif defect == "gate":
                    lifecycle.append(path, {"event": "attempt", "ticket": 7,
                                           "head": self.case.head, "actual_head": self.case.head,
                                           "gate": "FAIL"})
                elif defect == "review":
                    lifecycle.append(path, {"event": "review", "ticket": 7,
                                           "head": self.case.head, "actual_head": self.case.head,
                                           "verdict": "REVISE", "accepted": True})
                else:
                    key, value = {"veto": ("reviewDecision", "CHANGES_REQUESTED"),
                                  "draft": ("isDraft", True), "base": ("baseRefName", "other")}[defect]
                    self.pr[key] = value
                self.assertEqual(dispatch.main([]), 0)
                self.assertEqual(self.commands, [])
                with self.assertRaises(decisions.DecisionError) as caught:
                    self.case.prepare()
                self.assertEqual(caught.exception.code, "approval_evidence")

    def test_backfill_skips_busy_source_and_dry_run(self):
        self.assertEqual(dispatch.main(["--dry-run"]), 0)
        dispatch.configure(self.case.source)
        with dispatch.ticket_lock(7).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.case.comments, [])
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
