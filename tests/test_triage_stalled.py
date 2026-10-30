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
from factory.config import LABEL_AGENT, LABEL_TRIAGE, LABEL_WONTFIX


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
            "number": 1000,
            "title": "Reject empty parser input",
            "body": "Change the parser so empty input is rejected.\n\nAcceptance: a regression test passes.",
            "comments": [],
            "labels": [{"name": LABEL_TRIAGE}],
            "state": "OPEN",
            "updatedAt": "2026-10-30T00:00:00+00:00",
            "author": {"login": "owner"},
        }
        self.issues = {1000: self.issue}
        self.listed = [1000]
        self.decisions: dict[int, str] = {}
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
            return json.dumps([
                {"number": number}
                for number in self.listed
                if any(label["name"] == LABEL_TRIAGE for label in self.issues[number]["labels"])
            ])
        number = int(args[2])
        issue = self.issues[number]
        if args[:2] == ("issue", "view"):
            return json.dumps(issue)
        if args[:2] == ("issue", "comment"):
            issue["comments"].append({
                "body": args[args.index("--body") + 1],
                "createdAt": "2026-10-30T00:01:00+00:00",
                "author": {"login": "factory-bot"},
            })
            issue["updatedAt"] = "2026-10-30T00:01:00+00:00"
            return ""
        if args[:2] == ("issue", "edit"):
            issue["labels"] = [{"name": args[args.index("--add-label") + 1]}]
            return ""
        raise AssertionError(f"unexpected triage gh call: {args}")

    def urlopen(self, request, timeout):
        number = int(
            json.loads(request.data)["messages"][1]["content"].split(":")[0].removeprefix("Issue #")
        )
        content = self.decisions.get(number, "not json")
        return Reply(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

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
            [(1000, "unparseable_decision")],
        )

        self.assertEqual(cli.main(["watch"]), 0)
        creates = [call for call in self.watch_calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("unparseable_decision", creates[0][creates[0].index("--body") + 1])

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.issue["comments"]), 1)
        self.assertEqual(len(self.rows("triage_stalled")), 1)

    def test_streaks_are_consecutive_and_isolated_per_ticket(self) -> None:
        for number in (1001, 999):
            self.issues[number] = {
                **self.issue,
                "number": number,
                "comments": [],
                "labels": [{"name": LABEL_TRIAGE}],
            }
        self.listed = [1001, 999, 1000]

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.rows("triage_stalled"), [])

        self.decisions = {
            1001: json.dumps({
                "decision": LABEL_WONTFIX, "rationale": "not planned", "question": "", "brief": "",
            }),
            999: json.dumps({
                "decision": LABEL_AGENT, "rationale": "clear", "question": "", "brief": "do it",
            }),
            1000: json.dumps({
                "decision": LABEL_AGENT, "rationale": "clear", "question": "", "brief": "do it",
            }),
        }
        self.assertEqual(cli.main(["triage"]), 0)

        self.decisions.clear()
        for issue in self.issues.values():
            issue["labels"] = [{"name": LABEL_TRIAGE}]
            issue["updatedAt"] = "2026-10-30T01:00:00+00:00"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["triage"]), 1)

        self.assertEqual(self.rows("triage_stalled"), [])
        self.assertFalse(any(
            "Factory triage stalled" in comment["body"]
            for issue in self.issues.values()
            for comment in issue["comments"]
        ))

    def test_unstalled_parse_failure_does_not_hide_later_failure(self) -> None:
        dispatch.configure(self.cfg)
        for row in (
            {
                "event": "lifecycle", "kind": "exit", "stage": "triage-ticket",
                "outcome": "unknown", "reason": "unparseable_decision", "ticket": 1000,
                "at": "2026-10-30T00:00:00Z",
            },
            {
                "event": "lifecycle", "kind": "exit", "stage": "manage",
                "outcome": "mechanism_failure", "reason": "later_failure",
                "at": "2026-10-30T00:01:00Z",
            },
        ):
            lifecycle.append(self.cfg.factory / "events.jsonl", row)

        filed = watch.run_pass(self.cfg)

        self.assertEqual([signal["key"] for signal in filed], ["later_failure"])
        creates = [call for call in self.watch_calls if call[:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)


if __name__ == "__main__":
    unittest.main()
