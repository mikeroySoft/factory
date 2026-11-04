"""Exercise manager CLOSE through real subprocesses and a stateful fake gh."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_factory

from factory import config, lifecycle

TOKEN = "ghp_" + "a1B2c3D4e5" * 3

GH = r'''
import json, sys
from pathlib import Path
p = Path(__file__).with_name("state.json")
s = json.loads(p.read_text())
a = sys.argv[1:]
op = " ".join(a[:2])
if op == s.get("fail"):
    print(s["error"], file=sys.stderr)
    sys.exit(1)
if op == "pr list":
    print("[]")
elif op == "pr view":
    if s["pr"] is None:
        print('no pull requests found for branch "agent/7"', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(s["pr"]))
elif op == "issue list":
    print(json.dumps([s["issue"]] if s["issue"]["state"] == "OPEN" else []))
elif op == "issue view":
    print(json.dumps(s["issue"]))
elif a[0] == "api":
    if a[1].endswith("/timeline"):
        print(json.dumps(s["timeline"]))
    else:
        print(json.dumps(s["issue"]))
elif a[1] == "comment":
    s["comments"].append({"subject": a[0], "body": a[a.index("--body") + 1]})
    print(f'https://github.com/acme/widgets/issues/7#issuecomment-{100 + len(s["comments"])}')
elif op == "issue edit":
    s["issue"]["labels"].append({"name": a[a.index("--add-label") + 1]})
elif op == "issue close":
    s["issue"].update(state="CLOSED", reason=a[a.index("--reason") + 1])
elif op == "pr close":
    s["pr"]["state"] = "CLOSED"
else:
    raise AssertionError(a)
p.write_text(json.dumps(s))
'''


class ManagerCloseJournalTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        test_factory.host_file("")

    def scenario(self, code="no_change", pr=None, child=False, fail=None, error=""):
        repo, stubs, _ = test_factory.ManageTest.scenario(self)
        (repo / config.CONFIG_NAME).write_text(
            '[manager]\ncommand = ["printf", "DECISION: CLOSE\\nNo change should land."]\n'
        )
        journal = repo / ".factory/events.jsonl"
        rows = lifecycle.read_events(journal)
        rows[0].update(code=code, reason=code)
        if child:
            rows.insert(0, {"event": "issue-created", "ticket": 7, "parent": 1})
        journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
        gh = Path(stubs) / "gh"
        gh.write_text(f"#!{sys.executable}\n" + GH)
        state_path = Path(stubs) / "state.json"
        state_path.write_text(json.dumps({
            "pr": pr, "fail": fail, "error": error, "comments": [],
            "timeline": json.loads((repo / ".factory/timeline.json").read_text()),
            "issue": {"number": 7, "title": "Already handled", "body": "Original body",
                      "state": "OPEN", "labels": [{"name": config.LABEL_HUMAN}]},
        }))
        return repo, stubs, state_path

    def run_manager(self, repo, stubs, state_path):
        result = test_factory.factory(repo, "manage", path=stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return lifecycle.read_events(repo / ".factory/events.jsonl"), json.loads(state_path.read_text())

    def test_close_without_open_pr_applies_each_escalation_and_provenance(self):
        for code, pr in (("no_change", None), ("pr_unpublished", None),
                         ("gate_failed", {"number": 9, "state": "CLOSED", "baseRefName": "main"})):
            for child in (False, True):
                with self.subTest(code=code, child=child):
                    rows, state = self.run_manager(*self.scenario(code, pr, child))
                    self.assertEqual(state["comments"], [
                        {"subject": "issue", "body": "Factory manager: No change should land."},
                    ])
                    if child:
                        self.assertEqual((state["issue"]["state"], state["issue"]["reason"]),
                                         ("CLOSED", "not planned"))
                    else:
                        self.assertEqual(state["issue"]["state"], "OPEN")
                        self.assertIn({"name": config.LABEL_WONTFIX}, state["issue"]["labels"])
                    decision = next(i for i, row in enumerate(rows) if row.get("event") == "manage")
                    receipt = next(i for i, row in enumerate(rows)
                                   if row.get("event") == "comment" and row.get("decision") == "CLOSE")
                    self.assertLess(decision, receipt)
                    self.assertFalse(any(row.get("outcome") == "mechanism_failure" for row in rows))
                    self.assertFalse(any(row.get("event") == "handoff" for row in rows))
                    terminal = next(row for row in rows if row.get("kind") == "exit" and row.get("stage") == "manage")
                    self.assertNotIn("error", terminal)

    def test_failed_apply_journals_redacted_stderr(self):
        for command in ("pr view", "pr comment", "pr close", "issue comment", "issue edit", "issue close"):
            with self.subTest(command=command):
                error = "discarded prefix\n" + "x " * 1200 + f"\nGraphQL denied {TOKEN}\n"
                rows, _ = self.run_manager(*self.scenario(
                    pr={"number": 9, "state": "OPEN", "baseRefName": "main"},
                    child=command == "issue close", fail=command, error=error,
                ))
                terminal = next(row for row in rows if row.get("kind") == "exit" and row.get("stage") == "manage")
                self.assertEqual((terminal["outcome"], terminal["reason"]),
                                 ("mechanism_failure", "github_command_failed"))
                self.assertIn("GraphQL denied [REDACTED]", terminal.get("error", ""))
                self.assertLessEqual(len(terminal["error"]), 2000)
                self.assertNotIn("discarded prefix", terminal["error"])
                self.assertNotIn(TOKEN, json.dumps(rows))

    def test_failed_apply_without_stderr_keeps_exception_diagnosis(self):
        rows, _ = self.run_manager(*self.scenario(fail="pr view"))
        terminal = next(row for row in rows if row.get("kind") == "exit" and row.get("stage") == "manage")
        self.assertEqual((terminal["outcome"], terminal["reason"]),
                         ("mechanism_failure", "github_command_failed"))
        self.assertTrue(terminal.get("error"))

    def test_merged_or_other_base_pr_requires_human_without_mutation(self):
        for pr in ({"number": 9, "state": "MERGED", "baseRefName": "main"},
                   {"number": 9, "state": "OPEN", "baseRefName": "stable"}):
            with self.subTest(pr=pr):
                rows, state = self.run_manager(*self.scenario("gate_failed", pr))
                terminal = next(row for row in rows if row.get("kind") == "exit" and row.get("stage") == "manage")
                self.assertEqual((terminal["outcome"], terminal["reason"]),
                                 ("mechanism_failure", "github_command_failed"))
                self.assertTrue(terminal.get("error"))
                self.assertEqual(state["pr"], pr)
                self.assertEqual(state["issue"]["state"], "OPEN")
                self.assertNotIn({"name": config.LABEL_WONTFIX}, state["issue"]["labels"])
                self.assertFalse(any(comment["body"] == "Factory manager: No change should land."
                                     for comment in state["comments"]))


if __name__ == "__main__":
    unittest.main()
