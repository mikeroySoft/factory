"""Failed label cleanup must retain enough evidence for another dispatch pass."""
import copy
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_losing_contender_label_failure_is_recovered_after_live_winner_exits(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        read = ultron.gh_json
        started, release = threading.Event(), threading.Event()
        pool = self.enterContext(ThreadPoolExecutor(max_workers=1))
        future = None

        def blocked_worker(*args, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("winner was not released")
            return self.launch("tank")

        def winner_after_loser_admission(args):
            nonlocal future
            result = read(args)
            if "--slurp" in args and future is None:
                future = pool.submit(tank.process_ticket, copy.deepcopy(self.github.issue), 1, False)
                self.assertTrue(started.wait(timeout=2))
            return result

        try:
            with mock.patch.object(tank, "worker_round", side_effect=blocked_worker), \
                    mock.patch.object(ultron, "gh_json", side_effect=winner_after_loser_admission), \
                    self.refuse_host_label_removal(ultron), \
                    self.assertRaises(subprocess.CalledProcessError):
                ultron.process_ticket(copy.deepcopy(self.github.issue), 1, False)
            self.assertEqual([row["host"] for row in tank.host_claims(7)], ["tank", "ultron"])
            self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        finally:
            release.set()
        future.result(timeout=5)

        self.assertEqual(self.launched, ["tank"])
        self.assertEqual([row["host"] for row in tank.host_claims(7)], ["ultron"])
        self.github.comments[0]["body"] = (
            self.github.comments[0]["body"].rsplit("at=", 1)[0]
            + f"at={(datetime.now(UTC) - timedelta(hours=3)).isoformat()}"
        )
        self.github.open_prs = []
        thor = self.dispatcher("thor")
        with mock.patch.object(thor, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            [issue] = thor.frontier()
            self.assertNotIn("held", issue)
            thor.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank", "thor"])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertEqual(thor.host_claims(7), [])
        self.assertEqual(self.github.issue["assignees"], [])

    def test_owner_unclaim_label_failure_is_recovered_on_foreign_requeue(self):
        tank = self.dispatcher("tank")
        owned = self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        nonce = owned["body"].split("nonce=", 1)[1].split()[0]
        tank._HOST_CLAIMS[7] = tank.HostClaim(7, {**owned, "nonce": nonce})
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
