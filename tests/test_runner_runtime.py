"""Runner storage admission and temp-root crash recovery (#610)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, lifecycle

ROOT = Path(__file__).resolve().parents[1]


class RunnerRuntimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.enterContext(mock.patch.object(tempfile, "tempdir", str(self.base)))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        dispatch.configure(config.Config(self.base, "example/project"))

    def owner(self, *, child=False):
        # Exercise the real reviewer temp context. Block the owner at launch so
        # SIGKILL bypasses normal cleanup, optionally leaving a real live child.
        code = (
            "import subprocess,sys\nfrom factory import config, dispatch\n"
            f"from pathlib import Path\ndispatch.configure(config.Config(Path({str(self.base)!r}), 'example/project'))\n"
            "def launch(cmd, **kwargs):\n"
            "    print(kwargs['cwd'], flush=True)\n"
            f"    if {child!r}:\n"
            "        child = subprocess.Popen([sys.executable, '-u', '-c', "
            "'import time; print(\"ready\", flush=True); time.sleep(60)'], "
            "cwd=kwargs['cwd'], env=kwargs['env'], stdout=subprocess.PIPE, start_new_session=True, text=True)\n"
            "        assert child.stdout.readline().strip() == 'ready'\n"
            "        print(child.pid, flush=True)\n"
            "    sys.stdin.read()\n"
            "    return subprocess.CompletedProcess(cmd, 0, '', '')\n"
            "dispatch.run = launch\n"
            "dispatch._run_reviewer(['unused'])\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", code], cwd=ROOT,
            env=os.environ | {"TMPDIR": str(self.base)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()

        self.addCleanup(stop)
        path = Path(proc.stdout.readline().strip())
        self.assertTrue(path.is_dir())
        child_pid = int(proc.stdout.readline()) if child else None
        if child_pid:
            self.addCleanup(self.stop_child, child_pid)
        return proc, path, child_pid

    @staticmethod
    def stop_child(pid):
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass

    def test_next_preflight_removes_temp_root_abandoned_by_crashed_owner(self):
        owner, path, _ = self.owner()
        owner.kill()
        owner.wait(timeout=10)
        dispatch._sweep_stale_factory_tempdirs()
        self.assertFalse(path.exists(), f"abandoned review root retained: {path}")

    def test_dead_owner_does_not_reclaim_a_surviving_child_temp_root(self):
        owner, path, _ = self.owner(child=True)
        owner.kill()
        owner.wait(timeout=10)
        dispatch._sweep_stale_factory_tempdirs()
        self.assertTrue(path.exists(), "live child's review root was reaped")

    def test_review_root_hygiene_ignores_unreadable_unrelated_processes(self):
        owner, path, _ = self.owner()
        owner.kill()
        owner.wait(timeout=10)
        real_read = Path.read_bytes

        def unreadable_process(path):
            if str(path).startswith("/proc/") and path.name == "environ":
                raise PermissionError("unrelated process visibility unavailable")
            return real_read(path)

        with mock.patch.object(Path, "read_bytes", unreadable_process):
            dispatch._sweep_stale_factory_tempdirs()
        self.assertFalse(path.exists(), f"review-root hygiene leaked: {path}")

    def test_live_owner_is_not_reaped(self):
        owner, path, _ = self.owner()
        dispatch._sweep_stale_factory_tempdirs()
        self.assertIsNone(owner.poll())
        self.assertTrue(path.exists())

    def test_normal_review_cleanup_leaves_no_runner_roots_with_unknown_visibility(self):
        real_read = Path.read_bytes

        def unreadable_process(path):
            if str(path).startswith("/proc/") and path.name == "environ":
                raise PermissionError("process environment unavailable")
            return real_read(path)

        with mock.patch.object(Path, "read_bytes", unreadable_process):
            result = dispatch._run_reviewer([sys.executable, "-c", "print('reviewed')"])
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "reviewed"))
        self.assertEqual(list(self.base.glob("factory-runner-*")), [])

    def test_preflight_checks_temp_filesystem_and_recovers_at_threshold(self):
        from factory import runner

        repository = self.base / "repo"
        repository.mkdir()
        healthy = SimpleNamespace(f_bavail=runner.MIN_FREE_BYTES, f_frsize=1,
                                  f_favail=runner.MIN_FREE_INODES, f_files=10**7)
        low_temp = SimpleNamespace(**(vars(healthy) | {"f_favail": runner.MIN_FREE_INODES - 1}))
        with mock.patch.object(os, "statvfs", side_effect=[healthy, low_temp]):
            failure = runner.preflight(repository)
        self.assertEqual((failure["path"], failure["resource"], failure["available"]),
                         (str(self.base), "inodes", runner.MIN_FREE_INODES - 1))
        with mock.patch.object(os, "statvfs", return_value=healthy):
            self.assertIsNone(runner.preflight(repository))

    def test_preflight_refuses_low_bytes_or_inodes_without_claim_or_strike(self):
        issue = {"number": 610, "title": "runner", "state": "OPEN", "body": "runner",
                 "labels": [{"name": config.LABEL_AGENT}], "assignees": [], "comments": []}
        healthy = SimpleNamespace(f_bavail=10**9, f_frsize=4096, f_favail=10**6, f_files=10**7)
        for resource, low in (("bytes", {"f_bavail": 0}), ("inodes", {"f_favail": 0})):
            with self.subTest(resource=resource):
                stats = SimpleNamespace(**(vars(healthy) | low))
                with mock.patch.object(os, "statvfs", return_value=stats), \
                        mock.patch.object(dispatch, "gh_json", return_value=issue), \
                        mock.patch.object(dispatch, "run") as commands:
                    dispatch.process_ticket(issue, 1, False, forced=True)
                self.assertFalse(any("--add-assignee" in call.args[0] for call in commands.call_args_list))
                rows = lifecycle.read_events(dispatch.EVENTS)
                infra = [row for row in rows if row.get("event") == "runner-infra"]
                self.assertEqual(infra[-1]["reason"], "runner_storage_low")
                self.assertEqual(infra[-1]["resource"], resource)
                self.assertFalse(any(row.get("event") in {"claimed", "attempt", "escalate"} for row in rows))

    def test_storage_refusals_use_host_streaks_instead_of_engine_defects(self):
        from datetime import UTC, datetime, timedelta

        from factory import watch

        cfg = config.Config(self.base, "example/project")
        issue = {"number": 610, "title": "runner", "labels": []}
        low = SimpleNamespace(f_bavail=0, f_frsize=4096, f_favail=10**6, f_files=10**7)
        for reason, observation in (
            ("runner_storage_low", {"return_value": low}),
            ("runner_storage_unavailable", {"side_effect": OSError("filesystem unavailable")}),
        ):
            with self.subTest(reason=reason), \
                    mock.patch.object(os, "statvfs", **observation), \
                    mock.patch.object(watch, "failed_units", return_value=[]), \
                    mock.patch.object(watch, "runtime_errors", return_value=[]):
                events = []
                now = datetime.now(UTC)
                for minutes in (0, 30, 60):
                    at = (now + timedelta(minutes=minutes)).isoformat()
                    with mock.patch.object(lifecycle, "_now", return_value=at):
                        self.assertIs(dispatch.process_ticket(issue, 1, False, forced=True), False)
                    rows = lifecycle.read_events(dispatch.EVENTS)
                    events.append(next(row for row in reversed(rows)
                                       if row.get("event") == "lifecycle" and row.get("kind") == "exit"))
                    self.assertEqual(events[-1]["reason"], reason)
                    if minutes < cfg.watch_host_failure_minutes:
                        self.assertEqual(watch.signals(cfg, events), [])
                signals = watch.signals(cfg, events)
                self.assertEqual([(signal["kind"], signal["key"]) for signal in signals],
                                 [("host", reason)])
                self.assertEqual(signals[0]["rows"], events)
                events.append(events[-1] | {"outcome": "completed", "reason": None})
                self.assertEqual(watch.signals(cfg, events), [])

    def tearDown(self):
        # A red crash-recovery assertion must not itself leak a temp root.
        for path in self.base.glob("factory-*"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
