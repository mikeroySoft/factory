"""#553: triage read failures skip tickets without mutating them."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, triage
from factory.config import LABEL_AGENT, LABEL_TRIAGE


class Process:
    _pids = itertools.count(100_000)

    def __init__(self, result: subprocess.CompletedProcess[str]):
        self.args = result.args
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        self.pid = next(self._pids)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self):
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self):
        return self.returncode


class TriageReadFailures(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.labels = {number: {LABEL_TRIAGE} for number in (3, 2, 1)}
        self.comments = {number: [] for number in self.labels}
        self.calls: list[list[str]] = []
        self.read_failures: dict[int, list[str]] = {}
        self.mutation_failure: tuple[int, str] | None = None
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.object(config, "load", return_value=self.cfg))
        self.enterContext(mock.patch.object(triage, "identity_mismatch", return_value=None))
        self.enterContext(mock.patch.object(triage, "triage_issue", return_value={
            "decision": LABEL_AGENT,
            "rationale": "ready",
            "question": "",
            "brief": "implement it",
        }))
        self.enterContext(mock.patch.object(triage.subprocess, "Popen", side_effect=self.popen))
        self.enterContext(mock.patch("time.sleep"))

    @staticmethod
    def completed(command: list[str], code: int = 0, stdout: str = "", stderr: str = ""):
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    def popen(self, command: list[str], **kwargs) -> Process:
        self.calls.append(command)
        operation = command[1:3]
        if operation == ["issue", "list"]:
            listed = [{"number": number} for number in (3, 2, 1)
                      if LABEL_TRIAGE in self.labels[number]]
            return Process(self.completed(command, stdout=json.dumps(listed)))

        number = int(command[3])
        if operation == ["issue", "view"]:
            failures = self.read_failures.get(number, [])
            if failures:
                return Process(self.completed(command, 1, stderr=failures.pop(0)))
            issue = {
                "number": number,
                "title": f"Issue {number}",
                "body": "Implement the requested behavior.\n\nAcceptance: regression tests pass.",
                "comments": list(self.comments[number]),
                "labels": [{"name": label} for label in self.labels[number]],
                "state": "OPEN",
                "updatedAt": "2026-11-04T00:00:00Z",
                "author": {"login": "reporter"},
            }
            return Process(self.completed(command, stdout=json.dumps(issue)))

        if self.mutation_failure == (number, operation[1]):
            return Process(self.completed(command, 1, stderr="network is unreachable"))
        if operation == ["issue", "comment"]:
            self.comments[number].append({
                "body": command[command.index("--body") + 1],
                "author": {"login": "factory-bot"},
                "createdAt": "2026-11-04T00:01:00Z",
            })
        elif operation == ["issue", "edit"]:
            self.labels[number] = {command[command.index("--add-label") + 1]}
        else:
            self.fail(f"unexpected command: {command}")
        return Process(self.completed(command))

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == event]

    def views(self) -> list[int]:
        return [int(command[3]) for command in self.calls if command[1:3] == ["issue", "view"]]

    def test_failed_middle_fetch_leaves_ticket_unchanged_and_continues(self) -> None:
        self.read_failures[2] = ["GraphQL: internal server error"]

        self.assertEqual(triage.main([]), 1)

        self.assertEqual(self.views(), [3, 2, 1])
        self.assertEqual(self.labels, {3: {LABEL_AGENT}, 2: {LABEL_TRIAGE}, 1: {LABEL_AGENT}})
        self.assertEqual(self.comments[2], [])
        self.assertFalse(any(command[1:3] in (["issue", "comment"], ["issue", "edit"])
                             and command[3] == "2" for command in self.calls))
        skips = self.rows("read-skip")
        self.assertEqual([(row["ticket"], row["code"]) for row in skips], [(2, "gh_read_failed")])
        summary = self.rows("read-summary")[-1]
        self.assertEqual((summary["stage"], summary["failed"], summary["skipped"]), ("triage", 1, 1))
        ticket_exit = next(row for row in lifecycle.read_events(self.events)
                           if row.get("stage") == "triage-ticket" and row.get("ticket") == 2
                           and row.get("kind") == "exit")
        self.assertEqual((ticket_exit["outcome"], ticket_exit["reason"]),
                         ("mechanism_failure", "gh_read_failed"))
        self.assertIsNone(lifecycle.last_successful_passes(self.events)["triage"])

    def test_transient_fetch_recovers_in_direct_execute(self) -> None:
        self.read_failures[2] = ["could not resolve host: api.github.com"]
        args = argparse.Namespace(issue=None, replay=None, dry_run=False)

        with lifecycle.scope(self.events, "triage", lazy=True) as execution:
            result = triage.execute(args, execution)

        self.assertEqual(result, 0)
        self.assertEqual(self.views(), [3, 2, 2, 1])
        self.assertEqual(self.labels, {number: {LABEL_AGENT} for number in self.labels})
        self.assertEqual(self.rows("read-skip"), [])
        summary = self.rows("read-summary")[-1]
        self.assertEqual((summary["failed"], summary["skipped"], summary["retries"]), (0, 0, 1))

    def test_mutation_failure_is_not_retried(self) -> None:
        self.mutation_failure = (2, "comment")

        with self.assertRaises(SystemExit):
            triage.main(["--issue", "2"])

        comments = [command for command in self.calls
                    if command[1:3] == ["issue", "comment"] and command[3] == "2"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(self.labels[2], {LABEL_TRIAGE})


if __name__ == "__main__":
    unittest.main()
