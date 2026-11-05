"""Timeouts leave tickets retryable and escalate one durable streak, not every pass."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from factory import cli, config, dispatch, lifecycle, triage
from factory.config import LABEL_AGENT, LABEL_HUMAN, LABEL_TRIAGE


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TriageTimeoutTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), "local/triage")
        self.cfg.factory.mkdir()
        self.cfg.lock = self.cfg.root / "triage.lock"
        self.labels = {1: {LABEL_TRIAGE}, 2: {LABEL_TRIAGE}}
        self.timeouts = {1}
        self.comments = {1: [], 2: []}
        self.asked = []
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.dict(triage.__dict__))
        self.enterContext(patch.dict(dispatch.__dict__))
        self.enterContext(patch.object(config, "load", return_value=self.cfg))
        self.enterContext(patch.object(triage, "gh", side_effect=self.gh))
        self.enterContext(patch.object(triage.urllib.request, "urlopen", side_effect=self.urlopen))
        self.enterContext(patch.object(dispatch, "run", side_effect=self.run_command))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def gh(self, *args, execution=None):
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n} for n, labels in self.labels.items()
                               if LABEL_TRIAGE in labels])
        n = int(args[2])
        if args[:2] == ("issue", "view"):
            return json.dumps({
                "number": n, "title": f"Ticket {n}", "state": "OPEN",
                "body": "Reject empty parser input rather than silently accepting it.\n\n"
                        "Acceptance: regression test rejects empty input.",
                "comments": self.comments[n],
                "labels": [{"name": label} for label in self.labels[n]],
                "updatedAt": "2026-11-04T01:00:00+00:00",
            })
        if args[:2] == ("issue", "edit"):
            for flag, label in pairwise(args[3:]):
                if flag == "--remove-label":
                    self.labels[n].discard(label)
                elif flag == "--add-label":
                    self.labels[n].add(label)
            return ""
        if args[:2] == ("issue", "comment"):
            self.comments[n].append({
                "body": args[args.index("--body") + 1],
                "author": {"login": "bot"}, "createdAt": "2026-11-04T00:00:00+00:00",
            })
            return f"https://github.com/local/triage/issues/{n}#issuecomment-{len(self.comments[n])}"
        raise AssertionError(args)

    def run_command(self, command, **kwargs):
        self.assertEqual(command[0], "gh")
        stdout = self.gh(*command[1:])
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def urlopen(self, request, timeout):
        n = int(json.loads(request.data)["messages"][1]["content"].split(":")[0].removeprefix("Issue #"))
        self.asked.append(n)
        if n in self.timeouts:
            raise TimeoutError("model read timed out")
        decision = {"decision": LABEL_AGENT, "rationale": "clear", "question": "", "brief": "fix it"}
        return Reply(json.dumps({"choices": [{"message": {"content": json.dumps(decision)}}]}).encode())

    def rows(self, event):
        return [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                if row.get("event") == event]

    def test_triage_timeout_retry_and_single_escalation(self):
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.asked, [1, 2])
        self.assertEqual(self.labels, {1: {LABEL_TRIAGE}, 2: {LABEL_AGENT}})
        exits = [row for row in self.rows("lifecycle") if row.get("kind") == "exit"]
        self.assertEqual([(row["stage"], row["reason"]) for row in exits], [
            ("triage-ticket", "triage_endpoint_timeout"),
            ("triage-ticket", LABEL_AGENT), ("triage", "triage_endpoint_timeout"),
        ])
        self.assertEqual(self.rows("escalate"), [])
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.rows("escalate"), [])
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.labels[1], {LABEL_TRIAGE, LABEL_HUMAN})
        self.assertEqual([(row["ticket"], row["code"]) for row in self.rows("escalate")],
                         [(1, "triage_endpoint_timeout")])
        packet = Path(self.rows("escalate")[0]["packet"])
        self.assertIn("3 consecutive", packet.read_text())
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 1)
        self.assertEqual(len(self.comments[1]), 1)
        self.timeouts.clear()
        self.assertEqual(cli.main(["triage"]), 0)
        self.assertNotIn(LABEL_TRIAGE, self.labels[1])
        self.assertIn(LABEL_AGENT, self.labels[1])
        self.assertEqual(self.asked, [1, 2, 1, 1, 1, 1, 1])

    def test_triage_retry_success_resets_ticket_streak(self):
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        self.timeouts.clear()
        self.assertEqual(cli.main(["triage"]), 0)
        self.labels[1] = {LABEL_TRIAGE}
        self.timeouts = {1}
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.rows("escalate"), [])
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual([row["ticket"] for row in self.rows("escalate")], [1])

    def test_triage_timeout_streaks_are_ticket_local(self):
        self.timeouts = {1, 2}
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.rows("escalate"), [])
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual([row["ticket"] for row in self.rows("escalate")], [1, 2])
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 2)

    def test_triage_timeout_preview_does_not_escalate(self):
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertEqual(cli.main(["triage", "--dry-run"]), 1)
        self.assertEqual(cli.main(["triage", "--replay", "1"]), 1)
        self.assertEqual(lifecycle.read_events(self.cfg.factory / "events.jsonl"), rows)
        self.assertEqual(self.comments[1], [])
        self.assertEqual(self.labels[1], {LABEL_TRIAGE})


if __name__ == "__main__":
    unittest.main()
