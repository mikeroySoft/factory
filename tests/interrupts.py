"""SIGINT state for tests that interrupt a real child process.

A detached launcher (a background job of a non-interactive shell, or a parent
that blocks SIGINT) can start the suite with SIGINT ignored or blocked. Both
survive exec, so a child spawned from that runner never sees the test's SIGINT:
it waits out unrelated deadlines (a lock holder, a hung read) instead of
exercising the interrupt path the test asserts.
"""
from __future__ import annotations

import contextlib
import signal
from collections.abc import Iterator


@contextlib.contextmanager
def deliverable_sigint() -> Iterator[None]:
    """Children spawned from this thread inside the block start with SIGINT
    unblocked and at its default disposition; the runner's state is restored."""
    mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
    ignored = signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    if ignored:
        # Any Python handler resets to SIG_DFL at exec; SIG_IGN would not.
        signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        if ignored:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


@contextlib.contextmanager
def detached_launcher_sigint() -> Iterator[None]:
    """Reproduce a detached launcher: SIGINT ignored, and blocked in this thread."""
    handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        signal.signal(signal.SIGINT, signal.SIG_DFL if handler is None else handler)
