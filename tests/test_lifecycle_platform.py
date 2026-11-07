"""Portable identity regressions; real-host cases also run in macOS discovery."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from factory import lifecycle, runtime_events, platform_identity

BOOT = "11111111-2222-3333-4444-555555555555"
MACHINE = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
HOST = str(uuid.uuid5(uuid.NAMESPACE_URL, "factory:host:" + MACHINE))
START = "Wed Nov  4 17:15:00 2026"


def no_linux_files(original):
    def read(path, *args, **kwargs):
        if str(path).startswith("/proc/") or str(path) == "/etc/machine-id":
            raise FileNotFoundError(str(path))
        return original(path, *args, **kwargs)
    return read


class DarwinLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.processes = {os.getpid(): START, 424242: START}
        for context in (
            patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}),
            patch.object(sys, "platform", "darwin"),
            patch.object(Path, "read_text", no_linux_files(Path.read_text)),
            patch("subprocess.run", side_effect=self.probe),
        ):
            context.start()
            self.addCleanup(context.stop)

    def probe(self, args, **kwargs):
        if args[0].endswith("ioreg"):
            return subprocess.CompletedProcess(args, 0, f'"IOPlatformUUID" = "{MACHINE}"\n', "")
        if args[0].endswith("sysctl"):
            return subprocess.CompletedProcess(args, 0, BOOT + "\n", "")
        if args[0].endswith("ps"):
            start = self.processes.get(int(args[-1]))
            return subprocess.CompletedProcess(args, 0 if start else 1,
                                               f"{start} 1 {os.getuid()} S\n" if start else "", "")
        if args[0].endswith("lsof"):
            return subprocess.CompletedProcess(args, 0, f"p{os.getpid()}\nf3\nlW\n", "")
        raise AssertionError(args)

    def recorded(self):
        execution = lifecycle.Execution(self.path, "worker")
        execution.process = lifecycle._identity(424242)
        execution.emit("enter")
        return execution.process

    def test_dead_pid_reconciles_to_interrupted(self):
        identity = self.recorded()
        del self.processes[424242]
        first = lifecycle.observe(self.path)[0]
        self.assertEqual(first["state"], "interrupted")
        self.assertEqual(lifecycle._process_state(identity), "dead")
        self.assertTrue(first["events"][-1]["reconciled"])
        self.assertEqual(lifecycle.observe(self.path)[0]["events"], first["events"])

    def test_live_process_is_alive_in_both_readers(self):
        identity = self.recorded()
        self.assertEqual(lifecycle._process_state(identity), "alive")
        observation = runtime_events._Observation([])
        self.assertEqual(observation.process_state(identity), "alive")
        self.assertEqual(observation.host, HOST)
        self.assertEqual(observation.errors, [])

    def test_reused_pid_is_not_original_process(self):
        identity = self.recorded()
        self.processes[424242] = "Wed Nov  4 17:16:00 2026"
        self.assertEqual(lifecycle._process_state(identity), "dead")
        self.assertEqual(runtime_events._Observation([]).process_state(identity), "dead")
        self.assertEqual(lifecycle.observe(self.path)[0]["state"], "interrupted")
 

    def test_reboot_proves_interruption(self):
        identity = self.recorded()
        identity["boot_id"] = "previous-boot"
        self.assertEqual(lifecycle._process_state(identity), "dead")

    def test_probe_failure_does_not_prove_death(self):
        identity = self.recorded()
        with patch("subprocess.run", side_effect=PermissionError):
            self.assertEqual(lifecycle._process_state(identity), "unknown")
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "denied")):
            self.assertEqual(lifecycle._process_state(identity), "unknown")

    def test_hidden_processes_do_not_persist_interruption(self):
        identity = self.recorded()
        before = self.path.read_bytes()
        self.processes.clear()
        observed = lifecycle.observe(self.path)[0]
        self.assertEqual(observed["state"], "unknown")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(lifecycle._process_state(identity), "unknown")
        self.assertEqual(runtime_events._Observation([]).process_state(identity), "unknown")
        self.processes[os.getpid()] = START
        self.assertEqual(lifecycle.observe(self.path)[0]["state"], "interrupted")

    def test_lock_evidence_uses_locked_descriptor_not_every_open_file(self):
        path = Path(self.tmp.name) / "held.lock"
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            lock = lifecycle._lock(path)
            self.assertEqual(lifecycle._lock_holders(lock), [os.getpid()])
            observation = runtime_events._Observation([])
            self.assertEqual(observation.lock_state(lock), ("held", [os.getpid()]))
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "p123\nf4\nl \n", "")):
                self.assertIsNone(lifecycle._lock_holders(lock))
                self.assertEqual(lifecycle._lock_state(lock), "held")
                self.assertEqual(runtime_events._Observation([]).lock_state(lock), ("unknown", None))
        self.assertEqual(lifecycle._lock_state(lock), "free")
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(runtime_events._Observation([]).lock_state(lock), ("unknown", None))

    def test_passive_projection_never_acquires_flocks(self):
        path = Path(self.tmp.name) / "gpu.lock"
        path.touch()
        execution = lifecycle.Execution(self.path, "gate")
        execution.emit("enter")
        execution.resource("acquired", path, scope="host")
        for output, status, state in (
            (f"p{os.getpid()}\nf3\nlW\n", 0, "held"),
            (f"p{os.getpid()}\nf3\nl \n", 0, "unknown"),
            ("", 1, "unknown"),
        ):
            result = subprocess.CompletedProcess([], status, output, "")
            def probe(args, result=result, **kwargs):
                return result if args[0].endswith("lsof") else self.probe(args, **kwargs)

            with self.subTest(state=state, status=status), \
                    patch("subprocess.run", side_effect=probe), \
                    patch("fcntl.flock", side_effect=AssertionError("passive reader acquired flock")):
                projected = runtime_events.project(self.path, [(path, "host")])
                self.assertEqual(projected["resources"][0]["state"], state)

    def test_resource_owner_correlates_with_passive_reader(self):
        path = Path(self.tmp.name) / "gpu.lock"
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            execution = lifecycle.Execution(self.path, "gate")
            execution.emit("enter")
            execution.resource("acquired", path, scope="host")
            resource = lifecycle.resources(self.path, [(path, "host")])[0]
            self.assertEqual(resource["ownership"], "confirmed")
            self.assertEqual(resource["owner"]["execution_id"], execution.execution_id)
            self.assertEqual(resource["evidence"]["attribution"], "lsof")
            projected = runtime_events.project(self.path, [(path, "host")])
            self.assertEqual(projected["resources"][0]["owner"], resource["owner"])
            self.assertEqual(projected["resources"][0]["event_id"], resource["event_id"])
            self.assertEqual(projected["errors"], [])

    def test_unknown_probes_and_incomplete_identity_remain_unknown(self):
        identity = self.recorded()
        self.assertEqual(lifecycle._process_state({**identity, "start_ticks": None}), "unknown")
        for result in (subprocess.CompletedProcess([], 0, "malformed", ""),
                       subprocess.CompletedProcess([], 1, "", "permission denied")):
            with self.subTest(result=result), patch("subprocess.run", return_value=result):
                self.assertEqual(lifecycle._process_state(identity), "unknown")

    def test_lifecycle_lock_stays_held_but_passive_state_is_unknown_without_attribution(self):
        path = Path(self.tmp.name) / "gpu.lock"
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            observation = runtime_events._Observation([])
            with patch("subprocess.run", side_effect=FileNotFoundError):
                lock = lifecycle._lock(path)
                self.assertEqual(lifecycle._lock_state(lock), "held")
                self.assertEqual(observation.lock_state(lock), ("unknown", None))


class HostIdentityTest(unittest.TestCase):
    def test_darwin_host_id_stable_across_processes(self):
        code = '''
import subprocess, sys
from pathlib import Path
from unittest.mock import patch
from factory import lifecycle
original = Path.read_text
def read(path, *args, **kwargs):
    if str(path).startswith('/proc/') or str(path) == '/etc/machine-id':
        raise FileNotFoundError(str(path))
    return original(path, *args, **kwargs)
def probe(args, **kwargs):
    return subprocess.CompletedProcess(args, 0, '"IOPlatformUUID" = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"\\n', '')
with patch.object(sys, 'platform', 'darwin'), patch.object(Path, 'read_text', read), patch('subprocess.run', side_effect=probe):
    print(lifecycle._host_id())
'''
        hosts = [subprocess.check_output([sys.executable, "-c", code], text=True).strip() for _ in range(2)]
        self.assertEqual(hosts, [HOST, HOST])

    def test_native_current_and_killed_child(self):
        self.assertEqual(lifecycle._process_state(lifecycle._identity(os.getpid())), "alive")
        code = '''
import json, sys
from pathlib import Path
from factory import lifecycle
execution = lifecycle.Execution(Path(sys.argv[1]), 'worker')
execution.emit('enter')
print(json.dumps(execution.process), flush=True)
sys.stdin.readline()
'''
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
            path = Path(temp) / "events.jsonl"
            with subprocess.Popen([sys.executable, "-u", "-c", code, str(path)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as child:
                try:
                    identity = json.loads(child.stdout.readline())
                    self.assertEqual(lifecycle._process_state(identity), "alive")
                    self.assertEqual(runtime_events._Observation([]).process_state(identity), "alive")
                    child.kill()
                    child.wait(timeout=10)
                    self.assertEqual(runtime_events._Observation([]).process_state(identity), "dead")
                    self.assertEqual(lifecycle.observe(path)[0]["state"], "interrupted")
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=10)
        hosts = [subprocess.check_output([sys.executable, "-c", "from factory import lifecycle; print(lifecycle._host_id())"], text=True).strip() for _ in range(2)]
        self.assertEqual(hosts, [lifecycle._host_id()] * 2)

    def test_native_identity_fields_match_platform(self):
        pid = os.getpid()
        identity = lifecycle._identity(pid)
        if sys.platform == "linux":
            text = Path(f"/proc/{pid}/stat").read_text()
            fields = text[text.rfind(")") + 2:].split()
            self.assertEqual(identity["boot_id"], Path("/proc/sys/kernel/random/boot_id").read_text().strip())
            self.assertEqual(identity["start_ticks"], int(fields[19]))
            self.assertEqual(identity["pid_namespace"], os.readlink(f"/proc/{pid}/ns/pid"))
        elif sys.platform == "darwin":
            boot = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], text=True).strip()
            start = subprocess.check_output(
                ["/bin/ps", "-o", "lstart=", "-p", str(pid)], text=True,
                env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).strip()
            expected_start = datetime.strptime(start, "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
            self.assertEqual(identity["boot_id"], str(uuid.UUID(boot)))
            self.assertEqual(identity["start_ticks"], int(expected_start.timestamp()))
            self.assertEqual(identity["pid_namespace"], "darwin")
        else:
            self.fail(f"unsupported identity platform: {sys.platform}")

class BootProbeTest(unittest.TestCase):
    def test_boot_id_nonspawning_probe_succeeds_without_subprocess(self):
        with (
            patch.object(sys, "platform", "darwin"),
            patch("subprocess.run", side_effect=AssertionError("no spawn")),
            patch("factory.platform_identity._sysctl_string", return_value=BOOT),
        ):
            self.assertEqual(platform_identity.boot_id(), BOOT)
