"""An MCP approval must be withdrawn if a hold appears during the label edit."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, decisions, lifecycle, mcp


class McpApprovePrRaceTest(unittest.TestCase):
    def test_hold_during_approval_withdraws_label_without_approved_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cfg = config.Config(root=Path(temp), repo="acme/widgets")
            number, ticket, head = 257, 848, "a" * 40
            pr = {
                "number": number, "title": "Restore approval", "state": "OPEN", "isDraft": False,
                "headRefName": f"agent/{ticket}", "headRefOid": head, "baseRefName": "main",
                "labels": [{"name": "keep-me"}], "reviewDecision": "",
                "updatedAt": "2026-11-18T00:00:00Z",
            }
            mutations = []
            edit = ["gh", "pr", "edit", str(number), "--repo", cfg.repo]

            def transport(argv, **kwargs):
                if argv[:2] == ["gh", "api"]:
                    return subprocess.CompletedProcess(argv, 0, "real-agent\n", "")
                if argv[:3] == ["gh", "pr", "view"]:
                    return subprocess.CompletedProcess(argv, 0, json.dumps(pr), "")
                mutations.append(list(argv))
                if argv == [*edit, "--add-label", config.LABEL_APPROVED]:
                    pr["labels"].extend([{"name": config.LABEL_APPROVED}, {"name": "factory-held"}])
                elif argv == [*edit, "--remove-label", config.LABEL_APPROVED]:
                    pr["labels"] = [label for label in pr["labels"] if label["name"] != config.LABEL_APPROVED]
                else:
                    self.fail(f"unexpected mutation: {argv}")
                return subprocess.CompletedProcess(argv, 0, "ok", "")

            journal = cfg.factory / "events.jsonl"
            lifecycle.append(journal, {
                "event": "attempt", "ticket": ticket, "gate": "PASS", "head": head, "actual_head": head,
            })
            lifecycle.append(journal, {
                "event": "review", "ticket": ticket, "verdict": "APPROVE", "accepted": True,
                "head": head, "actual_head": head,
            })
            repositories = {cfg.repo: str(cfg.root)}
            with patch.object(mcp.config, "load", return_value=cfg), patch.object(decisions, "_run", side_effect=transport):
                prepared = mcp.call_tool("factory_prepare_action", {
                    "repository": cfg.repo, "action": "approve_pr", "args": {"number": number},
                }, repositories)
                preview = json.loads(prepared["content"][0]["text"])
                applied = mcp.call_tool("factory_apply_action", {
                    "repository": cfg.repo, "proposal_id": preview["proposal_id"], "confirmation": preview["confirmation"],
                }, repositories)
                receipt = json.loads(applied["content"][0]["text"])

            self.assertEqual(receipt["status"], "failure")
            self.assertIn("approval label withdrawn", receipt["steps"][0]["output"])
            self.assertEqual(mutations, [
                [*edit, "--add-label", config.LABEL_APPROVED],
                [*edit, "--remove-label", config.LABEL_APPROVED],
            ])
            self.assertEqual(pr["labels"], [{"name": "keep-me"}, {"name": "factory-held"}])
            rows = lifecycle.read_events(journal)
            self.assertFalse(any(row.get("event") == "approved" for row in rows))


if __name__ == "__main__":
    unittest.main()
