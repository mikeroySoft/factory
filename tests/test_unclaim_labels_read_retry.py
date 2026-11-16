"""unclaim's queue-labels read retries via gh_json and fails as a mechanism failure."""
import json
import os
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
if a[:2] == ["issue", "view"]:
    s["views"] += 1
    if s["view_failures"] != 0:
        s["view_failures"] -= 1
        p.write_text(json.dumps(s))
        print("HTTP 502: Something went wrong", file=sys.stderr)
        raise SystemExit(1)
    p.write_text(json.dumps(s))
    print(json.dumps({"labels": [{"name": label} for label in s["labels"]]}))
    raise SystemExit(0)
if a[:2] == ["issue", "edit"]:
    for i, value in enumerate(a[:-1]):
        if value == "--remove-label":
            s["labels"] = [x for x in s["labels"] if x != a[i + 1]]
        if value == "--add-label" and a[i + 1] not in s["labels"]:
            s["labels"].append(a[i + 1])
elif a[:2] == ["issue", "comment"]:
    s["comments"].append(a[a.index("--body") + 1])
    print("https://github.com/local/safety/issues/7#issuecomment-123")
p.write_text(json.dumps(s))
'''

VIEW = ["issue", "view", "7", "--repo", "local/safety", "--json", "labels"]


class UnclaimLabelsReadRetryTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = root / "state.json"
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

    def seed(self, view_failures):
        self.state.write_text(json.dumps({"labels": ["ready-for-agent"], "comments": [],
                                          "views": 0, "view_failures": view_failures}))

    def data(self):
        return json.loads(self.state.read_text())

    def failures(self):
        return [row for row in lifecycle.read_events(dispatch.EVENTS)
                if row.get("event") == "github-write-failed"]

    def test_transient_labels_read_failure_is_retried(self):
        self.seed(view_failures=1)
        with lifecycle.scope(dispatch.EVENTS, "ticket", ticket=7), \
                mock.patch.object(dispatch, "gh_json", wraps=dispatch.gh_json) as gh_json:
            dispatch.unclaim(7, "--add-label", "ready-for-human", check=True)
        self.assertEqual(gh_json.call_args_list, [mock.call(VIEW), mock.call(VIEW)])
        self.assertEqual(self.data()["views"], 2)
        self.assertEqual([row["attempt"] for row in self.failures()], [1])
        self.sleep.assert_called_once()
        self.assertEqual(self.data()["labels"], ["ready-for-human"])

    def test_exhausted_labels_read_during_escalate_is_mechanism_failure(self):
        self.seed(view_failures=-1)
        with self.assertRaises(dispatch.GitHubWriteFailed), \
                lifecycle.scope(dispatch.EVENTS, "ticket", ticket=7), \
                mock.patch.object(dispatch, "gh_json", wraps=dispatch.gh_json) as gh_json:
            dispatch.escalate(7, "gate failed", None, code="gate_failed")
        self.assertEqual(gh_json.call_args_list, [mock.call(VIEW), mock.call(VIEW)])
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(any(row.get("event") == "escalate" for row in rows))
        self.assertEqual([row["attempt"] for row in self.failures()], [1, 2])
        self.assertEqual(rows[-1]["outcome"], "mechanism_failure")
        self.assertEqual(rows[-1]["reason"], "github_write_failed")
        self.assertEqual(self.data()["views"], 2)
        self.assertEqual(self.data()["labels"], ["ready-for-agent"])


if __name__ == "__main__":
    unittest.main()
