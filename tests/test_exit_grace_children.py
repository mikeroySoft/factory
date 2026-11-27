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

    def test_disabling_exit_grace_keeps_live_child_idle_timeout(self):
        self.assertEqual(self.watch(lambda t: [4243], grace=0), ("idle_timeout", 11))

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


    def test_disabled_exit_grace_without_children_still_allows_normal_exit(self):
        self.assertEqual(self.watch(lambda t: [], grace=0, exits_at=15), (None, 15))

    def test_done_child_idle_timeout_is_in_minutes(self):
        self.assertEqual(self.watch(lambda t: [4243], idle=1), ("idle_timeout", 61))

    def test_reused_lower_pid_and_large_orphan_group_are_live_children(self):
        output = "9001 1 9001 Ss\n8000 9001 8000 S\n9002 1 9001 S\n9003 9001 9001 Z\n"
        with mock.patch.object(dispatch.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, stdout=output)):
            self.assertEqual(sorted(dispatch._live_children(9001)), [8000, 9002])

    def test_wrapped_worker_chain_is_not_child_work(self):
        output = ("9001 1 9001 Ss wrapper omp --prompt ticket\n"
                  "9002 9001 9001 S monitor omp --prompt ticket\n"
                  "9003 9002 9001 S bun /opt/bin/omp --prompt ticket\n"
                  "8000 9003 8000 S sleep 60\n9004 1 9001 S sleep 60\n")
        chain = {9001}
        with mock.patch.object(dispatch.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, stdout=output)) as ps:
            self.assertEqual(sorted(dispatch._live_children(
                9001, worker_argv=["omp", "--prompt", "ticket"], launch_chain=chain,
            )), [8000, 9004])
            # The agent can change its displayed title after launch.
            ps.return_value.stdout = output.replace("bun /opt/bin/omp --prompt ticket", "omp: waiting")
            self.assertEqual(sorted(dispatch._live_children(
                9001, worker_argv=["omp", "--prompt", "ticket"], launch_chain=chain,
            )), [8000, 9004])

    def test_wrapped_worker_timeout_and_journal_exclude_agent(self):
        wrapper = "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))"
        for busy in (False, True):
            with self.subTest(busy=busy), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                handoff = root / "handoff"
                events = root / "events.jsonl"
                agent = ("import os, pathlib, signal, subprocess\n"
                         + ("child = subprocess.Popen(['sleep', '60'])\n" if busy else "")
                         + "pathlib.Path('handoff').write_text(str(os.getpid())"
                         + (" + ':' + str(child.pid)" if busy else "") + ")\n"
                         + "signal.pause()\n")
                argv = [sys.executable, "-c", wrapper, sys.executable, "-c", agent]
                with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                        mock.patch.object(dispatch, "EVENTS", events, create=True), \
                        mock.patch.object(dispatch, "WATCH_POLL", 0.02), \
                        mock.patch.object(dispatch, "cfg", SimpleNamespace(
                            exit_grace=0.06, idle_timeout=0.006), create=True), \
                        mock.patch.object(dispatch, "_activity", side_effect=lambda *args, path=handoff:
                                          (None, "base", "", path.exists())):
                    _, fired = dispatch.run_worker(
                        argv, root, root / "log", handoff, time.monotonic() + 5, wrap=3,
                    )
                self.assertEqual(fired, "idle_timeout" if busy else "exit_grace")
                row = next(row for row in lifecycle.read_events(events)
                           if row.get("reason") == ("stuck" if busy else "hung_after_done"))
                pids = list(map(int, handoff.read_text().split(":")))
                self.assertEqual([child["pid"] for child in row["active_children"]], pids[1:])

    def test_unjournaled_timeout_still_stops_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(dispatch, "_watch", return_value="budget_min"):
                _, fired = dispatch.run_worker(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    root, root / "log", root / "handoff", journal=False,
                )
            self.assertEqual(fired, "budget_min")

    def test_unavailable_child_commands_preserve_pid_evidence(self):
        with mock.patch.object(dispatch.subprocess, "run", side_effect=OSError("ps unavailable")):
            self.assertEqual(dispatch._child_commands([9001]), [{"pid": 9001, "command": None}])

    def test_child_evidence_is_only_attached_to_decoded_terminal_event(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
            events = Path(directory) / "events.jsonl"
            execution = lifecycle.Execution(events, "worker")
            execution.exit_fields["active_children"] = [{"pid": 9001, "command": "sleep 60"}]
            execution.emit("checkpoint")
            execution.emit(bytes([101, 120, 105, 116]).decode())
            rows = lifecycle.read_events(events)
            self.assertNotIn("active_children", rows[0])
            self.assertEqual(rows[1]["active_children"], [{"pid": 9001, "command": "sleep 60"}])


if __name__ == "__main__":
    unittest.main()
