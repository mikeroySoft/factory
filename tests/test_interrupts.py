"""Tests that interrupt a real child must not inherit the runner's SIGINT state."""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from interrupts import LAUNCHER_MODES, deliverable_sigint, detached_launcher_sigint
except ImportError:  # full-suite discovery imports tests as a package
    from tests.interrupts import LAUNCHER_MODES, deliverable_sigint, detached_launcher_sigint

REPORT = '''import json, signal
handler = signal.getsignal(signal.SIGINT)
print(json.dumps({"ignored": handler == signal.SIG_IGN,
                  "default": handler is signal.default_int_handler,
                  "blocked": signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set())}))
'''

DELIVERABLE = {"ignored": False, "default": True, "blocked": False}
INHERITED = {
    "both": {"ignored": True, "default": False, "blocked": True},
    "blocked": {"ignored": False, "default": True, "blocked": True},
    "ignored": {"ignored": True, "default": False, "blocked": False},
}
MISSING = str(Path(__file__).resolve().parent / "no-such-interpreter-for-spawn-failure")


class SetupFailure(Exception):
    pass


def child_sigint() -> dict:
    out = subprocess.run([sys.executable, "-c", REPORT], capture_output=True, text=True,
                         check=True, timeout=30).stdout
    return json.loads(out)


def runner_sigint() -> tuple[object, bool]:
    return (signal.getsignal(signal.SIGINT),
            signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set()))


def failing_mask(how: int):
    """pthread_sigmask that fails only when asked to change SIGINT with ``how``."""
    real = signal.pthread_sigmask

    def mask(operation, signals):
        if operation == how and signal.SIGINT in set(signals):
            raise SetupFailure(f"pthread_sigmask({operation}) refused")
        return real(operation, signals)
    return patch.object(signal, "pthread_sigmask", mask)


def failing_handler(target: object):
    """signal.signal that fails only when installing ``target`` for SIGINT."""
    real = signal.signal

    def install(signum, handler):
        if signum == signal.SIGINT and handler is target:
            raise SetupFailure("signal.signal refused")
        return real(signum, handler)
    return patch.object(signal, "signal", install)


class InterruptsTest(unittest.TestCase):
    def test_detached_launcher_state_reaches_children(self) -> None:
        # The hazard itself: an ignored or blocked SIGINT survives exec.
        for mode in LAUNCHER_MODES:
            with self.subTest(mode):
                before = runner_sigint()
                with detached_launcher_sigint(mode):
                    self.assertEqual(child_sigint(), INHERITED[mode])
                self.assertEqual(runner_sigint(), before)

    def test_unknown_launcher_mode_changes_nothing(self) -> None:
        before = runner_sigint()
        with self.assertRaisesRegex(ValueError, "unknown launcher mode"), detached_launcher_sigint("neither"):
            self.fail("entered an unknown mode")
        self.assertEqual(runner_sigint(), before)

    def test_deliverable_sigint_resets_children_and_restores_runner(self) -> None:
        for mode in LAUNCHER_MODES:
            with self.subTest(mode):
                before = runner_sigint()
                with detached_launcher_sigint(mode):
                    hostile = runner_sigint()
                    with deliverable_sigint():
                        self.assertEqual(child_sigint(), DELIVERABLE)
                    self.assertEqual(runner_sigint(), hostile)
                    self.assertEqual(child_sigint(), INHERITED[mode])
                self.assertEqual(runner_sigint(), before)

    def test_blocked_only_runner_keeps_its_handler(self) -> None:
        # Unblocking must not replace a handler that was never ignored.
        with detached_launcher_sigint("blocked"):
            handler = signal.getsignal(signal.SIGINT)
            self.assertIsNot(handler, signal.SIG_IGN)
            with deliverable_sigint():
                self.assertIs(signal.getsignal(signal.SIGINT), handler)
                self.assertNotIn(signal.SIGINT, signal.pthread_sigmask(signal.SIG_BLOCK, set()))
            self.assertIn(signal.SIGINT, signal.pthread_sigmask(signal.SIG_BLOCK, set()))

    def test_deliverable_sigint_keeps_an_ordinary_runner_unchanged(self) -> None:
        before = runner_sigint()
        with deliverable_sigint():
            self.assertEqual(child_sigint(), DELIVERABLE)
        self.assertEqual(runner_sigint(), before)

    def test_deliverable_setup_failure_restores_mask_and_disposition(self) -> None:
        # Fail at each setup step, after any earlier step already changed state.
        cases = [(mode, "unblock", failing_mask(signal.SIG_UNBLOCK)) for mode in LAUNCHER_MODES]
        cases += [(mode, "handler", failing_handler(signal.default_int_handler))
                  for mode in LAUNCHER_MODES if INHERITED[mode]["ignored"]]
        for mode, step, failure in cases:
            with self.subTest(mode=mode, step=step):
                before = runner_sigint()
                with detached_launcher_sigint(mode):
                    hostile = runner_sigint()
                    with self.assertRaises(SetupFailure), failure, deliverable_sigint():
                        self.fail("setup failure entered the block")
                    self.assertEqual(runner_sigint(), hostile)
                    self.assertEqual(child_sigint(), INHERITED[mode])
                self.assertEqual(runner_sigint(), before)

    def test_launcher_setup_failure_restores_mask_and_disposition(self) -> None:
        cases = [("both", failing_mask(signal.SIG_BLOCK)), ("blocked", failing_mask(signal.SIG_BLOCK)),
                 ("ignored", failing_mask(signal.SIG_UNBLOCK)), ("both", failing_handler(signal.SIG_IGN))]
        for mode, failure in cases:
            with self.subTest(mode):
                before = runner_sigint()
                with self.assertRaises(SetupFailure), failure, detached_launcher_sigint(mode):
                    self.fail("setup failure entered the block")
                self.assertEqual(runner_sigint(), before)
                self.assertEqual(child_sigint(), DELIVERABLE)

    def test_spawn_failure_restores_mask_and_disposition(self) -> None:
        for mode in LAUNCHER_MODES:
            with self.subTest(mode):
                before = runner_sigint()
                with detached_launcher_sigint(mode):
                    hostile = runner_sigint()
                    with self.assertRaises(FileNotFoundError), deliverable_sigint():
                        subprocess.Popen([MISSING])
                    self.assertEqual(runner_sigint(), hostile)
                    self.assertEqual(child_sigint(), INHERITED[mode])
                self.assertEqual(runner_sigint(), before)


if __name__ == "__main__":
    unittest.main()
