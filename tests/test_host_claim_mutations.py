"""Observable ownership and lifecycle boundaries from the PR mutation report."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, lifecycle, manage
from tests import test_host_claim_transitions, test_host_claims, test_host_config


class HostClaimMutationTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def test_already_deleted_comment_does_not_confuse_later_foreign_claim(self):
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        foreign = self.github.add_claim("ultron", datetime.now(UTC))
        run = tank.run

        def delete_with_lost_response(cmd, *args, **kwargs):
            result = run(cmd, *args, **kwargs)
            if "DELETE" in cmd:
                # Another request already deleted this ID, not the higher ID.
                return subprocess.CompletedProcess(cmd, 1, "", "HTTP 404")
            return result

        with mock.patch.object(tank, "run", side_effect=delete_with_lost_response):
            tank.unclaim(7)

        self.assertEqual(self.github.comments, [foreign])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}])
        self.assertIn("ultron", tank.frontier()[0]["held"])

    def test_stale_cleanup_requires_exact_replacement_not_a_higher_comment_id(self):
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=4))
        self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        before = copy.deepcopy((self.github.issue, self.github.comments))

        tank.release_stale_claims(7, copy.deepcopy(self.github.issue),
                                 tank.host_claims(7), replacement=self.github.next_id + 1)

        self.assertEqual((self.github.issue, self.github.comments), before)

    def test_heartbeat_exits_cleanly_when_its_comment_has_been_released(self):
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        [comment] = tank.host_claims(7)
        heartbeat = tank.HostClaim(7, comment)
        tank.unclaim(7)

        # Deliver one scheduled heartbeat synchronously so a thread exception
        # cannot silently pass unittest. Ownership reads and PATCH remain real.
        with mock.patch.object(heartbeat.stopped, "wait", side_effect=[False, True]):
            heartbeat.heartbeat()

        self.assertEqual(self.github.patch_count, 0)
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.issue["assignees"], [])

    def test_single_host_refresh_gate_requeue_preserves_queue_and_posts_reason(self):
        single = self.dispatcher(None)
        read, run = single.gh_json, single.run
        self.github.issue["labels"] = [{"name": "ready-for-human"}]
        self.github.issue["assignees"] = [{"login": "@me"}]

        def github(args):
            if args[:2] == ["pr", "view"]:
                return {"baseRefName": single.cfg.main}
            return read(args)

        def command(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "pr", "edit"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "git":
                return subprocess.CompletedProcess(
                    cmd, int(cmd[1] == "merge-base"),
                    "a" * 40 if cmd[1] == "rev-parse" else "", "",
                )
            result = run(cmd, *args, **kwargs)
            self.assertTrue({"ready-for-agent", "ready-for-human", "factory-held"} & {
                row["name"] for row in self.github.issue["labels"]
            })
            return result

        with mock.patch.object(single, "gh_json", side_effect=github), \
                mock.patch.object(single, "run", side_effect=command), \
                mock.patch.object(single, "run_gate", return_value=(False, "- test: FAIL")), \
                mock.patch.object(single, "_refresh_failure_scope", return_value=(["app.py"], False)):
            self.assertFalse(single.refresh_pr_branch(7, 70, False))

        edits = [args for _, args in self.github.calls if args[:3] == ["gh", "issue", "edit"]]
        added = next(i for i, cmd in enumerate(edits) if "--add-label" in cmd)
        removed = [i for i, cmd in enumerate(edits) if "--remove-label" in cmd]
        self.assertTrue(removed)
        self.assertTrue(all(added < i for i in removed))
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(len(self.github.comments), 1)
        self.assertIn("gate failed twice", self.github.comments[0]["body"])
        self.assertFalse(any("user" in args or "--slurp" in args for _, args in self.github.calls))


class HostClaimManagerMutationTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def escalation(self):
        self.github = test_host_claim_transitions.TimelineGitHub()
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        tank.escalate(7, "gate failed", None, code="gate_failed")
        return tank

    def test_immediately_previous_cleanup_receipt_cannot_mask_human_activity(self):
        tank = self.escalation()
        original = lifecycle.read_events(tank.EVENTS)
        escalation = next(row for row in original if row.get("event") == "escalate")
        self.github.timeline.insert(-1, {
            "event": "unlabeled", "label": {"name": "host:retired"},
            "created_at": escalation["at"], "actor": {"login": "@me"},
        })

        # Journal history length must not change the generation boundary.
        for history in ([], [{"event": "claimed", "ticket": 7}]):
            events = [*history, *copy.deepcopy(original)]
            events.insert(events.index(escalation), {
                "event": "claim-label-removed", "ticket": 7,
                "label": "host:retired", "at": escalation["at"],
            })
            with self.subTest(history=len(history)), \
                    mock.patch.object(manage, "dispatch", tank), \
                    mock.patch.object(manage.handoff, "dispatch", tank):
                self.assertTrue(manage.human_activity(7, escalation, events))

    def test_other_journal_events_are_not_host_cleanup_receipts(self):
        tank = self.escalation()
        tank.record("attempt", ticket=7, attempt=2, gate="FAIL")
        events = lifecycle.read_events(tank.EVENTS)
        escalation = next(row for row in events if row.get("event") == "escalate")

        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(manage.handoff, "dispatch", tank):
            self.assertFalse(manage.human_activity(7, escalation, events))


class HostClaimDoctorMutationTest(unittest.TestCase):
    doctor_rows = test_host_config.HostConfigTest.doctor_rows
    setUp = test_host_claims.HostClaimRaceTest.setUp

    def test_host_settings_are_information_not_health_checks(self):
        cfg = config.Config(self.root, "local/claims", host_name="tank", triage_host="ultron")

        rows = self.doctor_rows(cfg, {"defaults": {"host_name": "tank", "triage_host": "ultron"}})

        for field in ("host_name", "triage_host", "claim_ttl"):
            with self.subTest(field=field):
                self.assertEqual(rows[field]["status"], "INFO")
