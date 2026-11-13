from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, onboard


class PromoteRegressionTest(unittest.TestCase):
    BRANCH = "agent/lessons-2026-10-27-t10"
    HEAD = "a" * 40

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(root=Path(temp.name), repo="acme/widgets", manager=["true"])
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.pr = {
            "number": 42,
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD,
            "baseRefName": "main",
            "isDraft": False,
            "reviewDecision": "",
        }
        self.compare = {"behind_by": 0, "files": [{"filename": config.LESSONS_NAME}]}
        self.run = self.enterContext(mock.patch.object(
            dispatch,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ))
        self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "gate", "bucket": "pass"}]))
        self.enterContext(mock.patch.object(dispatch, "log"))
        self.enterContext(mock.patch.multiple(
            dispatch,
            create=True,
            cfg=self.cfg,
            EVENTS=self.events,
            ROOT=self.cfg.root,
            REPO=self.cfg.repo,
        ))
        self.gh = self.enterContext(mock.patch.object(
            dispatch,
            "gh_json",
            side_effect=lambda args: self.compare if args[0] == "api" else {},
        ))
        previous = self.gh.side_effect
        self.gh.side_effect = lambda args: (
            {"isDraft": self.pr["isDraft"], "labels": self.pr.get("labels", [])}
            if args[-1] == "isDraft,labels" else previous(args)
        )

    def journal(self, *rows: dict) -> None:
        with self.events.open("a") as handle:
            for row in rows:
                handle.write(json.dumps({"at": "2026-10-27T00:00:00Z", **row}) + "\n")

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == event]

    def review(self) -> dict:
        return {"event": "review-result", "pr": 42, "head": self.HEAD, "verdict": "APPROVE"}

    def gate(self) -> dict:
        return {"event": "gate", "branch": self.BRANCH, "head": self.HEAD, "gate": "PASS"}

    def test_promote_gates_an_approved_legacy_head_once_then_merges(self) -> None:
        self.journal(self.review())

        def pass_gate(_wt: Path, branch: str, _protected: bool) -> str:
            dispatch.record("gate", branch=branch, head=self.HEAD, gate="PASS")
            return self.HEAD

        with mock.patch.object(learn, "gate_head", side_effect=pass_gate) as gate_head:
            learn.promote_pass([self.pr])
            learn.promote_pass([self.pr])

        gate_head.assert_called_once()
        self.assertEqual(len(self.rows("gate")), 1)
        self.assertEqual(len(self.rows("approved")), 1)
        self.assertEqual(len(self.rows("merged")), 1)

    def test_promote_refreshes_a_behind_lessons_only_head_without_approval_receipt(self) -> None:
        self.journal(self.gate(), self.review())
        self.compare = {"behind_by": 3, "files": [{"filename": config.LESSONS_NAME}]}
        self.run.side_effect = RuntimeError(
            'Repository rule violations found: Required status check "gate" is expected.'
        )

        with mock.patch.object(learn, "refresh") as refresh:
            learn.promote_pass([self.pr])

        refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
        self.assertEqual(self.rows("approved"), [])
        self.assertEqual(self.rows("merged"), [])

    def test_promote_does_not_repeat_a_failed_strict_base_refresh(self) -> None:
        self.journal(self.gate(), self.review())
        self.compare = {"behind_by": 3, "files": [{"filename": config.LESSONS_NAME}]}

        def reject_merge(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
            if args[:3] == ["gh", "pr", "merge"]:
                raise RuntimeError(
                    'Repository rule violations found: Required status check "gate" is expected.'
                )
            return subprocess.CompletedProcess(args, 0, "", "")

        self.run.side_effect = reject_merge
        with mock.patch.object(learn, "refresh", side_effect=RuntimeError("merge conflict")) as refresh:
            learn.promote_pass([self.pr])
            rows_after_failure = lifecycle.read_events(self.events)
            learn.promote_pass([self.pr])
            learn.promote_pass([self.pr])

        refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
        self.run.assert_not_called()
        self.assertEqual(lifecycle.read_events(self.events), rows_after_failure)
        [blocked] = self.rows("promotion-blocked")
        self.assertEqual((blocked["pr"], blocked["head"], blocked["reason"]), (
            42,
            self.HEAD,
            "refresh failed",
        ))
        self.assertEqual(self.rows("approved"), [])
        self.assertEqual(self.rows("merged"), [])

    def test_promote_records_one_block_for_repeated_red_ci(self) -> None:
        self.journal(self.gate(), self.review())
        with mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "lint", "bucket": "fail"}]):
            for _ in range(3):
                learn.promote_pass([self.pr])

        [blocked] = self.rows("promotion-blocked")
        self.assertEqual((blocked["pr"], blocked["head"]), (42, self.HEAD))
        self.assertEqual(self.rows("approved"), [])

    def test_promote_records_a_human_merge_and_opens_measurement(self) -> None:
        self.journal(self.gate(), self.review())
        self.gh.side_effect = lambda args: {
            "state": "MERGED",
            "mergedBy": {"login": "mikeroySoft"},
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD,
        }

        learn.promote_pass([])

        [merged] = self.rows("merged")
        [opened] = self.rows("promotion-open")
        self.assertEqual((merged["pr"], merged["by"], merged["actor"]), (42, "human", "mikeroySoft"))
        self.assertEqual((opened["pr"], opened["head"], opened["branch"]), (42, self.HEAD, self.BRANCH))

    def test_promote_reconciles_a_human_merge_without_approval(self) -> None:
        self.journal({"event": "review-intake", "pr": 42, "head": self.HEAD})
        self.gh.side_effect = lambda args: {
            "state": "MERGED",
            "mergedBy": {"login": "mikeroySoft"},
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD,
        }

        learn.promote_pass([])

        [merged] = self.rows("merged")
        [opened] = self.rows("promotion-open")
        self.assertEqual((merged["pr"], merged["by"], merged["actor"]), (42, "human", "mikeroySoft"))
        self.assertEqual((opened["pr"], opened["head"], opened["branch"]), (42, self.HEAD, self.BRANCH))

    def test_reconciliation_does_not_relabel_a_factory_merge_after_refresh(self) -> None:
        old_head = "b" * 40
        self.journal(
            {"event": "review-result", "pr": 42, "head": old_head, "verdict": "APPROVE"},
            {
                "event": "merged",
                "pr": 42,
                "head": self.HEAD,
                "branch": self.BRANCH,
                "method": "squash",
                "by": "factory",
            },
        )

        for _ in range(3):
            learn.promote_pass([])

        self.gh.assert_not_called()
        [merged] = self.rows("merged")
        self.assertEqual((merged["head"], merged["by"]), (self.HEAD, "factory"))

    def test_reconciliation_settles_historical_non_lessons_reviews(self) -> None:
        self.journal(self.review())
        self.gh.side_effect = lambda _args: {
            "state": "CLOSED",
            "mergedBy": None,
            "headRefName": "contributor/fix",
            "headRefOid": self.HEAD,
        }

        for _ in range(3):
            learn.promote_pass([])

        self.assertEqual(self.gh.call_count, 1)
        [reconciled] = self.rows("promotion-reconciled")
        self.assertEqual(
            (reconciled["pr"], reconciled["head"], reconciled["reason"]),
            (42, self.HEAD, "not a lessons branch"),
        )

    def test_reconciliation_settles_closed_lessons_reviews(self) -> None:
        self.journal(self.review())
        current_head = "b" * 40
        self.gh.side_effect = lambda _args: {
            "state": "CLOSED",
            "mergedBy": None,
            "headRefName": self.BRANCH,
            "headRefOid": current_head,
        }

        for _ in range(3):
            learn.promote_pass([])

        self.assertEqual(self.gh.call_count, 1)
        [blocked] = self.rows("promotion-blocked")
        self.assertEqual((blocked["pr"], blocked["head"], blocked["reason"]), (
            42,
            current_head,
            "closed without merge",
        ))


    def test_promote_does_not_repeat_a_same_head_merge_refusal(self) -> None:
        self.journal(self.gate(), self.review())
        refusal = "Pull request is not mergeable: commits must have verified signatures"

        def reject_merge(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
            if args[:3] == ["gh", "pr", "merge"]:
                raise subprocess.CalledProcessError(1, args, stderr=refusal)
            return subprocess.CompletedProcess(args, 0, "", "")

        self.run.side_effect = reject_merge
        with mock.patch.object(dispatch, "log") as log, mock.patch.object(learn, "refresh") as refresh:
            learn.promote_pass([self.pr])
            rows_after_failure = lifecycle.read_events(self.events)
            for _ in range(2):
                log.reset_mock()
                learn.promote_pass([self.pr])
                self.assertEqual(lifecycle.read_events(self.events), rows_after_failure)
                self.assertIn("verified signatures", str(log.call_args_list))
                self.assertIn("push a new head", str(log.call_args_list))

        refresh.assert_not_called()
        self.assertEqual([call.args[0][:3] for call in self.run.call_args_list], [
            ["gh", "pr", "edit"],
            ["gh", "pr", "merge"],
        ])
        [blocked] = self.rows("promotion-blocked")
        self.assertEqual((blocked["pr"], blocked["head"]), (42, self.HEAD))
        self.assertTrue(blocked["reason"].startswith("merge failed: "))
        self.assertEqual(self.rows("approved"), [])
        self.assertEqual(self.rows("merged"), [])

        new_head = "b" * 40
        self.pr["headRefOid"] = new_head
        self.journal({**self.gate(), "head": new_head}, {**self.review(), "head": new_head})
        self.run.side_effect = None
        learn.promote_pass([self.pr])

        self.assertEqual(self.run.call_count, 4)
        [approved] = self.rows("approved")
        [merged] = self.rows("merged")
        self.assertEqual((approved["head"], merged["head"]), (new_head, new_head))

    def test_promote_uses_one_block_reason_for_a_journaled_gate_failure(self) -> None:
        self.journal(self.review())
        detail = "gate report: required check failed"

        def fail_gate(_wt: Path, branch: str, _protected: bool) -> str:
            dispatch.record("gate", branch=branch, head=self.HEAD, gate="FAIL")
            raise ValueError(detail)

        with mock.patch.object(learn, "gate_head", side_effect=fail_gate) as gate_head, \
                mock.patch.object(dispatch, "log") as log:
            learn.promote_pass([self.pr])
            rows_after_failure = lifecycle.read_events(self.events)
            learn.promote_pass([self.pr])
            learn.promote_pass([self.pr])

        gate_head.assert_called_once()
        self.assertEqual(lifecycle.read_events(self.events), rows_after_failure)
        [blocked] = self.rows("promotion-blocked")
        self.assertEqual(blocked["reason"], "gate failed")
        self.assertIn(detail, str(log.call_args_list))
        self.assertEqual(len(self.rows("gate")), 1)
        self.assertEqual(self.rows("approved"), [])
        self.assertEqual(self.rows("merged"), [])

    def test_promote_logs_a_stored_refresh_failure_without_journal_growth(self) -> None:
        self.journal(self.gate(), self.review(), {
            "event": "promotion-blocked",
            "pr": 42,
            "head": self.HEAD,
            "branch": self.BRANCH,
            "reason": "refresh failed",
        })
        before = lifecycle.read_events(self.events)

        with mock.patch.object(dispatch, "log") as log:
            learn.promote_pass([self.pr])

        self.assertEqual(lifecycle.read_events(self.events), before)
        self.run.assert_not_called()
        self.assertIn("refresh failed", str(log.call_args_list))
        self.assertIn("push a new head", str(log.call_args_list))

    def test_promote_retries_recoverable_ci_and_review_blocks(self) -> None:
        self.journal(self.gate(), self.review(), *({
            "event": "promotion-blocked",
            "pr": 42,
            "head": self.HEAD,
            "branch": self.BRANCH,
            "reason": reason,
        } for reason in ("CI fail", "review approval missing")))

        learn.promote_pass([self.pr])

        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(len(self.rows("merged")), 1)

    def test_promote_reconciles_a_human_merge_after_a_merge_refusal(self) -> None:
        self.journal(self.review(), {
            "event": "promotion-blocked",
            "pr": 42,
            "head": self.HEAD,
            "branch": self.BRANCH,
            "reason": "merge failed: commits must have verified signatures",
        })
        self.gh.side_effect = lambda _args: {
            "state": "MERGED",
            "mergedBy": {"login": "mikeroySoft"},
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD,
        }

        learn.promote_pass([])

        [merged] = self.rows("merged")
        [opened] = self.rows("promotion-open")
        self.assertEqual(merged["by"], "human")
        self.assertEqual(opened["head"], self.HEAD)

    def test_lessons_pr_head_gets_required_gate_check(self) -> None:
        self.journal(self.gate(), self.review())
        self.compare = {"behind_by": 4, "files": [{"filename": config.LESSONS_NAME}]}

        def reject_merge(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
            if args[:3] == ["gh", "pr", "merge"]:
                raise RuntimeError(
                    'Repository rule violations found: Required status check "gate" is expected.'
                )
            return subprocess.CompletedProcess(args, 0, "", "")

        self.run.side_effect = reject_merge
        with mock.patch.object(learn, "refresh") as refresh:
            learn.promote_pass([self.pr])

        refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
        self.assertFalse(any(
            row["reason"].startswith("merge failed:") for row in self.rows("promotion-blocked")
        ))
        self.assertFalse(any(
            call.args[0][:3] == ["gh", "pr", "merge"] for call in self.run.call_args_list
        ))



    def test_merge_refusals_refresh_instead_of_becoming_permanent(self) -> None:
        for refusal, state in (
            ('Repository rule violations found: Required status check "gate" is expected.', "BLOCKED"),
            ("the head branch is not up to date", "BEHIND"),
            ("Pull request is not mergeable", "BEHIND"),
        ):
            with self.subTest(refusal=refusal):
                self.events.unlink(missing_ok=True)
                self.journal(self.gate(), self.review())
                self.gh.side_effect = lambda args, state=state: (
                    self.compare if args[0] == "api" else {"mergeStateStatus": state}
                )
                previous = self.gh.side_effect
                self.gh.side_effect = lambda args, previous=previous: (
                    {"isDraft": False, "labels": []} if args[-1] == "isDraft,labels" else previous(args)
                )

                def reject_merge(args: list[str], refusal=refusal, **_kwargs) -> subprocess.CompletedProcess:
                    if args[:3] == ["gh", "pr", "merge"]:
                        raise subprocess.CalledProcessError(1, args, stderr=refusal)
                    return subprocess.CompletedProcess(args, 0, "", "")

                self.run.side_effect = reject_merge
                with mock.patch.object(learn, "refresh") as refresh:
                    learn.promote_pass([self.pr])
                refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
                self.assertEqual(self.rows("promotion-blocked"), [])
                self.assertEqual(self.rows("merged"), [])

    def test_legacy_required_check_block_recovers_once_then_expires_on_new_head(self) -> None:
        self.compare["behind_by"] = 4
        self.journal(self.gate(), self.review(), {
            "event": "promotion-blocked", "pr": 42, "branch": self.BRANCH, "head": self.HEAD,
            "reason": 'merge failed: Required status check "gate" is expected.',
        })
        with mock.patch.object(learn, "refresh", side_effect=RuntimeError("merge conflict")) as refresh:
            learn.promote_pass([self.pr])
            after = lifecycle.read_events(self.events)
            learn.promote_pass([self.pr])
            learn.promote_pass([self.pr])
            refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
            self.assertEqual(lifecycle.read_events(self.events), after)
            self.assertEqual(self.rows("promotion-blocked")[-1]["reason"], "refresh failed")

        self.pr["headRefOid"] = new = "b" * 40
        self.journal({**self.gate(), "head": new}, {**self.review(), "head": new})
        self.compare["behind_by"] = 0
        learn.promote_pass([self.pr])
        self.assertEqual([row["head"] for row in self.rows("merged")], [new])

    def test_legacy_required_check_block_refreshes_even_if_compare_is_current(self) -> None:
        self.journal(self.gate(), self.review(), {
            "event": "promotion-blocked", "pr": 42, "head": self.HEAD,
            "reason": 'merge failed: Required status check "gate" is expected.',
        })
        with mock.patch.object(learn, "refresh") as refresh:
            learn.promote_pass([self.pr])
        refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
        self.run.assert_not_called()

    def test_lessons_and_curate_refresh_before_merge_including_dry_run(self) -> None:
        for kind, behind, state in (("lessons", 4, "BEHIND"), ("curate", 0, "BEHIND")):
            with self.subTest(kind=kind):
                self.events.unlink(missing_ok=True)
                branch = f"agent/{kind}-2026-10-27-t10"
                self.pr.update(headRefName=branch, mergeStateStatus=state)
                self.compare["behind_by"] = behind
                self.journal({**self.gate(), "branch": branch}, self.review())
                before = lifecycle.read_events(self.events)
                with mock.patch.object(learn, "refresh") as refresh:
                    learn.promote_pass([self.pr], dry_run=True)
                    refresh.assert_not_called()
                    self.assertEqual(lifecycle.read_events(self.events), before)
                    learn.promote_pass([self.pr])
                    refresh.assert_called_once_with(self.cfg, 42, branch, self.HEAD)
                self.run.assert_not_called()


    def test_refreshed_lessons_merge_unblocks_the_next_learning_pass(self) -> None:
        self.compare["behind_by"] = 4
        self.journal(self.gate(), self.review(), *(
            {"event": "merged", "ticket": n} for n in range(1, 11)
        ))
        open_prs = [self.pr]
        self.gh.side_effect = lambda args: self.compare if args[0] == "api" else open_prs
        previous = self.gh.side_effect
        self.gh.side_effect = lambda args: (
            {"isDraft": self.pr["isDraft"], "labels": []} if args[-1] == "isDraft,labels" else previous(args)
        )
        new = "b" * 40

        def refreshed(*_args) -> None:
            self.pr["headRefOid"] = new
            self.compare["behind_by"] = 0
            self.journal({**self.gate(), "head": new}, {**self.review(), "head": new})

        def merged(args: list[str], **_kwargs) -> subprocess.CompletedProcess:
            if args[:3] == ["gh", "pr", "merge"]:
                self.assertEqual(args[-1], new)
                open_prs.clear()
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(learn, "refresh", side_effect=refreshed) as refresh, \
                mock.patch.object(learn.score, "run"), \
                mock.patch.object(learn, "decide_promotions", return_value=[]), \
                mock.patch.object(learn, "run") as run_learn:
            learn.promote_pass(open_prs)
            self.run.assert_not_called()
            learn.learn_pass()
            run_learn.assert_not_called()
            refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
            self.run.side_effect = merged
            learn.promote_pass(open_prs)
            learn.learn_pass()
            run_learn.assert_called_once_with(self.cfg, learn.AUTO_EVERY, trigger="auto")
        self.assertEqual([row["head"] for row in self.rows("merged") if "pr" in row], [new])


class MergeRefreshTest(unittest.TestCase):
    def test_refresh_preserves_published_history_and_refuses_concurrent_commits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin, repo = root / "origin.git", root / "repo"

            def git(*args: str, cwd: Path = repo) -> str:
                return subprocess.run(
                    ["git", "-C", str(cwd), *args], check=True, text=True, capture_output=True,
                ).stdout.strip()

            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
            subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
            git("config", "user.email", "test@example.com")
            git("config", "user.name", "Test")
            git("config", "commit.gpgsign", "false")
            (repo / "README.md").write_text("base\n")
            git("add", "README.md")
            git("commit", "-qm", "base")
            branch = "agent/lessons-2026-11-04-t10"
            git("checkout", "-qb", branch)
            (repo / config.LESSONS_NAME).write_text("- Run the gate.\n")
            git("add", config.LESSONS_NAME)
            git("commit", "-qm", "lessons")
            head = git("rev-parse", "HEAD")
            git("push", "-q", "origin", branch)
            git("checkout", "-q", "main")
            (repo / "README.md").write_text("main advanced\n")
            git("commit", "-qam", "main advanced")
            tip = git("rev-parse", "HEAD")
            git("push", "-q", "origin", "main")
            cfg = config.Config(root=repo, repo="acme/widgets")
            cfg.factory.mkdir()
            with mock.patch.multiple(dispatch, create=True, cfg=cfg, EVENTS=cfg.factory / "events.jsonl"), \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate, \
                    mock.patch.object(dispatch, "review_external_pr") as review:
                learn.refresh(cfg, 42, branch, head)
                new = git("rev-parse", branch)
                self.assertEqual(git("rev-parse", f"{new}^1"), head)
                self.assertEqual(git("rev-parse", f"{new}^2"), tip)
                self.assertEqual(git("rev-parse", branch, cwd=origin), new)
                self.assertEqual(git("show", f"{new}:{config.LESSONS_NAME}"), "- Run the gate.")
                self.assertEqual(git("show", f"{new}:README.md"), "main advanced")
                review.assert_called_once_with(42, tip, new, branch)
                [row] = [r for r in lifecycle.read_events(dispatch.EVENTS) if r.get("event") == "gate"]
                self.assertEqual((row["head"], row["gate"]), (new, "PASS"))

                # A missing required check cannot be repaired by merging the same main again.
                real_run = dispatch.run

                def required_check_refusal(args: list[str], **kwargs) -> subprocess.CompletedProcess:
                    if args[:3] == ["gh", "pr", "merge"]:
                        raise subprocess.CalledProcessError(
                            1, args,
                            stderr='Repository rule violations found: Required status check "gate" is expected.',
                        )
                    if args[:3] == ["gh", "pr", "edit"]:
                        return subprocess.CompletedProcess(args, 0, "", "")
                    return real_run(args, **kwargs)

                pr = {
                    "number": 42, "headRefName": branch, "headRefOid": new,
                    "baseRefName": "main", "isDraft": False,
                }
                for legacy in (False, True):
                    with self.subTest(legacy=legacy):
                        dispatch.EVENTS.write_text("")
                        dispatch.record("gate", branch=branch, head=new, gate="PASS")
                        dispatch.record("review-result", pr=42, head=new, verdict="APPROVE")
                        if legacy:
                            dispatch.record(
                                "promotion-blocked", pr=42, head=new,
                                reason='merge failed: Required status check "gate" is expected.',
                            )
                        gates_before, reviews_before = gate.call_count, review.call_count
                        with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                                mock.patch.object(dispatch, "run", side_effect=required_check_refusal), \
                                mock.patch.object(dispatch, "pr_checks", return_value=[{"bucket": "pass"}]), \
                                mock.patch.object(dispatch, "gh_json", return_value={
                                    "behind_by": 0, "files": [{"filename": config.LESSONS_NAME}],
                                    "isDraft": False, "labels": [],
                                }):
                            for _ in range(3):
                                learn.promote_pass([pr])
                        self.assertLessEqual(gate.call_count - gates_before, 1)
                        self.assertEqual(review.call_count, reviews_before)
                        blocked = [
                            row for row in lifecycle.read_events(dispatch.EVENTS)
                            if row.get("event") == "promotion-blocked" and row["reason"] == "refresh failed"
                        ]
                        self.assertEqual([row["head"] for row in blocked], [new])
                        self.assertEqual(git("rev-parse", branch, cwd=origin), new)
                        self.assertFalse((cfg.factory / f"wt-{branch.removeprefix('agent/')}").exists())

                (repo / "README.md").write_text("main advanced again\n")
                git("commit", "-qam", "another main commit")
                git("push", "-q", "origin", "main")
                concurrent = git(
                    "-c", "user.email=test@example.com", "-c", "user.name=Test",
                    "commit-tree", f"{new}^{{tree}}", "-p", new, "-m", "concurrent", cwd=origin,
                )

                def race(*_args, **_kwargs) -> tuple[bool, str]:
                    git("update-ref", f"refs/heads/{branch}", concurrent, cwd=origin)
                    return True, "PASS"

                gate.side_effect = race
                with self.assertRaises(subprocess.CalledProcessError) as refused:
                    learn.refresh(cfg, 42, branch, new)
                self.assertIn("[rejected]", refused.exception.stderr)
                self.assertEqual(git("rev-parse", branch, cwd=origin), concurrent)
                review.assert_called_once()
                self.assertFalse((cfg.factory / f"wt-{branch.removeprefix('agent/')}").exists())


class LessonsFileTest(unittest.TestCase):
    def test_new_lessons_file_starts_with_the_repository_spdx_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text("MIT License\n\nCopyright (c) Example\n")
            cfg = config.Config(root=root, repo="acme/widgets", manager=None)
            cfg.factory.mkdir()
            with mock.patch.object(learn, "evidence", return_value=([1], "evidence")), \
                    mock.patch.object(learn, "propose", return_value=(["Run tests."], None)), \
                    mock.patch.object(dispatch, "record"), \
                    mock.patch.object(learn, "record_learn_decision"):
                learn.run(cfg, last=1)

            self.assertTrue(
                (root / config.LESSONS_NAME).read_text().startswith("<!-- SPDX-License-Identifier: MIT -->\n")
            )

    def test_apache_license_file_with_standard_indentation_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text(
                "                                 Apache License\n"
                "                           Version 2.0, January 2004\n"
            )

            self.assertEqual(learn.repository_spdx(root), "Apache-2.0")


class DoctorSigningKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("factory.claims.repository_host_problem", return_value=None))

    def test_doctor_warns_when_configured_ssh_signing_key_is_not_registered(self) -> None:
        self.enterContext(mock.patch.dict(os.environ))
        for name in list(os.environ):
            if name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS") or name.startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
            ):
                del os.environ[name]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            key = root / "agent.pub"
            key.write_text("ssh-ed25519 AAAAagent factory-agent\n")
            for name, value in (
                ("gpg.format", "ssh"),
                ("commit.gpgsign", "true"),
                ("user.signingkey", str(key)),
            ):
                subprocess.run(["git", "-C", str(root), "config", name, value], check=True)
            cfg = config.Config(root=root, repo="acme/widgets", manager=["true", "{prompt}", "{cwd}"])
            cfg.factory.mkdir()
            real_sh = onboard.sh
            commands: list[list[str]] = []

            def command(argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None):
                commands.append(argv)
                if argv == ["gh", "api", "user", "--jq", ".login"]:
                    return subprocess.CompletedProcess(argv, 0, "mikeroysoft-agent\n", "")
                if argv[:3] == ["gh", "api", "users/mikeroysoft-agent/ssh_signing_keys"]:
                    return subprocess.CompletedProcess(argv, 0, "ssh-ed25519 AAAAother another-key\n", "")
                if argv[:3] == ["gh", "api", "user/ssh_signing_keys"]:
                    return subprocess.CompletedProcess(argv, 0, "ssh-ed25519 AAAAother another-key\n", "")
                if argv[:3] == ["gh", "repo", "view"]:
                    return subprocess.CompletedProcess(argv, 0, "ADMIN\n", "")
                if argv[:3] == ["gh", "label", "list"]:
                    return subprocess.CompletedProcess(argv, 0, "[]\n", "")
                if argv[0] == "git":
                    return real_sh(argv, cwd)
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch.object(config, "load", return_value=cfg), \
                    mock.patch.object(onboard, "sh", side_effect=command), \
                    mock.patch.object(onboard, "report_stage_success"), \
                    mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")), \
                    mock.patch.object(onboard.shutil, "which", return_value="/bin/true"), \
                    mock.patch("factory.triage.probe_endpoint", return_value=(True, "ok")), \
                    mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
                output = io.StringIO()
                with mock.patch("sys.stdout", output):
                    onboard.doctor(["--json"])

            rows = json.loads(output.getvalue())["rows"]
            signing = next(row for row in rows if row["label"] == "SSH signing key registered")
            self.assertEqual(signing["status"], "WARN")
            self.assertIn("unknown_key", signing["detail"])
            self.assertIn(["gh", "api", "user", "--jq", ".login"], commands)
            self.assertIn(
                [
                    "gh",
                    "api",
                    "users/mikeroysoft-agent/ssh_signing_keys",
                    "--paginate",
                    "--jq",
                    ".[].key",
                ],
                commands,
            )


if __name__ == "__main__":
    unittest.main()
