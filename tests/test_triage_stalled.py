from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, lifecycle, triage, watch
from factory.config import LABEL_TRIAGE


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RepeatedUnparseableDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.cfg.lock = self.root / "triage.lock"
        self.issue = {
            "number": 7,
            "title": "Reject empty parser input",
            "body": "Change the parser so empty input is rejected.\n\nAcceptance: a regression test passes.",
            "comments": [],
            "labels": [{"name": LABEL_TRIAGE}],
            "state": "OPEN",
            "updatedAt": "2026-10-30T00:00:00+00:00",
            "author": {"login": "owner"},
        }
        self.triage_calls: list[tuple[str, ...]] = []
        self.watch_calls: list[list[str]] = []
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.object(config, "load", return_value=self.cfg))
        self.enterContext(mock.patch.object(triage, "gh", side_effect=self.gh))
        self.enterContext(mock.patch.object(triage.urllib.request, "urlopen", side_effect=self.urlopen))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.run_command))
        self.enterContext(mock.patch.object(watch, "failed_units", return_value=[]))
        self.enterContext(mock.patch.object(watch, "runtime_errors", return_value=[]))

    def gh(self, *args: str, execution=None) -> str:
        self.triage_calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": 7}])
        if args[:2] == ("issue", "view"):
            return json.dumps(self.issue)
        if args[:2] == ("issue", "comment"):
            self.issue["comments"].append({
                "body": args[args.index("--body") + 1],
                "createdAt": "2026-10-30T00:01:00+00:00",
                "author": {"login": "factory-bot"},
            })
            self.issue["updatedAt"] = "2026-10-30T00:01:00+00:00"
            return ""
        raise AssertionError(f"unexpected triage gh call: {args}")

    @staticmethod
    def urlopen(request, timeout):
        return Reply(json.dumps({"choices": [{"message": {"content": "not json"}}]}).encode())

    def run_command(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.watch_calls.append(command)
        if command[:3] == ["gh", "issue", "list"]:
            stdout = "[]"
        elif command[:3] == ["gh", "issue", "create"]:
            stdout = "https://github.com/mikeroySoft/factory/issues/101\n"
        else:
            raise AssertionError(f"unexpected watch command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                if row.get("event") == event]

    def test_second_unparseable_pass_comments_once_and_watch_files(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.issue["comments"], [])
        self.assertEqual(self.rows("triage_stalled"), [])

        dispatch.configure(self.cfg)
        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertFalse(any(call[:3] == ["gh", "issue", "create"] for call in self.watch_calls))

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.issue["comments"]), 1)
        self.assertIn("unparseable", self.issue["comments"][0]["body"])
        self.assertEqual(
            [(row["ticket"], row["reason"]) for row in self.rows("triage_stalled")],
            [(7, "unparseable_decision")],
        )

        self.assertEqual(cli.main(["watch"]), 0)
        creates = [call for call in self.watch_calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("unparseable_decision", creates[0][creates[0].index("--body") + 1])

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.issue["comments"]), 1)
        self.assertEqual(len(self.rows("triage_stalled")), 1)


if __name__ == "__main__":
    unittest.main()
