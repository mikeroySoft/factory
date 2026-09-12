"""Real gate contention and interruption, confined to disposable repositories/locks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from factory import lifecycle


CHECK = '''import os, sys, time
from pathlib import Path
control = Path(os.environ["GATE_CONTROL"])
step = sys.argv[1]
ready = control / (step + ".ready")
ready.with_suffix(".tmp").write_text(str(os.getpid()))
ready.with_suffix(".tmp").replace(ready)
deadline = time.monotonic() + 30
while not (control / (step + ".go")).exists():
    if time.monotonic() >= deadline:
        raise SystemExit("disposable check control timed out")
    time.sleep(0.01)
(control / (step + ".done")).touch()
print(step + " output")
raise SystemExit(int(os.environ.get("GATE_FAILURE", "0")) if step == "exclusive" else 0)
'''

HOLDER = '''import fcntl, select, sys
from pathlib import Path
with open(sys.argv[1], "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    Path(sys.argv[2]).touch()
    if not select.select([sys.stdin], [], [], 30)[0]:
        raise SystemExit("disposable holder control timed out")
    sys.stdin.readline()
    fcntl.flock(lock, fcntl.LOCK_UN)
'''


class GateResourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop(lifecycle.CONTEXT_ENV, None)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.lock = self.base / "exclusive.lock"
        self.events = self.repo / ".factory" / "events.jsonl"
        self.check = self.base / "check.py"
        self.check.write_text(CHECK)
        self.env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                    "XDG_CONFIG_HOME": str(self.base / "config"),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True, timeout=10)
        checks = "".join(
            '[[gate.check]]\nname = ' + json.dumps(name) + '\nrun = '
            + json.dumps([sys.executable, str(self.check), name])
            + '\nexclusive = ' + str(name == "exclusive").lower() + '\n'
            for name in ("exclusive", "later")
        )
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "local/gate-resources"\n[leak_scan]\npattern = ""\n'
            '[gate]\ntimeout = 30\nlock = ' + json.dumps(str(self.lock)) + '\n' + checks
        )

    def rows(self) -> list[dict]:
        return lifecycle.read_events(self.events)

    def until(self, predicate, message: str):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail(message)

    def stop(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        # gate.timed starts each check in its own session; do not leave it behind.
        children = [row["child_process"] for row in self.rows()
                    if row.get("kind") == "child_start" and row["process"]["pid"] == proc.pid]
        for child in children:
            if lifecycle._process_state(child) == "alive":
                try:
                    os.killpg(child["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if proc.stdout is not None and not proc.stdout.closed:
            proc.communicate(timeout=10)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def gate(self, label: str, failure: int = 0):
        control = self.base / label
        control.mkdir()
        proc = subprocess.Popen(
            [sys.executable, "-m", "factory", "gate", "--skip", "conflict-markers",
             "--report", str(control / "report.md")], cwd=self.repo,
            env={**self.env, "GATE_CONTROL": str(control), "GATE_FAILURE": str(failure)},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.addCleanup(self.stop, proc)
        return proc, control

    def external_holder(self):
        ready = self.base / "external.ready"
        ready.unlink(missing_ok=True)
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.lock), str(ready)],
                                env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop, proc)
        self.until(ready.exists, "uninstrumented process did not acquire disposable lock")
        return proc

    def event(self, proc: subprocess.Popen, kind: str):
        return self.until(lambda: next((row for row in self.rows()
                          if row.get("stage") == "gate" and row.get("kind") == kind
                          and row["process"]["pid"] == proc.pid), None),
                          f"gate {proc.pid} did not emit {kind}")

    def resource(self):
        observations = lifecycle.resources(self.events, paths=[(self.lock, "host")])
        self.assertEqual(len(observations), 1)
        return observations[0]

    def finish(self, proc: subprocess.Popen, control: Path, expected: int = 0) -> None:
        for step in ("exclusive", "later"):
            (control / (step + ".go")).touch()
        out, _ = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, expected, out)
        report = (control / "report.md").read_text()
        self.assertIn("- exclusive: " + ("FAIL" if expected else "PASS"), report)
        self.assertIn("- later: PASS", report)
        if expected:
            self.assertIn("exclusive output", report)
        terminal = self.event(proc, "exit")
        self.assertEqual(terminal["outcome"], "product_feedback" if expected else "completed")

    def test_requests_are_not_owners_and_lock_outlives_exclusive_check(self) -> None:
        first, a = self.gate("first")
        self.until((a / "exclusive.ready").exists, "first exclusive check did not start")
        acquired = self.event(first, "lock_acquired")
        held = self.resource()
        self.assertEqual((held["state"], held["ownership"]), ("held", "confirmed"))
        self.assertEqual(held["owner"]["execution_id"], acquired["execution_id"])

        second, b = self.gate("second", failure=3)
        requested = self.event(second, "resource_requested")
        waiting = self.event(second, "wait")
        self.assertEqual((waiting["wait"]["reason"], waiting["wait"]["mode"]),
                         ("exclusive_resource", "blocking"))
        self.assertEqual(requested["resource"]["id"], acquired["resource"]["id"])
        self.assertEqual(waiting["wait"]["resource"]["id"], acquired["resource"]["id"])
        held = self.resource()
        self.assertEqual(held["owner"]["execution_id"], acquired["execution_id"])
        self.assertEqual([row["execution_id"] for row in held["requests"]], [requested["execution_id"]])
        self.assertTrue(held["requests"][0]["blocking"])
        state = next(row for row in lifecycle.observe(self.events)
                     if row["execution_id"] == requested["execution_id"])
        self.assertEqual(state["state"], "active")
        self.assertEqual(state["wait"]["reason"], "exclusive_resource")
        self.assertFalse((b / "exclusive.ready").exists())

        (a / "exclusive.go").touch()
        self.until((a / "later.ready").exists, "later ordinary check did not start")
        self.assertFalse((b / "exclusive.ready").exists())
        self.assertEqual(self.resource()["owner"]["execution_id"], acquired["execution_id"])
        self.finish(first, a)
        self.until((b / "exclusive.ready").exists, "second gate did not acquire after unlock")
        next_acquired = self.event(second, "lock_acquired")
        released = self.event(first, "lock_released")
        self.assertEqual(released["acquisition_id"], acquired["acquisition_id"])
        self.assertNotEqual(next_acquired["acquisition_id"], acquired["acquisition_id"])
        rows = self.rows()
        self.assertLess(rows.index(requested), rows.index(released))
        later_exit = next(row for row in rows if row.get("stage") == "gate-check"
                          and row["kind"] == "exit" and row["process"]["pid"] == first.pid
                          and any(event.get("check") == "later" and event["execution_id"] == row["execution_id"]
                                  for event in rows))
        self.assertLess(rows.index(later_exit), rows.index(next_acquired))
        ended_wait = self.event(second, "wait_end")
        self.assertGreater(ended_wait["sequence"], next_acquired["sequence"])
        for _ in range(2):
            held = self.resource()
            self.assertEqual(held["resource"]["id"], acquired["resource"]["id"])
            self.assertEqual(held["owner"]["execution_id"], next_acquired["execution_id"])
            self.assertEqual(held["requests"], [])
        self.finish(second, b, expected=1)
        free = self.resource()
        self.assertEqual((free["state"], free["ownership"], free["owner"]), ("free", "none", None))

    def test_uninstrumented_holder_and_killed_requester_remain_distinct(self) -> None:
        holder = self.external_holder()
        held = self.resource()
        self.assertEqual((held["state"], held["ownership"], held["owner"]), ("held", "unknown", None))
        requester, control = self.gate("requester")
        wait = self.event(requester, "wait")
        held = self.resource()
        self.assertEqual(held["resource"]["id"], wait["wait"]["resource"]["id"])
        self.assertEqual(held["ownership"], "unknown")
        self.assertEqual(held["requests"][0]["execution_id"], wait["execution_id"])
        requester.kill()
        requester.wait(timeout=10)
        states = lifecycle.observe(self.events)
        stopped = next(row for row in states if row["execution_id"] == wait["execution_id"])
        self.assertEqual(stopped["state"], "interrupted")
        self.assertIsNone(stopped["wait"])
        self.assertFalse((control / "exclusive.ready").exists())
        held = self.resource()
        self.assertEqual((held["state"], held["ownership"], held["owner"]), ("held", "unknown", None))
        self.assertEqual(held["requests"], [])
        before = self.rows()
        lifecycle.observe(self.events)
        self.resource()
        self.assertEqual(self.rows(), before)
        self.assertFalse(any(row.get("execution_id") == wait["execution_id"]
                             and row.get("kind") in {"lock_acquired", "lock_released"} for row in before))
        interrupted, _ = self.gate("interrupted-requester")
        interrupted_wait = self.event(interrupted, "wait")
        interrupted.send_signal(signal.SIGINT)
        interrupted.communicate(timeout=10)
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertFalse(any(row.get("execution_id") == interrupted_wait["execution_id"]
                             and row.get("kind") in {"lock_acquired", "lock_released"} for row in self.rows()))
        self.assertEqual(self.resource()["ownership"], "unknown")
        self.assertEqual(self.resource()["requests"], [])
        holder.stdin.write("release\n")
        holder.stdin.flush()
        self.assertEqual(holder.wait(timeout=10), 0)
        self.assertEqual(self.resource()["state"], "free")

    def test_dead_holder_is_not_carried_into_external_or_new_gate_ownership(self) -> None:
        first, a = self.gate("killed-holder")
        self.until((a / "exclusive.ready").exists, "holding gate check did not start")
        acquired = self.event(first, "lock_acquired")
        self.assertEqual(self.resource()["owner"]["execution_id"], acquired["execution_id"])
        self.stop(first)
        state = self.until(lambda: next((row for row in lifecycle.observe(self.events)
                           if row["execution_id"] == acquired["execution_id"] and row["state"] != "active"), None),
                           "dead gate and check remained stage-active")
        self.assertEqual(state["state"], "unknown")  # F01 cannot prove absence of scrubbed descendants.
        free = self.resource()
        self.assertEqual((free["state"], free["ownership"], free["owner"]), ("free", "none", None))
        holder = self.external_holder()
        held = self.resource()
        self.assertEqual((held["state"], held["ownership"], held["owner"]), ("held", "unknown", None))
        self.assertEqual(held["resource"]["id"], acquired["resource"]["id"])
        holder.stdin.write("release\n")
        holder.stdin.flush()
        self.assertEqual(holder.wait(timeout=10), 0)
        second, b = self.gate("replacement")
        self.until((b / "exclusive.ready").exists, "replacement gate did not acquire")
        replacement = self.event(second, "lock_acquired")
        for _ in range(2):
            held = self.resource()
            self.assertEqual(held["owner"]["execution_id"], replacement["execution_id"])
            self.assertEqual(held["resource"]["id"], acquired["resource"]["id"])
        self.assertFalse(any(row.get("execution_id") == acquired["execution_id"]
                             and row.get("kind") == "lock_released" for row in self.rows()))
        self.finish(second, b)


if __name__ == "__main__":
    unittest.main()
