"""Failed label cleanup must retain enough evidence for another dispatch pass."""
import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class HostClaimRecoveryTest(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def refuse_host_label_removal(self, module):
        run = module.run

        def refuse(cmd, *args, **kwargs):
            if "--remove-label" in cmd and cmd[cmd.index("--remove-label") + 1].startswith("host:"):
                result = subprocess.CompletedProcess(cmd, 1, "", "temporary label failure")
                if kwargs.get("check", True):
                    result.check_returncode()
                return result
            return run(cmd, *args, **kwargs)

        return mock.patch.object(module, "run", side_effect=refuse)

    def test_stale_label_failure_is_recovered_by_next_dispatch_pass(self):
        old = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        self.github.issue["assignees"] = []
        tank = self.dispatcher("tank")

        with self.refuse_host_label_removal(tank), self.assertRaises(subprocess.CalledProcessError):
            tank.process_ticket(tank.frontier()[0], 1, False)

        self.assertIn(old, self.github.comments)
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
        self.assertEqual(self.launched, [])
        [issue] = tank.frontier()
        self.assertNotIn("held", issue)
        tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(tank.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])
        released = [row for row in lifecycle.read_events(tank.EVENTS)
                    if row.get("event") == "claim-released"]
        self.assertEqual([(row["host"], row["last_heartbeat"]) for row in released],
                         [("ultron", old["created_at"])])

    def test_losing_contender_label_failure_does_not_strand_foreign_label(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        read = ultron.gh_json
        raced = False

        def winner_after_loser_admission(args):
            nonlocal raced
            result = read(args)
            if "--slurp" in args and not raced:
                # Both hosts passed fresh admission before the elected claim appeared.
                raced = True
                self.github.add_claim("tank", datetime.now(UTC))
            return result

        with mock.patch.object(ultron, "gh_json", side_effect=winner_after_loser_admission), \
                self.refuse_host_label_removal(ultron), \
                self.assertRaises(subprocess.CalledProcessError):
            ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertTrue(raced)
        self.assertEqual(self.launched, [])
        self.assertEqual([row["host"] for row in tank.host_claims(7)], ["tank", "ultron"])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertIn("tank", ultron.frontier()[0]["held"])

        # The elected claim releases normally. A later host recovers the abandoned loser.
        tank.unclaim(7)
        thor = self.dispatcher("thor")
        with mock.patch.object(thor, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [issue] = thor.frontier()
            self.assertNotIn("held", issue)
            thor.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["thor"])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(thor.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])

    def test_owner_unclaim_label_failure_is_recovered_after_open_pr_closes(self):
        tank = self.dispatcher("tank")
        owned = self.github.add_claim("tank", datetime.now(UTC))
        self.github.open_prs = [{"number": 70}]

        with self.refuse_host_label_removal(tank), self.assertRaises(subprocess.CalledProcessError):
            tank.unclaim(7, "--add-label", "ready-for-agent")

        self.assertEqual([row["id"] for row in tank.host_claims(7)], [owned["id"]])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        self.assertEqual(self.github.issue["assignees"], [])
        ultron = self.dispatcher("ultron")
        self.assertEqual(
            ultron.frontier()[0].get("held"),
            "open agent PR #70 (awaiting merge or closure)",
        )
        self.github.open_prs = []
        with mock.patch.object(ultron, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [issue] = ultron.frontier()
            self.assertNotIn("held", issue)
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["ultron"])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(ultron.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])

    def test_owner_cleanup_preserves_same_host_contenders_label(self):
        tank = self.dispatcher("tank")
        first = self.github.add_claim("tank", datetime.now(UTC))
        contender = self.github.add_claim("tank", datetime.now(UTC))

        tank.unclaim(7)

        self.assertNotIn(first, self.github.comments)
        self.assertEqual(self.github.comments, [contender])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        ultron = self.dispatcher("ultron")
        self.assertIn("tank", ultron.frontier()[0]["held"])
