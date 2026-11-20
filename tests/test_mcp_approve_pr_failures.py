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

    def test_changed_head_with_its_own_evidence_still_withdraws_old_approval(self) -> None:
        for new_head in ("b" * 40, "0" * 40):
            with self.subTest(new_head=new_head):
                self.pr["headRefOid"] = self.head
                self.mutations.clear()
                preview = self.call("factory_prepare_action", action="approve_pr", args={"number": self.number})
                transport = self.transport

                def push_during_edit(argv, *, transport=transport, new_head=new_head, **kwargs):
                    result = transport(argv, **kwargs)
                    if argv == [*self.edit, "--add-label", config.LABEL_APPROVED]:
                        self.pr["headRefOid"] = new_head
                        lifecycle.append(self.journal, {
                            "event": "attempt", "ticket": self.ticket, "gate": "PASS",
                            "head": new_head, "actual_head": new_head,
                        })
                        lifecycle.append(self.journal, {
                            "event": "review", "ticket": self.ticket, "verdict": "APPROVE", "accepted": True,
                            "head": new_head, "actual_head": new_head,
                        })
                    return result

                with patch.object(decisions, "_run", side_effect=push_during_edit):
                    receipt = self.call("factory_apply_action", proposal_id=preview["proposal_id"],
                                        confirmation=preview["confirmation"])

                self.assertEqual(receipt["status"], "failure")
                self.assertEqual(self.pr["headRefOid"], new_head)
                self.assertEqual(self.pr["labels"], [{"name": "keep-me"}])
                self.assertEqual(self.mutations, [
                    [*self.edit, "--add-label", config.LABEL_APPROVED],
                    [*self.edit, "--remove-label", config.LABEL_APPROVED],
                ])
                self.assertFalse(any(row.get("event") == "approved" for row in lifecycle.read_events(self.journal)))

    def test_dashboard_approval_keeps_human_decision_provenance(self) -> None:
        preview = decisions.prepare(self.cfg, [
            {"op": "pr", "number": self.number, "add": [config.LABEL_APPROVED]},
        ], caller="dashboard")
        receipt = decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
        self.assertEqual((receipt["status"], receipt["actor"], receipt["caller"]),
                         ("success", "real-agent", "dashboard"))
        approved = [row for row in lifecycle.read_events(self.journal) if row.get("event") == "approved"]
        self.assertEqual([(row["source"], row["actor"], row["pr"]) for row in approved],
                         [("human-decision", "real-agent", self.number)])

    def test_discovery_advertises_approval_args_as_closed_to_extra_effects(self) -> None:
        response = mcp.handle(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', {})
        prepare = next(tool for tool in response["result"]["tools"] if tool["name"] == "factory_prepare_action")
        branch = next(alt["properties"] for alt in prepare["inputSchema"]["oneOf"]
                      if alt["properties"]["action"]["const"] == "approve_pr")
        schema = branch["args"]
        self.assertEqual(set(schema["properties"]), {"number", "ruling_id"})
        self.assertEqual(schema["required"], ["number"])
        self.assertIs(schema["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
