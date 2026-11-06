"""Queue transitions against a stateful gh process, including partial writes."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config


GH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ["EXIT_STATE"])
s = json.loads(p.read_text())
a = sys.argv[1:]
s["calls"].append(a)
operation = "comment" if a[:2] == ["issue", "comment"] else "add" if "--add-label" in a else "edit"
failure = s.get("failure") == operation and s.get("remaining", 0) != 0
# gh can apply removals before a failed addition in the same issue edit.
for i, value in enumerate(a[:-1]):
    if value == "--remove-label":
        s["labels"] = [x for x in s["labels"] if x != a[i + 1]]
    if value == "--remove-assignee":
        s["assigned"] = False
if failure:
    s["remaining"] -= 1
elif operation == "add":
    label = a[a.index("--add-label") + 1]
    if label not in s["labels"]:
        s["labels"].append(label)
elif operation == "comment":
    s["comments"].append(a[a.index("--body") + 1])
s["history"].append(list(s["labels"]))
p.write_text(json.dumps(s))
if failure:
    print(s.get("error", "GraphQL: secondary rate limit; retry-after: 75"), file=sys.stderr)
    raise SystemExit(1)
if operation == "comment":
    print("https://github.com/local/safety/issues/7#issuecomment-123")
'''


class EscalationWriteSafetyTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = root / "state.json"
        self.state.write_text(json.dumps({"labels": ["ready-for-agent"], "assigned": True,
                                          "comments": [], "calls": [], "history": []}))
        gh = root / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.enterContext(mock.patch.dict(os.environ, {
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "EXIT_STATE": str(self.state), lifecycle.CONTEXT_ENV: "",
        }))
        saved = getattr(dispatch, "cfg", None)
        if saved is not None:
            self.addCleanup(dispatch.configure, saved)
        dispatch.configure(Config(root=root, repo="local/safety"))
        self.sleep = self.enterContext(mock.patch.object(dispatch.time, "sleep"))
        self.enterContext(mock.patch.object(dispatch, "_ESCALATED", set()))

    def data(self):
        return json.loads(self.state.read_text())

    def inject_failure(self, operation, remaining=-1, error=None):
        state = self.data()
        state.update(failure=operation, remaining=remaining)
        if error:
            state["error"] = error
        self.state.write_text(json.dumps(state))

    def escalate(self):
        with lifecycle.scope(dispatch.EVENTS, "ticket", ticket=7):
            dispatch.escalate(7, "gate failed", None, code="gate_failed")

    def assert_loud_failure(self, operation):
        self.inject_failure(operation)
        with self.assertRaises(subprocess.CalledProcessError):
            self.escalate()
        rows = lifecycle.read_events(dispatch.EVENTS)
        failures = [row for row in rows if row.get("event") == "github-write-failed"]
        self.assertEqual([row["attempt"] for row in failures], [1, 2])
        self.assertTrue(all(row["returncode"] == 1 for row in failures))
        self.assertEqual(rows[-1]["outcome"], "mechanism_failure")
        self.assertEqual(rows[-1]["reason"], "github_write_failed")
        self.assertEqual(self.sleep.call_args.args, (75.0,))
        self.assertEqual(self.data()["labels"], ["ready-for-agent"])
        self.assertTrue(all(labels for labels in self.data()["history"]))
        child_failures = [row for row in rows if row.get("kind") == "child_exit"
                          and row.get("returncode") == 1]
        self.assertEqual(len(child_failures), 2)
        self.assertTrue(all(row["command"][:3] == ["gh", "issue", "comment" if operation == "comment" else "edit"]
                            for row in child_failures))
        self.assertNotIn(7, dispatch._ESCALATED)

    def test_label_add_failure_retains_queue_and_is_recorded(self):
        self.assert_loud_failure("add")
        self.assertIn("gate failed", self.data()["comments"][0])

    def test_comment_failure_retains_queue_and_is_recorded(self):
        self.assert_loud_failure("comment")

    def test_rate_limit_retry_recovers_with_backoff(self):
        for error in ("GraphQL: API rate limit already exceeded", "HTTP 403: secondary rate limit"):
            with self.subTest(error=error):
                self.inject_failure("add", remaining=1, error=error)
                self.escalate()
                self.assertGreaterEqual(self.sleep.call_args.args[0], 60)
                self.assertEqual(self.data()["labels"], ["ready-for-human"])
                self.assertTrue(all(labels for labels in self.data()["history"]))

    def test_non_rate_limit_failure_is_also_retried(self):
        self.inject_failure("comment", remaining=1, error="HTTP 502: Something went wrong")
        self.escalate()
        self.assertEqual(self.sleep.call_args.args, (1,))
        self.assertEqual(self.data()["labels"], ["ready-for-human"])
        self.assertIn("gate failed", self.data()["comments"][0])

    def test_escalation_codes_all_leave_one_queue_and_reason_comment(self):
        for code in sorted(dispatch.ESCALATION_CODES):
            with self.subTest(exit=code):
                state = self.data()
                state["labels"] = ["ready-for-agent", "factory-held"]
                self.state.write_text(json.dumps(state))
                dispatch.escalate(7, f"reason for {code}", None, code=code)
                self.assertEqual(self.data()["labels"], ["ready-for-human"])
                self.assertIn(f"reason for {code}", self.data()["comments"][-1])
                self.assertTrue(all(labels for labels in self.data()["history"]))
