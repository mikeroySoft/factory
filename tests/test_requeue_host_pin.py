"""Escalation and owner MCP requeue must not discard a ticket's host pin."""
import json
import subprocess
import unittest
from datetime import UTC, datetime
from unittest import mock

from factory import decisions, mcp
from tests import test_host_claims


class RequeueHostPinTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def transport(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"] and "user" in argv:
            return subprocess.CompletedProcess(argv, 0, "alice\n", "")
        if argv[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.github.issue), "")
        if argv[:3] == ["gh", "issue", "comment"]:
            self.github.json("owner", [
                "api", "repos/local/claims/issues/7/comments", "--method", "POST",
                "-f", f"body={argv[argv.index('--body') + 1]}",
            ])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["gh", "issue", "edit"]:
            for index, flag in enumerate(argv[:-1]):
                if flag in {"--add-label", "--remove-label", "--add-assignee", "--remove-assignee"}:
                    self.github.run("owner", [*argv[:4], flag, argv[index + 1]])
            return subprocess.CompletedProcess(argv, 0, "", "")
        return self.github.run("owner", argv, **kwargs)

    def requeue_after_escalation(self, intent, host, *, existing_host=None, previous_host=None):
        module = self.dispatcher(host)
        if previous_host:
            module.record("escalate", ticket=7, code="gate_failed", reason="previous escalation")
            module.record("claim-label-removed", ticket=7, label=f"host:{previous_host}")
        self.github.issue.update({
            "updatedAt": "2026-11-18T00:00:00Z",
            "url": "https://github.com/local/claims/issues/7",
        })
        if host:
            self.github.add_claim(host, datetime.now(UTC))
        else:
            self.github.issue["assignees"] = [{"login": "@me"}]
        with mock.patch.object(module, "run", side_effect=self.transport):
            module.escalate(7, "gate failed", None, code="gate_failed")
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(module.host_claims(7), [])
        self.assertIn({"name": "ready-for-human"}, self.github.issue["labels"])
        if existing_host:
            self.github.issue["labels"].append({"name": f"host:{existing_host}"})
        module.record("claim-label-removed", ticket=8, label="host:unrelated")

        with mock.patch.object(mcp.config, "load", return_value=module.cfg), \
                mock.patch.object(decisions, "_run", side_effect=self.transport):
            repositories = {module.cfg.repo: str(module.cfg.root)}
            prepared = mcp.call_tool("factory_prepare_action", {
                "repository": module.cfg.repo, "action": "requeue_agent",
                "args": {"number": 7, "intent": intent},
            }, repositories)
            self.assertNotIn("isError", prepared, prepared)
            preview = json.loads(prepared["content"][0]["text"])
            applied = mcp.call_tool("factory_apply_action", {
                "repository": module.cfg.repo, "proposal_id": preview["proposal_id"],
                "confirmation": preview["confirmation"],
            }, repositories)
            self.assertNotIn("isError", applied, applied)
            receipt = json.loads(applied["content"][0]["text"])
            self.assertEqual(receipt["status"], "success")
        pin = existing_host or host
        expected = {"ready-for-agent", *([f"host:{pin}"] if pin else [])}
        self.assertEqual({row["name"] for row in self.github.issue["labels"]}, expected)
        if host and not existing_host:
            foreign = self.dispatcher("tank")
            self.assertIn(host, foreign.frontier()[0]["held"])
            self.assertNotIn("held", module.frontier()[0])

    def test_resume_preserves_escalated_host_pin(self):
        self.requeue_after_escalation("resume", "ultron")

    def test_fresh_preserves_escalated_host_pin(self):
        self.requeue_after_escalation("fresh", "ultron")

    def test_resume_without_host_pin_stays_unpinned(self):
        self.requeue_after_escalation("resume", None)

    def test_fresh_without_host_pin_stays_unpinned(self):
        self.requeue_after_escalation("fresh", None)

    def test_current_host_pin_takes_precedence_over_escalation_cleanup(self):
        self.requeue_after_escalation("resume", "ultron", existing_host="tank")

    def test_unpinned_escalation_does_not_restore_previous_escalations_host(self):
        self.requeue_after_escalation("fresh", None, previous_host="ultron")
