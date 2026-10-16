"""`factory mcp`: stdio JSON-RPC envelope, registry discovery, and evidence routing."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_evidence import AT, GH, REPO

from factory import lifecycle

ROOT = Path(__file__).resolve().parents[1]
SECOND = "example/second"
WAIT = 60


class McpServerTest(unittest.TestCase):
    def setUp(self):
        context = patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""})
        context.start()
        self.addCleanup(context.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.tools = self.directory / "bin"
        self.tools.mkdir()
        (self.tools / "git").symlink_to(shutil.which("git"))
        self.executable("gh", GH)
        self.executable("systemctl", 'print("ActiveState=inactive\\nNextElapseUSecRealtime=0")\n')
        self.host = self.directory / "host"
        self.responses_path = self.directory / "responses.json"
        # No READ_GUARD sitecustomize: the adapter itself writes the child's stdin pipe.
        self.env = {**os.environ, "PYTHONPATH": str(ROOT),
                    "PYTHONDONTWRITEBYTECODE": "1", "PATH": str(self.tools),
                    "HOME": str(self.directory / "home"), "XDG_CONFIG_HOME": str(self.host),
                    "GH_CONFIG_DIR": str(self.directory / "gh"),
                    "GH_TOKEN": "", "GITHUB_TOKEN": "", lifecycle.CONTEXT_ENV: "",
                    "EVIDENCE_RESPONSES": str(self.responses_path),
                    "EVIDENCE_CALLS": str(self.directory / "calls.jsonl")}
        self.root = self.checkout("repo", REPO)
        self.other = self.checkout("other", SECOND)
        self.cwd = self.directory / "elsewhere"
        self.cwd.mkdir()
        issue = {"number": 7, "title": "Operator decision", "state": "open",
                 "html_url": f"https://github.com/{REPO}/issues/7", "body": "Keep the public API.",
                 "labels": [{"name": "ready-for-human"}], "assignees": [],
                 "created_at": AT, "updated_at": AT, "closed_at": None}
        prefix = f"repos/{REPO}/"
        self.responses_path.write_text(json.dumps({
            prefix + "issues": {"json": [issue]},
            prefix + "issues/7": {"json": issue},
            prefix + "issues/7/comments": {"json": []},
            prefix + "issues/7/timeline": {"json": []},
            "graphql": {"json": {"data": {"repository": {"pullRequests": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}}},
        }))

    def executable(self, name, content):
        path = self.tools / name
        path.write_text(f"#!{sys.executable} -S\n" + content)
        path.chmod(0o755)

    def checkout(self, name, slug):
        root = self.directory / name
        root.mkdir()
        git = ["git", "-C", str(root)]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        (root / ".factory.toml").write_text(f'[repo]\nslug = "{slug}"\n[gate]\nlock = "{root / "gpu.lock"}"\n')
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "init"], check=True)
        return root

    def register(self, text):
        (self.host / "factory").mkdir(parents=True, exist_ok=True)
        (self.host / "factory" / "config.toml").write_text(text)

    def start(self, registry=None):
        if registry is None:
            registry = {REPO: self.root, SECOND: self.other}
        self.register("".join(f'[repo."{slug}"]\npath = "{path}"\n' for slug, path in registry.items()))
        self.launch()

    def launch(self):
        self.proc = subprocess.Popen([sys.executable, "-B", "-m", "factory", "mcp"], cwd=self.cwd, env=self.env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, self.proc)
        self.lines = lines = queue.Queue()
        threading.Thread(target=lambda stdout=self.proc.stdout: [lines.put(line) for line in stdout], daemon=True).start()
        self.next_id = 0

    def stop(self, proc=None):
        proc = proc or self.proc
        if proc.stdout.closed:
            return
        if not proc.stdin.closed:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr = proc.stderr.read()
        proc.stderr.close()
        proc.stdout.close()
        self.assertNotIn(b"Traceback", stderr)

    def send(self, message):
        line = message if isinstance(message, bytes) else json.dumps(message).encode()
        self.proc.stdin.write(line + b"\n")
        self.proc.stdin.flush()

    def recv(self):
        line = self.lines.get(timeout=WAIT)
        self.assertTrue(line.endswith(b"\n"))
        self.assertNotIn(b"\n", line[:-1])
        return json.loads(line)

    def request(self, method, params=None, *, id=None):
        if id is None:
            self.next_id += 1
            id = self.next_id
        message = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        reply = self.recv()
        self.assertEqual(reply["jsonrpc"], "2.0")
        self.assertEqual(reply["id"], id)
        self.assertIs(type(reply["id"]), type(id))
        self.assertEqual(len({"result", "error"} & set(reply)), 1, reply)
        return reply

    def call(self, name, arguments=None):
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request("tools/call", params)

    def tool_text(self, name, arguments=None, *, error=False):
        reply = self.call(name, arguments)
        result = reply["result"]
        self.assertEqual(result.get("isError", False), error, result)
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertIsInstance(result["content"][0]["text"], str)
        return result["content"][0]["text"]

    def evidence(self, name, arguments, *, error=False):
        data = json.loads(self.tool_text(name, arguments, error=error))
        self.assertEqual(data["schema_version"], 1)
        self.assertIn(data["coverage"]["status"], ("complete", "bounded", "partial", "unavailable"))
        return data

    def ping(self):
        self.assertEqual(self.request("ping")["result"], {})

    def test_handshake_version_negotiation_and_ids(self):
        self.start()
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "t", "version": "0"}}, id=0)
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(reply["result"]["capabilities"], {"tools": {}})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "factory")
        self.assertIsInstance(reply["result"]["serverInfo"]["version"], str)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(self.request("ping", id="first")["result"], {})
        other = self.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}}, id="s")
        self.assertEqual(other["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(self.request("ping", {}, id=0)["result"], {})

    def test_tools_list_is_exactly_seven_object_rooted_tools(self):
        self.start()
        result = self.request("tools/list", {})["result"]
        self.assertEqual(set(result), {"tools"})
        self.assertEqual([tool["name"] for tool in result["tools"]],
                         ["factory_repositories", "factory_observe", "factory_inspect", "factory_investigate", "factory_capabilities",
                          "factory_prepare_action", "factory_apply_action"])
        for tool in result["tools"]:
            self.assertEqual(set(tool), {"name", "description", "inputSchema"})
            self.assertEqual(tool["inputSchema"]["type"], "object")
        schemas = {tool["name"]: tool["inputSchema"] for tool in result["tools"]}
        self.assertEqual(schemas["factory_repositories"], {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertEqual(schemas["factory_inspect"]["required"], ["repository", "number"])
        self.assertEqual(schemas["factory_inspect"]["properties"]["number"],
                         {"type": "integer", "minimum": 1, "maximum": 9223372036854775807})
        kinds = {alt["properties"]["kind"]["const"]: alt for alt in schemas["factory_investigate"]["oneOf"]}
        self.assertEqual(set(kinds), {"workflows", "file", "result", "pr", "checks", "runs", "run", "log",
                                      "roadmap", "initiative", "drift", "experiments", "experiment"})
        self.assertEqual(kinds["result"]["required"], ["repository", "kind", "number", "head", "offset"])
        self.assertFalse(kinds["result"]["additionalProperties"])
        self.assertEqual(kinds["result"]["properties"]["offset"], {"type": "integer", "minimum": 0, "maximum": 262144})
        self.assertEqual(kinds["workflows"]["required"], ["repository", "kind"])

    def test_repositories_lists_registered_checkouts_only(self):
        self.register(f'[defaults.install]\npython = "/x"\n[repo."{SECOND}"]\npath = "{self.other}"\n'
                      f'[repo."{REPO}"]\npath = "{self.root}"\n[repo."{REPO}".dashboard]\nport = 1\n'
                      f'[repo."nopath/here"]\n[repo."bad slug"]\npath = "{self.root}"\n')
        self.launch()
        self.assertEqual(json.loads(self.tool_text("factory_repositories")), {"repositories": [
            {"repository": REPO, "path": str(self.root)}, {"repository": SECOND, "path": str(self.other)}]})
        self.assertEqual(json.loads(self.tool_text("factory_repositories", {}))["repositories"][0]["repository"], REPO)
        self.assertNotIn("isError", self.call("factory_repositories")["result"])
        self.tool_text("factory_repositories", {"repository": REPO}, error=True)
        self.ping()

    def test_startup_fails_on_invalid_registry_without_stdout(self):
        for text in ('[repo."x/y"]\npath = "relative"\n', '[repo."x/y"]\npath = 3\n', '[repo\n'):
            self.register(text)
            proc = subprocess.run([sys.executable, "-B", "-m", "factory", "mcp"], cwd=self.cwd, env=self.env,
                                  input=b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n', capture_output=True, timeout=WAIT, check=False)
            self.assertNotEqual(proc.returncode, 0, text)
            self.assertEqual(proc.stdout, b"")
            self.assertTrue(proc.stderr.strip(), text)
            self.assertNotIn(b"Traceback", proc.stderr)

    def test_missing_host_config_is_an_empty_registry(self):
        self.launch()
        self.assertEqual(json.loads(self.tool_text("factory_repositories")), {"repositories": []})
        self.tool_text("factory_observe", {"repository": REPO}, error=True)

    def test_capabilities_per_repository_and_scope_mismatch(self):
        self.start({REPO: self.root, SECOND: self.root})
        data = self.evidence("factory_capabilities", {"repository": REPO})
        self.assertEqual(data["scope"], {"repository": REPO, "root": str(self.root)})
        self.assertTrue(data["ok"])
        self.assertIn("reads", data["capabilities"])
        mismatch = self.evidence("factory_capabilities", {"repository": SECOND}, error=True)
        self.assertEqual(mismatch["error"]["code"], "scope_mismatch")
        self.assertFalse(mismatch["ok"])
        self.stop()
        self.start()
        data = self.evidence("factory_capabilities", {"repository": SECOND})
        self.assertEqual(data["scope"], {"repository": SECOND, "root": str(self.other)})
        self.assertTrue(data["ok"])

    def test_investigate_partial_result_is_a_normal_tool_result(self):
        self.start()
        data = self.evidence("factory_investigate", {"repository": REPO, "kind": "result", "number": 7,
                                                     "head": "f" * 40, "offset": 0})
        self.assertFalse(data["ok"])
        self.assertIn(data["coverage"]["status"], ("partial", "unavailable"))
        self.assertEqual(data["investigation"]["kind"], "result")
        self.assertEqual(data["scope"]["repository"], REPO)

    def test_evidence_validation_and_adapter_boundary(self):
        self.start()
        for arguments in ({"repository": REPO, "number": True},
                          {"repository": REPO, "number": "7"}):
            data = self.evidence("factory_inspect", arguments, error=True)
            self.assertEqual(data["error"]["code"], "invalid_request")
        for arguments in ({"repository": REPO, "kind": "result", "number": 7, "head": "f" * 40},
                          {"repository": REPO, "kind": "pr", "number": 7, "run_id": 1},
                          {"repository": REPO, "kind": "pr", "number": 7, "root": "/elsewhere"},
                          {"repository": REPO, "kind": "nope"}):
            data = self.evidence("factory_investigate", arguments, error=True)
            self.assertEqual(data["error"]["code"], "invalid_request")
        for arguments in ({"repository": REPO, "schema_version": 1},
                          {"repository": REPO, "op": "observe"},
                          {"repository": 7}, {}, {"repository": "example/unregistered"}):
            text = self.tool_text("factory_observe", arguments, error=True)
            self.assertNotIn("schema_version\":", text)
        reply = self.request("tools/call", {"name": "factory_observe", "arguments": [REPO]})
        self.assertEqual(reply["error"]["code"], -32602)
        reply = self.request("tools/call", {"name": "factory_observe", "arguments": "x"})
        self.assertEqual(reply["error"]["code"], -32602)
        self.ping()
        self.assertFalse((self.directory / "calls.jsonl").exists(), "adapter-rejected calls reached gh")

    def test_observe_and_inspect_fixture_case(self):
        self.start()
        observed = self.evidence("factory_observe", {"repository": REPO})
        self.assertEqual([case["number"] for case in observed["cases"]], [7])
        self.assertEqual(observed["cases"][0]["stage"], "escalated")
        self.assertIsInstance(observed["errors"], list)
        self.assertIs(type(observed["ok"]), bool)
        inspected = self.evidence("factory_inspect", {"repository": REPO, "number": 7})
        self.assertEqual(inspected["case"]["number"], 7)
        self.assertEqual(inspected["case"]["title"], "Operator decision")
        self.assertTrue(any("Keep the public API." in row["text"] for row in inspected["sources"]))
        self.assertNotEqual(observed["observation_id"], inspected["observation_id"])
        self.assertFalse((self.root / ".factory").exists())

    def test_invalid_messages_and_notifications_recover(self):
        self.start()
        self.tool_text("factory_capabilities", {"repository": "example/unknown"}, error=True)
        self.assertEqual(self.request("tools/call", {"name": "factory_nope", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(self.request("tools/call", {})["error"]["code"], -32602)
        self.assertEqual(self.request("resources/list")["error"]["code"], -32601)
        self.send(b"{not json")
        reply = self.recv()
        self.assertEqual((reply["jsonrpc"], reply["id"], reply["error"]["code"]), ("2.0", None, -32700))
        self.ping()
        self.send(b"\xff\xfe")
        self.assertEqual(self.recv()["error"]["code"], -32700)
        self.ping()
        for raw in ({}, [], {"jsonrpc": "2.0"}, {"jsonrpc": "1.0", "method": "ping"}, {"jsonrpc": "2.0", "method": 1},
                    {"jsonrpc": "2.0", "id": None, "method": "ping"}, {"jsonrpc": "2.0", "id": True, "method": "ping"}):
            self.send(raw)
            reply = self.recv()
            self.assertEqual((reply["id"], reply["error"]["code"]), (None, -32600), raw)
            self.ping()
        self.send({"jsonrpc": "2.0", "method": "notifications/unknown", "params": {"x": 1}})
        self.send({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "factory_observe"}})
        self.ping()
        self.assertEqual(self.request("ping", {"x": 1})["result"], {})
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=WAIT), 0)
        self.assertTrue(self.lines.empty())


if __name__ == "__main__":
    unittest.main()
