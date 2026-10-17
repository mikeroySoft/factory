"""`factory learn` runs at the end of a dispatcher pass after every 10 finished tickets (#212)."""
# Also: `learn` mines each ticket's lowest-scored session instead of a log tail (#235).

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from factory import dispatch, learn, lifecycle
from factory.config import Config


def _msg(role: str, *content: dict, **fields: object) -> str:
    return json.dumps({"type": "message", "message": {"role": role, "content": list(content), **fields}})


def _call(call_id: str, name: str, **arguments: object) -> str:
    return _msg("assistant", {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments})


def _result(call_id: str, text: str) -> str:
    return _msg("toolResult", {"type": "text", "text": text}, toolCallId=call_id)


# One session: a failing test run (its result holds a Traceback), the same read twice in a row
# (a retry pair), a benign call, and a final message.
TRANSCRIPT = "\n".join([
    json.dumps({"type": "session", "version": 3}),
    _msg("user", {"type": "text", "text": "# Issue #10: t10\n\n## Scope\nx\n\n## Instructions\n\n- y\n"}),
    _call("c1", "bash", command="python -m unittest tests.test_thing"),
    _result("c1", "Traceback (most recent call last):\n  File \"tests/test_thing.py\"\nAssertionError: 1 != 2\n"
                  + "\n".join(f"noise {i}" for i in range(20))),
    _call("c2", "read", path="tests/test_thing.py"),
    _result("c2", "contents"),
    _call("c3", "read", path="tests/test_thing.py"),
    _result("c3", "contents"),
    _call("c4", "bash", command="git status"),
    _result("c4", "clean"),
    _msg("assistant", {"type": "text", "text": "Fixed the assertion; tests pass."}),
]) + "\n"


class AutoLearnTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(root=Path(temp.name), repo="acme/widgets", manager=["true"])
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory, LOGS=self.cfg.factory / "logs",
            SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl", EVENTS=self.events,
            MAX_ACTIVE=1, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
        self.enterContext(mock.patch("factory.stats.gh", return_value=[]))
        self.open_prs: list[dict] = []
        self.gh = self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=lambda _: self.open_prs))
        self.propose = self.enterContext(mock.patch.object(
            learn, "propose", return_value=(["Run `make test` before the gate."], None)))
        self.chore_pr = self.enterContext(mock.patch.object(learn, "chore_pr"))
        self.log = self.enterContext(mock.patch.object(dispatch, "log"))

    def finish(self, tickets: range, year: int = 2000) -> None:
        """Journal `merged` rows; year 2000 precedes any real `learn` row, 2999 follows it."""
        with self.events.open("a") as f:
            for n in tickets:
                f.write(json.dumps({"event": "claimed", "ticket": n, "title": f"t{n}", "at": f"{year}-01-01T00:00:00Z"}) + "\n")
                f.write(json.dumps({"event": "merged", "ticket": n, "at": f"{year}-01-01T00:{n % 60:02d}:00Z"}) + "\n")

    def session(self, n: int, *attempts: dict, scores: tuple[dict, ...] = (), text: str = TRANSCRIPT) -> Path:
        """Journal `attempt` rows (each with a `Working...` log and the transcript) and `score` rows for ticket `n`."""
        transcript = self.cfg.factory / "transcripts" / str(n) / "s.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(text)
        log = self.cfg.factory / "logs" / f"{n}.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text("Working...\n" * 50)
        with self.events.open("a") as f:
            for a in attempts:
                f.write(json.dumps({"event": "attempt", "ticket": n, "at": "2000-01-01T00:00:30Z", "log": str(log),
                                    "transcript": str(transcript), **a}) + "\n")
            for s in scores:
                f.write(json.dumps({"event": "score", "ticket": n, "at": "2000-01-01T00:00:40Z",
                                    "transcript": str(transcript), **s}) + "\n")
        return transcript

    def test_evidence_excerpts_the_lowest_scored_session_instead_of_the_log_tail(self) -> None:
        self.finish(range(10, 11))
        self.session(10, {"attempt": 1, "gate": "FAIL"}, {"attempt": 2, "gate": "PASS"}, scores=(
            {"attempt": 1, "efficiency": 0.5, "compliance": 0.9},
            {"attempt": 2, "efficiency": 0.5, "compliance": 0.4},
        ))
        tickets, text = learn.evidence(10)
        self.assertEqual(tickets, [10])
        self.assertIn("Attempt 2 session excerpt (efficiency 0.50, compliance 0.4, gate PASS)", text)
        self.assertIn('- bash {"command": "python -m unittest tests.test_thing"}\n  Traceback (most recent call last):', text)
        self.assertIn("AssertionError: 1 != 2", text)
        self.assertNotIn("noise 10", text)  # first 10 result lines only
        self.assertIn('Repeated tool calls:\n- read {"path": "tests/test_thing.py"}', text)
        self.assertNotIn("git status", text)  # a clean, unrepeated call is not evidence
        self.assertIn("Fixed the assertion; tests pass.", text)
        self.assertNotIn("Working...", text)
        self.assertNotIn("log tail", text)

    def test_lowest_efficiency_wins_then_gate_fail_breaks_the_tie(self) -> None:
        self.finish(range(10, 11))
        self.session(10, {"attempt": 1, "gate": "PASS"}, {"attempt": 2, "gate": "FAIL"}, {"attempt": 3, "gate": "PASS"}, scores=(
            {"attempt": 1, "efficiency": 0.3, "compliance": None},
            {"attempt": 2, "efficiency": 0.3, "compliance": None},
            {"attempt": 3, "efficiency": 0.9, "compliance": 0.1},
        ))
        self.assertIn("Attempt 2 session excerpt", learn.evidence(10)[1])

    def test_ticket_without_a_score_row_keeps_the_log_tail(self) -> None:
        self.finish(range(10, 11))
        self.session(10, {"attempt": 1, "gate": "FAIL"})
        text = learn.evidence(10)[1]
        self.assertIn("Attempt 1 log tail (gate FAILED after it)", text)
        self.assertIn("Working...", text)
        self.assertNotIn("session excerpt", text)

    def test_session_excerpts_stay_bounded(self) -> None:
        self.finish(range(1, 11))
        huge = TRANSCRIPT + _msg("assistant", {"type": "text", "text": "x" * 50_000}) + "\n"
        for n in range(1, 11):
            transcript = self.session(n, {"attempt": 1, "gate": "PASS"},
                                      scores=({"attempt": 1, "efficiency": 0.5, "compliance": 1},), text=huge)
        self.assertEqual(len(learn.score.excerpt(transcript)), learn.score.EXCERPT_CHARS)
        self.assertLessEqual(len(learn.evidence(10)[1]), learn.MAX_EVIDENCE)

    def test_learn_pass_scores_unscored_tickets_first(self) -> None:
        self.finish(range(1, 11))
        self.session(10, {"attempt": 1, "gate": "PASS"})
        with mock.patch.object(learn.triage, "call_llm", return_value="0.7"):
            learn.learn_pass()
        [row] = [e for e in lifecycle.read_events(self.events) if e.get("event") == "score"]
        self.assertEqual((row["ticket"], row["attempt"], row["compliance"], row["retries"]), (10, 1, 0.7, 1))
        self.assertIn("Attempt 1 session excerpt (efficiency 0.50, compliance 0.7, gate PASS)", self.propose.call_args.args[1])
        self.assertNotIn("Working...", self.propose.call_args.args[1])

    def learns(self) -> list[dict]:
        return [e for e in lifecycle.read_events(self.events) if e.get("event") == "learn"]

    def logged(self) -> list[str]:
        return [c.args[0] for c in self.log.call_args_list]

    def test_runs_once_after_ten_finished_tickets(self) -> None:
        self.finish(range(1, 10))
        learn.learn_pass()
        self.propose.assert_not_called()
        self.assertEqual(self.learns(), [])
        self.finish(range(10, 11))
        learn.learn_pass()
        self.propose.assert_called_once()
        self.chore_pr.assert_called_once()
        [event] = self.learns()
        self.assertEqual(event["trigger"], "auto")
        self.assertEqual(event["tickets"], list(range(1, 11)))
        self.assertEqual(event["branch"], f"agent/lessons-{datetime.now().astimezone().date().isoformat()}-t10")
        learn.learn_pass()  # the count restarts after the recorded run
        self.propose.assert_called_once()

    def test_open_lessons_pr_skips_and_keeps_the_count(self) -> None:
        self.finish(range(1, 11))
        self.open_prs = [{"number": 7, "headRefName": "agent/31"},
                         {"number": 42, "headRefName": "agent/curate-2026-10-01-t9"}]
        learn.learn_pass()
        self.propose.assert_not_called()
        self.assertIn("learn: skipped (open lessons PR #42)", self.logged())
        self.assertEqual(self.learns(), [])
        self.assertEqual(learn.finished_since_learn(), 10)
        self.open_prs = [{"number": 7, "headRefName": "agent/31"}]
        learn.learn_pass()
        self.propose.assert_called_once()
        self.assertEqual([e["trigger"] for e in self.learns()], ["auto"])

    def test_no_manager_skips_and_records_nothing(self) -> None:
        self.cfg.manager = None
        self.finish(range(1, 11))
        learn.learn_pass()
        self.assertEqual(self.logged(), ["learn: skipped (no manager)"])
        self.propose.assert_not_called()
        self.gh.assert_not_called()
        self.assertFalse((self.cfg.root / ".factory-lessons.md").exists())
        self.assertEqual(self.learns(), [])

    def test_failure_never_fails_the_pass_and_the_next_pass_retries(self) -> None:
        self.finish(range(1, 11))
        self.propose.side_effect = ValueError("model returned unparseable JSON twice")
        with mock.patch.object(dispatch.config, "load", return_value=self.cfg), \
                mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(dispatch, "land_pass"), \
                mock.patch.object(dispatch, "review_intake_pass"), \
                mock.patch("factory.manage.manage_pass"), \
                mock.patch.object(dispatch, "frontier", return_value=[]):
            self.assertEqual(dispatch.main([]), 0)
            [failed] = self.learns()
            self.assertEqual((failed["status"], failed["trigger"]), ("failed", "auto"))
            self.assertIn("unparseable JSON", failed["reason"])
            self.assertEqual(learn.finished_since_learn(), 10)
            self.propose.side_effect = None
            self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(self.propose.call_count, 2)
        self.assertNotIn("status", self.learns()[-1])
        self.assertEqual(learn.finished_since_learn(), 0)

    def test_same_day_runs_get_distinct_branches_the_merge_stage_ignores(self) -> None:
        self.finish(range(1, 11))
        learn.learn_pass()
        self.finish(range(11, 21), year=2999)
        learn.learn_pass()
        branches = [e["branch"] for e in self.learns()]
        today = datetime.now().astimezone().date().isoformat()
        self.assertEqual(branches, [f"agent/lessons-{today}-t10", f"agent/lessons-{today}-t20"])
        self.assertEqual(learn.branch_name("curate", [3, 20]), f"agent/curate-{today}-t20")
        self.open_prs = [{"number": i, "headRefName": b, "headRefOid": "x", "baseRefName": "main",
                          "isDraft": False, "labels": [{"name": dispatch.FACTORY_APPROVED}],
                          "reviewDecision": ""} for i, b in enumerate([*branches, learn.branch_name("curate", [20])])]
        with mock.patch.object(dispatch, "initiative_kind") as candidate:
            dispatch.merge_pass_locked(dry_run=True)
        candidate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
