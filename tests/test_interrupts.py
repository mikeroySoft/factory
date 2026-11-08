"""Tests that interrupt a real child must not inherit the runner's SIGINT state."""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import unittest

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

    def test_deliverable_sigint_keeps_an_ordinary_runner_unchanged(self) -> None:
        before = runner_sigint()
        with deliverable_sigint():
            self.assertEqual(child_sigint(), {"ignored": False, "default": True, "blocked": False})
        self.assertEqual(runner_sigint(), before)


if __name__ == "__main__":
    unittest.main()
