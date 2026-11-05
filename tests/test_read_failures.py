"""#553: failed remote reads defer work without changing ticket state."""
from __future__ import annotations

import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle, reads


class FetchOutageTest(unittest.TestCase):
    def test_fetch_outage_ends_dispatch_cleanly_before_claiming(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = []

            def command(cmd, *args, **kwargs):
                commands.append(cmd)
                if cmd[:2] == ["git", "fetch"]:
                    raise subprocess.CalledProcessError(
                        128, cmd, stderr="ssh: Could not resolve hostname github.com: No address associated with hostname\n"
                        "fatal: Could not read from remote repository.\n",
                    )
                raise AssertionError(f"unexpected command after failed fetch: {cmd}")

            with patch.object(config, "repo_root", return_value=root), \
                 patch.object(dispatch, "_run", side_effect=command), \
                 patch("time.sleep"):
                self.assertEqual(dispatch.main([]), 0)
            self.assertTrue(all(cmd[:2] == ["git", "fetch"] for cmd in commands))
            rows = lifecycle.read_events(root / ".factory/events.jsonl")
            skips = [row for row in rows if row.get("event") == "read-skip"]
            self.assertEqual([row["code"] for row in skips], ["gh_read_failed"])
            self.assertEqual(skips[0]["returncode"], 128)
            summary = next(row for row in rows if row.get("event") == "read-summary")
            self.assertEqual(summary["skipped"], 1)
            self.assertFalse(any(row.get("event") == "claimed" for row in rows))


class ReadPolicyTest(unittest.TestCase):
    command = ["gh", "issue", "view", "7", "--json", "state"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.journal = self.root / ".factory/events.jsonl"
        self.clock = self.enterContext(patch.object(reads.time, "time", return_value=100))
        self.sleep = self.enterContext(patch.object(reads.time, "sleep"))
        self.probe = self.enterContext(patch.object(reads.subprocess, "run", return_value=(
            subprocess.CompletedProcess(["gh", "api", "rate_limit"], 0, json.dumps({
                "resources": {"graphql": {"remaining": 0, "reset": 1000}},
            }), "")
        )))

    def rows(self, event):
        return [row for row in lifecycle.read_events(self.journal) if row.get("event") == event]

    def test_quota_reset_blocks_this_and_next_pass_until_reset_margin(self):
        calls = []

        def limited():
            calls.append(7)
            return subprocess.CompletedProcess(self.command, 1, "", "GraphQL: API rate limit exceeded (RATE_LIMITED)")

        with reads.pass_scope(self.journal, "watch"):
            for ticket in (7, 8, 9):
                with reads.item(ticket=ticket):
                    reads.run(self.command, limited)
        self.assertEqual(calls, [7])
        self.assertEqual(self.rows("read-backoff")[0]["until"], 1005)
        self.assertEqual(self.rows("read-summary")[0]["skipped"], 3)
        self.assertTrue(self.rows("read-summary")[0]["stopped"])
        self.probe.assert_called_once()
        self.assertEqual(self.probe.call_args.args[0], ["gh", "api", "rate_limit"])
        self.sleep.assert_not_called()
        with reads.pass_scope(self.journal, "dispatch"):
            with reads.item(ticket=10):
                reads.run(self.command, limited)
        self.assertEqual(calls, [7], "a later CLI pass must honor the same quota deadline")
        self.clock.return_value = 1006
        with reads.pass_scope(self.journal, "watch"):
            result = reads.run(self.command, lambda: subprocess.CompletedProcess(self.command, 0, '{"state":"OPEN"}', ""))
        self.assertEqual(json.loads(result.stdout)["state"], "OPEN")

    def test_plain_403_with_quota_left_is_not_rate_limited_or_retried(self):
        self.probe.return_value.stdout = json.dumps({"resources": {"graphql": {"remaining": 4999, "reset": 1000}}})
        calls = []
        with reads.pass_scope(self.journal, "watch"):
            with reads.item(ticket=7):
                reads.run(self.command, lambda: calls.append(7) or subprocess.CompletedProcess(
                    self.command, 1, "", "HTTP 403: Resource not accessible by integration"))
        self.assertEqual(calls, [7])
        self.assertEqual(self.rows("read-skip")[0]["code"], "gh_read_failed")
        self.assertFalse(self.rows("read-summary")[0]["stopped"])
        self.sleep.assert_not_called()

    def test_majority_failure_circuit_stops_reads_not_every_ticket_process(self):
        calls = []
        with reads.pass_scope(self.journal, "dispatch"):
            for ticket in range(10):
                with reads.item(ticket=ticket):
                    reads.run(self.command, lambda: calls.append(ticket) or subprocess.CompletedProcess(
                        self.command, 1, "", "GraphQL: internal server error"))
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(self.rows("read-stop")[0]["reason"], "most_reads_failed")
        self.assertEqual(self.rows("read-summary")[0]["skipped"], 10)
        self.assertEqual(self.rows("read-summary")[0]["failed"], 3)

    def test_dns_outage_is_not_rate_limit_even_when_probe_unavailable(self):
        self.probe.return_value = subprocess.CompletedProcess([], 1, "", "error connecting to api.github.com")
        with reads.pass_scope(self.journal, "watch"):
            with reads.item(ticket=7):
                reads.run(self.command, lambda: subprocess.CompletedProcess(
                    self.command, 1, "", "error connecting to api.github.com"))
        self.assertEqual(self.rows("read-skip")[0]["code"], "gh_read_failed")
        self.assertEqual(self.rows("read-skip")[0]["quota"], {"available": False, "returncode": 1})
        self.assertEqual([int(call.args[0]) for call in self.sleep.call_args_list], [5, 15, 30])

    def test_mutating_api_post_and_graphql_mutation_are_never_replayed(self):
        commands = [
            ["gh", "api", "repos/a/b/issues/7/comments", "--method", "POST", "-f", "body=claim"],
            ["gh", "api", "graphql", "-f", "query=mutation { addComment { id } }"],
            ["gh", "issue", "edit", "7", "--add-label", "ready-for-human"],
        ]
        calls = []
        with reads.pass_scope(self.journal, "dispatch"):
            for command in commands:
                def fail():
                    calls.append(command)
                    raise subprocess.CalledProcessError(1, command, stderr="error connecting to api.github.com")
                with self.assertRaises(subprocess.CalledProcessError):
                    reads.run(command, fail)
        self.assertEqual(calls, commands)
        self.sleep.assert_not_called()
        self.probe.assert_not_called()

    def test_failed_admission_read_does_not_escalate_or_claim(self):
        cfg = config.Config(self.root, "local/read-failure", workers={"default": [sys.executable, "{prompt}"]})
        self.enterContext(patch.dict(dispatch.__dict__))
        dispatch.configure(cfg)
        issue = {"number": 7, "title": "ticket", "labels": [{"name": "ready-for-agent"}], "assignees": []}
        commands = []

        def fail(command, *args):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "GraphQL: internal server error")

        with patch.object(dispatch, "_run", side_effect=fail), \
             reads.pass_scope(self.journal, "dispatch"):
            dispatch.process_ticket(issue, 1, False)
        self.assertEqual([cmd[1:3] for cmd in commands], [["issue", "view"]])
        self.assertEqual(issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(issue["assignees"], [])
        self.assertEqual(self.rows("read-skip")[0]["ticket"], 7)
        self.assertEqual(self.rows("claimed") + self.rows("escalate"), [])


if __name__ == "__main__":
    unittest.main()
