"""#828: a failed GitHub read skips its ticket, not the whole pass."""
from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, watch


class PassReadTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), "acme/widgets")
        dispatch.configure(self.cfg)
        self.signals = [
            {"fingerprint": f"signal-{n}", "latest": "2026-11-16T12:00:00Z", "kind": "host"}
            for n in range(1, 7)
        ]
        self.issues = {n: {"state": "OPEN", "body": "", "labels": [{"name": "needs-triage"}],
                           "assignees": [{"login": "owner"}]} for n in range(1, 7)}
        self.before = copy.deepcopy(self.issues)
        for n, signal in enumerate(self.signals, 1):
            lifecycle.append(dispatch.EVENTS, {
                "event": "watch", "fingerprint": signal["fingerprint"], "issue": n,
                "repo": self.cfg.repo, "at": "2026-11-01T00:00:00Z",
            })
        self.calls = []
        self.failures = {2}
        self.stderr = "error connecting to api.github.com"
        self.remaining = 5000
        self.reset = 4102444800
        self.enterContext(mock.patch.object(watch, "signals", return_value=self.signals))
        self.enterContext(mock.patch.object(dispatch, "_run", side_effect=self.command))
        self.enterContext(mock.patch("subprocess.run", side_effect=self.probe))

    def probe(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.assertEqual(cmd[:3], ["gh", "api", "rate_limit"])
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": {
            key: {"remaining": self.remaining, "reset": self.reset} for key in ("core", "graphql")
        }}), "")

    def command(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "view"]:
            n = int(cmd[3])
            if n in self.failures:
                raise subprocess.CalledProcessError(1, cmd, "", self.stderr)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.issues[n]), "")
        if cmd[:3] == ["gh", "issue", "comment"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)

    def rows(self, event):
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == event]

    def test_middle_read_failure_continues_without_touching_failed_ticket(self):
        filed = watch.run_pass(self.cfg)
        self.assertEqual([s["fingerprint"] for s in filed], [f"signal-{n}" for n in (1, 3, 4, 5, 6)])
        self.assertEqual(self.issues[2], self.before[2])
        self.assertFalse(any(cmd[:3] in (["gh", "issue", "edit"], ["gh", "issue", "comment"])
                             and cmd[3] == "2" for cmd in self.calls))
        [skip] = self.rows("gh-read-skipped")
        self.assertEqual((skip["code"], skip["ticket"]), ("gh_read_failed", 2))
        [summary] = self.rows("pass-summary")
        self.assertEqual((summary["failed_reads"], summary["skipped"]), (1, 1))

    def test_rate_limit_defers_next_pass_until_reported_reset(self):
        self.failures = {2, 3, 4, 5, 6}
        self.stderr = "GraphQL: API rate limit exceeded"
        self.remaining = 0
        watch.run_pass(self.cfg)
        self.assertFalse(any(cmd[:4] == ["gh", "issue", "view", "3"] for cmd in self.calls))
        [skip] = self.rows("gh-read-skipped")
        self.assertEqual((skip["code"], skip["reset"]), ("rate_limited", self.reset))
        self.calls.clear()
        watch.run_pass(self.cfg)
        self.assertEqual(self.calls, [], "backoff must survive the next pass without more GitHub reads")
        self.assertEqual(self.rows("pass-summary")[-1]["reason"], "rate_limited")

    def test_majority_read_failures_end_pass_cleanly(self):
        self.failures = {1, 2, 3, 4, 5, 6}
        self.assertEqual(watch.run_pass(self.cfg), [])
        views = [cmd for cmd in self.calls if cmd[:3] == ["gh", "issue", "view"]]
        self.assertEqual(len(views), 3)
        [summary] = self.rows("pass-summary")
        self.assertEqual((summary["reason"], summary["failed_reads"], summary["skipped"]),
                         ("gh_reads_unavailable", 3, 3))
        self.assertEqual(self.issues, self.before)
