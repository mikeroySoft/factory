"""Stats skips missing journaled tickets, not failed GitHub requests (#620)."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, stats


class UnresolvedTicketTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(root, "acme/widgets")
        self.cfg.factory.mkdir()
        (self.cfg.factory / "events.jsonl").write_text("".join(
            json.dumps({"event": "claimed", "ticket": number}) + "\n" for number in (7, 10000, 8)
        ))
        self.enterContext(mock.patch.object(stats, "resolve", return_value=self.cfg))
        self.enterContext(mock.patch.object(stats, "dispatcher_login", return_value=""))
        self.missing = "GraphQL: Could not resolve to an issue or pull request with the number of 10000. (repository.issue)"

    def github(self, command, **kwargs):
        if command[1:3] in (["pr", "list"], ["issue", "list"]):
            value = []
        elif command[1:3] == ["issue", "view"]:
            number = int(command[3])
            if number == 10000:
                return subprocess.CompletedProcess(command, 1, "", self.missing)
            value = {"number": number, "title": f"Ticket {number}", "state": "CLOSED",
                     "createdAt": "2026-10-01T00:00:00Z", "closedAt": None, "comments": []}
        elif command[1] == "api":
            if "/10000/" in command[2]:
                return subprocess.CompletedProcess(command, 1, "", "HTTP 404: Not Found")
            value = [[]]
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    def test_unresolvable_journal_ticket_does_not_abort_report(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(stats.subprocess, "run", side_effect=self.github), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(stats.main(["--quiet"]), 0)
        self.assertIn("Ticket 7", out.getvalue())
        self.assertIn("Ticket 8", out.getvalue())
        self.assertIn("Human-touched PRs:", out.getvalue())
        self.assertNotIn("Ticket 10000", out.getvalue())

    def test_unresolvable_ticket_with_an_agent_pr_is_skipped(self):
        def github(command, **kwargs):
            if command[1:3] == ["pr", "list"]:
                return subprocess.CompletedProcess(command, 0, json.dumps([
                    {"number": 70, "headRefName": "agent/10000", "state": "OPEN", "mergedAt": None},
                ]), "")
            return self.github(command, **kwargs)

        out = io.StringIO()
        with mock.patch.object(stats.subprocess, "run", side_effect=github), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(stats.main(["--json", "--quiet"]), 0)
        self.assertEqual([row["ticket"] for row in json.loads(out.getvalue())], [7, 8])

    def test_auth_network_and_other_lookup_errors_remain_fatal(self):
        for message in ("HTTP 401: Bad credentials", "dial tcp: network is unreachable",
                        "GraphQL: Resource not accessible by integration (repository.issue)"):
            with self.subTest(message=message):
                self.missing = message
                err = io.StringIO()
                with mock.patch.object(stats.subprocess, "run", side_effect=self.github), \
                        contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                    stats.configure(self.cfg)
                    stats.collect_rows()
                self.assertEqual(raised.exception.code, 1)
                self.assertIn(message, err.getvalue())

    def test_missing_timeline_is_not_treated_as_a_missing_ticket(self):
        def github(command, **kwargs):
            if command[1] == "api":
                return subprocess.CompletedProcess(command, 1, "", "HTTP 404: Not Found")
            return self.github(command, **kwargs)

        with mock.patch.object(stats.subprocess, "run", side_effect=github), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            stats.configure(self.cfg)
            stats.collect_rows()
