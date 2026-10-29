"""Skill loads are journaled from scored worker transcripts (#439)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle, score, triage
from factory.config import Config


def message(role: str, *content: dict, timestamp: str | None = None, **fields: object) -> str:
    row = {"type": "message", "message": {"role": role, "content": list(content), **fields}}
    if timestamp:
        row["timestamp"] = timestamp
    return json.dumps(row)


def call(call_id: str, name: str, timestamp: str, **arguments: object) -> str:
    return message("assistant", {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments},
                   timestamp=timestamp)


def result(call_id: str, text: str, *, error: bool = False) -> str:
    return message("toolResult", {"type": "text", "text": text}, toolCallId=call_id, isError=error)


class SkillScoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.cfg = Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
            EVENTS=self.events, MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.enterContext(mock.patch.object(triage, "call_llm", return_value="1"))
        skill = self.root / "skills" / "factory" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("12345678901234567890")
        self.transcript = self.cfg.factory / "transcripts" / "7" / "session.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("\n".join([
            json.dumps({"type": "session", "id": "session-7", "version": 3}),
            message("user", {"type": "text", "text": "Follow the alpha skill. Factory is not named here."}),
            call("a", "read", "2026-10-28T10:00:00Z", path="skill://alpha"),
            result("a", "Unknown skill: alpha\ninstall it", error=True),
            call("b", "read", "2026-10-28T10:01:00Z", path="skill://alpha", offset=2),
            result("b", "alpha contents"),
            call("c", "read", "2026-10-28T10:02:00Z", path="skill://alpha"),
            result("c", "alpha contents"),
            call("d", "read", "2026-10-28T10:03:00Z", path="skills/factory/SKILL.md"),
            result("d", "factory contents"),
            call("e", "bash", "2026-10-28T10:04:00Z", command="printf skill://ignored"),
            result("e", "skill://ignored"),
            message("assistant", {"type": "text", "text": "done"}),
        ]) + "\n")
        handoff = self.cfg.factory / "wt-7" / ".factory" / "handoff-7.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("done")
        with self.events.open("w") as stream:
            stream.write(json.dumps({"event": "attempt", "ticket": 7, "attempt": 1, "worker": "default",
                                     "transcript": str(self.transcript), "gate": "PASS",
                                     "at": "2026-10-28T10:05:00Z"}) + "\n")
            stream.write(json.dumps({"event": "merged", "ticket": 7, "at": "2026-10-28T10:06:00Z"}) + "\n")
            stream.write(json.dumps({"event": "attempt", "ticket": 8, "attempt": 1, "worker": "chore",
                                     "transcript": None, "gate": "PASS",
                                     "at": "2026-10-28T10:07:00Z"}) + "\n")
            stream.write(json.dumps({"event": "merged", "ticket": 8, "at": "2026-10-28T10:08:00Z"}) + "\n")

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == event]

    def test_records_load_shape_coverage_and_is_idempotent(self) -> None:
        score.run(self.cfg)

        loads = self.rows("skill-load")
        alpha = [row for row in loads if row["skill"] == "alpha"]
        self.assertEqual([(row["ok"], row["partial"], row["call_index"]) for row in alpha],
                         [(False, False, 0), (True, True, 1), (True, False, 2)])
        self.assertEqual(alpha[0]["error"], "Unknown skill: alpha")
        self.assertEqual({row["session"] for row in loads}, {str(self.transcript)})
        self.assertEqual({row["harness"] for row in loads}, {"default"})
        self.assertEqual([row["at"] for row in alpha], [
            "2026-10-28T10:00:00Z", "2026-10-28T10:01:00Z", "2026-10-28T10:02:00Z",
        ])
        self.assertTrue(all(row["explicit"] for row in alpha))
        [factory] = [row for row in loads if row["skill"] == "factory"]
        self.assertEqual((factory["path"], factory["tokens"], factory["explicit"]),
                         ("skills/factory/SKILL.md", 5, False))
        self.assertNotIn("ignored", [row["skill"] for row in loads])
        self.assertEqual(self.rows("skill-coverage"), [{
            "at": "2026-10-28T10:07:00Z", "event": "skill-coverage", "ticket": 8,
            "attempt": 1, "harness": "chore", "reason": "no transcript",
        }])

        saved = lifecycle.read_events(self.events)
        score.run(self.cfg)
        self.assertEqual(lifecycle.read_events(self.events), saved)


if __name__ == "__main__":
    unittest.main()
