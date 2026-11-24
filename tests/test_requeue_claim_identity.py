"""Explicit requeue authenticates retained claims as the dispatcher, not the human."""
import copy
import json
import subprocess
import unittest
from datetime import UTC, datetime

from factory import config
from tests.test_requeue_decision_ordering import RequeueDecisionOrderingTest as ActionHarness


class RequeueClaimIdentityTest(unittest.TestCase):
    harness_set_up = ActionHarness.setUp
    base_setUp = ActionHarness.base_setUp
    action_transport = ActionHarness.transport
    base_transport = ActionHarness.base_transport
    via_mcp = ActionHarness.via_mcp
    apply_mcp = ActionHarness.apply_mcp

    def setUp(self):
        self.harness_set_up()
        path = self.root / config.CONFIG_NAME
        path.write_text(path.read_text() + (
            '\n[install]\nenv = { GH_CONFIG_DIR = "/human" }\n'
            'dispatch_env = { GH_CONFIG_DIR = "/dispatcher" }\n'
        ))
        self.cfg.install["env"] = {"GH_CONFIG_DIR": "/human"}
        self.cfg.install["dispatch_env"] = {"GH_CONFIG_DIR": "/dispatcher"}
        self.initial_issue = copy.deepcopy(self.issue)
        self.comment_pages = [[]]
        self.dispatcher_identity_fails = False
        self.identity_lookups = []
        self.claim_reads = 0

    def transport(self, argv, **kwargs):
        config_dir = kwargs["env"].get("GH_CONFIG_DIR")
        if argv[-3:] == ["user", "--jq", ".login"]:
            self.identity_lookups.append(config_dir)
            if config_dir == "/dispatcher":
                return subprocess.CompletedProcess(
                    argv, int(self.dispatcher_identity_fails),
                    "" if self.dispatcher_identity_fails else "factory-bot\n",
                    "identity unavailable" if self.dispatcher_identity_fails else "",
                )
            self.assertEqual(config_dir, "/human")
            return subprocess.CompletedProcess(argv, 0, "alice\n", "")

        self.assertEqual(config_dir, "/human", argv)
        if argv[:2] == ["gh", "api"] and any("/comments" in arg for arg in argv):
            self.claim_reads += 1
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.comment_pages), "")
        return self.action_transport(argv, **kwargs)

    @staticmethod
    def claim(author, at="2020-01-01T00:00:00Z"):
        return {
            "body": f"Factory claim: host=tank nonce=kept at={at}",
            "user": {"login": author},
            "created_at": at,
        }

    def reset_action(self):
        self.issue = copy.deepcopy(self.initial_issue)
        self.mutations.clear()
        self.comment_pages = [[]]
        self.dispatcher_identity_fails = False
        self.identity_lookups.clear()
        self.claim_reads = 0

    def refuse_at(self, stage, comments, *, identity_failure=False):
        self.reset_action()
        if stage == "apply":
            preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
            self.assertNotIn("error", preview, preview)
        self.comment_pages = [comments]
        self.dispatcher_identity_fails = identity_failure
        before = copy.deepcopy(self.issue)
        result = (self.apply_mcp(preview) if stage == "apply" else
                  self.via_mcp("requeue_agent", {"number": 7, "host": "tank"}))
        self.assertEqual(self.issue, before)
        self.assertEqual(self.mutations, [])
        return result

    def test_dispatcher_claim_refuses_prepare_and_apply_when_expired_or_live(self):
        for at in ("2020-01-01T00:00:00Z", datetime.now(UTC).isoformat()):
            for stage in ("prepare", "apply"):
                with self.subTest(at=at, stage=stage):
                    result = self.refuse_at(stage, [self.claim("FACTORY-BOT", at)])
                    self.assertEqual(result.get("error", {}).get("code"), "target_busy", result)
                    self.assertIn("/dispatcher", self.identity_lookups)

    def test_human_and_outsider_claims_do_not_block_or_hide_dispatcher_claim(self):
        for author in ("alice", "outsider"):
            with self.subTest(author=author, case="alone"):
                self.reset_action()
                self.comment_pages = [[self.claim(author)]]
                preview = self.via_mcp("requeue_agent", {"number": 7, "host": "tank"})
                self.assertNotIn("error", preview, preview)
                self.assertEqual(self.apply_mcp(preview)["status"], "success")

            for stage in ("prepare", "apply"):
                with self.subTest(author=author, case="before-dispatcher", stage=stage):
                    comments = [self.claim(author), self.claim("Factory-Bot")]
                    result = self.refuse_at(stage, comments)
                    self.assertEqual(result.get("error", {}).get("code"), "target_busy", result)

    def test_dispatcher_identity_failure_fails_closed_at_prepare_and_apply(self):
        for stage in ("prepare", "apply"):
            with self.subTest(stage=stage):
                result = self.refuse_at(stage, [], identity_failure=True)
                self.assertEqual(result.get("error", {}).get("code"), "provider_unavailable", result)
                self.assertIn("/dispatcher", self.identity_lookups)

    def test_hostless_requeue_does_not_read_dispatcher_identity(self):
        self.reset_action()
        self.dispatcher_identity_fails = True
        self.comment_pages = [[self.claim("factory-bot")]]
        preview = self.via_mcp("requeue_agent", {"number": 7})
        self.assertNotIn("error", preview, preview)
        self.assertEqual(self.apply_mcp(preview)["status"], "success")
        self.assertNotIn("/dispatcher", self.identity_lookups)
        self.assertEqual(self.claim_reads, 0)


del ActionHarness


if __name__ == "__main__":
    unittest.main()
