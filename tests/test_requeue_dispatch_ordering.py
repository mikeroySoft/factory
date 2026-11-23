"""Explicit host requeues remain closed until cleanup, pinning and audit finish."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests.test_host_claim_transitions import TimelineGitHub
from tests.test_host_claims import HostClaimRaceTest as Harness


class RequeueDispatchOrderingTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def owner(self):
        self.github = TimelineGitHub()
        tank, foreign = self.dispatcher("tank"), self.dispatcher("ultron")
        # A frontier snapshot obtained before ownership must not bypass fresh readiness.
        stale = copy.deepcopy(self.github.issue)
        self.github.add_claim("old", datetime.now(UTC) - timedelta(hours=3))
        self.github.add_claim("tank", datetime.now(UTC))
        return tank, foreign, stale

    def test_foreign_admission_is_closed_at_every_transition(self):
        tank, foreign, stale = self.owner()
        run, record = tank.run, tank.record
        observed = []

        def probe(stage):
            observed.append(stage)
            self.assertNotIn({"name": "ready-for-agent"}, self.github.issue["labels"], stage)
            foreign.process_ticket(copy.deepcopy(stale), 1, False)
            self.assertEqual(self.launched, [], stage)

        def interleave(cmd, *args, **kwargs):
            result = run(cmd, *args, **kwargs)
            if cmd[:3] == ["gh", "issue", "edit"]:
                if "--remove-assignee" in cmd:
                    probe("unassign")
                elif "--add-label" in cmd and cmd[cmd.index("--add-label") + 1] == "host:tank":
                    probe("pin")
                elif "--remove-label" in cmd:
                    probe("remove-label")
            elif cmd[:2] == ["gh", "api"] and "DELETE" in cmd:
                probe("delete-claim")
            elif cmd[:3] == ["gh", "label", "create"]:
                probe("create-pin")
            return result

        def audit(event, **kwargs):
            if event == "requeue-host-pinned":
                probe("before-audit")
            result = record(event, **kwargs)
            if event == "requeue-host-pinned":
                probe("after-audit")
            return result

        with mock.patch.object(tank, "run", side_effect=interleave), \
                mock.patch.object(tank, "record", side_effect=audit):
            tank.unclaim(7, "--remove-label", "ready-for-human", "--add-label", "ready-for-agent", host="tank")
        for stage in ("unassign", "pin", "remove-label", "delete-claim", "create-pin", "before-audit", "after-audit"):
            self.assertIn(stage, observed)
        self.assertIn({"name": "ready-for-agent"}, self.github.issue["labels"])
        foreign.process_ticket(copy.deepcopy(stale), 1, False)
        self.assertEqual(self.launched, [])
        tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
        self.assertEqual(self.launched, ["tank"])

    def fail_transition(self, stage):
        tank, foreign, stale = self.owner()
        run, record = tank.run, tank.record

        def fail_run(cmd, *args, **kwargs):
            create = cmd[:3] == ["gh", "label", "create"]
            pin = "--add-label" in cmd and cmd[cmd.index("--add-label") + 1] == "host:tank"
            if (stage == "create" and create) or (stage == "pin" and pin):
                result = subprocess.CompletedProcess(cmd, 1, "", "permission denied")
                if kwargs.get("check", True):
                    result.check_returncode()
                return result
            return run(cmd, *args, **kwargs)

        def fail_record(event, **kwargs):
            if stage == "audit" and event == "requeue-host-pinned":
                raise OSError("audit unavailable")
            return record(event, **kwargs)

        with mock.patch.object(tank, "run", side_effect=fail_run), \
                mock.patch.object(tank, "record", side_effect=fail_record):
            with self.assertRaises((subprocess.CalledProcessError, OSError)):
                tank.unclaim(7, "--add-label", "ready-for-agent", host="tank")
        self.assertNotIn({"name": "ready-for-agent"}, self.github.issue["labels"])
        foreign.process_ticket(copy.deepcopy(stale), 1, False)
        self.assertEqual(self.launched, [])

    def test_pin_creation_failure_stays_nonready(self):
        self.fail_transition("create")

    def test_pin_application_failure_stays_nonready(self):
        self.fail_transition("pin")

    def test_audit_failure_stays_nonready(self):
        self.fail_transition("audit")


del Harness
