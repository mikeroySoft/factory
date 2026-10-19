"""A model timeout skips one ticket; an unreachable endpoint still ends the triage pass."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from factory import lifecycle, triage
from factory.config import LABEL_AGENT, LABEL_TRIAGE, LABEL_WONTFIX, Config

BODY = "Change the parser so empty input is rejected.\n\nExit gate: `python -m unittest` passes with a new test."
DECISION = json.dumps({"decision": LABEL_AGENT, "rationale": "clear", "question": "", "brief": "do it"})


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TriagePass(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.dict(triage.__dict__))
        cfg = Config(root=Path(temp.name), repo="local/triage")
        cfg.factory.mkdir(parents=True)
        triage.configure(cfg)
        self.events = cfg.factory / "events.jsonl"
        self.listed = [3, 2, 1]  # gh issue list: newest first
        self.labels = {n: {LABEL_TRIAGE} for n in self.listed}
        self.asked: list[int] = []
        self.failure: dict[int, BaseException] = {}
        self.decision: dict[int, str] = {}

    def gh(self, *args, execution=None):
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n} for n in self.listed if LABEL_TRIAGE in self.labels[n]])
        n = int(args[2])
        if args[:2] == ("issue", "view"):
            return json.dumps({"number": n, "title": f"t{n}", "body": BODY, "comments": [],
                               "labels": [{"name": label} for label in self.labels[n]], "state": "OPEN"})
        if args[:2] == ("issue", "edit"):
            self.labels[n] = (self.labels[n] - {args[args.index("--remove-label") + 1]}) | {
                args[args.index("--add-label") + 1]}
        return ""

    def urlopen(self, req, timeout):
        n = int(json.loads(req.data)["messages"][1]["content"].split(":")[0].removeprefix("Issue #"))
        self.asked.append(n)
        if n in self.failure:
            raise self.failure[n]
        decision = json.dumps({"decision": self.decision[n], "rationale": "no", "question": "", "brief": ""}) \
            if n in self.decision else DECISION
        return Reply(json.dumps({"choices": [{"message": {"content": decision}}]}).encode())

    def run_pass(self) -> int:
        args = argparse.Namespace(issue=None, replay=None, dry_run=False)
        with patch.object(triage, "gh", self.gh), \
                patch.object(triage.urllib.request, "urlopen", self.urlopen), \
                redirect_stderr(io.StringIO()), \
                lifecycle.scope(self.events, "triage", lazy=True) as execution:
            return triage.execute(args, execution)

    def ticket_exits(self) -> dict[int, str]:
        return {row["ticket"]: row["reason"] for row in lifecycle.read_events(self.events)
                if row.get("stage") == "triage-ticket" and row.get("kind") == "exit"}

    def test_timeout_skips_only_that_ticket(self):
        self.failure[3] = TimeoutError("timed out")
        self.assertEqual(self.run_pass(), 1)
        self.assertEqual(self.asked, [3, 2, 1])
        self.assertEqual(self.labels, {3: {LABEL_TRIAGE}, 2: {LABEL_AGENT}, 1: {LABEL_AGENT}})
        self.assertEqual(self.ticket_exits(), {3: "triage_endpoint_timeout", 2: LABEL_AGENT, 1: LABEL_AGENT})
        results = [row for row in lifecycle.read_events(self.events)
                   if row.get("kind") == "result" and row.get("ticket") == 3]
        self.assertEqual([row["timed_out"] for row in results], [True])

    def test_previously_timed_out_ticket_goes_after_untried_ones(self):
        self.failure[3] = TimeoutError("timed out")
        self.run_pass()
        self.listed = [4, 3, 2, 1]
        self.labels.update({4: {LABEL_TRIAGE}, 2: {LABEL_TRIAGE}, 1: {LABEL_TRIAGE}})
        self.asked.clear()
        self.run_pass()
        self.assertEqual(self.asked, [4, 2, 1, 3])

    def test_wontfix_proposal_is_labelled_and_not_retriaged(self):
        self.decision[2] = LABEL_WONTFIX
        self.run_pass()
        self.assertEqual(self.labels[2], {LABEL_WONTFIX})
        self.asked.clear()
        self.run_pass()
        self.assertEqual(self.asked, [])

    def test_connection_refused_ends_the_pass_after_one_attempt(self):
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        self.failure.update(dict.fromkeys(self.listed, refused))
        with self.assertRaises(SystemExit) as ended:
            self.run_pass()
        self.assertEqual(ended.exception.code, 2)
        self.assertEqual(self.asked, [3])
        self.assertEqual(self.labels, {n: {LABEL_TRIAGE} for n in self.listed})
        self.assertEqual(self.ticket_exits(), {3: "triage_endpoint_unavailable"})

    def test_connect_timeout_ends_the_pass_after_one_attempt(self):
        self.failure.update(dict.fromkeys(self.listed, urllib.error.URLError(TimeoutError("timed out"))))
        with self.assertRaises(SystemExit) as ended:
            self.run_pass()
        self.assertNotIsInstance(ended.exception, triage.EndpointTimeout)
        self.assertEqual(self.asked, [3])
        self.assertEqual(self.ticket_exits(), {3: "triage_endpoint_timeout"})


class Reproposal(unittest.TestCase):
    """At most one triage comment per issue state: only a change after it earns another."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.dict(triage.__dict__))
        cfg = Config(root=Path(temp.name), repo="local/triage")
        cfg.factory.mkdir(parents=True)
        triage.configure(cfg)
        self.events = cfg.factory / "events.jsonl"
        self.clock = 0  # seconds; every GitHub write advances it and bumps updatedAt
        self.updated = 0
        self.labels = {LABEL_TRIAGE}
        self.comments: list[dict] = []
        self.edit_fails = False
        self.asked = 0

    def stamp(self, seconds: int) -> str:
        return f"2026-10-17T{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}Z"

    def write(self):
        self.clock += 1
        self.updated = self.clock

    def gh(self, *args, execution=None):
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": 7}] if LABEL_TRIAGE in self.labels else [])
        if args[:2] == ("issue", "view"):
            return json.dumps({"number": 7, "title": "t7", "body": BODY, "comments": self.comments,
                               "labels": [{"name": label} for label in self.labels], "state": "OPEN",
                               "updatedAt": self.stamp(self.updated)})
        if args[:2] == ("issue", "comment"):
            self.write()
            self.comments.append({"author": {"login": "bot"}, "body": args[args.index("--body") + 1],
                                  "createdAt": self.stamp(self.clock)})
        if args[:2] == ("issue", "edit"):
            if self.edit_fails:
                raise SystemExit(1)
            self.write()
            self.labels = (self.labels - {args[args.index("--remove-label") + 1]}) | {
                args[args.index("--add-label") + 1]}
        return ""

    def urlopen(self, req, timeout):
        self.asked += 1
        decision = json.dumps({"decision": LABEL_WONTFIX, "rationale": "no", "question": "", "brief": ""})
        return Reply(json.dumps({"choices": [{"message": {"content": decision}}]}).encode())

    def run_pass(self) -> int:
        self.clock += 660  # the next timer pass
        args = argparse.Namespace(issue=None, replay=None, dry_run=False)
        with patch.object(triage, "gh", self.gh), \
                patch.object(triage.urllib.request, "urlopen", self.urlopen), \
                redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()), \
                lifecycle.scope(self.events, "triage", lazy=True) as execution:
            return triage.execute(args, execution)

    def human(self, comment: str | None = None, labels: set[str] | None = None):
        self.clock += 300
        self.write()
        if comment:
            self.comments.append({"author": {"login": "owner"}, "body": comment,
                                  "createdAt": self.stamp(self.clock)})
        if labels is not None:
            self.labels = labels

    def test_unchanged_needs_triage_issue_gets_one_proposal(self):
        self.edit_fails = True  # the comment lands, the label swap does not: still needs-triage
        with self.assertRaises(SystemExit):
            self.run_pass()
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(self.asked, 1)
        self.assertEqual(self.labels, {LABEL_TRIAGE})

    def test_new_human_comment_allows_another_proposal(self):
        self.edit_fails = True
        with self.assertRaises(SystemExit):
            self.run_pass()
        self.human(comment="It is still broken on main.")
        self.edit_fails = False
        self.run_pass()
        self.assertEqual([c["author"]["login"] for c in self.comments], ["bot", "owner", "bot"])
        self.assertEqual(self.labels, {LABEL_WONTFIX})

    def test_label_change_after_proposal_allows_another_proposal(self):
        self.run_pass()
        self.assertEqual(self.labels, {LABEL_WONTFIX})
        self.human(labels={LABEL_TRIAGE})  # the owner disagrees and puts needs-triage back
        self.run_pass()
        self.assertEqual(len(self.comments), 2)
        self.assertEqual(self.asked, 2)
        self.assertEqual(self.labels, {LABEL_WONTFIX})


if __name__ == "__main__":
    unittest.main()
