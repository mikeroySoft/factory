"""Skill regressions and load failures feed bounded learn evidence (#439)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import dispatch, learn, lifecycle
from factory.config import Config

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
AT = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")


def load(session: str, ticket: int, *, ok: bool = True) -> dict:
    return {"event": "skill-load", "ticket": ticket, "attempt": 1, "session": session,
            "harness": "default", "skill": "ub-mode", "path": "skill://ub-mode", "ok": ok,
            "error": None if ok else "Unknown skill: ub-mode", "partial": False,
            "call_index": 0, "explicit": True, "tokens": 953, "at": AT}


class SkillLearnEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
            EVENTS=self.events, MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.enterContext(mock.patch("factory.stats.gh", return_value=[]))

    def journal(self, *rows: dict) -> None:
        self.events.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_load_failure_is_evidence_below_ten_sessions(self) -> None:
        self.journal(
            {"event": "claimed", "ticket": 7, "title": "use ub", "at": AT},
            {"event": "attempt", "ticket": 7, "attempt": 1, "transcript": "s", "at": AT},
            load("s", 7, ok=False),
            {"event": "merged", "ticket": 7, "at": AT},
        )

        tickets, text = learn.evidence(10)

        self.assertEqual(tickets, [7])
        self.assertIn("Skill ub-mode failure evidence", text)
        self.assertIn("[load_failure]", text)
        self.assertIn("Unknown skill: ub-mode", text)

    def test_flagged_skill_has_at_most_three_bounded_tagged_excerpts(self) -> None:
        rows = []
        for ticket in range(1, 11):
            session = f"loaded-{ticket}"
            rows += [
                {"event": "score", "ticket": ticket, "attempt": 1, "transcript": session,
                 "call_errors": [False] + [True] * 10,
                 "failures": [{"call_index": index, "text": "command not found " + "x" * 4000}
                              for index in range(1, 11)], "at": AT},
                load(session, ticket),
            ]
        for ticket in range(20, 30):
            rows.append({"event": "score", "ticket": ticket, "attempt": 1,
                         "transcript": f"control-{ticket}", "call_errors": [False] * 11,
                         "failures": [], "at": AT})

        evidence = learn.skill_failure_evidence(rows)

        self.assertEqual(len(evidence), 3)
        self.assertTrue(all(item["skill"] == "ub-mode" for item in evidence))
        self.assertTrue(all(item["tag"] in {
            "trigger_miss", "load_failure", "stale_command", "skipped_step",
            "missed_section", "ignored_rule",
        } for item in evidence))
        self.assertTrue(all(len(item["excerpt"]) <= 3000 for item in evidence))
        self.assertTrue(all(item["tag"] == "stale_command" for item in evidence))


if __name__ == "__main__":
    unittest.main()
