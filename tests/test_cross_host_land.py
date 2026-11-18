"""#845: exact-head approval survives a host handoff; live work still excludes land."""
import copy
import json
from datetime import UTC, datetime
from unittest import TestCase, mock

from factory import config
from tests import test_land_host


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
                    self.enterContext(mock.patch.object(self.github, "pr", side_effect=lambda: {
                        **original(), "reviewDecision": "CHANGES_REQUESTED",
                    }))
                else:
                    self.enterContext(mock.patch.object(tank, "pr_checks", return_value=[
                        {"name": "gate", "bucket": "fail"},
                    ]))
                tank.merge_pass_locked(False)
                self.assertFalse(self.github.merged)
