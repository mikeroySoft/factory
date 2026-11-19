"""#855: published receipts cannot resurrect withdrawn source approval."""
from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from factory import config, decisions, dispatch, lifecycle
from tests import test_approval_backfill as backfill
from tests import test_cross_host_decisions as cross


class ApprovalRevocationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = backfill.ApprovalBackfillTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.fixture.pr["labels"] = [{"name": config.LABEL_APPROVED}]
        self.provider = self.fixture.read_provider
        dispatch.gh_json.side_effect = self.read_provider
        self.publisher = self.fixture.publish
        dispatch.run.side_effect = self.publish
        self.edits = []

    def read_provider(self, argv, **kwargs):
        if argv == ["api", "user"]:
            return {"login": "factory"}
        if argv[:2] == ["api", "--paginate"]:
            return [[{**comment, "id": 123} for comment in self.case.comments]]
        return self.provider(argv, **kwargs)

    def publish(self, argv, **kwargs):
        if argv[:3] == ["gh", "api", "--method"]:
            self.edits.append(argv)
            self.case.comments[0]["body"] = argv[argv.index("-f") + 1].removeprefix("body=")
            return self.case.driver.completed(argv)
        return self.publisher(argv, **kwargs)

    def test_source_revocation_prevents_prepare_apply_and_republication(self):
        for defect in ("revise", "review-result", "label", "head"):
            for phase in ("prepare", "apply"):
                with self.subTest(defect=defect, phase=phase):
                    path = self.case.source.factory / "events.jsonl"
                    path.unlink(missing_ok=True)
                    self.case.share()
                    self.case.comments = []
                    self.fixture.commands.clear()
                    self.fixture.pr.update(headRefOid=self.case.head,
                                           labels=[{"name": config.LABEL_APPROVED}])
                    self.assertEqual(dispatch.main([]), 0)
                    preview = self.case.prepare() if phase == "apply" else None
                    if defect in {"revise", "review-result"}:
                        lifecycle.append(path, {"event": "review" if defect == "revise" else defect,
                                               "ticket": 7, "pr": 17, "head": self.case.head,
                                               "actual_head": self.case.head, "verdict": "REVISE",
                                               "accepted": True, "at": "2026-11-18T19:16:00Z"})
                    elif defect == "label":
                        self.fixture.pr["labels"] = []
                    else:
                        self.fixture.pr["headRefOid"] = "b" * 40
                    self.assertEqual(dispatch.main([]), 0)
                    with self.assertRaises(decisions.DecisionError) as caught:
                        self.case.driver.apply(preview) if preview else self.case.prepare()
                    self.assertEqual(caught.exception.code, "approval_evidence")
                    self.fixture.pr.update(headRefOid=self.case.head,
                                           labels=[{"name": config.LABEL_APPROVED}])
                    self.assertEqual(dispatch.main([]), 0)
                    self.assertEqual(len(self.fixture.commands), 1)
                    self.assertEqual(self.case.driver.calls, [])

    def test_source_local_revise_cannot_fall_back_to_its_own_published_receipt(self):
        self.case.share()
        lifecycle.append(self.case.source.factory / "events.jsonl", {
            "event": "review", "ticket": 7, "head": self.case.head,
            "actual_head": self.case.head, "verdict": "REVISE", "accepted": True,
            # Journal order is authoritative even if the source clock went backwards.
            "at": "2026-11-18T19:14:00Z",
        })
        self.case.cfg.factory.mkdir(exist_ok=True)
        self.case.cfg.root = self.case.source.root
        self.case.cfg.host_name = "ultron"
        with self.assertRaises(decisions.DecisionError) as caught:
            self.case.prepare()
        self.assertEqual(caught.exception.code, "approval_evidence")

    def test_failed_receipt_post_escalates_and_other_dispatch_work_continues(self):
        for failure in (subprocess.CalledProcessError(1, ["gh"], stderr="denied"),
                        subprocess.TimeoutExpired(["gh"], 1), OSError("offline")):
            with self.subTest(failure=type(failure).__name__):
                dispatch.run.side_effect = failure
                with (mock.patch.object(dispatch, "escalate") as escalate,
                      mock.patch.object(dispatch, "log") as log):
                    self.assertEqual(dispatch.main([]), 0)
                escalate.assert_called_once()
                self.assertEqual(escalate.call_args.args[0], 7)
                self.assertIn("approval", " ".join(str(call) for call in log.call_args_list))
                dispatch.land_pass.assert_called()
                dispatch.ticket_pass.assert_called()
                self.assertFalse(any(row["event"] == "approval-published" for row in
                                     lifecycle.read_events(self.case.source.factory / "events.jsonl")))


class MergeReceiptGuardTest(unittest.TestCase):
    def setUp(self):
        self.case = cross.CrossHostDecisionTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_future_and_replayed_receipts_never_replace_local_merge_revise(self):
        for defect in ("future", "replay"):
            with self.subTest(defect=defect):
                receipt = self.case.share()
                if defect == "future":
                    receipt["at"] = "2099-01-01T00:00:00Z"
                else:
                    receipt["at"] = "2026-11-18T19:17:00Z"
                    self.case.comments[0].update(created_at="2026-11-18T19:17:01Z",
                                                 updated_at="2026-11-18T19:17:01Z")
                self.case.comments[0]["body"] = "Factory approval v1\n" + json.dumps(receipt)
                local = [{"event": "review", "ticket": 7, "head": self.case.head,
                          "verdict": "REVISE", "at": "2026-11-18T19:16:00Z"}]
                target = {"ticket": 7, "number": 17, "head": self.case.head}
                events = decisions._shared_approval_events(self.case.cfg, target, local, float("inf"))
                self.assertEqual(dispatch.latest_head_review_verdict(events, 7, 17, self.case.head), "REVISE")
                self.assertFalse(dispatch._head_evidence_matches(events, 7, self.case.head))


if __name__ == "__main__":
    unittest.main()
