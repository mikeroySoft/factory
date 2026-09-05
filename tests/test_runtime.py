"""Public runtime CLI: local evidence survives without writes or network access."""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from factory import lifecycle

ROOT = Path(__file__).resolve().parents[1]
GUARD = '''
import json, os, sys
from factory.cli import main
commands = []
attempts = []
mutations = []
def audit(event, args):
    if (event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.truncate",
                  "os.chmod", "os.chown", "fcntl.flock"}
        or event == "open" and args[0] != os.devnull
        and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
        mutations.append(event)
        raise AssertionError("mutation forbidden")
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.sendto",
                 "socket.gethostbyname", "socket.gethostbyaddr", "os.system", "os.posix_spawn"}:
        attempts.append(event)
        raise AssertionError("network forbidden")
    if event == "subprocess.Popen":
        argv = args[1]
        commands.append(argv)
        tool = os.path.basename(argv[0])
        if not (tool == "systemctl" or tool == "git" and
                ("rev-parse" in argv or "get-url" in argv)):
            attempts.append(argv)
            raise AssertionError("external command forbidden")
sys.addaudithook(audit)
if os.environ.get("RUNTIME_SHORT_READ"):
    real_read = os.read
    os.read = lambda fd, count: real_read(fd, min(count, 31))
try:
    result = main(["dashboard", "--runtime-json"])
finally:
    print("AUDIT:" + json.dumps({"commands": commands, "attempts": attempts, "mutations": mutations}), file=sys.stderr)
raise SystemExit(result)
'''


class RuntimeCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".factory.toml").write_text('[repo]\nslug = "example/runtime"\n[gate]\nlock = "' + str(self.root / "gpu.lock") + '"\n')
        self.journal = self.root / ".factory/events.jsonl"
        self.env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
                    "XDG_CONFIG_HOME": str(self.root / "host"), "GH_TOKEN": "", "GITHUB_TOKEN": "",
                    lifecycle.CONTEXT_ENV: ""}

    def state(self):
        return {str(p.relative_to(self.root)): (
                    p.lstat().st_ino, p.lstat().st_mtime_ns, p.lstat().st_mode,
                    p.read_bytes() if p.is_file() else None)
                for p in self.root.rglob("*") if ".git" not in p.parts}

    def invoke(self):
        before = self.state()
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-c", GUARD], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=10)
        self.last_elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 0, proc.stderr)
        audit = json.loads(proc.stderr.split("AUDIT:", 1)[1])
        self.last_audit = audit
        self.assertEqual(audit["attempts"], [])
        self.assertEqual(audit["mutations"], [])
        self.assertLessEqual(len(audit["commands"]), 5)
        self.assertEqual(before, self.state())
        return json.loads(proc.stdout)

    def test_missing_history_is_not_created_or_reported_empty(self):
        data = self.invoke()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["repo"], "example/runtime")
        self.assertEqual(data["executions"], [])
        self.assertEqual(data["events"], [])
        self.assertEqual(data["history"]["status"], "missing")
        self.assertIsNone(data["history"]["start_at"])
        self.assertFalse(data["history"]["complete"])
        self.assertFalse(self.journal.parent.exists())



    def test_concurrency_wait_retry_ownership_and_source_freshness(self):
        self.env["RUNTIME_SHORT_READ"] = "1"
        self.journal.parent.mkdir()
        (self.journal.parent / "locks").mkdir()
        with open(self.root / "gpu.lock", "w") as held, \
             open(self.root / "external.lock", "w") as external, \
             open(self.journal.parent / "locks/7.lock", "w") as admission:
            fcntl.flock(held, fcntl.LOCK_EX)
            fcntl.flock(external, fcntl.LOCK_EX)
            fcntl.flock(admission, fcntl.LOCK_EX)
            worker = lifecycle.Execution(self.journal, "worker", ticket=7, attempt=4, review_round=2)
            gate = lifecycle.Execution(self.journal, "gate", ticket=8, attempt=1)
            entered = worker.emit("enter")
            gate.emit("enter")
            acquisition = gate.resource("acquired", self.root / "gpu.lock", scope="host")
            worker.resource("requested", self.root / "external.lock", scope="host", blocking=True)
            waiting = worker.wait("exclusive_resource", resource=lifecycle._resource(
                self.journal, self.root / "external.lock", "host"))
            review = lifecycle.Execution(self.journal, "review", ticket=7, attempt=1, review_round=1)
            review.emit("enter")
            review.outcome = "product_feedback"
            review.reason = "REVISE"
            terminal = review.emit("exit")
            interrupted = lifecycle.Execution(self.journal, "triage")
            interrupted.process = {**interrupted.process, "boot_id": "previous-boot"}
            interrupted.emit("enter")
            first = self.invoke()
            second = self.invoke()
            fcntl.flock(held, fcntl.LOCK_UN)
            release = gate.resource("released", self.root / "gpu.lock", scope="host")
            third = self.invoke()
        executions = {row["execution_id"]: row for row in first["executions"]}
        self.assertEqual(executions[worker.execution_id]["state"], "active")
        self.assertEqual(executions[gate.execution_id]["state"], "active")
        self.assertEqual(executions[worker.execution_id]["entered_at"], entered["at"])
        self.assertEqual(executions[worker.execution_id]["review_round"], 2)
        self.assertEqual(executions[worker.execution_id]["attempt"], 4)
        self.assertEqual(executions[worker.execution_id]["wait"]["event_id"], waiting["event_id"])
        self.assertEqual(executions[review.execution_id]["ended_at"], terminal["at"])
        self.assertEqual(executions[review.execution_id]["state"], "completed")
        self.assertEqual(executions[interrupted.execution_id]["state"], "interrupted")
        self.assertIsNone(executions[interrupted.execution_id]["ended_at"])
        resources = {row["resource"]["lock"]["path"]: row for row in first["resources"]}
        self.assertEqual(resources[str(self.root / "gpu.lock")]["ownership"], "confirmed")
        self.assertEqual(resources[str(self.root / "gpu.lock")]["owner"]["acquisition_id"],
                         acquisition["acquisition_id"])
        self.assertEqual(resources[str(self.root / "external.lock")]["ownership"], "unknown")
        self.assertIsNone(resources[str(self.root / "external.lock")]["owner"])
        self.assertEqual(first["dispatcher"]["capacity"],
                         {"configured": 2, "active": 1, "complete": True})
        self.assertEqual(first["events"], second["events"])
        self.assertGreater(second["generated_at"], first["generated_at"])
        self.assertEqual([(r["event_id"], r["at"]) for r in first["resources"]],
                         [(r["event_id"], r["at"]) for r in second["resources"]])
        released = next(row for row in third["resources"]
                        if row["resource"]["lock"]["path"] == str(self.root / "gpu.lock"))
        self.assertEqual(released["ownership"], "none")
        self.assertIsNone(released["owner"])
        self.assertEqual(release["acquisition_id"], acquisition["acquisition_id"])
        self.assertIn(release["event_id"], [row["event_id"] for row in third["events"]])

    def test_corruption_duplicates_and_uncommitted_tail_preserve_valid_terminal(self):
        done = lifecycle.Execution(self.journal, "review", ticket=3, review_round=1)
        entry = done.emit("enter")
        done.reason = "SENSITIVE_TOKEN_DO_NOT_PROJECT"
        terminal = done.emit("exit")
        with self.journal.open("ab") as handle:
            handle.write(json.dumps(terminal).encode() + b"\n")
            handle.write(b"{broken\n")
            handle.write(json.dumps({**entry, "schema_version": 99}).encode() + b"\n")
            handle.write(json.dumps({"event": "claimed", "ticket": 3}).encode() + b"\n")
            handle.write(json.dumps({**terminal, "event_id": "uncommitted"}).encode())
        data = self.invoke()
        self.assertNotIn("SENSITIVE_TOKEN_DO_NOT_PROJECT", json.dumps(data))
        self.assertEqual([row["event_id"] for row in data["events"]],
                         [entry["event_id"], terminal["event_id"]])
        self.assertFalse(data["history"]["complete"])
        self.assertTrue(data["history"]["gaps"])
        self.assertGreaterEqual(len({row["code"] for row in data["errors"]}), 3)
        self.assertEqual(data["executions"][0]["ended_at"], terminal["at"])
        self.assertEqual(data["history"]["start_at"], entry["at"])
        self.assertEqual(data["history"]["end_at"], terminal["at"])

    def test_empty_and_mid_execution_window_do_not_invent_entry(self):
        self.journal.parent.mkdir()
        self.journal.touch()
        empty = self.invoke()
        self.assertEqual(empty["history"]["status"], "empty")
        self.assertTrue(empty["history"]["complete"])
        self.assertIsNone(empty["history"]["start_at"])
        execution = lifecycle.Execution(self.journal, "worker", ticket=9, attempt=2)
        execution.emit("enter")
        event = execution.wait("capacity_reached", mode="admission", active=2, max_active=2)
        self.journal.write_text(json.dumps(event) + "\n")
        data = self.invoke()
        self.assertEqual(data["executions"][0]["execution_id"], execution.execution_id)
        self.assertIsNone(data["executions"][0]["entered_at"])
        self.assertEqual(data["executions"][0]["observation"], "partial")
        self.assertFalse(data["history"]["complete"])

    def test_large_history_clips_beginning_and_preserves_mid_execution_identity(self):
        execution = lifecycle.Execution(self.journal, "worker", ticket=11)
        execution.emit("enter")
        waiting = execution.wait("capacity_reached", mode="admission", active=2, max_active=2)
        row = json.dumps(waiting).encode() + b"\n"
        with self.journal.open("wb") as handle:
            handle.write(b" " * (2 * 1024 * 1024) + b"\n")
            handle.write(row)
        data = self.invoke()
        self.assertLessEqual(data["history"]["bytes_read"], 1024 * 1024 + 1)
        self.assertTrue(data["history"]["truncated"])
        self.assertFalse(data["history"]["complete"])
        self.assertIsNone(data["executions"][0]["entered_at"])
        self.assertEqual(data["events"][0]["event_id"], waiting["event_id"])

    def test_hung_service_queries_are_bounded_and_local_facts_survive(self):
        import shutil
        import time

        tools = self.root / "bin"
        tools.mkdir()
        (tools / "git").symlink_to(shutil.which("git"))
        command = tools / "systemctl"
        command.write_text("#!" + sys.executable + "\nimport time\ntime.sleep(30)\n")
        command.chmod(0o755)
        self.env["PATH"] = str(tools)
        execution = lifecycle.Execution(self.journal, "worker", ticket=12)
        execution.emit("enter")
        started = time.monotonic()
        data = self.invoke()
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsNone(data["dispatcher"]["service_active"])
        self.assertIsNone(data["dispatcher"]["timer_active"])
        self.assertIsNone(data["dispatcher"]["next_at"])
        self.assertEqual(data["executions"][0]["state"], "active")
        self.assertTrue(any("timeout" in error["code"] for error in data["errors"]))

    def test_missing_service_tool_preserves_local_execution(self):
        import shutil

        tools = self.root / "bin"
        tools.mkdir()
        (tools / "git").symlink_to(shutil.which("git"))
        self.env["PATH"] = str(tools)
        execution = lifecycle.Execution(self.journal, "triage")
        execution.emit("enter")
        data = self.invoke()
        self.assertIsNone(data["dispatcher"]["service_active"])
        self.assertIsNone(data["dispatcher"]["paused"])
        self.assertEqual(data["executions"][0]["state"], "active")
        self.assertTrue(data["errors"])

    def test_unreadable_storage_and_unsupported_records_have_unknown_intervals(self):
        self.journal.mkdir(parents=True)
        data = self.invoke()
        self.assertEqual(data["history"]["status"], "unreadable")
        self.assertIsNone(data["history"]["start_at"])
        self.assertIn("not_regular", data["history"]["gaps"])
        self.journal.rmdir()
        self.journal.write_text('{"event":"lifecycle","schema_version":42}\n'
                                '{"event":"claimed"}\nnull\n{"x":NaN}\n')
        data = self.invoke()
        self.assertFalse(data["history"]["complete"])
        self.assertIsNone(data["history"]["start_at"])
        self.assertIsNone(data["history"]["end_at"])
        self.assertEqual(data["events"], [])
        self.assertIn("unsupported_version", data["history"]["gaps"])
        self.assertIn("unsupported_record", data["history"]["gaps"])

    def test_conflicting_duplicate_cannot_confirm_activity(self):
        execution = lifecycle.Execution(self.journal, "worker", ticket=13)
        row = execution.emit("enter")
        with self.journal.open("a") as handle:
            handle.write(json.dumps({**row, "stage": "gate"}) + "\n")
        data = self.invoke()
        self.assertEqual([row["event_id"] for row in data["events"]], [row["event_id"]])
        self.assertEqual(data["executions"][0]["state"], "unknown")
        self.assertIn("duplicate_conflict", data["history"]["gaps"])

    def test_accepted_check_and_admission_outcomes_remain_supported(self):
        execution = lifecycle.Execution(self.journal, "gate-check", ticket=14)
        execution.emit("enter")
        check = execution.emit("check", check="compile")
        result = execution.emit("result", check="compile", passed=False)
        execution.outcome = "product_feedback"
        execution.emit("exit")
        admission = lifecycle.Execution(self.journal, "ticket", ticket=14)
        admission.emit("enter")
        admission.outcome = "not_admitted"
        admission.reason = "state_changed"
        admission.emit("exit")
        data = self.invoke()
        events = {row["event_id"]: row for row in data["events"]}
        self.assertEqual(events[check["event_id"]]["check"], "compile")
        self.assertFalse(events[result["event_id"]]["passed"])
        self.assertEqual([row["outcome"] for row in data["executions"]],
                         ["product_feedback", "not_admitted"])
        self.assertTrue(data["history"]["complete"])

    def test_runtime_rejects_conflicting_cli_modes(self):
        for options in (["--json"], ["--port", "8765"], ["--host=localhost"], ["--no-open"]):
            proc = subprocess.run([sys.executable, "-m", "factory", "dashboard", "--runtime-json", *options],
                                  cwd=self.root, env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
