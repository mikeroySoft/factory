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

from factory import cli, config, dispatch, handoff, lifecycle, manage, triage
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

    def test_triage_timeout_escalation_preserves_uninterrupted_streak(self):
        for _ in range(7):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 1)
        self.assertEqual(len(self.comments[1]), 1)
        exits = [row for row in self.rows("lifecycle")
                 if row.get("kind") == "exit" and row.get("stage") == "triage-ticket"
                 and row.get("ticket") == 1]
        self.assertEqual([row["reason"] for row in exits], ["triage_endpoint_timeout"] * 7)
        self.assertEqual(self.labels[1], {LABEL_TRIAGE, LABEL_HUMAN})

    def test_triage_timeout_never_reaches_manager(self):
        for _ in range(3):
            self.assertEqual(cli.main(["triage"]), 1)
        self.cfg.manager = ["manager", "{prompt}"]
        self.cfg.manager_rounds = 4
        issue = json.loads(self.gh("issue", "view", "1"))
        manager_calls = []

        def run(command, **kwargs):
            if command[0] == "gh":
                return self.run_command(command, **kwargs)
            manager_calls.append(command)
            return subprocess.CompletedProcess(command, 0, "DECISION: RETRY\nTry again.", "")

        with patch.object(dispatch, "gh_json", return_value=[issue]), \
                patch.object(dispatch, "initiative_kind", return_value=None), \
                patch.object(manage, "human_activity", return_value=False), \
                patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()
        self.assertEqual(manager_calls, [], "a timeout must reach a human, never a manager")
        self.assertEqual(self.labels[1], {LABEL_TRIAGE, LABEL_HUMAN})
        self.assertIsNotNone(handoff.terminal(
            self.cfg, lifecycle.read_events(dispatch.EVENTS), self.rows("escalate")[-1],
        ))

    def expire_lifecycle_history(self):
        with patch.object(lifecycle, "MAX_BYTES", 1024), \
                patch.object(lifecycle, "RETENTION", 1):
            for _ in range(3):
                dispatch.record("rotation_churn", padding="x" * 2048)
        self.assertFalse(any(
            row.get("event") == "lifecycle" and row.get("kind") == "exit"
            and row.get("ticket") == 1
            for row in lifecycle.read_events(dispatch.EVENTS)
        ))

    def test_triage_timeout_retention_does_not_repeat_escalation(self):
        for _ in range(3):
            self.assertEqual(cli.main(["triage"]), 1)
        self.expire_lifecycle_history()
        for _ in range(3):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 1)
        self.assertEqual(len(self.comments[1]), 1)
        self.assertEqual(self.labels[1], {LABEL_TRIAGE, LABEL_HUMAN})

    def test_triage_timeout_retention_preserves_count_and_reset(self):
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        self.expire_lifecycle_history()
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 1)
        self.timeouts.clear()
        self.assertEqual(cli.main(["triage"]), 0)
        self.expire_lifecycle_history()
        self.labels[1] = {LABEL_TRIAGE}
        self.timeouts = {1}
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 1)
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.rows("escalate")), 2)

    def test_triage_timeout_does_not_spend_manager_round(self):
        for _ in range(3):
            self.assertEqual(cli.main(["triage"]), 1)
        self.timeouts.clear()
        self.assertEqual(cli.main(["triage"]), 0)
        self.cfg.manager = ["manager", "{prompt}"]
        self.cfg.manager_rounds = 1
        dispatch.escalate(1, "worker gate failed", None, code="gate_failed")
        issue = json.loads(self.gh("issue", "view", "1"))
        escalation = self.rows("escalate")[-1]
        self.assertIsNone(handoff.terminal(
            self.cfg, lifecycle.read_events(dispatch.EVENTS), escalation,
        ), "a recovered ticket must retain its manager recovery")
        manager_calls = []

        def run(command, **kwargs):
            if command[0] == "gh":
                return self.run_command(command, **kwargs)
            manager_calls.append(command)
            return subprocess.CompletedProcess(command, 0, "DECISION: RETRY\nTry again.", "")

        with patch.object(dispatch, "gh_json", return_value=[issue]), \
                patch.object(dispatch, "initiative_kind", return_value=None), \
                patch.object(manage, "human_activity", return_value=False), \
                patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()
        self.assertEqual(len(manager_calls), 1)
        self.assertEqual([(row["round"], row["decision"]) for row in self.rows("manage")],
                         [(1, "RETRY")])
        self.assertIn(LABEL_AGENT, self.labels[1])
        self.assertNotIn(LABEL_HUMAN, self.labels[1])

    def test_triage_timeout_retries_failed_human_gate_delivery(self):
        for _ in range(2):
            self.assertEqual(cli.main(["triage"]), 1)
        self.labels[2] = {LABEL_TRIAGE}
        self.comments[2] = []

        def fail_edit(command, **kwargs):
            if command[:4] == ["gh", "issue", "edit", "1"]:
                return subprocess.CompletedProcess(command, 1, "", "GitHub unavailable")
            return self.run_command(command, **kwargs)

        with patch.object(dispatch, "run", side_effect=fail_edit):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.labels, {1: {LABEL_TRIAGE}, 2: {LABEL_AGENT}})
        self.assertEqual(self.comments[1], [], "failed delivery must not post an escalation")
        self.expire_lifecycle_history()
        self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(self.labels[1], {LABEL_TRIAGE, LABEL_HUMAN})
        self.assertEqual(len(self.comments[1]), 1)
        self.expire_lifecycle_history()
        for _ in range(3):
            self.assertEqual(cli.main(["triage"]), 1)
        self.assertEqual(len(self.comments[1]), 1, "successful delivery must not repeat")


if __name__ == "__main__":
    unittest.main()
