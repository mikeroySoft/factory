"""Platform capability for kernel lock-holder attribution, and real-flock probes.

Non-Darwin hosts take the /proc/locks path for FLOCK holders. The guarded
Darwin path has no non-spawning holder source; an installed observer backend
(factory.platform_identity.lock_holders) may supply holders, but nothing in its
API says whether it will, so that case fails closed. The branch is on
capability, never on what a snapshot happened to report.
"""
from __future__ import annotations

import fcntl
import unittest
from pathlib import Path

from factory import darwin_observer, platform_identity

UNATTRIBUTED_EVIDENCE = {"lock_state": "held", "attribution": "unavailable", "holder_pids": None}
# The exact row schema lifecycle.resources() returns; it carries no capacity field.
RESOURCE_ROW_KEYS = frozenset({
    "resource", "state", "ownership", "owner", "requests", "evidence",
    "event_id", "at", "observed_at",
})


NO_DARWIN_HOST_ID = (
    "Darwin host_id() is None: lifecycle.resources() records the lock under a "
    "per-process unknown host, treats it as not local and reports state/ownership "
    "'unknown' without reading the kernel lock (pinned by "
    "test_resources.test_darwin_without_host_id_reports_not_local_unknown). A held-lock "
    "assertion here would be meaningless, so this test fails instead of passing vacuously.")
UNQUERYABLE_OBSERVER = (
    "a Darwin observer backend is installed, but the observer API "
    "(factory.darwin_observer / platform_identity.lock_holders) has no capability "
    "query for lock-holder attribution: backend.lock_holders() returns list|None per "
    "call, and None means 'not supplied' for any reason. Whether holders will be "
    "attributed cannot be known without a probe, so this fails closed.")


def lock_holders_attributable() -> bool:
    """True when this host can name a lock's holders; fails when that cannot be known.

    This is not "is Linux". Every non-Darwin host takes the /proc/locks path,
    so this is True there whether or not that source exists. On Darwin:

    * no host_id: resources() cannot treat any lock as local (NO_DARWIN_HOST_ID),
      so the caller's held/unattributed assertions do not apply; fail clearly.
    * no installed observer: native attribution is unavailable; False.
    * an installed observer: it must actually supply holders, but the observer
      API has no non-experimental capability query (UNQUERYABLE_OBSERVER).
      Assuming either answer could pass vacuously, so fail closed.
    """
    if not platform_identity.darwin():
        return True
    if platform_identity.host_id() is None:
        raise AssertionError(NO_DARWIN_HOST_ID)
    if darwin_observer.installed() is None:
        return False
    raise AssertionError(UNQUERYABLE_OBSERVER)


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
    """A held lock without attribution: held/unknown, no owner, owner evidence unavailable.

    Exact equality excludes every wrong reading of unknown: the row has exactly
    the lifecycle.resources() schema, state is not free, there is no owner pid,
    and ``holder_pids`` is None rather than ``[pid]`` or ``[]``. None proves
    only that passive owner evidence is unavailable; it says nothing about
    dispatcher capacity.

    Capacity is a different surface: runtime_local.dispatcher()'s
    ``capacity`` row ({"configured", "active", "complete"}), which reads
    ``active: None, complete: False`` when Darwin has no passive lock table.
    Native tests already cover it against real FLOCK contention, admission
    waits and reopen in tests/test_native_capability.py:
    test_linux_kernel_table_counts_only_live_ticket_flocks,
    test_external_holder_blocks_admission_until_the_process_exits,
    test_numeric_ticket_lock_is_not_a_capacity_count and
    test_gate_exclusive_still_blocks_when_passive_capacity_is_unavailable.
    """
    case.assertEqual(set(observed), RESOURCE_ROW_KEYS)
    case.assertEqual((observed["state"], observed["ownership"], observed["owner"]), ("held", "unknown", None))
    case.assertEqual(observed["evidence"], UNATTRIBUTED_EVIDENCE)
