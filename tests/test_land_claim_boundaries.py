"""Consumer boundaries for landing-claim takeover and recovery."""
from __future__ import annotations

import copy
import unittest
from unittest import mock

from factory import config, lifecycle
from tests import test_land_host as fixture


class LandClaimBoundaryTest(unittest.TestCase):
    setUp = fixture.LandHostTest.setUp
    dispatcher = fixture.LandHostTest.dispatcher
    claim = fixture.LandHostTest.claim

    def retain_claim(self, host: str, *, age: int = 0) -> dict:
        self.claim(host, age=age)
        self.comments[-1]["body"] += "\nFactory claim history: retained"
        return self.comments[-1]

    def test_only_live_land_pass_resumes_a_retained_landing_claim(self):
        for host, dry_run in (("tank", True), ("ultron", False)):
            with self.subTest(host=host, dry_run=dry_run):
                module = self.dispatcher(host)
                self.comments.clear()
                self.retain_claim(host, age=3 * 3600)
                before = copy.deepcopy(self.comments)
                self.commands[host].clear()
                self.reads[host].clear()

                with module.landing_host_claim(
                    9, None, dry_run=dry_run, pr=102, head=self.github.head(),
                ) as owned:
                    self.assertTrue(owned)

                self.assertEqual(self.comments, before)
                self.assertEqual(self.commands[host], [])
                self.assertEqual(self.reads[host], [])
                self.assertEqual(module._HOST_CLAIMS, {})

    def test_takeover_forces_reacquisition_without_ready_label_and_retains_history(self):
        tank = self.dispatcher("tank")
        self.github.issue_labels.clear()
        foreign_comment = copy.deepcopy(self.retain_claim("ultron", age=3 * 3600))
        foreign = tank.elected_host_claim(tank.host_claims(9))
        head = self.github.head()

        with tank.landing_host_claim(
            9, foreign, dry_run=False, pr=102, head=head,
        ) as owned:
            self.assertTrue(owned)
            local = tank.owned_host_claim(9)
            self.assertIsNotNone(local)
            self.assertEqual(local["host"], "tank")
            self.assertTrue(local["preserve_history"])

        self.assertIn(foreign_comment, self.comments)
        local_claims = [row for row in tank.host_claims(9) if row["host"] == "tank"]
        self.assertEqual(len(local_claims), 1)
        self.assertTrue(local_claims[0]["preserve_history"])
        [takeover] = [row for row in lifecycle.read_events(tank.EVENTS)
                      if row.get("event") == "claim-takeover"]
        self.assertEqual(
            (takeover["ticket"], takeover["pr"], takeover["head"], takeover["host"],
             takeover["nonce"], takeover["foreign_host"], takeover["foreign_nonce"]),
            (9, 102, head, "tank", local_claims[0]["nonce"], "ultron", "ultron-nonce"),
        )

    def test_recovery_requires_exact_takeover_identity_and_records_refresh_result(self):
        fixture.git(self.origin, "branch", "agent/1000", "agent/9")
        tank = self.dispatcher("tank")
        self.retain_claim("tank")
        recovery_claim = tank.owned_host_claim(1000)
        self.assertIsNotNone(recovery_claim)
        head = self.github.head()
        original_json = tank.gh_json.side_effect

        def ticket_1000(args):
            value = original_json(args)
            if args[:2] not in (["pr", "list"], ["pr", "view"]):
                return value

            def changed(row):
                return {**row, "number": 1002, "headRefName": "agent/1000"}

            return [changed(row) for row in value] if isinstance(value, list) else changed(value)

        exact = {
            "event": "claim-takeover",
            "ticket": int("1000"),
            "pr": int("1002"),
            "head": head,
            "host": "tank",
            "nonce": recovery_claim["nonce"],
        }
        cases = (
            ("event sorts lower", {**exact, "event": "aaa"}, False, "project_escalation"),
            ("event sorts higher", {**exact, "event": "zzz"}, False, "project_escalation"),
            ("ticket lower", {**exact, "ticket": 999}, False, "project_escalation"),
            ("ticket higher", {**exact, "ticket": 1001}, False, "project_escalation"),
            ("pull request lower", {**exact, "pr": 1001}, False, "project_escalation"),
            ("pull request higher", {**exact, "pr": 1003}, False, "project_escalation"),
            ("head lower", {**exact, "head": "0" * 40}, False, "project_escalation"),
            ("head higher", {**exact, "head": "f" * 40}, False, "project_escalation"),
            ("host lower", {**exact, "host": "aaa"}, False, "project_escalation"),
            ("host higher", {**exact, "host": "zzzz"}, False, "project_escalation"),
            ("nonce lower", {**exact, "nonce": "aaa"}, False, "project_escalation"),
            ("nonce higher", {**exact, "nonce": "zzz"}, False, "project_escalation"),
            ("exact, refresh refused", exact, False, "completed"),
            ("exact, refresh completed", exact, True, "refreshed"),
        )
        read_events = lifecycle.read_events

        for name, event, refreshed, expected_outcome in cases:
            with self.subTest(name=name), \
                    mock.patch.object(tank, "gh_json", side_effect=ticket_1000), \
                    mock.patch.object(lifecycle, "read_events", return_value=[event]), \
                    mock.patch.object(tank, "refresh_pr_branch", return_value=refreshed), \
                    mock.patch.object(tank, "escalate"):
                tank.merge_pass_locked(False)

            exits = [row for row in read_events(tank.EVENTS)
                     if row.get("event") == "lifecycle"
                     and row.get("stage") == "merge-eligibility"
                     and row.get("kind") == "exit"
                     and row.get("ticket") == 1000]
            self.assertEqual(exits[-1]["outcome"], expected_outcome)
            self.github.pr_labels.add(config.LABEL_APPROVED)

    def test_dry_run_previews_foreign_takeover_refresh_without_mutation(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        before = copy.deepcopy(self.comments)
        self.commands["tank"].clear()

        with mock.patch.object(tank, "log") as log:
            tank.merge_pass_locked(True)

        log.assert_any_call(
            "PR #102: would refresh (fresh local gate and review required after takeover)",
        )
        self.assertEqual(self.comments, before)
        self.assertFalse(self.github.merged)
        self.assertFalse(any(
            command[:3] in (["gh", "issue", "edit"], ["gh", "pr", "edit"],
                            ["gh", "pr", "merge"])
            or "--method" in command
            for command in self.commands["tank"]
        ))


if __name__ == "__main__":
    unittest.main()
