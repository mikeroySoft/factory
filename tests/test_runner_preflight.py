"""Storage admission and real gate-owner crashes in disposable temp roots."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import dispatch, lifecycle
from factory.config import Config


class RunnerPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.scratch = self.base / "tmp"
        self.scratch.mkdir()
        self.enterContext(patch.object(tempfile, "tempdir", str(self.scratch)))
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(root=self.base, repo="local/preflight")
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.base, REPO=self.cfg.repo,
            FACTORY=self.cfg.factory, EVENTS=self.cfg.factory / "events.jsonl",
        ))

    def until(self, predicate):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("disposable gate child did not reach its checkpoint")

    def crashed_gate(self):
        repo = self.base / "repo"
        repo.mkdir()
        ready = self.base / "ready.json"
        go = self.base / "go"
        done = self.base / "done"
        check = (
            "import json, os, time; from pathlib import Path; "
            "root = Path(os.environ['TMPDIR']); "
            f"Path({str(ready)!r}).write_text(json.dumps([str(root), os.getpid()])); "
            f"deadline = time.monotonic() + 30\n"
            f"while not Path({str(go)!r}).exists() and time.monotonic() < deadline: time.sleep(.01)\n"
            f"(root / 'still-used').write_text('child survived'); Path({str(done)!r}).touch()"
        )
        (repo / ".factory.toml").write_text(
            '[repo]\nslug = "local/preflight"\n[leak_scan]\npattern = ""\n'
            '[[gate.check]]\nname = "child"\nexclusive = false\nrun = '
            + json.dumps([sys.executable, "-c", check]) + "\n"
        )
        env = os.environ | {
            "TMPDIR": str(self.scratch),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        }
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       env=env, check=True, capture_output=True)
        owner = subprocess.Popen(
            [sys.executable, "-m", "factory", "gate", "--skip", "conflict-markers"],
            cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: owner.poll() is None and (owner.kill(), owner.wait(timeout=10)))
        self.until(ready.exists)
        self.until(lambda: ready.stat().st_size > 0)
        root, pid = json.loads(ready.read_text())
        self.addCleanup(self.kill_child, pid)
        owner.kill()
        owner.wait(timeout=10)
        return Path(root), pid, go, done

    @staticmethod
    def kill_child(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_next_preflight_removes_temp_root_abandoned_by_crashed_owner(self):
        root, pid, _, _ = self.crashed_gate()
        self.kill_child(pid)
        self.until(lambda: lifecycle._process_state(lifecycle._identity(pid)) != "alive")
        (root / "leftover").write_text("abandoned")
        dispatch._sweep_stale_factory_tempdirs()
        self.assertFalse(root.exists(), "next preflight must reap without waiting 24 hours")

    def test_dead_owner_does_not_reclaim_a_surviving_child_temp_root(self):
        root, _, go, done = self.crashed_gate()
        os.utime(root, (0, 0))
        dispatch._sweep_stale_factory_tempdirs()
        self.assertTrue(root.exists(), "age does not make a live child's temp root disposable")
        go.touch()
        self.until(done.exists)
        self.assertEqual((root / "still-used").read_text(), "child survived")

    def test_low_bytes_or_inodes_refuses_claim_as_infrastructure(self):
        issue = {"number": 7, "title": "local ticket", "labels": []}
        for field in ("f_bavail", "f_favail"):
            for forced in (False, True):
                with self.subTest(field=field, forced=forced):
                    values = dict(f_bavail=10**12, f_frsize=4096, f_favail=10**9, f_files=10**9)
                    values[field] = 0
                    with patch("os.statvfs", return_value=SimpleNamespace(**values)), \
                            patch.object(dispatch, "gh_json") as query, \
                            patch.object(dispatch, "run") as run, \
                            patch.object(dispatch, "acquire_host_claim") as claim:
                        dispatch.process_ticket(issue, 1, False, forced=forced)
                    query.assert_not_called()
                    run.assert_not_called()
                    claim.assert_not_called()
                    rows = lifecycle.read_events(dispatch.EVENTS)
                    exit_row = [r for r in rows if r.get("kind") == "exit"][-1]
                    self.assertEqual((exit_row["outcome"], exit_row["reason"]),
                                     ("infra", "runner_storage_low"))
                    self.assertFalse(any(r.get("event") in {"claimed", "attempt", "escalate"} for r in rows))

    def test_unknown_owner_and_symlinks_are_retained(self):
        unknown = self.scratch / "factory-gate-unknown"
        unknown.mkdir()
        (unknown / ".factory-owner.json").write_text("not json")
        target = self.base / "outside"
        target.mkdir()
        link = self.scratch / "factory-review-link"
        link.symlink_to(target, target_is_directory=True)
        os.utime(unknown, (0, 0))
        dispatch._sweep_stale_factory_tempdirs()
        self.assertTrue(unknown.exists())
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())
