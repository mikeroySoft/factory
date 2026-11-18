"""#855: dashboard decisions consume authenticated exact-head host evidence."""
from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from factory import config, decisions, dispatch, lifecycle, workspace
from tests import test_decisions as base
from tests import test_risk_merge


class CrossHostDecisionTest(unittest.TestCase):
    def setUp(self):
        self.driver = base.DecisionTest()
        self.driver.setUp()
        self.addCleanup(self.driver.doCleanups)
        self.cfg = self.driver.cfg
        self.cfg.host_name = "tank"
        self.cfg.install["dispatch_env"] = {"GH_TOKEN": "dispatcher-test-token"}
        self.head = self.driver.pr["headRefOid"]
        self.source = config.Config(self.driver.root.parent / "ultron", self.cfg.repo, host_name="ultron")
        self.comments = []
        decisions._run.side_effect = self.transport
        self.request = {"op": "pr", "number": 17, "add": [config.LABEL_APPROVED]}

    def transport(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"] and argv[-1] == "user":
            login = "factory" if kwargs["env"].get("GH_TOKEN") == "dispatcher-test-token" else "alice"
            return self.driver.completed(argv, out=json.dumps({"login": login}))
        if argv[:2] == ["gh", "api"] and argv[-1] == f"repos/{self.cfg.repo}/issues/17/comments":
            return self.driver.completed(argv, out=json.dumps([self.comments]))
        return self.driver.transport(argv, **kwargs)

    def share(self):
        for row in (
            {"event": "attempt", "gate": "PASS", "head": self.head, "actual_head": self.head},
            {"event": "review", "verdict": "APPROVE", "accepted": True,
             "head": self.head, "actual_head": self.head},
            {"event": "approved", "pr": 17, "head": self.head,
             "gate_head": self.head, "review_head": self.head},
        ):
            lifecycle.append(self.source.factory / "events.jsonl", {
                **row, "ticket": 7, "at": "2026-11-18T19:15:23Z",
            })
        rows = lifecycle.read_events(self.source.factory / "events.jsonl")
        receipt = {**rows[-1], "host": "ultron", "evidence": rows[:-1]}
        self.comments = [{"user": {"login": "factory"},
                          "body": "Factory approval v1\n" + json.dumps(receipt)}]
        return receipt

    def prepare(self):
        # The dashboard API delegates to this exact workspace entry point.
        target = SimpleNamespace(repository_config=lambda *args, **kwargs: self.cfg)
        return workspace.Workspace.prepare(target, self.cfg.repo, {
            "repository": self.cfg.repo, "requests": [self.request],
        })

    def test_dashboard_prepare_accepts_remote_only_exact_head_evidence(self):
        self.share()
        self.assertEqual(lifecycle.read_events(self.cfg.factory / "events.jsonl"), [])
        preview = self.prepare()
        self.assertEqual(preview["targets"][0]["head"], self.head)
        self.assertEqual(preview["caller"], "dashboard")
        receipt = self.driver.apply(preview)
        self.assertTrue(receipt["ok"], receipt)
        self.assertIn(config.LABEL_APPROVED, {row["name"] for row in self.driver.pr["labels"]})
        # A local approval receipt is not a later gate/review refusal.
        self.assertEqual(self.prepare()["targets"][0]["head"], self.head)

    def test_newer_remote_approval_supersedes_older_local_revise(self):
        self.share()
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "review", "ticket": 7, "head": self.head, "actual_head": self.head,
            "verdict": "REVISE", "accepted": True, "at": "2026-11-18T19:14:00Z",
        })
        self.assertEqual(self.prepare()["targets"][0]["head"], self.head)

    def test_missing_wrong_head_or_untrusted_evidence_refuses(self):
        original = self.share()
        for defect in ("missing", "head", "gate_head", "review_head", "ticket", "pr",
                       "gate", "review", "actual_head", "verdict", "author", "malformed"):
            with self.subTest(defect=defect):
                receipt = copy.deepcopy(original)
                author = "factory"
                if defect in {"head", "gate_head", "review_head"}:
                    receipt[defect] = "b" * 40
                elif defect in {"ticket", "pr"}:
                    receipt[defect] += 1
                elif defect in {"gate", "review"}:
                    kind = "attempt" if defect == "gate" else "review"
                    receipt["evidence"] = [row for row in receipt["evidence"] if row["event"] != kind]
                elif defect in {"actual_head", "verdict"}:
                    receipt["evidence"][1][defect] = "b" * 40 if defect == "actual_head" else "REVISE"
                elif defect == "author":
                    author = "alice"  # The human decision actor is not the dispatcher.
                elif defect == "malformed":
                    receipt = []
                self.comments = [] if defect == "missing" else [{
                    "user": {"login": author}, "body": "Factory approval v1\n" + json.dumps(receipt),
                }]
                with self.assertRaises(decisions.DecisionError) as caught:
                    self.prepare()
                self.assertEqual(caught.exception.code, "approval_evidence")
                self.assertEqual(self.driver.calls, [])

    def test_shared_evidence_is_rechecked_before_apply(self):
        self.share()
        preview = self.prepare()
        self.comments = []
        with self.assertRaises(decisions.DecisionError) as caught:
            self.driver.apply(preview)
        self.assertEqual(caught.exception.code, "approval_evidence")
        self.assertEqual(self.driver.calls, [])

    def test_human_veto_and_newer_local_refusal_remain_blocking(self):
        self.share()
        self.driver.pr["reviewDecision"] = "CHANGES_REQUESTED"
        with self.assertRaises(decisions.DecisionError) as veto:
            self.prepare()
        self.assertEqual(veto.exception.code, "approval_scope")
        self.driver.pr["reviewDecision"] = ""
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "review", "ticket": 7, "head": self.head, "actual_head": self.head,
            "verdict": "REVISE", "accepted": True, "at": "2026-11-18T19:16:00Z",
        })
        with self.assertRaises(decisions.DecisionError) as refused:
            self.prepare()
        self.assertEqual(refused.exception.code, "approval_evidence")
        self.assertEqual(self.driver.calls, [])

    def test_manager_all_still_requires_exact_head_manager_evidence(self):
        self.share()
        self.cfg.manager = ["manager"]
        self.cfg.manager_review = "all"
        with self.assertRaises(decisions.DecisionError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "approval_evidence")
        self.assertIn("manager", str(caught.exception))

    def test_high_risk_receipt_requires_configured_mutation_proof(self):
        receipt = self.share()
        receipt["evidence"] = test_risk_merge.high_events(head=self.head)
        self.cfg.checks = [config.Check("mutation", ["mutation"], when="pr", proof=config.MUTATION_PROOF)]
        for valid in (False, True):
            with self.subTest(valid=valid):
                for row in receipt["evidence"]:
                    if row["event"] == "risk-check" and valid:
                        row.update(mutation_scope="1" * 64, mutation_proof=config.MUTATION_PROOF)
                self.comments[0]["body"] = "Factory approval v1\n" + json.dumps(receipt)
                if valid:
                    self.assertEqual(self.prepare()["targets"][0]["head"], self.head)
                else:
                    with self.assertRaises(decisions.DecisionError) as caught:
                        self.prepare()
                    self.assertEqual(caught.exception.code, "approval_evidence")

    def test_named_dispatcher_publishes_journal_evidence_before_label(self):
        self.share()
        self.comments = []
        commands = []
        pr = {"number": 17, "state": "OPEN", "headRefOid": self.head,
              "baseRefName": "main", "reviewDecision": "", "files": []}
        with (mock.patch.object(dispatch, "cfg", self.source, create=True),
              mock.patch.object(dispatch, "REPO", self.cfg.repo, create=True),
              mock.patch.object(dispatch, "EVENTS", self.source.factory / "events.jsonl", create=True),
              mock.patch.object(dispatch, "gh_json", return_value=pr),
              mock.patch.object(dispatch, "run", side_effect=lambda argv, **kw: (
                  commands.append(argv) or self.driver.completed(argv))),
              mock.patch.object(dispatch, "manager_approval", return_value=True),
              mock.patch.object(dispatch, "record"), mock.patch.object(dispatch, "retain_handoff")):
            self.assertTrue(dispatch.approve_pr(7, self.head))
        publication = next(argv for argv in commands if argv[:3] == ["gh", "pr", "comment"])
        label = next(argv for argv in commands if "--add-label" in argv)
        self.assertLess(commands.index(publication), commands.index(label))
        body = publication[publication.index("--body") + 1]
        self.assertTrue(body.startswith("Factory approval v1\n"))
        self.comments = [{"user": {"login": "factory"}, "body": body}]
        preview = self.prepare()
        self.assertTrue(self.driver.apply(preview)["ok"])
        self.assertIn(config.LABEL_APPROVED, {row["name"] for row in self.driver.pr["labels"]})


if __name__ == "__main__":
    unittest.main()
