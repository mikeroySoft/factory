"""`factory learn` runs at the end of a dispatcher pass after every 10 finished tickets (#212)."""
# Also: `learn` mines each ticket's lowest-scored session instead of a log tail (#235).

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from factory import dispatch, learn, lifecycle
from factory.config import Config

REAL_RUN, REAL_CHORE_PR = dispatch.run, learn.chore_pr  # captured before any test patches them


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

    def test_repeated_calls_survive_the_cap_when_flagged_results_overflow_it(self) -> None:
        noisy = "\n".join(_call(f"e{i}", "bash", command=f"grep error file{i}") + "\n"
                          + _result(f"e{i}", "\n".join(f"error line {j}" for j in range(10))) for i in range(40))
        text = learn.score.excerpt(self.session(10, text=TRANSCRIPT + noisy + "\n"))
        self.assertEqual(len(text), learn.score.EXCERPT_CHARS)
        self.assertIn('Repeated tool calls:\n- read {"path": "tests/test_thing.py"}', text)
        self.assertIn("Fixed the assertion; tests pass.", text)
        self.assertIn("grep error file0", text)
        self.assertNotIn("grep error file39", text)

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


class PromotionTest(unittest.TestCase):
    """Lessons/curate PRs merge through gate + review + CI and are kept or reverted by score drift (#238)."""

    BRANCH, HEAD = "agent/lessons-2026-10-17-t10", "a" * 40

    def setUp(self) -> None:
        AutoLearnTest.setUp(self)
        self.checks = [{"name": "gate", "bucket": "pass"}]
        self.enterContext(mock.patch.object(dispatch, "pr_checks", side_effect=lambda _: self.checks))
        self.run = self.enterContext(mock.patch.object(dispatch, "run"))
        self.open_prs = [{"number": 42, "headRefName": self.BRANCH, "headRefOid": self.HEAD, "baseRefName": "main",
                          "isDraft": False, "reviewDecision": ""}]
        self.compare = {"behind_by": 0, "files": [{"filename": ".factory-lessons.md"}]}
        self.gh.side_effect = lambda args: self.compare if "/compare/" in args[1] else self.open_prs

    def journal(self, *rows: dict) -> None:
        with self.events.open("a") as f:
            for i, row in enumerate(rows):
                f.write(json.dumps({"at": f"2000-01-01T00:00:{i % 60:02d}Z", **row}) + "\n")

    def scores(self, efficiency: float, n: int = 10, gate_first_pass: bool = True) -> list[dict]:
        return [{"event": "score", "ticket": i, "attempt": 1, "efficiency": efficiency, "compliance": 0.5,
                 "gate_first_pass": gate_first_pass} for i in range(n)]

    def evidence(self) -> list[dict]:
        return [{"event": "gate", "branch": self.BRANCH, "head": self.HEAD, "gate": "PASS"},
                {"event": "review-result", "pr": 42, "head": self.HEAD, "verdict": "APPROVE"}]

    def rows(self, *events: str) -> list[dict]:
        return [e for e in lifecycle.read_events(self.events) if e.get("event") in events]

    def gh_calls(self) -> list[str]:
        return [" ".join(c.args[0]) for c in self.run.call_args_list if c.args[0][0] == "gh"]

    def test_lessons_pr_merges_after_gate_review_and_ci_and_opens_a_promotion(self) -> None:
        self.journal(*self.scores(0.5, 3), {"event": "score", "ticket": 9, "skipped": "missing"},
                     *self.scores(0.9, 10), *self.evidence())
        self.open_prs[0]["labels"] = []
        with mock.patch.object(dispatch, "initiative_kind") as ticket_stage:
            dispatch.merge_pass_locked(dry_run=False)  # the ticket merge stage hands the listing to promote_pass
        ticket_stage.assert_not_called()
        self.assertEqual(self.gh_calls(), [
            "gh pr edit 42 --repo acme/widgets --add-label factory-approved",
            f"gh pr merge 42 --repo acme/widgets --squash --match-head-commit {self.HEAD}",
        ])
        approved, merged, opened = self.rows("approved", "merged", "promotion-open")
        self.assertEqual((approved["pr"], approved["head"], approved["gate_head"], approved["review_head"]),
                         (42, self.HEAD, self.HEAD, self.HEAD))
        self.assertEqual((merged["pr"], merged["branch"], merged["method"]), (42, self.BRANCH, "squash"))
        self.assertEqual(opened["baseline"], {"efficiency_mean": 0.9, "compliance_mean": 0.5,
                                              "gate_first_pass_rate": 1.0, "n": 10})
        self.assertNotIn("human merge", learn.PIPELINE + learn.CURATE_OFFER)

    def test_merge_waits_for_head_bound_gate_and_review_and_green_ci(self) -> None:
        learn.promote_pass(self.open_prs)  # no evidence at all
        self.journal({"event": "gate", "branch": self.BRANCH, "head": "b" * 40, "gate": "PASS"},
                     {"event": "review-result", "pr": 42, "head": self.HEAD, "verdict": "APPROVE"})
        learn.promote_pass(self.open_prs)  # gate bound to another head
        self.journal({"event": "gate", "branch": self.BRANCH, "head": self.HEAD, "gate": "FAIL"})
        learn.promote_pass(self.open_prs)  # gate failed at this head
        self.journal({"event": "gate", "branch": self.BRANCH, "head": self.HEAD, "gate": "PASS"})
        self.checks = [{"name": "gate", "bucket": "pending"}, {"name": "lint", "bucket": "pass"}]
        learn.promote_pass(self.open_prs)  # CI pending
        self.checks = []
        learn.promote_pass(self.open_prs)  # no CI reported
        self.open_prs[0]["reviewDecision"] = "CHANGES_REQUESTED"
        self.checks = [{"name": "gate", "bucket": "pass"}]
        learn.promote_pass(self.open_prs)  # human veto
        self.open_prs[0]["reviewDecision"] = ""
        learn.promote_pass(self.open_prs, dry_run=True)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.rows("approved", "merged", "promotion-open"), [])
        self.assertIn("PR #42: would merge agent/lessons-2026-10-17-t10", [c.args[0] for c in self.log.call_args_list])
        self.open_prs[0]["headRefName"] = "agent/7"
        learn.promote_pass(self.open_prs)  # ticket PRs belong to the ticket merge stage
        self.assertEqual(self.gh_calls(), [])
        self.open_prs[0]["headRefName"] = self.BRANCH
        self.run.side_effect = RuntimeError("gh down")
        with mock.patch.object(dispatch, "initiative_kind"):
            dispatch.merge_pass_locked(dry_run=False)  # a failing lessons merge never blocks the ticket stage
        self.assertIn(f"PR #42: {self.BRANCH} promotion failed (RuntimeError: gh down)", [c.args[0] for c in self.log.call_args_list])

    def test_revert_merge_opens_no_promotion(self) -> None:
        self.open_prs[0]["headRefName"] = branch = "agent/revert-lessons-42"
        self.journal({"event": "gate", "branch": branch, "head": self.HEAD, "gate": "PASS"},
                     {"event": "review-result", "pr": 42, "head": self.HEAD, "verdict": "APPROVE"})
        learn.promote_pass(self.open_prs)
        self.assertEqual(len(self.gh_calls()), 2)
        self.assertEqual(self.rows("promotion-open"), [])

    def ran(self, scores: list[dict]) -> list[dict]:
        """Each score row preceded by the `attempt` row it scores."""
        return [row for s in scores for row in ({"event": "attempt", "ticket": s["ticket"], "attempt": s["attempt"]}, s)]

    def open_promotion(self, after: list[dict]) -> None:
        self.journal(*self.scores(0.9), {"event": "promotion-open", "pr": 42, "head": self.HEAD, "branch": self.BRANCH,
                                         "baseline": learn.promotion.window(self.scores(0.9))}, *self.ran(after))

    def test_late_scored_pre_merge_attempts_never_fill_the_after_window(self) -> None:
        self.journal(*({"event": "attempt", "ticket": 100 + i, "attempt": 1} for i in range(10)))  # old prompt
        self.open_promotion([])
        self.journal(*({**s, "ticket": 100 + s["ticket"]} for s in self.scores(0.1)))  # scored after the merge
        learn.learn_pass()
        self.assertEqual(self.rows("promotion-keep", "promotion-revert"), [])
        self.journal(*self.ran(self.scores(0.9)))
        learn.learn_pass()
        [row] = self.rows("promotion-keep", "promotion-revert")
        self.assertEqual((row["event"], row["after"]["efficiency_mean"]), ("promotion-keep", 0.9))

    def test_behind_head_changing_more_than_lessons_is_refreshed_not_merged(self) -> None:
        self.journal(*self.evidence())
        self.compare = {"behind_by": 3, "files": [{"filename": ".factory-lessons.md"}, {"filename": "tests/test_parser.py"}]}
        with mock.patch.object(learn, "refresh") as refresh:
            learn.promote_pass(self.open_prs)
        refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
        self.assertEqual(self.gh_calls(), [])
        self.compare["files"] = [{"filename": ".factory-lessons.md"}]
        learn.promote_pass(self.open_prs)  # no check reads the lessons file: it merges behind main
        self.assertEqual(len(self.gh_calls()), 2)

    def test_refresh_rebases_gates_pushes_with_a_lease_and_reviews_the_new_head(self) -> None:
        self.enterContext(mock.patch.object(dispatch, "run", REAL_RUN))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        origin, repo = root / "origin.git", root / "repo"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)

        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()

        def commit(path: str) -> str:
            (repo / path).parent.mkdir(exist_ok=True)
            (repo / path).write_text(f"{path}\n")
            git("add", path)
            git("commit", "-qm", path)
            return git("rev-parse", "HEAD")

        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        commit("README.md")
        branch = "agent/curate-2026-10-17-t10"
        git("checkout", "-qb", branch)
        head = commit("tests/test_parser.py")
        git("push", "-q", "origin", branch)
        git("checkout", "-q", "main")
        tip = commit("factory/landed.py")  # a ticket landed after the curate PR opened
        git("push", "-q", "origin", "main")
        cfg = Config(root=repo, repo="acme/widgets", signoff=False)
        cfg.factory.mkdir()
        with mock.patch.multiple(dispatch, cfg=cfg, ROOT=repo, EVENTS=cfg.factory / "events.jsonl"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "")), \
                mock.patch.object(dispatch, "review_external_pr") as review:
            learn.refresh(cfg, 42, branch, head)
            git("fetch", "-q", "origin")
            new = git("rev-parse", f"origin/{branch}")
            self.assertEqual(git("rev-parse", f"{new}~1"), tip)
            self.assertEqual(git("ls-tree", "-r", "--name-only", new).split(),
                             ["README.md", "factory/landed.py", "tests/test_parser.py"])
            self.assertEqual([(r["branch"], r["head"], r["gate"]) for r in lifecycle.read_events(cfg.factory / "events.jsonl")
                              if r.get("event") == "gate"], [(branch, new, "PASS")])
            review.assert_called_once_with(42, tip, new, branch)
            git("push", "-q", "--force", "origin", f"main:{branch}")  # someone else moved the branch
            with self.assertRaises(subprocess.CalledProcessError):
                learn.refresh(cfg, 42, branch, new)  # the lease on the head promote_pass saw refuses
        self.assertEqual(git("ls-remote", "origin", branch).split()[0], tip)
        review.assert_called_once()

    def test_drop_beyond_noise_opens_one_revert_pr(self) -> None:
        self.open_promotion(self.scores(0.5))
        self.gh.side_effect = lambda args: {"mergeCommit": {"oid": "c" * 40}} if args[:2] == ["pr", "view"] else []
        learn.learn_pass()
        learn.learn_pass()
        self.chore_pr.assert_called_once()
        _, branch, edit, title, body = self.chore_pr.call_args.args
        self.assertEqual((branch, title), ("agent/revert-lessons-42", "agent/revert-lessons-42: revert #42"))
        self.assertTrue(self.chore_pr.call_args.kwargs["protected_override"])
        self.assertIn(f"Reverts #42 ({'c' * 12}): efficiency_mean 0.900 -> 0.500 (-0.400, noise 0.000) beyond noise", body)
        self.assertIn("| efficiency_mean | 0.900 | 0.500 |", body)
        self.assertIn("| gate_first_pass_rate | 1.000 | 1.000 |", body)
        [row] = self.rows("promotion-keep", "promotion-revert")
        self.assertEqual((row["event"], row["pr"], row["branch"], row["after"]["efficiency_mean"], row["baseline"]["n"]),
                         ("promotion-revert", 42, "agent/revert-lessons-42", 0.5, 10))
        wt = self.cfg.factory / "wt"
        self.run.return_value.stdout = ".factory-lessons.md\n"
        self.assertEqual(edit(wt), [".factory-lessons.md"])
        self.assertEqual(self.run.call_args_list[-2].args[0], ["git", "revert", "--no-commit", "c" * 40])

    def test_scoring_that_reverts_skips_the_lessons_pr_in_the_same_pass(self) -> None:
        self.open_promotion([])
        AutoLearnTest.finish(self, range(1, 11))
        for n in range(1, 11):
            AutoLearnTest.session(self, n, {"attempt": 1, "gate": "PASS"})  # scores at efficiency 0.50
        self.gh.side_effect = lambda args: {"mergeCommit": {"oid": "c" * 40}} if args[:2] == ["pr", "view"] else []
        with mock.patch.object(learn.triage, "call_llm", return_value="0.7"):
            learn.learn_pass()
        self.assertEqual(len(self.rows("score")), 20)
        self.chore_pr.assert_called_once()
        self.assertEqual(self.chore_pr.call_args.args[1], "agent/revert-lessons-42")
        self.propose.assert_not_called()  # the lessons PR would collide with the revert
        self.assertEqual(self.rows("learn"), [])
        [row] = self.rows("promotion-keep", "promotion-revert")
        self.assertEqual(row["event"], "promotion-revert")
        self.assertIn("learn: skipped (revert PR opened: agent/revert-lessons-42)", [c.args[0] for c in self.log.call_args_list])

    def test_within_noise_keeps_once(self) -> None:
        self.open_promotion(self.scores(0.9, 9))
        learn.learn_pass()
        self.assertEqual(self.rows("promotion-keep", "promotion-revert"), [])  # fewer than 10 scored after the merge
        self.journal(*self.ran(self.scores(0.9, 1)))
        learn.learn_pass()
        learn.learn_pass()
        [row] = self.rows("promotion-keep", "promotion-revert")
        self.assertEqual((row["event"], row["pr"], row["after"]["n"]), ("promotion-keep", 42, 10))
        self.chore_pr.assert_not_called()
        self.gh.assert_not_called()

    def test_first_pass_drop_reverts_and_failed_revert_is_retried_not_replayed(self) -> None:
        self.open_promotion(self.scores(0.9, gate_first_pass=False))
        self.gh.side_effect = RuntimeError("gh down")
        learn.learn_pass()
        self.assertIn("promotion #42: failed (RuntimeError: gh down)", [c.args[0] for c in self.log.call_args_list])
        self.assertEqual(self.rows("promotion-keep", "promotion-revert"), [])
        self.gh.side_effect = lambda args: {"mergeCommit": {"oid": "c" * 40}}
        learn.learn_pass()
        [row] = self.rows("promotion-revert")
        self.assertIn("gate_first_pass_rate 1.000 -> 0.000", row["reason"])

    def test_a_revert_that_keeps_failing_never_blocks_later_promotion_decisions(self) -> None:
        self.open_promotion(self.scores(0.5))  # #42 drops: its revert conflicts with the later lessons merge
        self.journal({"event": "promotion-open", "pr": 43, "head": self.HEAD, "branch": self.BRANCH,
                      "baseline": learn.promotion.window(self.scores(0.5))}, *self.ran(self.scores(0.9)))
        self.gh.side_effect = lambda args: {"mergeCommit": {"oid": "c" * 40}}
        self.chore_pr.side_effect = RuntimeError("revert conflicts")
        learn.learn_pass(dry_run=True)
        self.assertEqual(
            [c.args[0].split(" (", 1)[0] for c in self.log.call_args_list if c.args[0].startswith("promotion #")],
            ["promotion #42: would revert", "promotion #43: would keep"],
        )
        self.log.reset_mock()
        learn.learn_pass()
        learn.learn_pass()
        [row] = self.rows("promotion-keep", "promotion-revert")
        self.assertEqual((row["event"], row["pr"]), ("promotion-keep", 43))
        self.assertEqual([c.args[0] for c in self.log.call_args_list].count("promotion #42: failed (RuntimeError: revert conflicts)"), 2)

    def test_lessons_pr_is_admitted_to_review_intake_without_a_label(self) -> None:
        self.open_prs = [{"number": n, "state": "OPEN", "isDraft": False, "headRefName": b, "headRefOid": "h", "baseRefOid": "b",
                          "labels": [], "reviewRequests": []} for n, b in ((42, self.BRANCH), (43, "agent/7"), (44, "agent/revert-lessons-42"))]
        self.open_prs.append({"number": 45, "state": "OPEN", "isDraft": False, "isCrossRepository": True, "headRefName": self.BRANCH,
                              "headRefOid": "h", "baseRefOid": "b", "labels": [], "reviewRequests": []})
        dispatch.review_intake_pass(dry_run=True)
        self.assertEqual([c.args[0] for c in self.log.call_args_list],
                         ["PR #42: would record review intake at h", "PR #44: would record review intake at h"])

    def test_lessons_pr_review_posts_a_comment_but_journals_the_verdict(self) -> None:
        # The dispatcher authored the PR; GitHub refuses APPROVE/REQUEST_CHANGES from the author (422).
        self.cfg.reviewer = ["reviewer", "{prompt}"]
        self.open_prs = [{"number": 42, "state": "OPEN", "isDraft": False, "headRefName": self.BRANCH, "headRefOid": "h",
                          "baseRefOid": "b", "labels": [], "reviewRequests": []}]
        self.run.side_effect = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, '```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE'
            if cmd[0] == "reviewer" else "{}", "")
        dispatch.review_intake_pass(dry_run=False)
        [post] = [c.args[0] for c in self.run.call_args_list if c.args[0][:4] == ["gh", "api", "--method", "POST"]]
        self.assertEqual(post[4:8], ["repos/acme/widgets/pulls/42/reviews", "-f", "commit_id=h", "-f"])
        self.assertEqual(post[8], "event=COMMENT")
        [result] = self.rows("review-result")
        self.assertEqual((result["pr"], result["head"], result["verdict"]), (42, "h", "APPROVE"))
        self.assertEqual(result["branch"], self.BRANCH)

    def test_revert_of_a_merge_that_added_a_file_commits_the_deletion(self) -> None:
        # Real git: the first lessons PR creates .factory-lessons.md, so its revert deletes it.
        self.enterContext(mock.patch.object(dispatch, "run", REAL_RUN))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        origin, repo = root / "origin.git", root / "repo"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)

        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()

        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (repo / "README.md").write_text("hello\n")
        git("add", "README.md")
        git("commit", "-qm", "init")
        (repo / ".factory-lessons.md").write_text("- lesson\n")
        git("add", ".factory-lessons.md")
        git("commit", "-qm", "lessons")
        merge = git("rev-parse", "HEAD")
        git("push", "-q", "origin", "main")
        cfg = Config(root=repo, repo="acme/widgets", signoff=False)
        cfg.factory.mkdir()
        self.gh.side_effect = lambda _: {"mergeCommit": {"oid": merge}}
        self.enterContext(mock.patch.object(learn, "chore_pr", REAL_CHORE_PR))
        with mock.patch.multiple(dispatch, cfg=cfg, ROOT=repo, EVENTS=cfg.factory / "events.jsonl"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "")), \
                mock.patch.object(dispatch, "push_and_pr") as push:
            window = learn.promotion.window([])
            learn.revert(cfg, {"pr": 42, "head": merge, "baseline": window}, "efficiency_mean fell", window)
        push.assert_called_once()
        self.assertEqual(git("ls-tree", "--name-only", "agent/revert-lessons-42"), "README.md")
        self.assertEqual(git("log", "-1", "--format=%s", "agent/revert-lessons-42"), "agent/revert-lessons-42: revert #42")

    def test_stats_promotions_joins_open_and_decision_rows(self) -> None:
        from factory import stats

        self.open_promotion(self.scores(0.5))
        self.gh.side_effect = lambda args: {"mergeCommit": {"oid": "c" * 40}}
        learn.learn_pass()
        [row] = stats.promotions(self.events)
        self.assertEqual((row["pr"], row["branch"], row["decision"], row["revert"], row["baseline"]["n"], row["after"]["efficiency_mean"]),
                         (42, self.BRANCH, "revert", "agent/revert-lessons-42", 10, 0.5))
        self.assertTrue(row["opened_at"] < row["decided_at"])


if __name__ == "__main__":
    unittest.main()
