"""Deferred review rework crosses hosts without losing ownership or findings."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, dispatch
from tests import test_host_claims


class HostClaimReworkTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp

    def configure(self, host):
        dispatch.configure(config.Config(self.root / host, "local/claims", host_name=host))
        # This fixture isolates claim-safe transport/cleanup below run admission.
        # Revoked rework authorization is exercised in test_refresh_revocation.
        self.enterContext(mock.patch.object(dispatch, "require_ticket_claim"))
        self.enterContext(mock.patch.object(dispatch, "_HOST_CLAIMS", {}))
        self.enterContext(mock.patch.object(dispatch, "gh_json",
                                           side_effect=lambda args: self.github.json(host, args)))
        self.enterContext(mock.patch.object(dispatch, "run",
                                           side_effect=lambda cmd, *a, **kw: self.github.run(host, cmd, *a, **kw)))
        dispatch.host_claim_login.cache_clear()
        self.addCleanup(dispatch.host_claim_login.cache_clear)

    def test_owner_deferred_rework_releases_claim_and_shares_findings(self):
        self.configure("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        self.github.issue["labels"].append({"name": "ready-for-human"})
        self.github.open_prs = [{"number": 70}]

        dispatch._record_rework(7, "a" * 40, "Resolve shared.py conflict", deferred=True)

        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(dispatch.host_claims(7), [])
        self.assertIn("Resolve shared.py conflict", dispatch.host_claim_state(7)[1])

    def test_foreign_deferred_rework_keeps_live_owner_then_resumes_after_expiry(self):
        self.configure("ultron")
        old = self.github.add_claim("tank", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]
        self.github.issue["labels"].append({"name": "ready-for-human"})
        before_claim = copy.deepcopy(old)

        dispatch._record_rework(7, "a" * 40, "Resolve shared.py conflict", deferred=True)

        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(dispatch.host_claim_hold(7, self.github.issue, dispatch.host_claims(7)),
                         "host claim (tank)")
        self.assertIn(before_claim, self.github.comments)
        self.assertNotIn({"name": "ready-for-human"}, self.github.issue["labels"])
        with mock.patch.object(dispatch, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            claims, context = dispatch.host_claim_state(7)
            self.assertIsNone(dispatch.host_claim_hold(7, self.github.issue, claims))
            self.assertIn("Resolve shared.py conflict", context)

    def test_deferred_rework_edit_failure_retains_claim_for_recovery(self):
        self.configure("tank")
        old = self.github.add_claim("tank", datetime.now(UTC))
        self.github.issue["labels"].append({"name": "ready-for-human"})
        run = dispatch.run

        def fail_assignment_edit(cmd, *args, **kwargs):
            if "--remove-assignee" in cmd:
                result = subprocess.CompletedProcess(cmd, 1, "", "temporary failure")
                if kwargs.get("check", True):
                    result.check_returncode()
                return result
            return run(cmd, *args, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=fail_assignment_edit), \
                self.assertRaises(subprocess.CalledProcessError):
            dispatch._record_rework(7, "a" * 40, "Resolve shared.py conflict", deferred=True)

        self.assertIn(old, self.github.comments)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
