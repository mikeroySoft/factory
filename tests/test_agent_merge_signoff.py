"""Agent-created merge commits are signed without rewriting existing history."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, learn
from factory.config import Config


REAL_RUN = dispatch.run
SIGNOFF_FORMAT = "%(trailers:key=Signed-off-by,valueonly)"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(repo: Path, path: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{message}\n")
    git(repo, "add", path)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def repository(parent: Path, branch: str) -> tuple[Path, Path, str, str, dict[str, str]]:
    origin, repo = parent / "origin.git", parent / "repo"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(repo)],
        check=True,
        capture_output=True,
    )
    git(repo, "config", "user.name", "Factory Agent")
    git(repo, "config", "user.email", "factory@example.test")

    base = commit(repo, "README.md", "base")
    git(repo, "checkout", "-qb", "human/topic")
    human_topic = commit(repo, "human-topic.txt", "Human topic")
    git(repo, "checkout", "-q", "main")
    human_main = commit(repo, "human-main.txt", "Human mainline")
    git(repo, "merge", "-q", "--no-ff", "-m", "Human merge without signoff", "human/topic")
    human_merge = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "-q", "origin", "main")

    git(repo, "checkout", "-qb", branch)
    branch_head = commit(repo, "agent-work.txt", "Agent work")
    git(repo, "push", "-q", "origin", branch)
    git(repo, "checkout", "-q", "main")
    main_tip = commit(repo, "landed-later.txt", "Human change after branch")
    git(repo, "push", "-q", "origin", "main")

    existing = {
        oid: git(repo, "cat-file", "commit", oid)
        for oid in (base, human_topic, human_main, human_merge, branch_head, main_tip)
    }
    if git(repo, "show", "-s", f"--format={SIGNOFF_FORMAT}", human_merge):
        raise AssertionError("fixture's human merge unexpectedly has a sign-off")
    return repo, origin, branch_head, main_tip, existing


class AgentMergeSignoffTest(unittest.TestCase):
    def assert_merge_policy(self, repo: Path, merge: str, signoff: bool) -> None:
        trailers = git(repo, "show", "-s", f"--format={SIGNOFF_FORMAT}", merge)
        self.assertEqual(bool(trailers), signoff)
        self.assertEqual(len(git(repo, "show", "-s", "--format=%P", merge).split()), 2)

    def assert_history_untouched(self, repo: Path, existing: dict[str, str]) -> None:
        self.assertEqual(
            {oid: git(repo, "cat-file", "commit", oid) for oid in existing}, existing
        )
        human_merge = next(
            oid
            for oid in existing
            if git(repo, "show", "-s", "--format=%s", oid)
            == "Human merge without signoff"
        )
        self.assertFalse(git(repo, "show", "-s", f"--format={SIGNOFF_FORMAT}", human_merge))

    def test_upstream_sync_merge_signs_only_its_new_commit_and_honors_opt_out(self) -> None:
        for signoff in (True, False):
            with self.subTest(signoff=signoff), tempfile.TemporaryDirectory() as directory:
                repo, _origin, _head, tip, existing = repository(
                    Path(directory), "agent/7"
                )
                git(repo, "checkout", "-q", "agent/7")
                dispatch.configure(Config(repo, "acme/widgets", signoff=signoff))

                result = dispatch.merge_upstream_rev(repo, tip, tip, "1")

                self.assertEqual(result.returncode, 0, result.stderr)
                merge = git(repo, "rev-parse", "HEAD")
                self.assert_merge_policy(repo, merge, signoff)
                self.assert_history_untouched(repo, existing)

    def test_pr_refresh_merge_signs_only_its_new_commit_and_honors_opt_out(self) -> None:
        for signoff in (True, False):
            with self.subTest(signoff=signoff), tempfile.TemporaryDirectory() as directory:
                repo, _origin, _head, _tip, existing = repository(
                    Path(directory), "agent/7"
                )
                cfg = Config(repo, "acme/widgets", signoff=signoff)
                cfg.factory.mkdir()
                dispatch.configure(cfg)

                def gh_json(args: list[str]) -> dict:
                    if args[:2] == ["pr", "view"]:
                        return {"baseRefName": "main"}
                    if args[:2] == ["issue", "view"]:
                        return {"state": "OPEN", "labels": [], "body": "", "comments": []}
                    raise AssertionError(args)

                def run(args: list[str], *positional: object, **keywords: object):
                    if args[0] == "gh":
                        return subprocess.CompletedProcess(args, 0, "", "")
                    return REAL_RUN(args, *positional, **keywords)

                with mock.patch.object(dispatch, "gh_json", side_effect=gh_json), \
                        mock.patch.object(dispatch, "run", side_effect=run), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "ok")), \
                        mock.patch.object(dispatch, "push_agent", return_value="pushed"), \
                        mock.patch.object(dispatch, "record_risk"), \
                        mock.patch.object(dispatch, "risk_for_head", return_value=("Low", [])), \
                        mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")), \
                        mock.patch.object(dispatch, "pr_comment"), \
                        mock.patch.object(dispatch, "approve_pr", return_value=True), \
                        mock.patch.object(dispatch, "log"):
                    self.assertTrue(dispatch.refresh_pr_branch(7, 70, True))

                wt = cfg.factory / "wt-7"
                merge = git(wt, "rev-parse", "HEAD")
                self.assert_merge_policy(wt, merge, signoff)
                self.assert_history_untouched(repo, existing)

    def test_chore_refresh_merge_signs_only_its_new_commit_and_honors_opt_out(self) -> None:
        branch = "agent/curate-2026-11-06-t7"
        for signoff in (True, False):
            with self.subTest(signoff=signoff), tempfile.TemporaryDirectory() as directory:
                repo, origin, head, _tip, existing = repository(Path(directory), branch)
                cfg = Config(repo, "acme/widgets", signoff=signoff)
                cfg.factory.mkdir()
                dispatch.configure(cfg)

                with mock.patch.object(dispatch, "run_gate", return_value=(True, "ok")), \
                        mock.patch.object(dispatch, "review_external_pr"):
                    learn.refresh(cfg, 42, branch, head)

                merge = git(origin, "rev-parse", branch)
                self.assert_merge_policy(repo, merge, signoff)
                self.assert_history_untouched(repo, existing)


if __name__ == "__main__":
    unittest.main()
