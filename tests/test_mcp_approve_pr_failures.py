"""Post-edit MCP approval failures leave no unaudited approval behind."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, decisions, lifecycle, mcp


class McpApprovePrFailuresTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(root=Path(temp.name), repo="acme/widgets")
        self.number, self.ticket, self.head = 257, 848, "a" * 40
        self.pr = {
            "number": self.number, "title": "Restore approval", "state": "OPEN", "isDraft": False,
            "headRefName": f"agent/{self.ticket}", "headRefOid": self.head, "baseRefName": "main",
            "labels": [{"name": "keep-me"}], "reviewDecision": "",
            "updatedAt": "2026-11-18T00:00:00Z",
        }
        self.edit = ["gh", "pr", "edit", str(self.number), "--repo", self.cfg.repo]
        self.mutations = []
        self.journal = self.cfg.factory / "events.jsonl"
        lifecycle.append(self.journal, {
            "event": "attempt", "ticket": self.ticket, "gate": "PASS",
            "head": self.head, "actual_head": self.head,
        })
        lifecycle.append(self.journal, {
            "event": "review", "ticket": self.ticket, "verdict": "APPROVE", "accepted": True,
            "head": self.head, "actual_head": self.head,
        })
        for patcher in (
            patch.object(mcp.config, "load", return_value=self.cfg),
            patch.object(decisions, "_run", side_effect=self.transport),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def transport(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(argv, 0, "real-agent\n", "")
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.pr), "")
        self.mutations.append(list(argv))
        if argv == [*self.edit, "--add-label", config.LABEL_APPROVED]:
            self.pr["labels"].append({"name": config.LABEL_APPROVED})
        elif argv == [*self.edit, "--remove-label", config.LABEL_APPROVED]:
            self.pr["labels"] = [label for label in self.pr["labels"] if label["name"] != config.LABEL_APPROVED]
        else:
            self.fail(f"unexpected mutation: {argv}")
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    def call(self, tool: str, **arguments) -> dict:
        result = mcp.call_tool(tool, {"repository": self.cfg.repo, **arguments},
                               {self.cfg.repo: str(self.cfg.root)})
        return json.loads(result["content"][0]["text"])

    def test_journal_read_failure_after_label_edit_withdraws_and_persists_failure(self) -> None:
        preview = self.call("factory_prepare_action", action="approve_pr", args={"number": self.number})
        read_events = lifecycle.read_events

        def read_without_unaudited_approval(path):
            if {"name": config.LABEL_APPROVED} in self.pr["labels"]:
                raise OSError("approval evidence journal is unreadable")
            return read_events(path)

        arguments = {"proposal_id": preview["proposal_id"], "confirmation": preview["confirmation"]}
        with patch.object(lifecycle, "read_events", side_effect=read_without_unaudited_approval):
            receipt = self.call("factory_apply_action", **arguments)
            self.assertEqual(self.call("factory_apply_action", **arguments), receipt)

        self.assertEqual(receipt["status"], "failure")
        self.assertIn("approval label withdrawn", receipt["steps"][0]["output"])
        self.assertEqual(self.pr["labels"], [{"name": "keep-me"}])
        self.assertEqual(self.mutations, [
            [*self.edit, "--add-label", config.LABEL_APPROVED],
            [*self.edit, "--remove-label", config.LABEL_APPROVED],
        ])
        rows = read_events(self.journal)
        self.assertFalse(any(row.get("event") == "approved" for row in rows))
        decisions_rows = [row for row in rows if row.get("event") == "human-decision"]
        self.assertEqual([row["status"] for row in decisions_rows], ["prepared", "started", "failure"])
        self.assertEqual(decisions_rows[-1]["receipt"]["steps"], receipt["steps"])


if __name__ == "__main__":
    unittest.main()
