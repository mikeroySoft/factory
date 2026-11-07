"""Required #564 review fixes: cancellation, atomic settings, and gate descendants."""
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, settings
from tests.test_settings import isolated_xdg, make_repo, snapshot


class BudgetRevisionTest(unittest.TestCase):
    def test_budgeted_command_cancels_before_command_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "ready"
            cancel = threading.Event()
            command = [sys.executable, "-c", (
                "import time; from pathlib import Path; "
                f"Path({str(ready)!r}).touch(); time.sleep(60)"
            )]

            def interrupt_when_running():
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                cancel.set()

            interrupter = threading.Thread(target=interrupt_when_running)
            with mock.patch.object(dispatch, "cfg", config.Config(root, "acme/widgets"), create=True), \
                    mock.patch.object(dispatch, "_CANCEL", cancel), \
                    mock.patch.object(dispatch, "WATCH_POLL", 0.02), \
                    dispatch.wall_budget(7, 90):
                interrupter.start()
                started = time.monotonic()
                try:
                    with self.assertRaises(KeyboardInterrupt):
                        dispatch.run(command, timeout=3)
                finally:
                    interrupter.join(timeout=3)
                self.assertTrue(ready.exists(), "the command must start before cancellation")
                self.assertLess(time.monotonic() - started, 1.5)

    def test_budgeted_polling_preserves_output_and_explicit_timeout(self):
        root = Path.cwd()
        with mock.patch.object(dispatch, "cfg", config.Config(root, "acme/widgets"), create=True), \
                mock.patch.object(dispatch, "WATCH_POLL", 0.02), dispatch.wall_budget(7, 90):
            result = dispatch.run([sys.executable, "-c", (
                "import time; print('first', flush=True); time.sleep(0.1); print('last')"
            )], timeout=2)
            self.assertEqual(result.stdout, "first\nlast\n")
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                dispatch.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.1)
            self.assertLess(time.monotonic() - started, 1)

    def test_settings_reject_budget_above_effective_cap_without_writing(self):
        for cap in (720, 180, 1440):
            with self.subTest(cap=cap), tempfile.TemporaryDirectory() as directory, \
                    isolated_xdg(Path(directory)):
                toml = f"[dispatch]\nbudget_cap_min = {cap}\n" if cap != 720 else ""
                repo = make_repo(Path(directory), toml)
                path = repo / config.CONFIG_NAME
                before = path.read_bytes() if path.exists() else None
                current = snapshot(repo)
                result = settings.save(repo, {
                    "revision": current["revision"],
                    "changes": {"dispatch.budget_min": cap + 1, "dispatch.max_active": 5},
                })
                self.assertFalse(result["ok"], result)
                self.assertEqual(path.read_bytes() if path.exists() else None, before)
                self.assertEqual(config.load(repo).budget_cap_min, cap)
                accepted = settings.save(repo, {
                    "revision": current["revision"], "changes": {"dispatch.budget_min": cap},
                })
                self.assertTrue(accepted["ok"], accepted)
                self.assertEqual(config.load(repo).budget_min, cap)

    def test_cap_stops_check_descendant_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            address = root / "listener.json"
            sleeper = (
                "import json, os, socket, time; from pathlib import Path; "
                "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); "
                f"Path({str(address)!r}).write_text(json.dumps([os.getpid(), s.getsockname()[1]])); "
                "time.sleep(60)"
            )
            parent = f"import subprocess; subprocess.Popen({[sys.executable, '-c', sleeper]!r})"
            gate_command = [sys.executable, "-c", (
                "import signal; from factory import gate; "
                "signal.signal(signal.SIGTERM, gate._terminate); "
                f"gate.timed({[sys.executable, '-c', parent]!r})"
            )]
            try:
                with mock.patch.object(dispatch, "cfg", config.Config(root, "acme/widgets"), create=True), \
                        dispatch.wall_budget(7, 90) as budget:
                    budget.cap_seconds = 2
                    with self.assertRaises(dispatch.BudgetExceeded):
                        dispatch.run(gate_command)
                self.assertTrue(address.exists(), "the orphaned check descendant must start")
                _, port = json.loads(address.read_text())
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0,
                                        "check descendant survived the hard cap")
            finally:
                if address.exists():
                    pid, _ = json.loads(address.read_text())
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)
