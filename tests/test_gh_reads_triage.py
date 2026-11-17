"""#828: triage isolates a failed GitHub read before mutating its ticket."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, gh_reads, lifecycle, triage


class TriageReadFailureTest(unittest.TestCase):
    def test_failed_ticket_read_is_skipped_while_other_decisions_apply(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cfg = config.Config(root, "acme/widgets")
        cfg.factory.mkdir()
        triage.configure(cfg)
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["gh", "api", "rate_limit"]:
                budgets = {name: {"remaining": 5000, "reset": 4102444800}
                           for name in ("core", "graphql")}
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": budgets}), "")
            if cmd[:3] == ["gh", "issue", "list"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([
                    {"number": number} for number in (1, 2, 3)
                ]), "")
            if cmd[:3] == ["gh", "issue", "view"]:
                number = int(cmd[3])
                if number == 2:
                    return subprocess.CompletedProcess(cmd, 1, "", "error connecting to api.github.com")
                issue = {
                    "number": number,
                    "title": f"issue {number}",
                    "body": "short",
                    "comments": [],
                    "labels": [{"name": config.LABEL_TRIAGE}],
                    "state": "OPEN",
                }
                return subprocess.CompletedProcess(cmd, 0, json.dumps(issue), "")
            if cmd[:3] in (["gh", "issue", "comment"], ["gh", "issue", "edit"]):
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(cmd)

        args = argparse.Namespace(issue=None, replay=None, dry_run=False)
        with mock.patch("subprocess.run", side_effect=run), \
                gh_reads.scope(cfg.factory / "events.jsonl", "triage"):
            self.assertEqual(triage.execute(args, None), 1)

        mutations = [cmd for cmd in calls if cmd[:3] in (
            ["gh", "issue", "comment"], ["gh", "issue", "edit"],
        )]
        self.assertEqual({int(cmd[3]) for cmd in mutations}, {1, 3})
        [skipped] = [row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                     if row.get("event") == "gh-read-skipped"]
        self.assertEqual((skipped["code"], skipped["ticket"]), ("gh_read_failed", 2))


if __name__ == "__main__":
    unittest.main()
