"""#845: exact-head approval survives a host handoff; live work still excludes land."""
import copy
import json
import sys
from datetime import UTC, datetime
from unittest import TestCase, mock

from factory import config
from tests import test_land_host, test_risk_merge


class CrossHostLandTest(TestCase):
    setUp = test_land_host.LandHostTest.setUp
    claim = test_land_host.LandHostTest.claim
    approve = test_land_host.LandHostTest.approve
    events = test_land_host.LandHostTest.events

    def dispatcher(self, host):
        module = test_land_host.LandHostTest.dispatcher(self, host)
        original_run = module.run.side_effect
        original_json = module.gh_json.side_effect
        if not hasattr(self, "approval_comments"):
            self.approval_comments = []

        def run(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "pr", "comment"] and "--body" in cmd:
                self.approval_comments.append({
                    "id": len(self.approval_comments) + 100,
                    "user": {"login": "factory"},
                    "created_at": datetime.now(UTC).isoformat(),
                    "body": cmd[cmd.index("--body") + 1],
                })
            if cmd[:2] == ["gh", "api"] and "DELETE" in cmd:
                comment_id = int(cmd[2].rsplit("/", 1)[1])
                self.comments[:] = [row for row in self.comments if row["id"] != comment_id]
            return original_run(cmd, *args, **kwargs)

        def gh_json(args):
            if args[:3] == ["api", "--paginate", "--slurp"] and "/issues/102/comments" in args[-1]:
                return [copy.deepcopy(self.approval_comments)]
            if args[:2] == ["api", "repos/acme/widgets/issues/9/comments"] and "POST" in args:
                comment = {
                    "id": len(self.comments) + 1, "user": {"login": "factory"},
                    "created_at": datetime.now(UTC).isoformat(),
                    "body": next(arg[5:] for arg in args if arg.startswith("body=")),
                }
                self.comments.append(comment)
                return comment
            return original_json(args)

        self.enterContext(mock.patch.object(module, "run", side_effect=run))
        self.enterContext(mock.patch.object(module, "gh_json", side_effect=gh_json))
        return module

    def revise(self, module, at="2000-01-01T00:00:00Z"):
        head = self.github.head()
        module.record("review", ticket=9, head=head, actual_head=head,
                      accepted=True, verdict="REVISE", role="primary", at=at)
        module.record("review-result", ticket=9, pr=102, head=head,
                      verdict="REQUEST_CHANGES", at=at)

    def test_expired_foreign_claim_lands_verified_approval(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(ultron)
        self.claim("ultron", age=3 * 3600)
        tank.merge_pass_locked(False)
        self.assertTrue(self.github.merged)
        self.assertEqual(self.events(tank, "escalate"), [])

    def test_live_foreign_claim_still_blocks_land(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(ultron)
        self.claim("ultron")
        tank.merge_pass_locked(False)
        self.assertFalse(self.github.merged)
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertEqual(self.events(tank, "escalate"), [])

    def test_newer_foreign_approve_overrides_local_same_head_revise(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.revise(tank)
        self.approve(ultron)
        tank.merge_pass_locked(False)
        self.assertTrue(self.github.merged)
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertEqual(self.events(tank, "escalate"), [])

    def test_successful_approval_releases_source_claim(self):
        ultron = self.dispatcher("ultron")
        self.claim("ultron")
        self.github.issue_labels.add("host:ultron")
        self.approve(ultron)
        self.assertIsNone(ultron.elected_host_claim(ultron.host_claims(9)))
        self.assertNotIn("host:ultron", self.github.issue_labels)
        self.assertEqual(self.github.assignees, [])

    def test_successful_worker_handoff_leaves_no_elected_host(self):
        ultron = self.dispatcher("ultron")
        ultron.cfg.workers["default"] = [sys.executable]
        self.github.assignees = []
        head = self.github.head()

        def worker(*_args, **_kwargs):
            ultron.record("attempt", ticket=9, head=head, actual_head=head, gate="PASS")
            return True, "gate passed", None, head

        def review(*_args, **_kwargs):
            ultron.record("review", ticket=9, head=head, actual_head=head, accepted=True,
                          verdict="APPROVE", role="primary")
            return "APPROVE", "", False

        with (mock.patch.object(ultron, "worker_round", side_effect=worker),
              mock.patch.object(ultron, "pr_gate_and_review", side_effect=review),
              mock.patch.object(ultron, "push_and_pr", return_value=True),
              mock.patch.object(ultron, "agent_lease", return_value=head),
              mock.patch.object(ultron, "record_risk"),
              mock.patch.object(ultron, "pr_comment"),
              mock.patch.object(ultron, "admit_plan", return_value=None)):
            ultron.process_ticket(self.github.issue(), 1, False)
        self.assertEqual(len(self.events(ultron, "approved")), 1)
        self.assertIsNone(ultron.elected_host_claim(ultron.host_claims(9)))
        self.assertEqual(self.events(ultron, "escalate"), [])

    def test_high_tier_receipt_preserves_second_review_and_refresh_worker_model(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.revise(tank)
        head = self.github.head()
        for row in test_risk_merge.high_events(head=head):
            row = {**row, "ticket": 9}
            if row["event"] == "attempt":
                row["head"] = row["actual_head"] = "b" * 40
            event = row.pop("event")
            ultron.record(event, **row)
        ultron.record("refreshed", ticket=9, gate_head=head, actual_head=head, gate="PASS")
        self.assertTrue(ultron.approve_pr(9, head))
        tank.merge_pass_locked(False)
        self.assertTrue(self.github.merged)

    def test_untrusted_stale_or_incomplete_record_cannot_override_revise(self):
        for defect in ("author", "head", "gate", "review", "newer_revise", "malformed"):
            with self.subTest(defect=defect):
                # Fresh fixtures also ensure one case cannot land the next one's PR.
                self.setUp()
                tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
                self.approval_comments = []
                self.revise(tank)
                self.approve(ultron)
                self.assertTrue(self.approval_comments)
                comment = self.approval_comments[-1]
                if defect == "author":
                    comment["user"]["login"] = "outsider"
                elif defect == "newer_revise":
                    self.revise(tank, at="2099-01-01T00:00:00Z")
                elif defect == "malformed":
                    comment["body"] = "Factory approval v1\n[]"
                else:
                    prefix, payload = comment["body"].split("\n", 1)
                    record = json.loads(payload)
                    if defect == "head":
                        record["head"] = "f" * 40
                    else:
                        kind = "attempt" if defect == "gate" else "review"
                        record["evidence"] = [row for row in record["evidence"] if row["event"] != kind]
                    comment["body"] = prefix + "\n" + json.dumps(record)
                tank.merge_pass_locked(False)
                self.assertFalse(self.github.merged)
                self.assertNotIn(config.LABEL_APPROVED, self.github.pr_labels)

    def test_foreign_approval_does_not_bypass_human_veto_or_failed_ci(self):
        for veto in ("human", "ci"):
            with self.subTest(veto=veto):
                self.setUp()
                tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
                self.approval_comments = []
                self.revise(tank)
                self.approve(ultron)
                if veto == "human":
                    original = self.github.pr
                    self.enterContext(mock.patch.object(self.github, "pr", side_effect=lambda original=original: {
                        **original(), "reviewDecision": "CHANGES_REQUESTED",
                    }))
                else:
                    self.enterContext(mock.patch.object(tank, "pr_checks", return_value=[
                        {"name": "gate", "bucket": "fail"},
                    ]))
                tank.merge_pass_locked(False)
                self.assertFalse(self.github.merged)
