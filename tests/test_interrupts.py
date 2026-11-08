"""Tests that interrupt a real child must not inherit the runner's SIGINT state."""
from __future__ import annotations

import contextlib
import json
import signal
import subprocess
import sys
import unittest
from collections.abc import Iterator

try:
    from interrupts import deliverable_sigint, detached_launcher_sigint
except ImportError:  # full-suite discovery imports tests as a package
    from tests.interrupts import deliverable_sigint, detached_launcher_sigint

REPORT = '''import json, signal
handler = signal.getsignal(signal.SIGINT)
print(json.dumps({"ignored": handler == signal.SIG_IGN,
                  "default": handler is signal.default_int_handler,
                  "blocked": signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set())}))
'''


def child_sigint() -> dict:
    out = subprocess.run([sys.executable, "-c", REPORT], capture_output=True, text=True,
                         check=True, timeout=30).stdout
    return json.loads(out)


@contextlib.contextmanager
def blocked_only_sigint() -> Iterator[None]:
    """SIGINT blocked in this thread; its handler is left as it is."""
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


@contextlib.contextmanager
def ignored_only_sigint() -> Iterator[None]:
    """SIGINT ignored, with this thread's mask left unblocked."""
    mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
    handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL if handler is None else handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def runner_sigint() -> tuple[object, bool]:
    return (signal.getsignal(signal.SIGINT),
            signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set()))


class InterruptsTest(unittest.TestCase):
    def test_detached_launcher_state_reaches_children(self) -> None:
        # The hazard itself: an ignored or blocked SIGINT survives exec.
        before = runner_sigint()
        with detached_launcher_sigint():
            self.assertEqual(child_sigint(), {"ignored": True, "default": False, "blocked": True})
        self.assertEqual(runner_sigint(), before)

    def test_deliverable_sigint_resets_children_and_restores_runner(self) -> None:
        before = runner_sigint()
        with detached_launcher_sigint():
            hostile = runner_sigint()
            with deliverable_sigint():
                self.assertEqual(child_sigint(), {"ignored": False, "default": True, "blocked": False})
            self.assertEqual(runner_sigint(), hostile)
        self.assertEqual(runner_sigint(), before)

    def test_blocked_mask_and_ignored_handler_are_detected_and_cleared_separately(self) -> None:
        # Each hazard alone must be visible in the child and cleared on its own:
        # a blocked mask is not an ignored handler, and vice versa.
        cases = {
            "blocked": (blocked_only_sigint, {"ignored": False, "default": True, "blocked": True}),
            "ignored": (ignored_only_sigint, {"ignored": True, "default": False, "blocked": False}),
        }
        for label, (hazard, inherited) in cases.items():
            with self.subTest(label):
                before = runner_sigint()
                with hazard():
                    self.assertEqual(child_sigint(), inherited)
                    hostile = runner_sigint()
                    with deliverable_sigint():
                        self.assertEqual(child_sigint(), {"ignored": False, "default": True, "blocked": False})
                    self.assertEqual(runner_sigint(), hostile)
                self.assertEqual(runner_sigint(), before)

    def test_blocked_only_runner_keeps_its_handler(self) -> None:
        # Unblocking must not replace a handler that was never ignored.
        with blocked_only_sigint():
            handler = signal.getsignal(signal.SIGINT)
            with deliverable_sigint():
                self.assertIs(signal.getsignal(signal.SIGINT), handler)
                self.assertNotIn(signal.SIGINT, signal.pthread_sigmask(signal.SIG_BLOCK, set()))
            self.assertIn(signal.SIGINT, signal.pthread_sigmask(signal.SIG_BLOCK, set()))

    def test_deliverable_sigint_keeps_an_ordinary_runner_unchanged(self) -> None:
        before = runner_sigint()
        with deliverable_sigint():
            self.assertEqual(child_sigint(), {"ignored": False, "default": True, "blocked": False})
        self.assertEqual(runner_sigint(), before)


if __name__ == "__main__":
    unittest.main()
