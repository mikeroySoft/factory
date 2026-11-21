"""Host mutation admission across independent gate processes and repositories."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config

GATE = r'''
import sys
from pathlib import Path
from unittest import mock
from factory import config, gate
root, prefix, timeout = sys.argv[1:4]
def proof(cmd, base, duration=None):
    passed, output = gate.run(cmd)
    return passed, output, "PASS" if passed else "FAIL", ""
with mock.patch.object(config, "repo_root", return_value=Path(root)), \
     mock.patch.object(gate, "MUTATION_SLOT_PREFIX", Path(prefix), create=True), \
     mock.patch.object(gate, "toolchain", return_value=dict(rocm="n/a", kernel="test", amdgpu="n/a")), \
     mock.patch.object(gate, "run_mutation_proof", side_effect=proof):
    raise SystemExit(gate.main(["--tier", "pr", "--check-timeout", timeout,
                               "--report", str(Path(root) / "report.md"), *sys.argv[4:]]))
'''
CHECK = r'''
import sys, time
from pathlib import Path
started, release, finished = map(Path, sys.argv[1:])
started.write_text(str(time.monotonic()))
while not release.exists():
    time.sleep(0.01)
finished.write_text(str(time.monotonic()))
raise SystemExit(int(release.read_text() or "0"))
'''


class HostMutationSlotsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="factory-slots-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = self.root / "config/factory/config.toml"
        self.host.parent.mkdir(parents=True)
        self.env = {**os.environ, "XDG_CONFIG_HOME": str(self.root / "config"),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        self.processes = []
        self.addCleanup(self.stop)

    def stop(self) -> None:
        for root, proc in self.processes:
            (root / "release").write_text("0")
        for _, proc in self.processes:
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

    def slots(self, value: int | None) -> None:
        self.host.write_text("" if value is None else f"[defaults.gate]\nmutation_slots = {value}\n")

    def launch(self, *, timeout: int = 10, name: str = "mutation", when: str = "pr",
               proof: bool = False):
        root = self.root / f"repo-{len(self.processes)}"
        root.mkdir()
        command = [sys.executable, "-c", CHECK, str(root / "started"),
                   str(root / "release"), str(root / "finished")]
        (root / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n[[gate.check]]\n'
            f'name = "{name}"\nwhen = "{when}"\nrun = {json.dumps(command)}\n'
            + ('proof = "mutation-v1"\n' if proof else "")
        )
        proc = subprocess.Popen([sys.executable, "-c", GATE, str(root),
                                 str(self.root / "host-mutation"), str(timeout),
                                 *(["--mutation-proof"] if proof else [])],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, cwd=root)
        self.processes.append((root, proc))
        return root, proc

    def wait_for(self, predicate, proc) -> None:
        deadline = time.monotonic() + 10
        while not predicate():
            if proc.poll() is not None:
                self.fail(f"gate exited early: {proc.communicate()[0]}")
            if time.monotonic() >= deadline:
                self.fail("gate did not reach expected state")
            time.sleep(0.01)

    def waiting_or_started(self, root: Path) -> bool:
        events = root / ".factory/events.jsonl"
        return (root / "started").exists() or (
            events.exists() and any(
                json.loads(row).get("wait", {}).get("reason") == "mutation_slot"
                for row in events.read_text().splitlines(keepends=True) if row.endswith("\n")
            ))

    def finish(self, root: Path, proc, code: int = 0) -> str:
        (root / "release").write_text(str(code))
        output = proc.communicate(timeout=10)[0]
        self.assertEqual(proc.returncode, code, output)
        return (root / "report.md").read_text()

    def test_one_slot_serializes_and_wait_does_not_use_check_timeout(self) -> None:
        self.slots(1)
        first, p1 = self.launch()
        self.wait_for(lambda: (first / "started").exists(), p1)
        second, p2 = self.launch(timeout=1)
        self.wait_for(lambda: self.waiting_or_started(second), p2)
        self.assertFalse((second / "started").exists(), "mutation runs overlapped")
        time.sleep(1.2)  # wait exceeds second's entire check timeout
        self.finish(first, p1)
        self.wait_for(lambda: (second / "started").exists(), p2)
        report = self.finish(second, p2)
        self.assertGreaterEqual(float((second / "started").read_text()),
                                float((first / "finished").read_text()))
        recorded = re.search(r"Mutation slot wait: mutation ([0-9.]+)s", report)
        self.assertIsNotNone(recorded, report)
        self.assertGreaterEqual(float(recorded[1]), 1.2)
        self.assertIn("- mutation: PASS", report)

    def test_unset_slots_preserve_concurrent_runs_and_report(self) -> None:
        self.slots(None)
        first, p1 = self.launch()
        self.wait_for(lambda: (first / "started").exists(), p1)
        second, p2 = self.launch()
        self.wait_for(lambda: (second / "started").exists(), p2)
        self.assertFalse((first / "finished").exists())
        for root, proc in ((first, p1), (second, p2)):
            self.assertNotIn("Mutation slot wait:", self.finish(root, proc))
        self.assertEqual(list(self.root.glob("host-mutation-*.lock")), [])

    def test_two_slots_allow_two_but_not_three_and_release_after_failure(self) -> None:
        self.slots(2)
        first, p1 = self.launch()
        second, p2 = self.launch()
        for root, proc in ((first, p1), (second, p2)):
            self.wait_for(lambda root=root: (root / "started").exists(), proc)
        third, p3 = self.launch()
        self.wait_for(lambda: self.waiting_or_started(third), p3)
        self.assertFalse((third / "started").exists(), "third run exceeded two slots")
        self.finish(first, p1, code=1)
        self.wait_for(lambda: (third / "started").exists(), p3)
        self.assertFalse((second / "finished").exists())
        self.finish(second, p2)
        self.finish(third, p3)

    def test_other_pr_checks_are_not_throttled(self) -> None:
        self.slots(1)
        first, p1 = self.launch()
        self.wait_for(lambda: (first / "started").exists(), p1)
        second, p2 = self.launch(name="lint")
        self.wait_for(lambda: (second / "started").exists(), p2)
        self.assertNotIn("Mutation slot wait:", self.finish(second, p2))
        self.finish(first, p1)

    def test_host_config_precedence_and_validation(self) -> None:
        repo = self.root / "config-repo"
        repo.mkdir()
        (repo / ".factory.toml").write_text('[repo]\nslug = "acme/widgets"\n')
        with mock.patch.object(config, "repo_root", return_value=repo), \
                mock.patch.dict(os.environ, self.env):
            self.slots(None)
            self.assertIsNone(config.load(repo).mutation_slots)
            self.slots(2)
            self.assertEqual(config.load(repo).mutation_slots, 2)
            self.host.write_text('[defaults.gate]\nmutation_slots = 2\n'
                                 '[repo."acme/widgets".gate]\nmutation_slots = 1\n')
            self.assertEqual(config.load(repo).mutation_slots, 1)
            for section in ('defaults', 'repo."acme/widgets"'):
                for value in ('0', '-1', '1.5', 'true', '"2"', '[]', '{}'):
                    with self.subTest(section=section, value=value):
                        self.host.write_text(f'[{section}.gate]\nmutation_slots = {value}\n')
                        with self.assertRaisesRegex(config.ConfigError, r"gate\.mutation_slots"):
                            config.load(repo)

    def test_renamed_proof_check_uses_slots_independent_of_exclusive_lock(self) -> None:
        self.slots(1)
        exclusive = self.root / "exclusive.lock"
        with exclusive.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.host.write_text(f'[defaults.gate]\nmutation_slots = 1\nlock = "{exclusive}"\n')
            first, p1 = self.launch()
            self.wait_for(lambda: (first / "started").exists(), p1)
            second, p2 = self.launch(name="renamed-proof", proof=True)
            self.wait_for(lambda: self.waiting_or_started(second), p2)
            self.assertFalse((second / "started").exists(), "proof run bypassed host slots")
            self.finish(first, p1)
            self.wait_for(lambda: (second / "started").exists(), p2)
            self.assertIn("Mutation proof: renamed-proof PASS", self.finish(second, p2))

    def test_timeout_releases_slot_for_next_run(self) -> None:
        self.slots(1)
        first, p1 = self.launch(timeout=1)
        self.wait_for(lambda: (first / "started").exists(), p1)
        second, p2 = self.launch()
        output = p1.communicate(timeout=10)[0]
        self.assertEqual(p1.returncode, 1, output)
        self.assertIn("check timed out after 1s", (first / "report.md").read_text())
        self.wait_for(lambda: (second / "started").exists(), p2)
        self.finish(second, p2)

    def test_repository_cannot_override_host_admission_policy(self) -> None:
        repo = self.root / "policy-repo"
        repo.mkdir()
        (repo / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n[gate]\nmutation_slots = 3\n')
        self.slots(1)
        with mock.patch.object(config, "repo_root", return_value=repo), \
                mock.patch.dict(os.environ, self.env), \
                self.assertRaisesRegex(config.ConfigError, r"gate\.mutation_slots is host-only"):
            config.load(repo)

    def test_interrupted_gate_keeps_slot_until_mutation_finishes(self) -> None:
        import signal

        self.slots(1)
        for interrupt in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=interrupt):
                first, p1 = self.launch()
                self.wait_for(lambda first=first: (first / "started").exists(), p1)
                p1.send_signal(interrupt)
                p1.wait(timeout=10)
                # The command is in its own session and still waiting for release.
                with (self.root / "host-mutation-0.lock").open("a") as slot, \
                        self.assertRaises(BlockingIOError, msg="orphan mutation lost admission"):
                    fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
                second, p2 = self.launch()
                self.wait_for(lambda second=second: self.waiting_or_started(second), p2)
                self.assertFalse((second / "started").exists(), "orphan mutation overlapped")
                (first / "release").write_text("0")
                p1.communicate(timeout=10)
                self.wait_for(lambda second=second: (second / "started").exists(), p2)
                self.finish(second, p2)
                self.assertGreaterEqual(float((second / "started").read_text()),
                                        float((first / "finished").read_text()))
