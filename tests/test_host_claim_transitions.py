"""Host cleanup failures and escalation provenance against shared GitHub state."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle, manage
from tests.test_host_claims import FakeGitHub
from tests.test_host_claims import HostClaimRaceTest as Harness


class TimelineGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.timeline = []
        self.manager_runs = 0

    def json(self, host, args):
        if args[:2] == ["api", "repos/local/claims/issues/7/timeline"]:
            return [copy.deepcopy(self.timeline)]
        return super().json(host, args)

    def run(self, host, cmd, *args, **kwargs):
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if cmd[0] == "fake-manager":
            self.manager_runs += 1
            return subprocess.CompletedProcess(cmd, 0, "DECISION: RETRY\nTry the worker again", "")
        if cmd[:3] == ["gh", "issue", "comment"]:
            comment = super().json(host, [
                "api", "repos/local/claims/issues/7/comments", "--method", "POST",
                "-f", f"body={cmd[cmd.index('--body') + 1]}",
            ])
            self.timeline.append({"event": "commented", "id": comment["id"],
                                  "created_at": at, "updated_at": at})
            url = f"https://github.com/local/claims/issues/7#issuecomment-{comment['id']}"
            return subprocess.CompletedProcess(cmd, 0, url, "")
        before = copy.deepcopy(self.issue)
        result = super().run(host, cmd, *args, **kwargs)
        if cmd[:3] == ["gh", "issue", "edit"]:
            for field, event in (("labels", "unlabeled"), ("assignees", "unassigned")):
                for row in before[field]:
                    if row not in self.issue[field]:
                        item = {"event": event, "created_at": at, "actor": {"login": "@me"}}
                        item["label" if field == "labels" else "assignee"] = row
                        self.timeline.append(item)
            for row in self.issue["labels"]:
                if row not in before["labels"]:
                    self.timeline.append({"event": "labeled", "label": row, "created_at": at})
        return result


class HostClaimTransitionTest(unittest.TestCase):
    setUp = Harness.setUp
    dispatcher = Harness.dispatcher
    launch = Harness.launch

    def test_failed_unassignment_retains_claim_for_ttl_recovery(self):
        tank = self.dispatcher("tank")
        claim = self.github.add_claim("tank", datetime.now(UTC))
        before = copy.deepcopy((self.github.issue, self.github.comments))
        run = tank.run

        def refuse_unassignment(cmd, *args, **kwargs):
            if "--remove-assignee" in cmd:
                result = subprocess.CompletedProcess(cmd, 1, "", "permission denied")
                if kwargs.get("check"):
                    result.check_returncode()
                return result
            return run(cmd, *args, **kwargs)

        for assignee_last in (False, True):
            with self.subTest(assignee_last=assignee_last), \
                    mock.patch.object(tank, "run", side_effect=refuse_unassignment):
                tank.unclaim(7, "--remove-label", "ready-for-agent", "--add-label", "ready-for-human",
                             assignee_last=assignee_last)
                self.assertEqual((self.github.issue, self.github.comments), before)
        with mock.patch.object(tank, "run", side_effect=refuse_unassignment), \
                self.assertRaises(subprocess.CalledProcessError):
            tank.unclaim(7, check=True)
        self.assertEqual((self.github.issue, self.github.comments), before)

        ultron = self.dispatcher("ultron")
        with mock.patch.object(ultron, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [issue] = ultron.frontier()
            self.assertNotIn("held", issue)
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["ultron"])
        self.assertNotIn(claim, self.github.comments)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}])

    def test_owner_escalation_reaches_manager_after_host_cleanup(self):
        self.github = TimelineGitHub()
        tank = self.dispatcher("tank")
        tank.cfg.manager = ["fake-manager", "{prompt}"]
        self.github.add_claim("old", datetime.now(UTC) - timedelta(hours=3))
        self.github.add_claim("tank", datetime.now(UTC))
        tank.escalate(7, "gate failed", None, code="gate_failed")
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-human"}])
        with mock.patch.object(manage, "dispatch", tank), \
                mock.patch.object(manage.handoff, "dispatch", tank):
            manage.escalation_pass()
        self.assertEqual(self.github.manager_runs, 1)
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        decisions = [row["decision"] for row in lifecycle.read_events(tank.EVENTS)
                     if row.get("event") == "manage"]
        self.assertEqual(decisions, ["RETRY"])

    def test_human_host_label_removals_still_block_manager(self):
        self.github = TimelineGitHub()
        tank = self.dispatcher("tank")
        self.github.add_claim("tank", datetime.now(UTC))
        tank.escalate(7, "gate failed", None, code="gate_failed")
        events = lifecycle.read_events(tank.EVENTS)
        escalation = next(row for row in events if row.get("event") == "escalate")
        original = copy.deepcopy(self.github.timeline)
        for label, before_marker in (("host:tank", False), ("host:tank", True), ("host:human", True)):
            with self.subTest(label=label, before_marker=before_marker):
                self.github.timeline = copy.deepcopy(original)
                activity = {"event": "unlabeled", "label": {"name": label},
                            "created_at": escalation["at"], "actor": {"login": "human"}}
                self.github.timeline.insert(-1 if before_marker else len(original), activity)
                with mock.patch.object(manage, "dispatch", tank), \
                        mock.patch.object(manage.handoff, "dispatch", tank):
                    self.assertTrue(manage.human_activity(7, escalation, events))


# Keep unittest discovery from collecting the imported harness twice.
del Harness
