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

    # _users() scan of the Linux /proc (#694, r3-2215). These call _users directly against a fake
    # /proc tree, never through reap(), whose `except OSError` would hide a raising _users.
    def users(self, root, processes, *, listing_error=None):
        """Run runner._users(root) against a fake /proc holding `processes`.

        `processes` maps a pid string to the files of that process: "stat" (bytes), "cwd" (symlink
        target) and "environ" (bytes), each optional. The fake /proc is listed in sorted order so
        the scan order is deterministic. `listing_error` makes listing /proc itself fail.
        """
        from factory import runner

        proc = Path(tempfile.mkdtemp(prefix="proc-", dir=self.base))
        for pid, files in processes.items():
            process = proc / pid
            process.mkdir()
            if "stat" in files:
                (process / "stat").write_bytes(files["stat"])
            if "cwd" in files:
                (process / "cwd").symlink_to(files["cwd"])
            if "environ" in files:
                (process / "environ").write_bytes(files["environ"])
        real_path = Path

        def path(value):
            if str(value) != "/proc":
                return real_path(value)
            if listing_error is not None:
                return SimpleNamespace(iterdir=mock.Mock(side_effect=listing_error))
            return SimpleNamespace(iterdir=lambda: iter(sorted(proc.iterdir())))

        with mock.patch.object(runner, "Path", path), mock.patch.object(runner.sys, "platform", "linux"):
            return runner._users(root)

    def scratch_root(self):
        root = (self.base / "scratch-root").resolve()
        root.mkdir(exist_ok=True)
        return root

    @staticmethod
    def proc_stat(pid, comm, state):
        return f"{pid} ({comm}) {state} 1 {pid} {pid} 0 -1 4194560 0 0 0 0".encode()

    def test_users_ignores_an_env_var_that_is_not_a_scratch_variable(self):
        root = self.scratch_root()
        elsewhere = str(self.base.resolve() / "elsewhere")
        stat = self.proc_stat("7001", "worker", "S")
        cases = {
            "unrelated variable inside root": (f"FOO={root}\0PATH=/usr/bin\0", False),
            "unrelated variable beside TMPDIR elsewhere": (f"FOO={root}\0TMPDIR={elsewhere}\0", False),
            "TMPDIR outside root": (f"TMPDIR={elsewhere}\0", False),
            "TMPDIR inside root": (f"TMPDIR={root}\0", True),
            "GH_CONFIG_DIR inside root": (f"PATH=/usr/bin\0GH_CONFIG_DIR={root}/gh\0", True),
        }
        for label, (environ, expected) in cases.items():
            with self.subTest(label):
                found = self.users(root, {"7001": {
                    "stat": stat, "cwd": elsewhere, "environ": environ.encode()}})
                self.assertIs(found, expected)

    def test_users_skips_zombie_and_dead_processes_whatever_their_name_length(self):
        root = self.scratch_root()
        cwd_in_root = str(root / "work")
        indexes = []
        for length in range(1, 9):
            comm = "c" * length
            # The state letter follows ") ", so the ")" index shifts with the name length and
            # is even or odd in its second bit across these lengths.
            indexes.append(self.proc_stat("7001", comm, "Z").rfind(b")"))
            for state, expected in (("Z", False), ("X", False), ("S", True)):
                with self.subTest(length=length, state=state):
                    found = self.users(root, {"7001": {
                        "stat": self.proc_stat("7001", comm, state), "cwd": cwd_in_root}})
                    self.assertIs(found, expected)
        self.assertEqual({bool(index & 2) for index in indexes}, {False, True})

    def test_users_is_false_not_an_error_when_proc_cannot_be_listed(self):
        root = self.scratch_root()
        for label, error in (("missing", FileNotFoundError("/proc")), ("denied", PermissionError("/proc"))):
            with self.subTest(label):
                self.assertIs(self.users(root, {}, listing_error=error), False)

    def test_users_skips_a_process_whose_stat_vanished_or_is_malformed(self):
        root = self.scratch_root()
        live = {"stat": self.proc_stat("7003", "live", "S"), "cwd": str(root / "work")}
        broken = {
            "stat vanished": {"cwd": str(root / "work")},
            "stat malformed": {"stat": b"7001 (short)", "cwd": str(root / "work")},
        }
        for label, files in broken.items():
            with self.subTest(label):
                self.assertIs(self.users(root, {"7001": files}), False)
                self.assertIs(self.users(root, {"7001": files, "7003": live}), True)

    @unittest.skipUnless(sys.platform == "linux", "Linux /proc process-name encoding")
    def test_non_utf8_process_name_preserves_only_confirmed_live_review_users(self):
        from factory import runner

        code = (
            "import ctypes, sys\n"
            "assert ctypes.CDLL(None).prctl(15, b'review\\xff) (child', 0, 0, 0) == 0\n"
            "print('ready', flush=True)\n"
            "sys.stdin.read()\n"
        )
        for uses_root in (False, True):
            with self.subTest(uses_root=uses_root):
                child = None
                try:
                    with runner.temp_root("review") as name:
                        root = Path(name)
                        child = subprocess.Popen(
                            [sys.executable, "-u", "-c", code],
                            cwd=root if uses_root else ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True,
                        )
                        self.assertEqual(child.stdout.readline().strip(), "ready")
                        self.assertIn(b"review\xff) (child", Path(f"/proc/{child.pid}/stat").read_bytes())
                    self.assertEqual(root.exists(), uses_root,
                                     "only a confirmed live scratch user may retain the review root")
                finally:
                    if child is not None:
                        child.kill()
                        child.wait(timeout=10)
                        child.stdin.close()
                        child.stdout.close()
                        child.stderr.close()
                runner.preflight(self.base)
                self.assertFalse(root.exists(), f"preflight leaked an abandoned review root: {root}")

    def test_reap_open_refuses_a_regular_file_or_symlink_swapped_in_after_the_checks(self):
        # reap() lstat()s a root, then re-opens it with O_DIRECTORY | O_NOFOLLOW. If the path
        # becomes a regular file or a symlink between those steps, the open itself must refuse
        # it before the flock, _users or removal run (#694 survivor M131 dropped O_DIRECTORY).
        from factory import runner

        regular = self.base / "factory-runner-regular-file"
        regular.write_text("not a directory")
        target = self.base / "real-directory"
        target.mkdir()
        link = self.base / "factory-runner-symlink"
        link.symlink_to(target, target_is_directory=True)
        real_open = os.open
        for path in (regular, link):
            with self.subTest(path=path.name):
                refused = []

                def recording_open(file, *args, _real=real_open, _path=path, _refused=refused, **kwargs):
                    try:
                        return _real(file, *args, **kwargs)
                    except OSError as error:
                        if Path(file) == _path:
                            _refused.append(error)
                        raise

                # Pretend the is_symlink/is_dir checks ran before the swap, so the open decides.
                with mock.patch.object(Path, "is_symlink", lambda self: False), \
                        mock.patch.object(Path, "is_dir", lambda self: True), \
                        mock.patch.object(os, "open", recording_open), \
                        mock.patch.object(runner.fcntl, "flock") as flock, \
                        mock.patch.object(runner, "_users") as users:
                    runner.reap()
                self.assertEqual(len(refused), 1, f"os.open did not refuse {path.name}")
                flock.assert_not_called()
                users.assert_not_called()
                self.assertTrue(os.path.lexists(path), f"{path.name} must be left untouched")
                self.assertTrue(target.is_dir())

    def tearDown(self):
        # A red crash-recovery assertion must not itself leak a temp root.
        for path in self.base.glob("factory-*"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
