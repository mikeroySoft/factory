"""`factory score` rubric-scores worker attempts from their persisted OMP transcripts (#234)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle, results, score, triage
from factory.config import Config

PROMPT = (
    "# Issue #7: add thing\n\n## Scope\nAdd `factory thing`.\n\n## Exit gate\n"
    "`unittest tests.test_thing` passes.\n\n## Instructions\n\n- Commit with `git commit -s`.\n"
)


def _msg(role: str, *content: dict) -> str:
    return json.dumps({"type": "message", "id": "x", "message": {"role": role, "content": list(content)}})


def _call(name: str, **arguments: object) -> str:
    return _msg("assistant", {"type": "toolCall", "id": "t", "name": name, "arguments": arguments})


RESULT = _msg("toolResult", {"type": "text", "text": "ok"})

# 20 lines: one synthetic OMP session. Seven tool calls (three edit/write), one identical
# consecutive pair (lines 3 and 5), a repeated-but-not-consecutive bash (9 and 15).
FIXTURE = "\n".join([
    json.dumps({"type": "session", "version": 3, "id": "s", "cwd": "/wt"}),
    _msg("user", {"type": "text", "text": PROMPT}),
    _call("read", path="a.py"),
    RESULT,
    _call("read", path="a.py"),
    RESULT,
    _call("edit", path="a.py", input="PUT 1.=1:\n+x"),
    RESULT,
    _call("bash", command="python -m unittest"),
    RESULT,
    _call("write", path="b.py", content="y"),
    RESULT,
    _call("edit", path="a.py", input="PUT 2.=2:\n+z"),
    RESULT,
    _call("bash", command="python -m unittest"),
    RESULT,
    json.dumps({"type": "model_usage", "model": "m", "usage": {"input": 1, "output": 1}}),
    _msg("assistant", {"type": "thinking", "thinking": "done"}, {"type": "text", "text": "Added factory thing; tests pass."}),
    json.dumps({"type": "custom", "customType": "tool_execution_start", "data": {"toolName": "bash"}}),
    json.dumps({"type": "title", "title": "add thing"}),
]) + "\n"


class ScoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.cfg = Config(root=Path(temp.name), repo="acme/widgets")
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory, LOGS=self.cfg.factory / "logs",
            SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl", EVENTS=self.events,
            MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.judge = self.enterContext(mock.patch.object(triage, "call_llm", return_value="0.9"))
        self.transcript = self.cfg.factory / "transcripts" / "7" / "s.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(FIXTURE)
        handoff = self.cfg.factory / "wt-7" / ".factory" / "handoff-7.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("Changed a.py and b.py.")

    def journal(self, *attempts: dict, finished: str = "merged") -> None:
        with self.events.open("a") as f:
            f.write(json.dumps({"event": "claimed", "ticket": 7, "title": "add thing", "at": "2000-01-01T00:00:00Z"}) + "\n")
            for a in attempts:
                f.write(json.dumps({"event": "attempt", "ticket": 7, "at": "2000-01-01T00:01:00Z",
                                    "transcript": str(self.transcript), **a}) + "\n")
            f.write(json.dumps({"event": finished, "ticket": 7, "at": "2000-01-01T00:02:00Z"}) + "\n")

    def scores(self) -> list[dict]:
        return [e for e in lifecycle.read_events(self.events) if e.get("event") == "score"]

    def test_fixture_metrics_and_judge(self) -> None:
        self.journal({"attempt": 1, "gate": "PASS"})
        score.run(self.cfg)
        [row] = self.scores()
        self.assertEqual({k: row[k] for k in ("ticket", "attempt", "transcript", "gate_first_pass", "tool_calls",
                                               "edits", "retries", "efficiency", "compliance", "judge_model")},
                         {"ticket": 7, "attempt": 1, "transcript": str(self.transcript), "gate_first_pass": True,
                          "tool_calls": 7, "edits": 3, "retries": 1, "efficiency": 0.5, "compliance": 0.9,
                          "judge_model": triage.LLM_MODEL})
        [messages] = self.judge.call_args.args
        asked = messages[-1]["content"]
        self.assertIn("## Scope\nAdd `factory thing`.", asked)
        self.assertIn("## Exit gate\n`unittest tests.test_thing` passes.", asked)
        self.assertNotIn("git commit -s", asked)
        self.assertIn("Added factory thing; tests pass.", asked)
        self.assertIn("Changed a.py and b.py.", asked)

    def test_gate_first_pass_follows_attempt_row(self) -> None:
        self.journal({"attempt": 1, "gate": "FAIL"}, {"attempt": 2, "gate": "PASS"}, finished="escalate")
        score.run(self.cfg)
        self.assertEqual([(r["attempt"], r["gate_first_pass"]) for r in self.scores()], [(1, False), (2, False)])

    def test_second_run_adds_no_rows(self) -> None:
        self.journal({"attempt": 1, "gate": "PASS"})
        score.run(self.cfg)
        score.run(self.cfg)
        self.assertEqual(len(self.scores()), 1)
        self.judge.assert_called_once()

    def test_requeued_sessions_are_both_scored(self) -> None:
        self.journal({"attempt": 1, "gate": "FAIL"}, {"attempt": 1, "gate": "FAIL"}, finished="escalate")
        second = self.transcript.with_name("requeued.jsonl")
        second.write_text(FIXTURE)
        self.journal({"attempt": 1, "gate": "PASS", "transcript": str(second)})

        score.run(self.cfg)

        self.assertEqual([(r["ticket"], r["attempt"], r["transcript"]) for r in self.scores()],
                         [(7, 1, str(self.transcript)), (7, 1, str(second))])
        saved = self.scores()
        self.assertEqual(score.run(self.cfg), [])
        self.assertEqual(self.scores(), saved)

    def test_requeued_session_is_scored_after_first_session_was_scored(self) -> None:
        self.journal({"attempt": 1, "gate": "FAIL"}, finished="escalate")
        score.run(self.cfg)
        second = self.transcript.with_name("requeued.jsonl")
        second.write_text(FIXTURE)
        self.journal({"attempt": 1, "gate": "PASS", "transcript": str(second)})

        rows = score.run(self.cfg)

        self.assertEqual([(r["ticket"], r["attempt"], r["transcript"]) for r in rows],
                         [(7, 1, str(second))])
        saved = self.scores()
        self.assertEqual([(r["ticket"], r["attempt"], r["transcript"]) for r in saved],
                         [(7, 1, str(self.transcript)), (7, 1, str(second))])
        self.assertEqual(score.run(self.cfg), [])
        self.assertEqual(self.scores(), saved)

    def test_dry_run_records_nothing(self) -> None:
        self.journal({"attempt": 1, "gate": "PASS"})
        self.assertEqual(len(score.run(self.cfg, dry_run=True)), 1)
        self.assertEqual(self.scores(), [])

    def test_missing_transcript_is_skipped(self) -> None:
        self.transcript.unlink()
        self.journal({"attempt": 1, "gate": "PASS"})
        score.run(self.cfg)
        [row] = self.scores()
        self.assertEqual((row["ticket"], row["attempt"], row["skipped"]), (7, 1, "missing"))
        self.judge.assert_not_called()

    def test_unreachable_judge_leaves_compliance_null(self) -> None:
        self.judge.side_effect = SystemExit(2)
        self.journal({"attempt": 1, "gate": "PASS"})
        score.run(self.cfg)
        [row] = self.scores()
        self.assertIsNone(row["compliance"])
        self.assertIsNone(row["judge_model"])
        self.assertEqual(row["tool_calls"], 7)

    def test_non_numeric_judge_answer_is_not_guessed(self) -> None:
        self.judge.return_value = "Looks mostly fine, maybe 0.7"
        self.journal({"attempt": 1, "gate": "PASS"})
        score.run(self.cfg)
        self.assertIsNone(self.scores()[0]["compliance"])

    def test_out_of_range_judge_answer_is_not_guessed(self) -> None:
        self.judge.return_value = "1.5"
        self.journal({"attempt": 1, "gate": "PASS"})
        score.run(self.cfg)
        self.assertIsNone(self.scores()[0]["compliance"])

    def test_removed_worktree_falls_back_to_retained_handoff(self) -> None:
        shutil.rmtree(self.cfg.factory / "wt-7")
        self.journal({"attempt": 1, "gate": "PASS"})
        with mock.patch.object(results, "latest_result", return_value={"status": "ok", "text": "Retained handoff."}) as latest:
            score.run(self.cfg)
        latest.assert_called_once_with(self.cfg, 7)
        [messages] = self.judge.call_args.args
        self.assertIn("Retained handoff.", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
