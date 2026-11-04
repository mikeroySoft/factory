"""Scoring and measured rollback must progress while a learning proposal waits (#485)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, watch


class LearningProgressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets", manager=["true"])
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.root, REPO=self.cfg.repo,
            FACTORY=self.cfg.factory, EVENTS=self.events,
        ))
        self.prs = [{"number": 42, "headRefName": "agent/lessons-pending"}]
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=lambda _: self.prs))
        self.enterContext(mock.patch.object(learn.triage, "call_llm", return_value="0.7"))
        self.propose = self.enterContext(mock.patch.object(learn, "propose"))
        self.enterContext(mock.patch.object(watch, "failed_units", return_value=[]))
        self.enterContext(mock.patch.object(watch, "runtime_errors", return_value=[]))
        self.now = datetime(2026, 11, 1, tzinfo=UTC)
        self.clock = self.enterContext(mock.patch.object(lifecycle, "_now", side_effect=lambda: self.now.isoformat()))

    def journal(self, *rows: dict) -> None:
        for row in rows:
            lifecycle.append(self.events, {"at": self.now.isoformat(), **row})

    def rows(self, kind: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == kind]

    def finish(self, count: int = 10) -> None:
        self.journal(*({"event": "merged", "ticket": n} for n in range(1, count + 1)))

    def attempts(self) -> None:
        for n in range(1, 4):
            transcript = self.root / f"session-{n}.jsonl"
            transcript.write_text("\n".join(json.dumps({"type": "message", "message": message}) for message in [
                {"role": "user", "content": [{"type": "text", "text": "## Scope\nFix the bug"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Fixed the bug"}]},
            ]) + "\n")
            self.journal({"event": "attempt", "ticket": n, "attempt": 1, "gate": "PASS",
                          "transcript": str(transcript)})

    def test_dispatcher_scores_while_learning_is_blocked_without_duplicates(self) -> None:
        self.finish()
        self.attempts()
        with mock.patch.object(dispatch.config, "load", return_value=self.cfg), \
                mock.patch.object(dispatch, "pin_root", return_value=[]), \
                mock.patch.object(dispatch, "land_pass"), \
                mock.patch.object(dispatch, "review_intake_pass"), \
                mock.patch("factory.manage.manage_pass"), \
                mock.patch.object(dispatch, "ticket_pass"):
            self.assertEqual(dispatch.main([]), 0)
            self.assertEqual(dispatch.main([]), 0)
        self.assertEqual([(r["ticket"], r["attempt"], r["compliance"]) for r in self.rows("score")],
                         [(1, 1, 0.7), (2, 1, 0.7), (3, 1, 0.7)])
        self.assertEqual(self.rows("learn"), [])
        self.propose.assert_not_called()

    def test_scores_below_learning_threshold_without_manager(self) -> None:
        self.cfg.manager = None
        self.finish(3)
        self.attempts()
        learn.learn_pass()
        self.assertEqual([r["ticket"] for r in self.rows("score")], [1, 2, 3])
        self.assertEqual(self.rows("learn"), [])

    def test_blocked_alert_is_once_per_episode_and_different_pr_resets_clock(self) -> None:
        self.finish()
        learn.learn_pass()
        self.now += timedelta(hours=24)
        learn.learn_pass()
        self.assertEqual(self.rows("learn-blocked"), [])
        self.now += timedelta(hours=1)
        learn.learn_pass()
        learn.learn_pass()
        self.assertEqual([r["pr"] for r in self.rows("learn-blocked")], [42])
        [signal] = watch.signals(self.cfg, lifecycle.read_events(self.events))
        self.assertEqual(signal["kind"], "learn-blocked")
        self.assertEqual(signal["rows"][0]["pr"], 42)
        with mock.patch("builtins.print"):
            [alert] = watch.run_pass(self.cfg, dry_run=True)
        self.assertEqual(alert["fingerprint"], signal["fingerprint"])
        self.prs = [{"number": 43, "headRefName": "agent/curate-pending"}]
        learn.learn_pass()
        self.assertEqual(watch.signals(self.cfg, lifecycle.read_events(self.events)), [])
        self.now += timedelta(hours=25)
        learn.learn_pass()
        learn.learn_pass()
        self.assertEqual([r["pr"] for r in self.rows("learn-blocked")], [42, 43])
        self.prs = []
        with mock.patch.object(learn, "run"):
            learn.learn_pass()
        self.assertEqual(watch.signals(self.cfg, lifecycle.read_events(self.events)), [])
        self.prs = [{"number": 42, "headRefName": "agent/lessons-pending"}]
        learn.learn_pass()
        self.assertEqual([r["pr"] for r in self.rows("learn-blocked")], [42, 43])
        self.now += timedelta(hours=25)
        learn.learn_pass()
        self.assertEqual([r["pr"] for r in self.rows("learn-blocked")], [42, 43, 42])

    def test_dry_run_does_not_score_or_start_blocker_clock(self) -> None:
        self.finish()
        self.attempts()
        before = self.events.read_bytes()
        learn.learn_pass(dry_run=True)
        self.assertEqual(self.events.read_bytes(), before)


class MeasuredRollbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.root / "repo"
        self.origin = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True, capture_output=True)
        self.git("config", "user.name", "Factory test")
        self.git("config", "user.email", "factory@example.test")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / config.LESSONS_NAME).write_text("Measured instructions\n")
        self.commit("baseline")
        (self.repo / config.LESSONS_NAME).write_text("Regressed instructions\n")
        self.merge = self.commit("promotion")
        self.git("push", "-q", "origin", "main")
        self.branch = "agent/revert-lessons-431"
        self.git("checkout", "-qb", self.branch)
        self.git("revert", "--no-edit", self.merge)
        self.head = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", self.branch)
        self.cfg = config.Config(self.repo, "acme/widgets")
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.multiple(dispatch, create=True, cfg=self.cfg, ROOT=self.repo,
                                             EVENTS=self.events, REPO=self.cfg.repo))
        self.pr = {"number": 468, "headRefName": self.branch, "headRefOid": self.head,
                   "baseRefName": "main", "isDraft": False, "reviewDecision": "CHANGES_REQUESTED"}
        self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[{"bucket": "pass"}]))
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.gh))
        real_run = dispatch.run
        self.commands: list[list[str]] = []

        def run(cmd, **kwargs):
            if cmd[0] == "gh":
                self.commands.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, **kwargs)

        self.enterContext(mock.patch.object(dispatch, "run", side_effect=run))
        self.base = {"n": 10, "efficiency_mean": 0.846, "compliance_mean": 0.8, "gate_first_pass_rate": 1.0}
        self.journal({"event": "promotion-open", "pr": 431, "head": "a" * 40, "baseline": self.base},
                     {"event": "promotion-revert", "pr": 431, "head": "a" * 40,
                      "branch": self.branch, "baseline": self.base},
                     {"event": "gate", "branch": self.branch, "head": self.head, "gate": "PASS"},
                     {"event": "review-result", "pr": 468, "head": self.head, "verdict": "REQUEST_CHANGES"})

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def gh(self, args: list[str]) -> dict:
        if args[:2] == ["pr", "view"]:
            return {"mergeCommit": {"oid": self.merge}}
        return {"behind_by": 0, "files": [{"filename": config.LESSONS_NAME}]}

    def journal(self, *rows: dict) -> None:
        for row in rows:
            lifecycle.append(self.events, {"at": "2026-10-30T00:00:00Z", **row})

    def merged(self) -> list[dict]:
        return [r for r in lifecycle.read_events(self.events) if r.get("event") == "merged"]

    def test_recorded_restored_score_merges_despite_request_changes(self) -> None:
        self.cfg.eval_schedule = True
        learn.promote_pass([self.pr])
        self.assertEqual([(r["pr"], r["head"]) for r in self.merged()], [(468, self.head)])
        approved = next(r for r in lifecycle.read_events(self.events) if r.get("event") == "approved")
        self.assertEqual(approved["restored_score"], self.base)
        self.assertIsNone(approved.get("review_head"))
        self.assertTrue(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in self.commands))

    def test_same_branch_without_recorded_score_needs_review(self) -> None:
        rows = lifecycle.read_events(self.events)
        self.events.write_text("\n".join(json.dumps({**r, "baseline": {}}) for r in rows) + "\n")
        learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])
        self.pr["reviewDecision"] = ""
        learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])
        self.journal({"event": "review-result", "pr": 468, "head": self.head, "verdict": "APPROVE"})
        learn.promote_pass([self.pr])
        self.assertEqual([r["pr"] for r in self.merged()], [468])

    def test_changed_restoration_cannot_reuse_recorded_score(self) -> None:
        (self.repo / config.LESSONS_NAME).write_text("Unmeasured instructions\n")
        self.pr["headRefOid"] = self.commit("tampered restore")
        self.git("push", "-q", "origin", self.branch)
        self.journal({"event": "gate", "branch": self.branch, "head": self.pr["headRefOid"], "gate": "PASS"})
        learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])

    def test_measured_restore_still_requires_gate_and_ci(self) -> None:
        with mock.patch.object(dispatch, "pr_checks", return_value=[{"bucket": "fail"}]):
            learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])
        self.journal({"event": "gate", "branch": self.branch, "head": self.head, "gate": "FAIL"})
        learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])

    def test_lessons_and_curate_request_changes_still_block(self) -> None:
        for branch in ("agent/lessons-new", "agent/curate-new"):
            self.pr["headRefName"] = branch
            self.journal({"event": "gate", "branch": branch, "head": self.head, "gate": "PASS"})
            learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])

    def test_extra_changes_cannot_reuse_restored_score(self) -> None:
        (self.repo / "extra.md").write_text("Unmeasured addition\n")
        self.pr["headRefOid"] = self.commit("extra unmeasured change")
        self.git("push", "-q", "origin", self.branch)
        self.journal({"event": "gate", "branch": self.branch, "head": self.pr["headRefOid"], "gate": "PASS"})
        learn.promote_pass([self.pr])
        self.assertEqual(self.merged(), [])

    def test_recorded_restoration_does_not_depend_on_revert_prefix(self) -> None:
        branch = "agent/curate-restoration"
        self.git("push", "-q", "origin", f"HEAD:{branch}")
        self.pr["headRefName"] = branch
        self.journal({"event": "promotion-revert", "pr": 431, "head": "a" * 40,
                      "branch": branch, "baseline": self.base},
                     {"event": "gate", "branch": branch, "head": self.head, "gate": "PASS"})
        learn.promote_pass([self.pr])
        self.assertEqual([r["pr"] for r in self.merged()], [468])
