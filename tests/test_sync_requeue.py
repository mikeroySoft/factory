"""Upstream-sync issues are system-only in the Ops inbox: the ticket projection
flags them by title prefix so the workspace never offers Re-queue agent."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dashboard, dispatch

ISSUE = {
    "number": 7, "state": "OPEN", "url": "issue/7",
    "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z", "closedAt": None,
    "labels": {"nodes": [{"name": config.LABEL_HUMAN}]},
}
DISK = {"lock_held": False, "attempts": [], "gate": None}


class SyncRequeueExclusionTest(unittest.TestCase):
    def ticket(self, title: str) -> dict:
        with patch.object(dashboard, "cfg", config.Config(Path("/unused"), "acme/widgets"), create=True):
            return dashboard.build_ticket({**ISSUE, "title": title}, None, DISK)

    def test_sync_titled_ready_for_human_issue_is_flagged_system_only(self) -> None:
        ticket = self.ticket(dispatch.SYNC_TITLE + "merge conflict at abc123456789")
        self.assertEqual((ticket["stage"], ticket["sync"]), ("escalated", True))

    def test_ordinary_escalation_keeps_the_requeue_path(self) -> None:
        ticket = self.ticket("Fix parser")
        self.assertEqual((ticket["stage"], ticket["sync"]), ("escalated", False))


if __name__ == "__main__":
    unittest.main()
