"""Reviewers start outside any worker-written checkout so project config cannot execute."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, dispatch, lifecycle

# Like agent CLIs: run .omp/startup.py found in cwd or an ancestor, then report the environment.
PROBE = """
import json, os, sys
from pathlib import Path
cwd = Path.cwd().resolve()
for d in (cwd, *cwd.parents):
    startup = d / ".omp" / "startup.py"
    if startup.is_file():
        exec(compile(startup.read_text(), str(startup), "exec"))
        break
json.dump({"cwd": str(cwd), "git": [k for k in os.environ if k.startswith("GIT_")],
           "snapshot": (cwd / "snapshot" / ".omp" / "startup.py").is_file(),
           "diff": (cwd / "review.diff").read_text() if (cwd / "review.diff").is_file() else None},
          open(sys.argv[1], "w"))
print('```json\\n{"verdict":"APPROVE","findings":[]}\\n```\\nVERDICT: APPROVE')
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


class ReviewIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "README.md").write_text("base\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.sentinel = self.tmp / "sentinel"
        startup = self.repo / ".omp" / "startup.py"
        startup.parent.mkdir()
        startup.write_text(f"open({str(self.sentinel)!r}, 'w').write('ran')\n")
        git(self.repo, "add", ".omp")
        git(self.repo, "commit", "-q", "-m", "worker")
        self.head = git(self.repo, "rev-parse", "HEAD")
        # A linked ticket worktree distinct from the root, as the dispatcher uses.
        self.wt = self.tmp / "wt-7"
        git(self.repo, "worktree", "add", "-q", "--detach", str(self.wt), self.head)
        # Keep the root on another revision so inherited GIT_* would redirect controller reads.
        git(self.repo, "reset", "-q", "--hard", self.base)
        self.out = self.tmp / "seen.json"
        self.cfg = config.Config(
            root=self.repo,
            repo="acme/widgets",
            reviewer=[sys.executable, "-c", PROBE, str(self.out)],
        )
        dispatch.configure(self.cfg)
        self.cfg.factory.mkdir(exist_ok=True)
        env = mock.patch.dict(os.environ, {
            "GIT_DIR": str(self.repo / ".git"),
            "GIT_WORK_TREE": str(self.repo),
            "GIT_INDEX_FILE": str(self.tmp / "wrong-index"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.enterContext(mock.patch.object(dispatch, "gh_json", return_value={
            "title": "Fixture issue", "body": "Fixture acceptance criteria", "comments": [],
        }))

    def assert_isolated(self) -> dict:
        self.assertFalse(self.sentinel.exists(), "reviewer executed worker-written startup file")
        seen = json.loads(self.out.read_text())
        cwd = Path(seen["cwd"])
        for checkout in (self.repo, self.wt):
            self.assertNotIn(checkout, (cwd, *cwd.parents))
        self.assertEqual(seen["git"], [])
        return seen

    def test_positive_control_probe_runs_startup_inside_checkout(self) -> None:
        subprocess.run([sys.executable, "-c", PROBE, str(self.out)], cwd=self.wt, check=True)
        self.assertTrue(self.sentinel.exists())

    def test_ticket_review_starts_outside_checkout_with_gated_snapshot(self) -> None:
        verdict, _ = dispatch.review(self.wt, 7, "PASS", self.head)
        self.assertEqual(verdict, "APPROVE")
        seen = self.assert_isolated()
        self.assertTrue(seen["snapshot"])
        self.assertIn(".omp/startup.py", seen["diff"])

    def test_failed_snapshot_fails_the_review_closed(self) -> None:
        real = subprocess.run

        def run(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "archive"]:
                raise subprocess.CalledProcessError(128, cmd, b"", b"fatal: not a valid object")
            return real(cmd, *args, **kwargs)

        with mock.patch.object(dispatch.subprocess, "run", side_effect=run):
            verdict, findings = dispatch.review(self.wt, 7, "PASS", self.head)
        self.assertEqual(verdict, "REVISE")
        self.assertIn("CalledProcessError", findings)
        self.assertFalse(self.sentinel.exists())

    def test_failed_external_diff_fails_closed(self) -> None:
        failure = subprocess.CalledProcessError(1, ["gh", "api"])
        with mock.patch.object(dispatch, "run", side_effect=failure):
            dispatch.review_external_pr(17, "base", self.head)
        self.assertFalse(self.out.exists())
        [exit_event] = [
            event for event in lifecycle.read_events(dispatch.EVENTS)
            if event.get("stage") == "review" and event.get("kind") == "exit"
        ]
        self.assertEqual(exit_event["outcome"], "unknown")
        self.assertIn("CalledProcessError", exit_event["reason"])

    def test_external_pr_review_starts_outside_checkout(self) -> None:
        real_run = dispatch.run

        def run(cmd, **kwargs):
            if cmd[0] == "gh":
                if any("/compare/" in arg for arg in cmd):
                    self.assertFalse(any(key.startswith("GIT_") for key in kwargs["env"]))
                return subprocess.CompletedProcess(cmd, 0, "diff", "")
            return real_run(cmd, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run):
            dispatch.review_external_pr(17, "base", self.head)
        self.assert_isolated()

    def test_reviewer_prompt_carries_frozen_issue_and_runs_tokenless(self) -> None:
        issue = {
            "title": "Preserve the accepted scope",
            "body": "Acceptance: keep the original behavior.\n```python\nexample()\n```",
            "comments": [
                {"author": {"login": "owner"}, "body": "Approved scope: original only."},
                {"author": {"login": "triage"}, "body": "Agent brief: verify the boundary."},
                {"author": None, "body": "Deleted author's comment is still evidence."},
            ],
        }
        script = """
import json, os, sys
from pathlib import Path
json.dump({
    "prompt": sys.argv[2],
    "tokens": [k for k in ("GH_TOKEN", "GITHUB_TOKEN") if k in os.environ],
    "config": os.environ["GH_CONFIG_DIR"],
    "config_entries": [p.name for p in Path(os.environ["GH_CONFIG_DIR"]).iterdir()],
}, open(sys.argv[1], "w"))
print('```json\\n{"verdict":"APPROVE","findings":[]}\\n```\\nVERDICT: APPROVE')
"""
        self.cfg.reviewer = [sys.executable, "-c", script, str(self.out), "{prompt}"]
        from factory import binding

        for bound in (True, False):
            with self.subTest(bound=bound), \
                    mock.patch.dict(os.environ, {
                        "GH_TOKEN": "dispatcher-gh", "GITHUB_TOKEN": "dispatcher-github",
                        "GH_CONFIG_DIR": str(self.tmp),
                    }), \
                    mock.patch.object(binding, "accepted", return_value={"issue": issue} if bound else None), \
                    mock.patch.object(dispatch, "gh_json", return_value=issue) as fetch:
                verdict, _ = dispatch.review(self.wt, 7, "PASS", self.head)
                self.assertEqual(verdict, "APPROVE")
                seen = json.loads(self.out.read_text())
                self.assertEqual(seen["tokens"], [])
                self.assertEqual(seen["config_entries"], [])
                self.assertNotEqual(seen["config"], str(self.tmp))
                prompt = seen["prompt"]
                self.assertIn(issue["title"], prompt)
                supplied = prompt.split("--- BEGIN UNTRUSTED ISSUE ---\n", 1)[1].split(
                    "\n--- END UNTRUSTED ISSUE ---", 1,
                )[0]
                self.assertIn(f"# Issue #7: {issue['title']}", supplied)
                self.assertIn(issue["body"], supplied)
                for author, comment in zip(("owner", "triage", "unknown"), issue["comments"]):
                    self.assertIn(f"## Comment by {author}\n\n{comment['body']}", supplied)
                self.assertNotIn("Read the issue comments", prompt)
                if bound:
                    fetch.assert_not_called()
                else:
                    fetch.assert_called_once_with([
                        "issue", "view", "7", "--repo", "acme/widgets", "--json", "title,body,comments",
                    ])
                self.assertEqual(os.environ["GH_TOKEN"], "dispatcher-gh")
                self.assertEqual(os.environ["GITHUB_TOKEN"], "dispatcher-github")

    def test_learning_proposal_prompt_does_not_fetch_issue_context(self) -> None:
        from factory import binding

        with mock.patch.object(binding, "accepted") as accepted, \
                mock.patch.object(dispatch, "gh_json") as fetch:
            prompt = dispatch.review_prompt(None, "PASS", proposal="Use the evaluated guidance.")
        accepted.assert_not_called()
        fetch.assert_not_called()
        self.assertIn("## Learning proposal\n\nUse the evaluated guidance.", prompt)
        self.assertNotIn("UNTRUSTED ISSUE", prompt)


if __name__ == "__main__":
    unittest.main()
