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

LAUNCHER_MODES = ("both", "blocked", "ignored")


def _snapshot() -> tuple[object, object]:
    """This thread's mask and the SIGINT handler, read without changing either."""
    return signal.pthread_sigmask(signal.SIG_BLOCK, set()), signal.getsignal(signal.SIGINT)


def _restore(mask: object, handler: object) -> None:
    # Handler first, so a SIGINT left pending by the mask meets the restored disposition.
    # None means a handler not installed from Python; it was never replaced here.
    if handler is not None and signal.getsignal(signal.SIGINT) != handler:
        signal.signal(signal.SIGINT, handler)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)


@contextlib.contextmanager
def deliverable_sigint() -> Iterator[None]:
    """Children spawned from this thread inside the block start with SIGINT
    unblocked and at its default disposition; the runner's state is restored,
    including when changing it fails part way."""
    mask, handler = _snapshot()
    try:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
        if handler == signal.SIG_IGN:
            # Any Python handler resets to SIG_DFL at exec; SIG_IGN would not.
            signal.signal(signal.SIGINT, signal.default_int_handler)
        yield
    finally:
        _restore(mask, handler)


@contextlib.contextmanager
def detached_launcher_sigint(mode: str = "both") -> Iterator[None]:
    """Reproduce a detached launcher's SIGINT in this thread.

    ``both``: ignored and blocked. ``blocked``: blocked, handler untouched.
    ``ignored``: ignored, mask explicitly unblocked.
    """
    if mode not in LAUNCHER_MODES:
        raise ValueError(f"unknown launcher mode {mode!r}")
    mask, handler = _snapshot()
    try:
        if mode in {"both", "ignored"}:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        if mode in {"both", "blocked"}:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        else:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
        yield
    finally:
        _restore(mask, handler)
