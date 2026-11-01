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

    def test_snapshot_setup_failure_removes_read_only_tree(self) -> None:
        scratch = self.tmp / "review-tmp"
        scratch.mkdir()
        write_text = Path.write_text

        def fail_diff(path, *args, **kwargs):
            if path.name == "review.diff":
                raise OSError("write failed")
            return write_text(path, *args, **kwargs)

        with mock.patch.object(dispatch.tempfile, "tempdir", str(scratch)), \
                mock.patch.object(Path, "write_text", fail_diff), \
                mock.patch.object(
                    dispatch, "_make_tree_writable", wraps=dispatch._make_tree_writable,
                ) as writable, \
                self.assertRaisesRegex(OSError, "write failed"):
            dispatch._run_reviewer(
                [sys.executable, "-c", ""], self.wt, self.head, "diff",
            )
        writable.assert_called_once()
        self.assertEqual(list(scratch.iterdir()), [])

    def test_stale_read_only_snapshot_is_swept(self) -> None:
        scratch = self.tmp / "review-tmp"
        snapshot = scratch / "factory-review-stale" / "snapshot"
        snapshot.mkdir(parents=True)
        source = snapshot / "source.py"
        source.write_text("pass\n")
        source.chmod(0o444)
        snapshot.chmod(0o555)
        os.utime(snapshot.parent, (0, 0))

        with mock.patch.object(dispatch.tempfile, "tempdir", str(scratch)):
            dispatch._sweep_stale_factory_tempdirs()

        self.assertFalse(snapshot.parent.exists())

    def test_make_tree_writable_changes_only_owner_write_permission(self) -> None:
        tree = self.tmp / "read-only"
        tree.mkdir()
        source = tree / "source.py"
        source.write_text("pass\n")
        tree.chmod(0o555)
        source.chmod(0o444)

        dispatch._make_tree_writable(tree)

        self.assertEqual(tree.stat().st_mode & 0o777, 0o755)
        self.assertEqual(source.stat().st_mode & 0o777, 0o644)

    def test_snapshot_sweep_selects_only_directories_older_than_one_day(self) -> None:
        scratch = self.tmp / "review-tmp"
        scratch.mkdir()
        now = 2_000_000

        def snapshot(name: str, age: float) -> Path:
            path = scratch / f"factory-review-{name}"
            path.mkdir()
            modified = now - age * 60 * 60
            os.utime(path, (modified, modified))
            return path

        stale = snapshot("stale", 24.5)
        boundary = snapshot("boundary", 24)
        recent = snapshot("recent", 23.5)
        unreadable = snapshot("unreadable", 48)
        target = scratch / "link-target"
        target.mkdir()
        link = scratch / "factory-review-link"
        link.symlink_to(target, target_is_directory=True)
        is_symlink = Path.is_symlink

        def fail_unreadable(path: Path) -> bool:
            if path == unreadable:
                raise OSError("stat failed")
            return is_symlink(path)

        with mock.patch.object(dispatch.tempfile, "tempdir", str(scratch)), \
                mock.patch.object(dispatch.time, "time", return_value=now), \
                mock.patch.object(Path, "is_symlink", fail_unreadable), \
                mock.patch.object(dispatch, "_make_tree_writable") as writable, \
                mock.patch.object(dispatch.shutil, "rmtree") as rmtree:
            dispatch._sweep_stale_factory_tempdirs()

        writable.assert_called_once_with(stale)
        rmtree.assert_called_once_with(stale, ignore_errors=True)
        self.assertTrue(boundary.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unreadable.exists())
        self.assertTrue(link.is_symlink())

    def test_reviewer_snapshot_is_read_only_and_archive_failure_checked(self) -> None:
        subprocess_run = dispatch.subprocess.run
        chmod = Path.chmod
        original_modes = {}

        def checked_run(command, *args, **kwargs):
            if command[:2] == ["git", "archive"]:
                self.assertIs(kwargs.get("check"), True)
            return subprocess_run(command, *args, **kwargs)

        def record_chmod(path: Path, mode: int) -> None:
            original_modes.setdefault(path, path.stat().st_mode & 0o777)
            chmod(path, mode)

        def inspect_snapshot(command, **kwargs):
            snapshot = Path(kwargs["cwd"]) / "snapshot"
            for path in [snapshot, *snapshot.rglob("*")]:
                self.assertEqual(
                    path.stat().st_mode & 0o777,
                    original_modes[path] & ~0o222,
                )
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(dispatch.subprocess, "run", side_effect=checked_run), \
                mock.patch.object(Path, "chmod", record_chmod), \
                mock.patch.object(dispatch, "run", side_effect=inspect_snapshot):
            result = dispatch._run_reviewer(
                [sys.executable, "-c", ""], self.wt, self.head, "diff",
            )

        self.assertEqual(result.returncode, 0)

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


if __name__ == "__main__":
    unittest.main()
