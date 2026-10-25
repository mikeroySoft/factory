from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, triage, watch
from factory.config import LABEL_AGENT


class WatchLifecycleTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        triage.configure(self.cfg)

    @staticmethod
    def issue(*, watched: bool) -> dict:
        return {
            "number": 7,
            "title": "factory runtime failure: lifecycle worker boom" if watched else "parser bug",
            "body": "Fingerprint: abc123\n\nevidence" if watched else
                    ("## Problem\nThe config parser raises KeyError on an empty [gate] table instead of using defaults.\n\n"
                     "## Acceptance criteria\n- [ ] An empty [gate] table loads with default checks.\n\n"
                     "## Exit gate\n`python -m unittest tests.test_config` passes."),
            "comments": [],
            "labels": [{"name": "bug"}],
        }

    def test_watch_issue_bypasses_model_for_agent_investigation(self):
        lifecycle.append(dispatch.EVENTS, {"event": "watch", "fingerprint": "abc123", "issue": 7,
                                           "at": "2026-01-01T00:00:00Z"})
        with mock.patch.object(triage, "call_llm", side_effect=AssertionError("model called")):
            decision = triage.triage_issue(self.issue(watched=True))
        self.assertEqual(decision["decision"], LABEL_AGENT)
        self.assertIn("commit nothing", decision["brief"])
        self.assertIn("manager can resolve it", decision["brief"])

    def test_copied_watch_format_without_a_journal_filing_uses_model(self):
        answer = json.dumps({"decision": LABEL_AGENT, "rationale": "clear", "question": "", "brief": "fix it"})
        with mock.patch.object(triage, "call_llm", return_value=answer):
            decision = triage.triage_issue(self.issue(watched=True))
        self.assertNotEqual(decision["brief"], triage.WATCH_BRIEF)

    def test_normal_issue_still_uses_model(self):
        answer = json.dumps({"decision": LABEL_AGENT, "rationale": "clear", "question": "", "brief": "fix it"})
        with mock.patch.object(triage, "call_llm", return_value=answer) as model:
            decision = triage.triage_issue(self.issue(watched=False))
        self.assertEqual(decision["decision"], LABEL_AGENT)
        model.assert_called_once()

    def journal(self, fingerprint: str, issue: int, age: timedelta, **fields):
        at = (datetime.now(UTC) - age).isoformat().replace("+00:00", "Z")
        lifecycle.append(dispatch.EVENTS, {"event": "watch", "fingerprint": fingerprint,
                                           "issue": issue, "at": at, **fields})
        return at

    @mock.patch.object(watch, "orphaned_claims", new=lambda cfg: [])
    def run_watch(self, states: dict[int, str], *, signals=(), dry_run=False):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["gh", "issue", "view"]:
                state = states[int(command[3])]
                assignees = [{"login": "agent"}] if state == "CLAIMED" else []
                label = config.LABEL_HUMAN if state == "HUMAN" else config.LABEL_AGENT
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"state": "OPEN" if state in ("CLAIMED", "HUMAN") else state,
                                            "assignees": assignees, "labels": [{"name": label}]}), "")
            if command[:3] == ["gh", "issue", "close"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ["gh", "issue", "list"]:  # no open engine filing shares the fingerprint
                return subprocess.CompletedProcess(command, 0, "[]", "")
            raise AssertionError(command)

        with mock.patch.object(dispatch, "run", side_effect=run), \
             mock.patch.object(watch, "signals", return_value=list(signals)), \
             mock.patch.object(config, "engine_commit", return_value="a" * 40):
            watch.run_pass(self.cfg, dry_run=dry_run)
        return calls

    def test_absent_old_open_filing_closes_and_journals(self):
        at = self.journal("old", 10, timedelta(hours=25))
        calls = self.run_watch({10: "OPEN"})
        close = next(call for call in calls if call[:3] == ["gh", "issue", "close"])
        self.assertEqual(close[close.index("--reason") + 1], "completed")
        comment = close[close.index("--comment") + 1]
        self.assertIn(f"has not recurred since {at}", comment)
        self.assertIn("engine aaaaaaaaaaaa", comment)
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(rows[-1]["closed"])

    def test_recurring_and_young_filings_stay_open(self):
        self.journal("recurs", 11, timedelta(days=2))
        self.journal("young", 12, timedelta(hours=23))
        signal = {"fingerprint": "recurs", "latest": None}
        calls = self.run_watch({11: "OPEN", 12: "OPEN"}, signals=[signal])
        self.assertFalse(any(call[:3] == ["gh", "issue", "close"] for call in calls))

    def test_signal_with_only_old_evidence_still_closes(self):
        self.journal("stale", 14, timedelta(days=3))
        old = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
        calls = self.run_watch({14: "OPEN"}, signals=[{"fingerprint": "stale", "latest": old}])
        self.assertTrue(any(call[:4] == ["gh", "issue", "close", "14"] for call in calls))

    def test_claimed_issue_is_not_closed(self):
        self.journal("claimed", 15, timedelta(days=3))
        calls = self.run_watch({15: "CLAIMED"})
        self.assertFalse(any(call[:3] == ["gh", "issue", "close"] for call in calls))

    def test_issue_handed_to_a_human_is_not_closed(self):
        self.journal("escalated", 16, timedelta(days=3))
        calls = self.run_watch({16: "HUMAN"})
        self.assertFalse(any(call[:3] == ["gh", "issue", "close"] for call in calls))

    def test_externally_closed_filing_is_journaled_and_not_rechecked(self):
        self.journal("fixed", 17, timedelta(days=3))
        first = self.run_watch({17: "CLOSED"})
        self.assertTrue(any(call[:3] == ["gh", "issue", "view"] for call in first))
        second = self.run_watch({17: "CLOSED"})
        self.assertFalse(any(call[:3] == ["gh", "issue", "view"] for call in second))

    def test_dry_run_closes_nothing(self):
        self.journal("old", 13, timedelta(days=2))
        with mock.patch("builtins.print") as output:
            calls = self.run_watch({13: "OPEN"}, dry_run=True)
        self.assertEqual(calls, [])
        self.assertTrue(any("would close #13" in str(call) for call in output.call_args_list))
        self.assertFalse(any(row.get("closed") for row in lifecycle.read_events(dispatch.EVENTS)))


if __name__ == "__main__":
    unittest.main()
