"""An uneventful dispatcher pass over a parked ticket leaves no lifecycle rows behind."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dashboard, dispatch, handoff, lifecycle, manage, triage
from factory.config import Config


class IdlePasses(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = Config(root=Path(temp.name), repo="local/idle", manager=["true"], manager_rounds=2)
        self.events = self.cfg.factory / "events.jsonl"
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.cfg.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", EVENTS=self.events, MAX_ACTIVE=1,
        ))
        self.packet = self.cfg.factory / "packet-7.md"
        self.packet.parent.mkdir(parents=True)
        self.packet.write_text("evidence")
        # Ticket 7 is parked: escalated, manager said HUMAN, handoff already published to the same owner.
        dispatch.record("escalate", ticket=7, round=1, packet=str(self.packet), reason="gate_failed")
        dispatch.record("comment", ticket=7, kind="escalation", comment=1, url="u")
        dispatch.record("manage", ticket=7, decision="HUMAN", round=1, packet=str(self.packet))
        dispatch.record("handoff", ticket=7, round=1, request="7/1", token="factory-handoff 7/1 #1", target="@alice", why="w")
        dispatch.record("comment", ticket=7, kind="handoff", round=1, request="7/1", token="factory-handoff 7/1 #1",
                        target="@alice", comment=2, url="u")
        self.route = {"status": "routed", "owner": "alice", "candidates": [], "source": "s", "reason": "r", "provenance": []}
        self.issues = [{"number": 7, "title": "parked", "body": "", "labels": [{"name": "ready-for-human"}]}]

    def lifecycle_rows(self):
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == "lifecycle"]

    def run_passes(self):
        with patch.object(dispatch, "gh_json", return_value=self.issues), \
                patch.object(handoff, "observe", return_value={"route": self.route, "pr_url": None}):
            manage.escalation_pass()
            handoff.handoff_pass()

    def test_parked_ticket_pass_writes_no_lifecycle_rows(self):
        before = self.events.read_text()
        self.run_passes()
        self.assertEqual(self.events.read_text(), before)
        self.assertFalse(dispatch.lock_held(dispatch.ticket_lock(7)))

    def test_lock_contention_still_writes_the_trail(self):
        lock = dispatch.ticket_lock(7)
        holder = self.enterContext(lock.open("w"))
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.run_passes()
        rows = self.lifecycle_rows()
        self.assertEqual([r["kind"] for r in rows], ["enter", "resource_requested", "wait", "exit"] * 2)
        self.assertEqual({r["ticket"] for r in rows}, {7})

    def test_new_decision_still_writes_the_full_trail(self):
        self.route["owner"] = "bob"  # owner changed: a new handoff request is published
        receipt = subprocess.CompletedProcess([], 0, "https://github.com/local/idle/issues/7#issuecomment-3\n", "")
        with patch.object(dispatch, "run", return_value=receipt):
            self.run_passes()
        rows = self.lifecycle_rows()
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds, ["enter", "resource_requested", "lock_acquired", "lock_released", "exit"])
        audit = [e for e in lifecycle.read_events(self.events) if e.get("event") == "handoff" and e.get("target") == "@bob"]
        self.assertEqual(audit[0]["execution_id"], rows[0]["execution_id"])
        self.assertEqual(rows[-1]["outcome"], "completed")

    def test_idle_landing_writes_no_rows(self):
        with patch.object(dispatch, "sync_pass"), patch.object(dispatch, "merge_pass_locked"):
            dispatch.land_pass(False)
        self.assertEqual(self.lifecycle_rows(), [])

    def full_pass(self, ready=None):
        """One timer pass (triage, then dispatch) with a real `gh` child process per query."""
        bin_dir = self.cfg.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text("#!/bin/sh\necho '[]'\n")
        (bin_dir / "gh").chmod(0o755)
        with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}), \
                patch.dict(triage.__dict__), patch.object(config, "load", return_value=self.cfg), \
                patch.object(dispatch, "frontier", wraps=dispatch.frontier) as frontier:
            if ready is not None:
                frontier.side_effect = lambda: ready
            self.assertEqual(triage.main([]), 0)
            self.assertEqual(dispatch.main([]), 0)

    def test_idle_pass_appends_at_most_one_row_and_heartbeat_reports_last_pass(self):
        before = len(lifecycle.read_events(self.events))
        self.full_pass()
        self.assertLessEqual(len(lifecycle.read_events(self.events)) - before, 1)
        last = 1_791_000_000_000_000  # systemd timer LastTriggerUSec of this pass

        def systemctl(cmd, **kwargs):
            if "list-timers" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"last": last}]), "")
            return subprocess.CompletedProcess(cmd, 3, "inactive", "")

        with patch.dict(dashboard.__dict__), \
                patch.object(dashboard.subprocess, "run", side_effect=systemctl), \
                patch.object(dashboard, "journal_runs", return_value=[]):
            dashboard.configure(self.cfg)
            self.assertEqual(dashboard.dispatcher()["timer"]["last"], dashboard.iso(last / 1e6))

    def test_pass_that_claims_a_ticket_keeps_the_dispatcher_trail(self):
        def work(issue, budget_min, dry_run):
            with lifecycle.scope(self.events, "ticket", ticket=issue["number"]):
                pass

        with patch.object(dispatch, "process_ticket", side_effect=work):
            self.full_pass(ready=[{"number": 8, "title": "work"}])
        rows = self.lifecycle_rows()
        ids = {row["execution_id"] for row in rows if row["kind"] == "enter"}
        ticket = next(row for row in rows if row["stage"] == "ticket")
        self.assertIn(ticket["parent_execution_id"], ids)
        for stage in ("dispatcher", "scheduling"):
            kinds = [row["kind"] for row in rows if row["stage"] == stage]
            self.assertEqual((kinds[0], kinds[-1]), ("enter", "exit"))
        self.assertIn("child_start", [row["kind"] for row in rows if row["stage"] == "dispatcher"])


if __name__ == "__main__":
    unittest.main()
