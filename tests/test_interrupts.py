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
    import interrupts
    from interrupts import LAUNCHER_MODES, deliverable_sigint, detached_launcher_sigint
except ImportError:  # full-suite discovery imports tests as a package
    from tests import interrupts
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
KNOWN = (signal.default_int_handler, False)
RESTORE_TESTS = (
    "test_launcher_setup_failure_restores_mask_and_disposition",
    "test_foreign_handler_replaced_here_is_not_left_ignored",
    "test_mask_is_restored_when_restoring_the_handler_fails",
)
# Runs RESTORE_TESTS in a runner that starts with SIGINT ignored and blocked, and
# reports that starting state, the test result, and the state left afterwards.
HOSTILE_RUNNER = """import json, signal, sys, unittest
def state():
    return {"ignored": signal.getsignal(signal.SIGINT) == signal.SIG_IGN,
            "blocked": signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set())}
start = state()
sys.path.insert(0, sys.argv[1])
import test_interrupts
suite = unittest.defaultTestLoader.loadTestsFromNames(
    ["InterruptsTest." + name for name in test_interrupts.RESTORE_TESTS], test_interrupts)
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
print(json.dumps({"start": start, "end": state(), "run": result.testsRun,
                  "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "ok": result.wasSuccessful()}))
"""


class SetupFailure(Exception):
    pass


def child_sigint() -> dict:
    out = subprocess.run([sys.executable, "-c", REPORT], capture_output=True, text=True,
                         check=True, timeout=30).stdout
    return json.loads(out)


def runner_sigint() -> tuple[object, bool]:
    return (signal.getsignal(signal.SIGINT),
            signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set()))


def put_back_runner_sigint(handler: object, mask: object) -> None:
    """Restore a saved handler and mask; the mask even if the handler fails.

    A None handler was not installed from Python and cannot be reinstalled. If
    the disposition is still that unknown one it is left alone; otherwise it is
    set to SIG_DFL, the nearest deliverable disposition, rather than raising
    TypeError from ``signal.signal(SIGINT, None)``.
    """
    try:
        if handler is None:
            if signal.getsignal(signal.SIGINT) is not None:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
        else:
            signal.signal(signal.SIGINT, handler)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def establish_known_sigint() -> None:
    """Python's default handler, SIGINT unblocked: independent of the runner's start."""
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})


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


def foreign_snapshot():
    """Snapshot as if the runner's SIGINT handler had not been installed from
    Python: ``signal.getsignal`` reports None for such a handler. The mask is real."""
    real = interrupts._snapshot

    def snapshot():
        mask, _ = real()
        return mask, None
    return patch.object(interrupts, "_snapshot", snapshot)


def reported_foreign():
    """signal.getsignal reports a still-foreign (None) handler; installs are recorded."""
    installed = []
    real_install = signal.signal

    def install(signum, handler):
        installed.append((signum, handler))
        return real_install(signum, handler)
    return (patch.object(signal, "getsignal", lambda signum: None),
            patch.object(signal, "signal", install), installed)


class InterruptsTest(unittest.TestCase):
    def keep_runner_sigint(self) -> None:
        # These tests replace the real handler and mask; put both back afterwards.
        handler = signal.getsignal(signal.SIGINT)
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        self.addCleanup(put_back_runner_sigint, handler, mask)

    def known_sigint(self) -> tuple[object, bool]:
        """Establish KNOWN for this test step and return it, checked."""
        establish_known_sigint()
        self.assertEqual(runner_sigint(), KNOWN)
        return KNOWN

    def test_keep_runner_sigint_restores_a_foreign_none_handler(self) -> None:
        # A saved None handler must not make cleanup raise TypeError.
        for current, expected in ((signal.SIG_IGN, signal.SIG_DFL),
                                  (signal.default_int_handler, signal.SIG_DFL)):
            with self.subTest(current=current):
                self.keep_runner_sigint()
                establish_known_sigint()
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
                signal.signal(signal.SIGINT, current)
                put_back_runner_sigint(None, mask)
                self.assertIs(signal.getsignal(signal.SIGINT), expected)
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), mask)
        self.keep_runner_sigint()
        establish_known_sigint()
        getsignal, install, installed = reported_foreign()
        with getsignal, install:
            put_back_runner_sigint(None, signal.pthread_sigmask(signal.SIG_BLOCK, set()))
        self.assertEqual(installed, [])

    def test_restore_tests_pass_when_the_runner_starts_hostile(self) -> None:
        with detached_launcher_sigint("both"):
            self.assertEqual(child_sigint(), INHERITED["both"])
            proc = subprocess.run([sys.executable, "-c", HOSTILE_RUNNER, str(Path(__file__).resolve().parent)],
                                  capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(report, {"start": {"ignored": True, "blocked": True},
                                  "end": {"ignored": True, "blocked": True},
                                  "run": len(RESTORE_TESTS), "failures": 0, "errors": 0,
                                  "skipped": 0, "ok": True}, proc.stderr)

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
        self.keep_runner_sigint()
        cases = [("both", failing_mask(signal.SIG_BLOCK)), ("blocked", failing_mask(signal.SIG_BLOCK)),
                 ("ignored", failing_mask(signal.SIG_UNBLOCK)), ("both", failing_handler(signal.SIG_IGN))]
        for mode, failure in cases:
            with self.subTest(mode):
                before = self.known_sigint()
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

    def test_foreign_handler_replaced_here_is_not_left_ignored(self) -> None:
        # A handler Python cannot name (snapshot None) that this module replaced
        # with SIG_IGN is restored to SIG_DFL, never left ignored.
        self.keep_runner_sigint()
        for mode in ("both", "ignored"):
            with self.subTest(mode):
                self.known_sigint()
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                with foreign_snapshot(), detached_launcher_sigint(mode):
                    self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
                self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_DFL)
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), mask)
                self.assertEqual(child_sigint(), {"ignored": False, "default": True, "blocked": False})

    def test_foreign_handler_left_in_place_is_not_replaced(self) -> None:
        # Still foreign at restore time: nothing here replaced it, so no install.
        self.keep_runner_sigint()
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        getsignal, install, installed = reported_foreign()
        with getsignal, install:
            interrupts._restore(mask, None)
        self.assertEqual(installed, [])
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), mask)

    def test_mask_is_restored_when_restoring_the_handler_fails(self) -> None:
        # Modes whose hostile mask blocks SIGINT, so a skipped mask restore is visible.
        self.keep_runner_sigint()
        for mode in ("both", "blocked"):
            with self.subTest(mode):
                before = self.known_sigint()
                self.assertFalse(before[1])
                launcher = detached_launcher_sigint(mode)
                launcher.__enter__()
                self.assertTrue(runner_sigint()[1])
                # Force a handler restore (blocked mode leaves the handler alone).
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                with self.assertRaises(SetupFailure), failing_handler(before[0]):
                    launcher.__exit__(None, None, None)
                # The handler restore failed, but the mask is the runner's again.
                self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
                self.assertFalse(runner_sigint()[1])
                signal.signal(signal.SIGINT, before[0])
                self.assertEqual(runner_sigint(), before)

    def test_deliverable_mask_is_restored_when_restoring_the_handler_fails(self) -> None:
        # Only "both" both ignores (so a handler restore happens) and blocks (so a
        # skipped mask restore is visible).
        self.keep_runner_sigint()
        for mode in ("both",):
            with self.subTest(mode), detached_launcher_sigint(mode):
                hostile = runner_sigint()
                self.assertTrue(hostile[1])
                with self.assertRaises(SetupFailure), failing_handler(signal.SIG_IGN), deliverable_sigint():
                    self.assertEqual(runner_sigint(), (signal.default_int_handler, False))
                # SIG_IGN could not be put back, but the hostile mask was.
                self.assertEqual(runner_sigint()[1], hostile[1])
                self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                self.assertEqual(runner_sigint(), hostile)


if __name__ == "__main__":
    unittest.main()
