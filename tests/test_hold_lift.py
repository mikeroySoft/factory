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

    def test_batch_hold_transitions_observe_previous_effects(self) -> None:
        self.approved_hold()
        for transitions, held in (
            (("remove", "add"), True),
            (("add", "remove"), False),
        ):
            with self.subTest(transitions=transitions):
                preview = decisions.prepare(self.cfg, [
                    {"op": "pr", "number": 17, transition: [config.LABEL_HELD]}
                    for transition in transitions
                ])
                result = decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
                self.assertEqual(result["status"], "success", result)
                self.assertEqual(self.pr["isDraft"], held)
                self.assertEqual(self.labels(), {config.LABEL_HELD if held else config.LABEL_APPROVED})

    def test_lift_racing_push_preserves_current_head_audit(self) -> None:
        self.approved_hold()
        self.check_lock = True
        self.push_on_approve = True
        preview = self.prepare("lift_hold", ruling_id="r3-race")
        result = self.apply(preview)
        self.assertEqual(result["status"], "failure", result)
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), set())
        lifted = self.events("hold-lifted")
        self.assertEqual(len(lifted), 1)
        self.assertEqual(
            {key: lifted[0][key] for key in (
                "head", "approved_head", "approval_restored", "reason", "actor", "caller", "ruling_id",
            )},
            {"head": "b" * 40, "approved_head": self.head, "approval_restored": False,
             "reason": "head_moved", "actor": "robot-agent", "caller": "mcp", "ruling_id": "r3-race"},
        )
        count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.action("lift_hold")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.events("hold-lifted"), lifted)

    def test_lift_uncertain_approval_preserves_audit_and_receipt(self) -> None:
        self.approved_hold()
        with mock.patch.object(decisions, "_approve", side_effect=decisions._AmbiguousCommand("lost result")):
            result = self.apply(self.prepare("lift_hold"))
        self.assertEqual(result["status"], "uncertain", result)
        self.assertFalse(self.pr["isDraft"])
        self.assertNotIn(config.LABEL_HELD, self.labels())
        lifted = self.events("hold-lifted")[-1]
        self.assertEqual(lifted["head"], self.head)
        self.assertEqual(lifted["reason"], "approval_uncertain")
        self.assertFalse(lifted["approval_restored"])

    def test_lift_post_removal_read_failure_keeps_audit_and_receipt(self) -> None:
        self.approved_hold()
        preview = self.prepare("lift_hold", ruling_id="r3-read-failure")
        read_target = decisions._pr_target

        def fail_after_removal(*args):
            if config.LABEL_HELD not in self.labels():
                raise decisions.DecisionError("post-lift provider read failed", "provider_unavailable")
            return read_target(*args)

        with mock.patch.object(decisions, "_pr_target", side_effect=fail_after_removal):
            result = self.apply(preview)
        self.assertEqual(result["status"], "uncertain", result)
        self.assertEqual(result["steps"][-1]["output"], "post-lift provider read failed")
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), set())
        lifted = self.events("hold-lifted")
        self.assertEqual(len(lifted), 1)
        self.assertIsNone(lifted[0]["head"])
        self.assertFalse(lifted[0]["approval_restored"])
        self.assertEqual(lifted[0]["reason"], "approval_uncertain")
        self.assertEqual(
            (lifted[0]["actor"], lifted[0]["caller"], lifted[0]["ruling_id"]),
            ("robot-agent", "mcp", "r3-read-failure"),
        )
        count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.action("lift_hold")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.events("hold-lifted"), lifted)

    def test_lift_policy_rejection_is_a_successful_unapproved_lift(self) -> None:
        self.approved_hold()
        preview = self.prepare("lift_hold")
        with mock.patch.object(
            decisions, "_approval_policy",
            side_effect=decisions.DecisionError("exact-head evidence disappeared", "approval_evidence"),
        ):
            result = self.apply(preview)
        self.assertEqual(result["status"], "success", result)
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), set())
        self.assertEqual(len(self.events("approved")), 1)
        lifted = self.events("hold-lifted")[-1]
        self.assertEqual(lifted["head"], self.head)
        self.assertEqual(lifted["reason"], "approval_evidence")
        self.assertFalse(lifted["approval_restored"])

    def test_lift_failed_stale_withdrawal_is_not_a_policy_refusal(self) -> None:
        self.approved_hold()
        read_target = decisions._pr_target
        for observable in (True, False):
            with self.subTest(observable=observable):
                self.pr["headRefOid"] = self.head
                self.pr["isDraft"] = True
                self.pr["labels"] = [{"name": config.LABEL_HELD}]
                lifecycle.append(self.cfg.factory / "events.jsonl", {
                    "event": "held", "ticket": 7, "pr": 17,
                    "head": self.head, "approved_head": self.head,
                })
                preview = self.prepare("lift_hold")
                self.push_on_approve = True
                withdrawals = []

                def fail_withdrawal(*args, withdrawals=withdrawals):
                    withdrawals.append(True)
                    raise decisions.DecisionError("withdrawal deadline expired", "deadline")

                def observe_target(*args, observable=observable, withdrawals=withdrawals):
                    if withdrawals and not observable:
                        raise decisions.DecisionError("audit read unavailable", "provider_unavailable")
                    return read_target(*args)

                before = len(self.events("hold-lifted"))
                with (
                    mock.patch.object(decisions, "_withdraw_approval", side_effect=fail_withdrawal),
                    mock.patch.object(decisions, "_pr_target", side_effect=observe_target),
                ):
                    result = self.apply(preview)
                self.assertTrue(withdrawals)
                self.assertEqual(result["status"], "uncertain", result)
                self.assertEqual(result["steps"][-1]["output"], "withdrawal deadline expired")
                self.assertFalse(self.pr["isDraft"])
                self.assertEqual(self.labels(), {config.LABEL_APPROVED})
                lifted = self.events("hold-lifted")
                self.assertEqual(len(lifted), before + 1)
                self.assertEqual(lifted[-1]["head"], "b" * 40 if observable else None)
                self.assertEqual(lifted[-1]["reason"], "approval_uncertain")
                self.assertFalse(lifted[-1]["approval_restored"])
                self.assertEqual(self.apply(preview), result)

    def test_lift_ambiguous_removal_reconciles_completed_lift_and_keeps_receipt(self) -> None:
        self.approved_hold()
        self.check_lock = True
        preview = self.prepare("lift_hold", ruling_id="r3-transport-failure")
        transport = self.transport

        def lose_removal_result(argv, **kwargs):
            result = transport(argv, **kwargs)
            if argv[:3] == ["gh", "pr", "edit"] and config.LABEL_HELD in argv:
                raise decisions._AmbiguousCommand("hold removal transport timed out")
            return result

        with mock.patch.object(decisions, "_run", side_effect=lose_removal_result):
            result = self.apply(preview)
        self.assertEqual(result["status"], "uncertain", result)
        self.assertEqual(result["steps"][-1]["output"], "hold removal transport timed out")
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), set())
        lifted = self.events("hold-lifted")
        self.assertEqual(len(lifted), 1)
        self.assertEqual(
            {key: lifted[0][key] for key in (
                "head", "approved_head", "approval_restored", "reason", "actor", "caller", "ruling_id",
            )},
            {"head": self.head, "approved_head": self.head, "approval_restored": False,
             "reason": "hold_removal_uncertain", "actor": "robot-agent", "caller": "mcp",
             "ruling_id": "r3-transport-failure"},
        )
        count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.action("lift_hold")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.events("hold-lifted"), lifted)

    def test_lift_ambiguous_removal_does_not_audit_an_unconfirmed_lift(self) -> None:
        self.approved_hold()
        for readable in (True, False):
            with self.subTest(readable=readable):
                preview = self.prepare("lift_hold")
                transport = self.transport
                attempted = []

                def fail_removal(argv, readable=readable, attempted=attempted, transport=transport, **kwargs):
                    if argv[:3] == ["gh", "pr", "edit"] and config.LABEL_HELD in argv:
                        attempted.append(True)
                        return subprocess.CompletedProcess(argv, 1, "", "hold removal failed")
                    if attempted and not readable and argv[:3] == ["gh", "pr", "view"]:
                        return subprocess.CompletedProcess(argv, 1, "", "provider unavailable")
                    return transport(argv, **kwargs)

                with mock.patch.object(decisions, "_run", side_effect=fail_removal):
                    result = self.apply(preview)
                self.assertTrue(attempted)
                self.assertEqual(result["status"], "uncertain", result)
                self.assertEqual(result["steps"][-1]["output"], "hold removal failed")
                self.assertEqual(self.labels(), {config.LABEL_HELD})
                self.assertEqual(self.events("hold-lifted"), [])
                self.assertEqual(self.apply(preview), result)

    def test_hold_ambiguous_application_preserves_snapshot_for_lift(self) -> None:
        self.evidence()
        self.action("approve_pr", add=[config.LABEL_APPROVED])
        self.check_lock = True
        preview = self.prepare("hold_pr", ruling_id="r3-hold-transport")
        transport = self.transport

        def lose_hold_result(argv, **kwargs):
            result = transport(argv, **kwargs)
            if argv[:3] == ["gh", "pr", "edit"] and config.LABEL_HELD in argv:
                raise decisions._AmbiguousCommand("hold application transport timed out")
            return result

        with mock.patch.object(decisions, "_run", side_effect=lose_hold_result):
            result = self.apply(preview)
        self.assertEqual(result["status"], "uncertain", result)
        self.assertEqual(result["steps"][-1]["output"], "hold application transport timed out")
        self.assertTrue(self.pr["isDraft"])
        self.assertEqual(self.labels(), {config.LABEL_HELD})
        held = self.events("held")
        self.assertEqual(len(held), 1)
        self.assertEqual(
            {key: held[0][key] for key in (
                "head", "approved_head", "approval_restored", "reason", "actor", "caller", "ruling_id",
            )},
            {"head": self.head, "approved_head": self.head, "approval_restored": False,
             "reason": "hold_application_uncertain", "actor": "robot-agent", "caller": "mcp",
             "ruling_id": "r3-hold-transport"},
        )
        count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.action("hold_pr")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.events("held"), held)
        self.action("lift_hold")
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), {config.LABEL_APPROVED})
        self.assertTrue(self.events("hold-lifted")[-1]["approval_restored"])
        self.assertEqual(self.events("approved")[-1]["caller"], "mcp")
        self.assertEqual(self.apply(preview), result)

    def test_hold_ambiguous_application_does_not_audit_unconfirmed_completion(self) -> None:
        self.evidence()
        for state in ("unheld", "unreadable", "approved", "ready"):
            with self.subTest(state=state):
                self.pr["isDraft"] = False
                self.pr["labels"] = []
                self.action("approve_pr", add=[config.LABEL_APPROVED])
                preview = self.prepare("hold_pr")
                attempted = []
                transport = self.transport

                def fail_hold(argv, state=state, attempted=attempted, transport=transport, **kwargs):
                    if argv[:3] == ["gh", "pr", "edit"] and config.LABEL_HELD in argv:
                        attempted.append(True)
                        if state in {"approved", "ready"}:
                            transport(argv, **kwargs)
                            if state == "approved":
                                self.pr["labels"].append({"name": config.LABEL_APPROVED})
                            else:
                                self.pr["isDraft"] = False
                        return subprocess.CompletedProcess(argv, 1, "", "hold application failed")
                    if attempted and state == "unreadable" and argv[:3] == ["gh", "pr", "view"]:
                        return subprocess.CompletedProcess(argv, 1, "", "provider unavailable")
                    return transport(argv, **kwargs)

                with mock.patch.object(decisions, "_run", side_effect=fail_hold):
                    result = self.apply(preview)
                self.assertTrue(attempted)
                self.assertEqual(result["status"], "uncertain", result)
                self.assertEqual(result["steps"][-1]["output"], "hold application failed")
                self.assertEqual(self.events("held"), [])
                self.assertEqual(self.apply(preview), result)

    def test_hold_retry_recovers_snapshot_after_response_and_reconciliation_loss(self) -> None:
        self.evidence()
        self.action("approve_pr", add=[config.LABEL_APPROVED])
        self.check_lock = True
        preview = self.prepare("hold_pr", ruling_id="r3-disconnected-hold")
        attempted = []
        transport = self.transport

        def disconnect_after_hold(argv, **kwargs):
            if attempted and argv[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(argv, 1, "", "provider unavailable")
            result = transport(argv, **kwargs)
            if argv[:3] == ["gh", "pr", "edit"] and config.LABEL_HELD in argv:
                attempted.append(True)
                raise decisions._AmbiguousCommand("hold response lost")
            return result

        with mock.patch.object(decisions, "_run", side_effect=disconnect_after_hold):
            result = self.apply(preview)
        self.assertEqual(result["status"], "uncertain", result)
        self.assertEqual(result["steps"][-1]["output"], "hold response lost")
        self.assertEqual(self.events("held"), [])
        self.assertTrue(self.pr["isDraft"])
        self.assertEqual(self.labels(), {config.LABEL_HELD})
        count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.action("hold_pr", ruling_id="r3-retry")
        self.assertEqual(len(self.calls), count)
        held = self.events("held")
        self.assertEqual(len(held), 1)
        self.assertEqual(
            (held[0]["approved_head"], held[0]["actor"], held[0]["caller"], held[0]["ruling_id"]),
            (self.head, "robot-agent", "mcp", "r3-disconnected-hold"),
        )
        self.action("hold_pr")
        self.assertEqual(self.events("held"), held)
        self.action("lift_hold")
        self.assertEqual(self.labels(), {config.LABEL_APPROVED})
        self.assertTrue(self.events("hold-lifted")[-1]["approval_restored"])
        self.assertEqual(self.apply(preview), result)

    def test_lift_policy_journal_read_oserror_keeps_audit_and_receipt(self) -> None:
        self.approved_hold()
        preview = self.prepare("lift_hold", ruling_id="r3-journal-read")
        read_events = lifecycle.read_events

        def fail_policy_read(*args, **kwargs):
            if config.LABEL_HELD not in self.labels():
                raise OSError("approval journal temporarily unreadable")
            return read_events(*args, **kwargs)

        with mock.patch.object(lifecycle, "read_events", side_effect=fail_policy_read):
            result = self.apply(preview)
        self.assertEqual(result["status"], "uncertain", result)
        self.assertEqual(result["steps"][-1]["output"], "approval journal temporarily unreadable")
        self.assertFalse(self.pr["isDraft"])
        self.assertEqual(self.labels(), set())
        lifted = self.events("hold-lifted")
        self.assertEqual(len(lifted), 1)
        self.assertEqual(
            (lifted[0]["head"], lifted[0]["approval_restored"], lifted[0]["reason"],
             lifted[0]["actor"], lifted[0]["caller"], lifted[0]["ruling_id"]),
            (self.head, False, "approval_uncertain", "robot-agent", "mcp", "r3-journal-read"),
        )
        count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.action("lift_hold")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.events("hold-lifted"), lifted)

    def test_pending_hold_recovery_requires_all_effects_and_preserves_head_binding(self) -> None:
        self.evidence()
        path = self.cfg.factory / "events.jsonl"
        for state in ("approved", "ready", "unchanged", "moved"):
            with self.subTest(state=state):
                self.pr["headRefOid"] = "b" * 40 if state == "moved" else self.head
                self.pr["isDraft"] = state != "ready"
                self.pr["labels"] = [{"name": config.LABEL_HELD}]
                if state == "approved":
                    self.pr["labels"].append({"name": config.LABEL_APPROVED})
                lifecycle.append(path, {
                    "event": "hold-started", "pr": 17, "ticket": 7,
                    "head": self.head, "approved_head": self.head,
                    "actor": "original-agent", "caller": "mcp", "ruling_id": "r3-original",
                })
                before = len(self.events("held"))
                if state in {"approved", "ready"}:
                    result = self.apply(self.prepare("hold_pr"))
                    self.assertEqual(result["status"], "uncertain", result)
                    self.assertEqual(len(self.events("held")), before)
                else:
                    self.action("lift_hold")
                    self.assertEqual(len(self.events("held")), before + 1)
                    held = self.events("held")[-1]
                    self.assertEqual((held["actor"], held["ruling_id"]), ("original-agent", "r3-original"))
                    lifted = self.events("hold-lifted")[-1]
                    self.assertEqual(lifted["approved_head"], self.head)
                    self.assertEqual(lifted["approval_restored"], state == "unchanged")
                    self.assertEqual(self.labels(), {config.LABEL_APPROVED} if state == "unchanged" else set())


if __name__ == "__main__":
    unittest.main()
