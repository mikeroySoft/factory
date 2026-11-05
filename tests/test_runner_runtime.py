from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import dispatch, lifecycle
from factory.config import Config

ROOT = Path(__file__).resolve().parents[1]


class RunnerRuntimeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = os.environ | {"TMPDIR": str(self.root), "PYTHONPATH": str(ROOT)}
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))

    def preflight(self):
        # Import here so the reproducing commit remains discoverable before the fix.
        from factory import runner

        with patch.object(tempfile, "tempdir", str(self.root)):
            return runner.preflight(self.root)

    def owner(self, child=False):
        source = (
            "import json, os, subprocess, sys, time\n"
            "from factory import runner\n"
            "with runner.temp_root('gate') as root:\n"
            "    (root / 'payload').write_text('keep me')\n"
            "    child = None\n"
            + ("    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], "
               "env=os.environ | {'TMPDIR': str(root)}, start_new_session=True)\n" if child else "")
            + "    print(json.dumps({'root': str(root), 'child': child.pid if child else None}), flush=True)\n"
            "    time.sleep(120)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", source], env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop, proc)
        line = proc.stdout.readline()
        if not line:
            _, err = proc.communicate(timeout=10)
            self.fail(err)
        info = json.loads(line)
        if info["child"]:
            self.addCleanup(self.kill, info["child"])
        proc.kill()
        proc.wait(timeout=10)
        return Path(info["root"]), info["child"]

    @staticmethod
    def kill(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)

    def test_next_preflight_removes_temp_root_abandoned_by_crashed_owner(self):
        root, _ = self.owner()
        self.assertTrue((root / "payload").exists())
        self.preflight()
        self.assertFalse(root.exists())

    def test_dead_owner_does_not_reclaim_a_surviving_child_temp_root(self):
        root, child = self.owner(child=True)
        os.kill(child, 0)
        self.preflight()
        self.assertEqual((root / "payload").read_text(), "keep me")

    def test_low_bytes_or_inodes_refuse_claim_as_infra_without_attempt(self):
        cfg = Config(root=self.root, repo="local/runner", workers={"default": [sys.executable]})
        issue = {"number": 7, "title": "ticket", "labels": [{"name": "ready-for-agent"}]}
        events = cfg.factory / "events.jsonl"
        for field in ("f_bavail", "f_favail"):
            with self.subTest(field=field):
                stats = {"f_bavail": 2**30, "f_frsize": 4096, "f_favail": 1_000_000, "f_files": 2_000_000}
                stats[field] = 0
                with patch.multiple(dispatch, create=True, cfg=cfg, ROOT=cfg.root, REPO=cfg.repo,
                                    FACTORY=cfg.factory, EVENTS=events), \
                        patch("os.statvfs", return_value=SimpleNamespace(**stats)), \
                        patch.object(tempfile, "tempdir", str(self.root)), \
                        patch.object(dispatch, "run") as run, \
                        patch.object(dispatch, "gh_json", return_value={
                            **issue, "state": "CLOSED", "assignees": [], "body": "",
                        }), patch.object(dispatch, "acquire_host_claim") as host_claim:
                    dispatch.process_ticket(issue, 1, False)
                run.assert_not_called()
                host_claim.assert_not_called()
                rows = lifecycle.read_events(events)
                exits = [r for r in rows if r.get("kind") == "exit" and r.get("stage") == "ticket"]
                self.assertEqual((exits[-1]["outcome"], exits[-1]["reason"]),
                                 ("infra", "runner_storage_low"))
                self.assertFalse(any(r.get("event") in {"claimed", "attempt", "escalate"} for r in rows))


if __name__ == "__main__":
    unittest.main()
