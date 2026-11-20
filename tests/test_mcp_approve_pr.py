"""MCP re-approval uses exact-head policy and performs only the fixed label edit."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, decisions, lifecycle, mcp

SLUG = "acme/widgets"
NUMBER = 257
TICKET = 848
HEAD = "a" * 40


class McpApprovePrTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(root=Path(temp.name), repo=SLUG)
        self.pr = {
            "number": NUMBER, "title": "Restore approval", "state": "OPEN", "isDraft": False,
            "headRefName": f"agent/{TICKET}", "headRefOid": HEAD, "baseRefName": "main",
            "labels": [{"name": "keep-me"}], "reviewDecision": "",
            "updatedAt": "2026-11-18T00:00:00Z",
        }
        self.mutations = []
        for patcher in (
            patch.object(mcp.config, "load", return_value=self.cfg),
            patch.object(decisions, "_run", side_effect=self.transport),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "attempt", "ticket": TICKET, "gate": "PASS", "head": HEAD, "actual_head": HEAD,
        })
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "review", "ticket": TICKET, "verdict": "APPROVE", "accepted": True,
            "head": HEAD, "actual_head": HEAD,
        })

    def transport(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(argv, 0, "real-agent\n", "")
        if argv[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.pr), "")
        self.mutations.append(list(argv))
        if argv == ["gh", "pr", "edit", str(NUMBER), "--repo", SLUG, "--add-label", config.LABEL_APPROVED]:
            self.pr["labels"].append({"name": config.LABEL_APPROVED})
        else:
            self.fail(f"unexpected mutation: {argv}")
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    def call(self, tool: str, **arguments) -> dict:
        result = mcp.call_tool(tool, {"repository": SLUG, **arguments}, {SLUG: str(self.cfg.root)})
        return json.loads(result["content"][0]["text"])

    def prepare(self, **args) -> dict:
        return self.call("factory_prepare_action", action="approve_pr", args={"number": NUMBER, **args})

    def apply(self, preview: dict) -> dict:
        return self.call("factory_apply_action", proposal_id=preview["proposal_id"], confirmation=preview["confirmation"])

    def refused(self, code: str) -> None:
        self.assertEqual(self.prepare()["error"]["code"], code)
        self.assertEqual(self.mutations, [])
        self.assertNotIn(config.LABEL_APPROVED, [label["name"] for label in self.pr["labels"]])

    def test_approval_adds_only_fixed_label_and_records_real_mcp_caller(self) -> None:
        preview = self.prepare(ruling_id="r3-0740")
        self.assertEqual(preview["requests"], [{"op": "pr", "number": NUMBER, "add": [config.LABEL_APPROVED]}])
        self.assertEqual(self.mutations, [])
        receipt = self.apply(preview)
        self.assertEqual((receipt["status"], receipt["actor"], receipt["caller"]), ("success", "real-agent", "mcp"))
        self.assertEqual(receipt["action"], {"name": "approve_pr", "args": {"number": NUMBER, "ruling_id": "r3-0740"}})
        self.assertEqual(self.pr["labels"], [{"name": "keep-me"}, {"name": config.LABEL_APPROVED}])
        self.assertEqual(self.mutations, [["gh", "pr", "edit", str(NUMBER), "--repo", SLUG, "--add-label", config.LABEL_APPROVED]])
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        approved = [row for row in rows if row.get("event") == "approved"]
        self.assertEqual(len(approved), 1)
        self.assertEqual((approved[0]["actor"], approved[0]["source"], approved[0]["pr"], approved[0]["ticket"]),
                         ("real-agent", "mcp", NUMBER, TICKET))
        decisions_rows = [row for row in rows if row.get("event") == "human-decision"]
        self.assertEqual([(row["status"], row["actor"], row["caller"]) for row in decisions_rows],
                         [(status, "real-agent", "mcp") for status in ("prepared", "started", "success")])
        self.assertEqual(self.apply(preview), receipt)
        self.assertEqual(len(self.mutations), 1)

    def test_ruling_id_is_optional(self) -> None:
        self.assertEqual(self.apply(self.prepare())["status"], "success")

    def test_refuses_draft(self) -> None:
        self.pr["isDraft"] = True
        self.refused("approval_scope")

    def test_refuses_held(self) -> None:
        self.pr["labels"].append({"name": "factory-held"})
        self.refused("approval_scope")

    def test_refuses_closed(self) -> None:
        self.pr["state"] = "CLOSED"
        self.refused("approval_scope")

    def test_refuses_non_main_base(self) -> None:
        self.pr["baseRefName"] = "release"
        self.refused("approval_scope")

    def test_exact_head_evidence_is_required_and_rechecked_at_apply(self) -> None:
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "attempt", "ticket": TICKET, "gate": "FAIL", "head": HEAD, "actual_head": HEAD,
        })
        self.refused("approval_evidence")
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "attempt", "ticket": TICKET, "gate": "PASS", "head": HEAD, "actual_head": HEAD,
        })
        preview = self.prepare()
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "review", "ticket": TICKET, "verdict": "REVISE", "accepted": False,
            "head": HEAD, "actual_head": HEAD,
        })
        self.assertEqual(self.apply(preview)["error"]["code"], "approval_evidence")
        self.assertEqual(self.mutations, [])

    def test_rejects_extra_effects_arrays_and_invalid_ruling_id(self) -> None:
        for args in ({"add": ["other"]}, {"remove": ["keep-me"]}, {"comment": "not allowed"},
                     {"number": [NUMBER]}, {"ruling_id": ["r3-0740"]}, {"ruling_id": 17}):
            with self.subTest(args=args):
                self.assertEqual(self.prepare(**args)["error"]["code"], "invalid_args")
        self.assertEqual(self.mutations, [])


if __name__ == "__main__":
    unittest.main()
