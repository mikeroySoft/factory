"""Agent-callable holds preserve exact-head approval without bypassing policy."""
from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest import mock

from factory import config, decisions, dispatch, lifecycle, mcp


class HoldLiftTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cfg = config.Config(root=self.root, repo="acme/widgets")
        self.head = "a" * 40
        self.pr = {
            "number": 17, "title": "Fix parser", "state": "OPEN", "isDraft": False,
            "headRefName": "agent/7", "headRefOid": self.head, "baseRefName": "main",
            "labels": [], "reviewDecision": "", "updatedAt": "2026-11-17T00:00:00Z",
        }
        self.calls = []
        self.check_lock = False
        self.push_on_approve = False
        run = mock.patch.object(decisions, "_run", side_effect=self.transport)
        load = mock.patch.object(mcp.config, "load", return_value=self.cfg)
        run.start()
        load.start()
        self.addCleanup(run.stop)
        self.addCleanup(load.stop)
        for name, value in (("cfg", self.cfg), ("REPO", self.cfg.repo)):
            patcher = mock.patch.object(dispatch, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def labels(self) -> set[str]:
        return {item["name"] for item in self.pr["labels"]}

    def transport(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        if argv[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(argv, 0, "robot-agent\n", "")
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.pr), "")
        if self.check_lock:
            with (
                (self.cfg.factory / "locks" / "merge.lock").open("a") as lock,
                self.assertRaises(BlockingIOError),
            ):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.calls.append(argv)
        labels = self.labels()
        if argv[:3] == ["gh", "pr", "ready"]:
            self.pr["isDraft"] = "--undo" in argv
        elif argv[:3] == ["gh", "pr", "edit"]:
            for flag, label in pairwise(argv):
                if flag == "--add-label":
                    labels.add(label)
                    if label == config.LABEL_APPROVED and self.push_on_approve:
                        self.pr["headRefOid"] = "b" * 40
                elif flag == "--remove-label":
                    labels.discard(label)
            self.pr["labels"] = [{"name": label} for label in sorted(labels)]
        else:
            self.fail(f"unexpected mutation: {argv}")
        self.pr["updatedAt"] = str(len(self.calls))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    def evidence(self, head: str | None = None) -> None:
        head = head or self.head
        for row in (
            {"event": "attempt", "gate": "PASS"},
            {"event": "review", "verdict": "APPROVE", "accepted": True},
        ):
            lifecycle.append(self.cfg.factory / "events.jsonl", {
                **row, "ticket": 7, "head": head, "actual_head": head,
            })

    def events(self, name: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                if row.get("event") == name]

    def prepare(self, name: str, **args) -> dict:
        result = mcp.call_tool("factory_prepare_action", {
            "repository": self.cfg.repo, "action": name, "args": {"number": 17, **args},
        }, {self.cfg.repo: str(self.root)})
        return json.loads(result["content"][0]["text"])

    def apply(self, preview: dict) -> dict:
        self.assertIn("proposal_id", preview, preview)
        result = mcp.call_tool("factory_apply_action", {
            "repository": self.cfg.repo, "proposal_id": preview["proposal_id"],
            "confirmation": preview["confirmation"],
        }, {self.cfg.repo: str(self.root)})
        return json.loads(result["content"][0]["text"])

    def action(self, name: str, **args) -> dict:
        result = self.apply(self.prepare(name, **args))
        self.assertEqual(result["status"], "success", result)
        return result

    def approved_hold(self) -> None:
        self.evidence()
        self.action("approve_pr", add=[config.LABEL_APPROVED])
        self.action("hold_pr", ruling_id="r3-0910")
        self.assertTrue(self.pr["isDraft"])
        self.assertEqual(self.labels(), {"factory-held"})
        held = self.events("held")[-1]
        self.assertEqual((held["approved_head"], held["actor"], held["caller"], held["ruling_id"]),
                         (self.head, "robot-agent", "mcp", "r3-0910"))
        count = len(self.calls)
        self.action("hold_pr")
        self.assertEqual(len(self.calls), count)

    def test_unchanged_hold_lift_restores_approval_and_merge_candidate(self) -> None:
        self.approved_hold()
        self.check_lock = True
        preview = self.prepare("lift_hold", ruling_id="r3-0911")
        receipt = self.apply(preview)
        self.assertEqual(receipt["status"], "success", receipt)
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), {config.LABEL_APPROVED})
        approved = self.events("approved")[-1]
        self.assertEqual((approved["head"], approved["actor"], approved["caller"], approved["source"]),
                         (self.head, "robot-agent", "mcp", "mcp"))
        lifted = self.events("hold-lifted")[-1]
        self.assertTrue(lifted["approval_restored"])
        self.assertEqual((lifted["head"], lifted["ruling_id"]), (self.head, "r3-0911"))
        count = len(self.calls)
        self.assertEqual(self.apply(preview), receipt)
        self.action("lift_hold")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(len(self.events("hold-lifted")), 1)
        with (
            mock.patch.object(dispatch, "cfg", self.cfg),
            mock.patch.object(dispatch, "REPO", self.cfg.repo),
            mock.patch.object(dispatch, "gh_json", return_value=[dict(self.pr)]),
            mock.patch("factory.learn.promote_pass"),
            mock.patch.object(dispatch, "initiative_kind", return_value=True) as candidate,
        ):
            dispatch.merge_pass_locked(True)
        candidate.assert_called_once_with(7)

    def test_moved_head_lifts_without_restoring_even_with_new_head_evidence(self) -> None:
        self.approved_hold()
        self.pr["headRefOid"] = "b" * 40
        self.evidence("b" * 40)
        self.action("lift_hold")
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), set())
        row = self.events("hold-lifted")[-1]
        self.assertFalse(row["approval_restored"])
        self.assertEqual(row["reason"], "head_moved")
        self.assertEqual(row["approved_head"], self.head)

    def test_missing_or_mismatched_review_evidence_lifts_without_approval(self) -> None:
        for evidence_head in (None, "b" * 40):
            with self.subTest(evidence_head=evidence_head):
                self.pr["labels"] = [{"name": "factory-held"}]
                self.pr["isDraft"] = True
                path = self.cfg.factory / "events.jsonl"
                path.unlink(missing_ok=True)
                lifecycle.append(path, {"event": "held", "pr": 17, "ticket": 7,
                                        "head": self.head, "approved_head": self.head})
                if evidence_head:
                    self.evidence(evidence_head)
                self.action("lift_hold")
                self.assertEqual(self.labels(), set())
                self.assertFalse(self.pr["isDraft"])
                self.assertEqual(self.events("hold-lifted")[-1]["reason"], "approval_evidence")

    def test_unrecorded_hold_cannot_restore_approval(self) -> None:
        self.evidence()
        self.pr["labels"] = [{"name": "factory-held"}]
        self.pr["isDraft"] = True
        self.action("lift_hold")
        self.assertEqual(self.labels(), set())
        self.assertEqual(self.events("hold-lifted")[-1]["reason"], "hold_evidence_missing")

    def test_lift_refuses_busy_merge_lock_and_stale_proposal(self) -> None:
        self.approved_hold()
        preview = self.prepare("lift_hold")
        count = len(self.calls)
        with (self.cfg.factory / "locks" / "merge.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.prepare("lift_hold")["error"]["code"], "target_busy")
            result = self.apply(preview)
        self.assertEqual(result["error"]["code"], "target_busy")
        self.assertEqual(len(self.calls), count)
        self.pr["headRefOid"] = "b" * 40
        self.assertEqual(self.apply(preview)["error"]["code"], "stale_target")
        self.assertEqual(len(self.calls), count)

    def test_mcp_strict_approval_refuses_evidence_drift_and_other_labels(self) -> None:
        self.assertEqual(self.prepare("approve_pr", add=[config.LABEL_APPROVED])["error"]["code"],
                         "approval_evidence")
        self.evidence("b" * 40)
        self.assertEqual(self.prepare("approve_pr", add=[config.LABEL_APPROVED])["error"]["code"],
                         "approval_evidence")
        for args in ({"add": ["ready-for-agent"]}, {"remove": [config.LABEL_APPROVED]},
                     {"add": [config.LABEL_APPROVED, "wontfix"]}, {"add": "factory-approved"},
                     {}, {"comment": "not an approval effect"}):
            self.assertEqual(self.prepare("approve_pr", **args)["error"]["code"], "invalid_args")
        self.evidence()
        preview = self.prepare("approve_pr", add=[config.LABEL_APPROVED])
        self.pr["headRefOid"] = "c" * 40
        self.assertEqual(self.apply(preview)["error"]["code"], "stale_target")
        self.assertEqual(self.calls, [])

    def test_strict_approval_rechecks_evidence_at_apply_and_withdraws_racing_push(self) -> None:
        self.evidence()
        preview = self.prepare("approve_pr", add=[config.LABEL_APPROVED])
        (self.cfg.factory / "events.jsonl").unlink()
        self.assertEqual(self.apply(preview)["error"]["code"], "approval_evidence")
        self.assertEqual(self.calls, [])
        self.evidence()
        self.push_on_approve = True
        result = self.apply(self.prepare("approve_pr", add=[config.LABEL_APPROVED]))
        self.assertEqual(result["status"], "failure")
        self.assertNotIn(config.LABEL_APPROVED, self.labels())
        self.assertEqual(self.events("approved"), [])

    def test_explicit_approval_can_lift_draft_hold_but_refuses_missing_evidence(self) -> None:
        self.approved_hold()
        args = {"add": [config.LABEL_APPROVED], "remove": ["factory-held"]}
        self.action("approve_pr", **args)
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), {config.LABEL_APPROVED})
        self.assertEqual(self.events("approved")[-1]["caller"], "mcp")
        self.action("hold_pr")
        (self.cfg.factory / "events.jsonl").unlink()
        count = len(self.calls)
        self.assertEqual(self.prepare("approve_pr", **args)["error"]["code"], "approval_evidence")
        self.assertTrue(self.pr["isDraft"])
        self.assertEqual(self.labels(), {"factory-held"})
        self.assertEqual(len(self.calls), count)

    def test_dashboard_hold_and_lift_use_the_same_policy_and_snapshot(self) -> None:
        self.evidence()
        for request in (
            {"add": [config.LABEL_APPROVED]},
            {"add": ["factory-held"]},
            {"remove": ["factory-held"]},
        ):
            preview = decisions.prepare(self.cfg, [{"op": "pr", "number": 17, **request}])
            result = decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
            self.assertEqual(result["status"], "success", result)
        self.assertEqual(self.labels(), {config.LABEL_APPROVED})
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.events("approved")[-1]["caller"], "dashboard")

    def test_hold_does_not_rebind_an_old_approval_to_the_current_head(self) -> None:
        self.evidence()
        self.action("approve_pr", add=[config.LABEL_APPROVED])
        self.pr["headRefOid"] = "b" * 40
        self.evidence("b" * 40)
        self.action("hold_pr")
        self.assertEqual(self.events("held")[-1]["approved_head"], self.head)
        self.action("lift_hold")
        self.assertEqual(self.labels(), set())
        self.assertEqual(self.events("hold-lifted")[-1]["reason"], "head_moved")


if __name__ == "__main__":
    unittest.main()
