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

    def test_stale_foreign_label_pins_until_human_removal(self):
        old = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        self.github.issue["assignees"] = []
        tank = self.dispatcher("tank")

        with self.refuse_host_label_removal(tank):
            for _ in range(2):
                [issue] = tank.frontier()
                self.assertEqual(issue["held"], "host label (ultron)")
                tank.process_ticket(issue, 1, False)

        self.assertIn(old, self.github.comments)
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
        self.assertEqual(self.launched, [])
        # A human explicitly authorizes the takeover by removing the pin.
        self.github.issue["labels"].remove({"name": "host:ultron"})
        [issue] = tank.frontier()
        self.assertNotIn("held", issue)
        with self.refuse_host_label_removal(tank):
            tank.process_ticket(issue, 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:tank"}])
        self.assertEqual([row["host"] for row in tank.host_claims(7)], ["tank"])
        released = [row for row in lifecycle.read_events(tank.EVENTS)
                    if row.get("event") == "claim-released"]
        self.assertEqual([(row["host"], row["last_heartbeat"]) for row in released],
                         [("ultron", old["created_at"])])

    def test_losing_contender_label_failure_requires_human_removal_for_takeover(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        read = ultron.gh_json
        raced = False

        def winner_after_loser_admission(args):
            nonlocal raced
            result = read(args)
            if "--slurp" in args and not raced:
                # Both hosts passed fresh admission before either wrote a claim.
                raced = True
                tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)
            return result

        with mock.patch.object(ultron, "gh_json", side_effect=winner_after_loser_admission), \
                self.refuse_host_label_removal(ultron), \
                self.assertRaises(subprocess.CalledProcessError):
            ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertTrue(raced)
        self.assertEqual(self.launched, ["tank"])
        self.assertEqual([row["host"] for row in tank.host_claims(7)], ["tank", "ultron"])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertIn("tank", ultron.frontier()[0]["held"])

        # The winner can release only its own label, not the abandoned loser's pin.
        tank.unclaim(7)
        thor = self.dispatcher("thor")
        with mock.patch.object(thor, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [issue] = thor.frontier()
            self.assertEqual(issue["held"], "host label (ultron)")
            thor.process_ticket(issue, 1, False)
            self.assertEqual(self.launched, ["tank"])
            self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])
            # A human removes the failed contender's pin before reassigning.
            self.github.issue["labels"].remove({"name": "host:ultron"})
            [issue] = thor.frontier()
            self.assertNotIn("held", issue)
            thor.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank", "thor"])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:thor"}])
        self.assertEqual([row["host"] for row in thor.host_claims(7)], ["thor"])

    def test_owner_unclaim_label_failure_requires_human_removal_for_foreign_requeue(self):
        tank = self.dispatcher("tank")
        tank.process_ticket(tank.frontier()[0], 1, False)
        [owned] = tank.host_claims(7)
        self.github.open_prs = [{"number": 70}]

        with self.refuse_host_label_removal(tank), self.assertRaises(subprocess.CalledProcessError):
            tank.unclaim(7, "--add-label", "ready-for-agent")

        self.assertEqual([row["id"] for row in tank.host_claims(7)], [owned["id"]])
        self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
        self.assertEqual(self.github.issue["assignees"], [])
        ultron = self.dispatcher("ultron")
        with mock.patch.object(ultron, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [issue] = ultron.frontier()
            self.assertEqual(issue["held"], "host label (tank)")
            ultron.process_ticket(issue, 1, False)
            self.assertEqual(self.launched, ["tank"])
            self.assertIn({"name": "host:tank"}, self.github.issue["labels"])
            # Requeue evidence does not authorize another host to remove the pin.
            self.github.issue["labels"].remove({"name": "host:tank"})
            [issue] = ultron.frontier()
            self.assertNotIn("held", issue)
            ultron.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank", "ultron"])
        self.assertEqual(self.github.issue["labels"],
                         [{"name": "ready-for-agent"}, {"name": "host:ultron"}])
        self.assertEqual([row["host"] for row in ultron.host_claims(7)], ["ultron"])

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
