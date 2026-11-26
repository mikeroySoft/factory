"""#761: blocked child jobs hold exit_grace, but not beyond idle_timeout."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import dispatch, lifecycle


class ExitGraceChildrenTest(unittest.TestCase):
    START = (None, "base", "", None)
    DONE = (None, "commit", "", None)

    def watch(self, children, *, activity=None, exits_at=None, grace=2, idle=10 / 60,
              deadline=100):
        clock = [0.0]

        class Worker:
            pid = 4242

            def wait(self, timeout):
                clock[0] += timeout
                if exits_at is not None and clock[0] >= exits_at:
                    return 0
                raise subprocess.TimeoutExpired("worker", timeout)

        with mock.patch.object(dispatch, "WATCH_POLL", 1), \
                mock.patch.object(dispatch.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(dispatch, "_activity", side_effect=lambda *a: (activity or self.DONE)), \
                mock.patch.object(dispatch, "raise_if_interrupted"), \
                mock.patch.object(dispatch, "cfg", SimpleNamespace(exit_grace=grace, idle_timeout=idle),
                                  create=True), \
                mock.patch.object(dispatch, "_live_children", create=True,
                                  side_effect=lambda pid: children(clock[0])):
            fired = dispatch._watch(Worker(), Path("wt"), Path("log"), Path("handoff"),
                                    deadline, self.START)
        return fired, clock[0]

    def test_blocked_child_after_commit_survives_exit_grace(self):
        self.assertEqual(self.watch(lambda t: [4243], exits_at=8), (None, 8))

    def test_idle_helper_is_bounded_by_idle_timeout(self):
        self.assertEqual(self.watch(lambda t: [4243]), ("idle_timeout", 11))

    def test_last_child_exit_restarts_full_exit_grace(self):
        self.assertEqual(self.watch(lambda t: [4243] if t < 7 else []), ("exit_grace", 9))

    def test_child_ending_inside_grace_restarts_it(self):
        self.assertEqual(self.watch(lambda t: [4243] if t < 2 else []), ("exit_grace", 4))

    def test_child_churn_does_not_reset_idle_deadline(self):
        self.assertEqual(self.watch(lambda t: [4243 + int(t)]), ("idle_timeout", 11))

    def test_handoff_and_commit_without_children_keep_exit_grace(self):
        for done in (self.DONE, (None, "base", "", (4, 1))):
            with self.subTest(done=done):
                self.assertEqual(self.watch(lambda t: [], activity=done), ("exit_grace", 3))

    def test_budget_still_bounds_blocked_children(self):
        self.assertEqual(self.watch(lambda t: [4243], deadline=5), ("budget_min", 5))

    def test_disabled_idle_timeout_holds_until_budget(self):
        self.assertEqual(self.watch(lambda t: [4243], idle=0, deadline=15), ("budget_min", 15))

    def test_disabling_exit_grace_preserves_done_worker_behavior(self):
        self.assertEqual(self.watch(lambda t: [4243], grace=0, exits_at=15), (None, 15))

    def test_unfinished_worker_still_uses_idle_timeout(self):
        self.assertEqual(self.watch(lambda t: [4243], activity=self.START), ("idle_timeout", 10))

    def test_live_children_parses_portable_ps(self):
        output = ("1 0 1 Ss\n100 1 100 Ss\n101 100 100 S\n102 101 102 S\n"
                  "103 1 100 S\n104 100 100 Z+\n105 104 105 S\n200 1 200 S\ngarbage\n")
        with mock.patch.object(dispatch.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, stdout=output)):
            self.assertEqual(sorted(dispatch._live_children(100)), [101, 102, 103, 105])
        for failure in (OSError("ps unavailable"), subprocess.TimeoutExpired("ps", 5)):
            with self.subTest(failure=failure), \
                    mock.patch.object(dispatch.subprocess, "run", side_effect=failure):
                self.assertEqual(dispatch._live_children(100), [])
        with mock.patch.object(dispatch.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 1, stdout="")):
            self.assertEqual(dispatch._live_children(100), [])

    def test_real_sleep_child_holds_watch_then_exit_restarts_grace(self):
        script = ("import subprocess, sys\n"
                  "child = subprocess.Popen(['sleep', '60'])\n"
                  "print(child.pid, flush=True)\n"
                  "sys.stdin.read()\n"
                  "child.terminate()\nchild.wait()\n")
        with subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, start_new_session=True) as worker:
            try:
                child = int(worker.stdout.readline())
                self.assertIn(child, dispatch._live_children(worker.pid))
                original = dispatch._live_children

                def children(t):
                    if t == 7:
                        worker.stdin.close()
                        worker.wait(timeout=5)
                    return original(worker.pid)

                self.assertEqual(self.watch(children), ("exit_grace", 9))
            finally:
                if worker.poll() is None:
                    worker.stdin.close()
                    worker.wait(timeout=5)

    def test_hung_lifecycle_row_records_children_before_termination(self):
        script = ("import pathlib, signal, subprocess, sys\n"
                  "child = subprocess.Popen(['sleep', '60'])\n"
                  "def stop(*args):\n"
                  "    child.wait()\n    sys.exit(0)\n"
                  "signal.signal(signal.SIGTERM, stop)\n"
                  "pathlib.Path('handoff').write_text(str(child.pid))\n"
                  "signal.pause()\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handoff = root / "handoff"
            events = root / "events.jsonl"

            def misclassified(proc, *args):
                deadline = time.monotonic() + 5
                while not handoff.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(handoff.exists())
                return "exit_grace"

            with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                    mock.patch.object(dispatch, "EVENTS", events, create=True), \
                    mock.patch.object(dispatch, "_watch", side_effect=misclassified):
                _, fired = dispatch.run_worker([sys.executable, "-c", script], root,
                                               root / "log", handoff)
            self.assertEqual(fired, "exit_grace")
            row = next(row for row in lifecycle.read_events(events)
                       if row.get("reason") == "hung_after_done")
            children = row["active_children"]
            self.assertEqual([child["pid"] for child in children], [int(handoff.read_text())])
            self.assertIn("sleep", children[0]["command"])
            self.assertIn("60", children[0]["command"])


if __name__ == "__main__":
    unittest.main()
