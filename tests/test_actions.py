"""Named actions behave identically through the dashboard and `factory mcp`; no live mutations."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import actions, config, decisions, dispatch, lifecycle, mcp, workspace

SLUG = "acme/widgets"


class ActionPathsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-q", "-b", "main"], check=True)
        (self.root / config.CONFIG_NAME).write_text(f'[repo]\nslug = "{SLUG}"\n')
        self.cfg = config.load(self.root)
        self.issue = {
            "number": 7, "title": "Fix parser", "body": "Body", "state": "OPEN",
            "labels": [{"name": config.LABEL_HUMAN}], "assignees": [{"login": "alice"}],
            "updatedAt": "2026-10-09T00:00:01Z", "url": f"https://github.com/{SLUG}/issues/7",
        }
        self.mutations: list[list[str]] = []
        patcher = mock.patch.object(decisions, "_run", side_effect=self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dashboard = mock.Mock(spec=workspace.Workspace)
        self.dashboard.repository_config.return_value = self.cfg

    def transport(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(argv, 0, "alice\n", "")
        if argv[:3] == ["gh", "issue", "view"]:
            fields = {key: self.issue[key] for key in argv[-1].split(",")}
            return subprocess.CompletedProcess(argv, 0, json.dumps(fields), "")
        self.mutations.append(list(argv))
        self.issue["updatedAt"] = f"2026-10-09T00:00:{len(self.mutations) + 1:02d}Z"
        if argv[:3] == ["gh", "issue", "close"]:
            self.issue["state"] = "CLOSED"
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    # Dashboard path: the HTTP body workspace-decisions.js posts, through Workspace.prepare/apply.
    def via_dashboard(self, action: str, args: dict) -> dict:
        body = {"repository": SLUG, "action": action, "args": args}
        return workspace.Workspace.prepare(self.dashboard, SLUG, body)

    def apply_dashboard(self, preview: dict) -> dict:
        body = {"repository": SLUG, "proposal_id": preview["proposal_id"], "confirmation": preview["confirmation"]}
        return workspace.Workspace.apply(self.dashboard, SLUG, body)

    # MCP path: the tool call an agent makes.
    def via_mcp(self, action: str, args: dict) -> dict:
        result = mcp.call_tool("factory_prepare_action", {"repository": SLUG, "action": action, "args": args}, {SLUG: str(self.root)})
        return json.loads(result["content"][0]["text"])

    def apply_mcp(self, preview: dict) -> dict:
        arguments = {"repository": SLUG, "proposal_id": preview["proposal_id"], "confirmation": preview["confirmation"]}
        result = mcp.call_tool("factory_apply_action", arguments, {SLUG: str(self.root)})
        return json.loads(result["content"][0]["text"])

    def refusals(self, action: str, args: dict) -> tuple[str, str]:
        with self.assertRaises(decisions.DecisionError) as dashboard:
            self.via_dashboard(action, args)
        mcp_error = self.via_mcp(action, args)["error"]
        self.assertEqual(mcp_error["code"], dashboard.exception.code)
        self.assertEqual(mcp_error["message"], str(dashboard.exception))
        self.assertEqual(self.mutations, [])
        return dashboard.exception.code, mcp_error["message"]

    def journal(self) -> list[dict]:
        return [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl") if row.get("event") == "human-decision"]

    def test_requeue_agent_compiles_identically_and_journals_each_caller(self) -> None:
        expected = [{"op": "issue", "number": 7, "add": [config.LABEL_AGENT], "remove": [config.LABEL_HUMAN], "unassign": True, "comment": "why"}]
        dashboard = self.via_dashboard("requeue_agent", {"number": 7, "comment": "why"})
        agent = self.via_mcp("requeue_agent", {"number": 7, "comment": "why"})
        self.assertEqual(dashboard["requests"], expected)
        self.assertEqual(agent["requests"], expected)
        self.assertEqual((dashboard["caller"], agent["caller"]), ("dashboard", "mcp"))
        self.assertEqual(agent["action"], {"name": "requeue_agent", "args": {"number": 7, "comment": "why"}})
        self.assertEqual([(row["status"], row["caller"]) for row in self.journal()], [("prepared", "dashboard"), ("prepared", "mcp")])

        receipt = self.apply_mcp(agent)
        self.assertEqual((receipt["status"], receipt["caller"], receipt["action"]["name"]), ("success", "mcp", "requeue_agent"))
        self.assertEqual([argv[:3] for argv in self.mutations], [["gh", "issue", "comment"], ["gh", "issue", "edit"]])
        self.assertIn("--remove-assignee", self.mutations[1])
        self.assertEqual([(row["status"], row["caller"]) for row in self.journal()[2:]], [("started", "mcp"), ("success", "mcp")])
        self.assertEqual(self.apply_mcp(agent), receipt)
        with self.assertRaises(decisions.DecisionError) as stale:
            self.apply_dashboard(dashboard)
        self.assertEqual(stale.exception.code, "stale_target")

    def test_successful_human_requeue_records_round_budget_reset(self) -> None:
        preview = self.via_dashboard("requeue_agent", {"number": 7, "comment": "ACs amended"})

        receipt = self.apply_dashboard(preview)

        self.assertEqual(receipt["status"], "success")
        [reset] = [
            row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
            if row.get("event") == "rounds-reset"
        ]
        self.assertEqual((reset["ticket"], reset["actor"]), (7, "alice"))
        self.assertEqual(reset["requeued_at"], reset["at"])

    def test_requeue_agent_refuses_upstream_sync_and_wrong_state_on_both_paths(self) -> None:
        self.issue["title"] = dispatch.SYNC_TITLE + "main drifted"
        code, message = self.refusals("requeue_agent", {"number": 7})
        self.assertEqual(code, "routing_refused")
        self.assertIn("upstream-sync", message)
        self.issue["title"] = "Fix parser"
        self.issue["labels"] = [{"name": config.LABEL_AGENT}]
        self.assertEqual(self.refusals("requeue_agent", {"number": 7})[0], "precondition_failed")
        self.issue["labels"] = [{"name": config.LABEL_HUMAN}]
        self.issue["state"] = "CLOSED"
        self.assertEqual(self.refusals("requeue_agent", {"number": 7})[0], "precondition_failed")

    def test_invalid_input_is_rejected_at_the_action_boundary(self) -> None:
        self.assertEqual(self.refusals("retitle", {"number": 7})[0], "unknown_action")
        self.assertEqual(self.refusals("requeue_agent", {"number": "7"})[0], "invalid_args")
        self.assertEqual(self.refusals("requeue_agent", {"number": 7, "add": ["x"]})[0], "invalid_args")
        self.assertEqual(self.refusals("resolve_and_close", {"number": 7})[0], "invalid_args")
        self.assertEqual(self.refusals("resolve_and_close", {"number": 7, "reason": "duplicate"})[0], "invalid_args")
        self.issue["labels"] = [{"name": "wontfix"}]
        self.assertEqual(self.refusals("requeue_agent", {"number": 7})[0], "precondition_failed")
        self.assertEqual(self.journal(), [])

    def test_resolve_and_close_closes_with_reason_on_both_paths(self) -> None:
        args = {"number": 7, "reason": "not planned", "comment": "Superseded"}
        dashboard = self.via_dashboard("resolve_and_close", args)
        self.assertEqual(dashboard["requests"], [{"op": "issue", "number": 7, "close": "not planned", "comment": "Superseded"}])
        self.assertEqual(self.via_mcp("resolve_and_close", args)["requests"], dashboard["requests"])
        receipt = self.apply_dashboard(dashboard)
        self.assertEqual((receipt["status"], receipt["caller"]), ("success", "dashboard"))
        self.assertEqual(self.mutations[-1][:3], ["gh", "issue", "close"])
        self.assertIn("not planned", self.mutations[-1])
        self.mutations.clear()
        self.assertEqual(self.refusals("resolve_and_close", {"number": 7, "reason": "completed"})[0], "precondition_failed")

    def test_add_needs_triage_requires_an_unrouted_open_issue(self) -> None:
        self.issue["labels"] = []
        dashboard = self.via_dashboard("add_needs_triage", {"number": 7})
        self.assertEqual(dashboard["requests"], [{"op": "issue", "number": 7, "add": [config.LABEL_TRIAGE]}])
        self.assertEqual(self.via_mcp("add_needs_triage", {"number": 7})["requests"], dashboard["requests"])
        self.assertEqual(self.apply_mcp(self.via_mcp("add_needs_triage", {"number": 7}))["status"], "success")
        self.assertEqual(self.mutations[-1][:3], ["gh", "issue", "edit"])
        self.assertIn(config.LABEL_TRIAGE, self.mutations[-1])
        self.mutations.clear()
        for label in (config.LABEL_TRIAGE, config.LABEL_INFO, config.LABEL_AGENT, config.LABEL_HUMAN):
            self.issue["labels"] = [{"name": label}]
            self.assertEqual(self.refusals("add_needs_triage", {"number": 7})[0], "precondition_failed")
        self.issue["labels"] = [{"name": config.LABEL_INITIATIVE}]
        self.assertEqual(self.refusals("add_needs_triage", {"number": 7})[0], "routing_refused")
        self.issue["labels"] = []
        self.issue["title"] = dispatch.SYNC_TITLE + "main drifted"
        self.assertEqual(self.refusals("add_needs_triage", {"number": 7})[0], "routing_refused")

    def test_mcp_tool_arguments_are_closed_and_scoped(self) -> None:
        repos = {SLUG: str(self.root)}
        self.assertTrue(mcp.call_tool("factory_prepare_action", {"repository": SLUG, "action": "requeue_agent"}, repos)["isError"])
        self.assertTrue(mcp.call_tool("factory_apply_action", {"repository": SLUG, "proposal_id": "a" * 32}, repos)["isError"])
        self.via_mcp("requeue_agent", {"number": 7})
        result = mcp.call_tool("factory_apply_action", {"repository": SLUG, "proposal_id": "nope", "confirmation": "x"}, repos)
        self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "invalid_proposal")
        result = mcp.call_tool("factory_prepare_action", {"repository": "acme/other", "action": "requeue_agent", "args": {}}, {"acme/other": str(self.root)})
        self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "scope_mismatch")
        names = [tool["name"] for tool in mcp.TOOLS]
        self.assertEqual(names[-2:], ["factory_prepare_action", "factory_apply_action"])
        schema = mcp.TOOLS[-2]["inputSchema"]
        self.assertEqual(schema["properties"]["action"]["enum"], list(actions.ACTIONS))
        self.assertEqual([alt["properties"]["args"] for alt in schema["oneOf"]], [spec["schema"] for spec in actions.ACTIONS.values()])


if __name__ == "__main__":
    unittest.main()
