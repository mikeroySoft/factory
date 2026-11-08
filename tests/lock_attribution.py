"""Platform capability for kernel lock-holder attribution, and real-flock probes.

Non-Darwin hosts take the /proc/locks path for FLOCK holders. The guarded
Darwin path has no non-spawning holder source, so attribution exists there only
when an observer backend is installed (factory.platform_identity.lock_holders). The branch is on that
capability, never on what a snapshot happened to report.
"""
from __future__ import annotations

import fcntl
import unittest
from pathlib import Path

from factory import darwin_observer, platform_identity

UNATTRIBUTED_EVIDENCE = {"lock_state": "held", "attribution": "unavailable", "holder_pids": None}


def lock_holders_attributable() -> bool:
    """True when this host can name a lock's holders: not Darwin, or Darwin
    with an installed observer.

    This is not "is Linux". Every non-Darwin host takes the /proc/locks path,
    so this is True there whether or not that source exists. On Darwin it is
    True only with an installed observer backend.
    """
    return not platform_identity.darwin() or darwin_observer.installed() is not None


def flock_blocked(path: Path) -> bool:
    """True when a real LOCK_EX is refused now; a free lock is released at once."""
    with Path(path).open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def assert_unattributed_hold(case: unittest.TestCase, observed: dict) -> None:
    """A held lock without attribution: held/unknown, no owner, holder count unavailable.

    Exact equality excludes every wrong reading of unknown: state is not free,
    there is no owner pid, and ``holder_pids`` is None, so the partial holder
    count is neither full (``[pid]``) nor zero (``[]``).
    """
    case.assertEqual((observed["state"], observed["ownership"], observed["owner"]), ("held", "unknown", None))
    case.assertEqual(observed["evidence"], UNATTRIBUTED_EVIDENCE)
