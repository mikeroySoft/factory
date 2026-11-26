"""Interrupted publication recovery and durable human-assignment provenance."""
from __future__ import annotations

import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class DispatchReviewFollowup(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def expired_publication(self, host="tank"):
        claim = self.github.add_claim(host, datetime.now(UTC) - timedelta(hours=3))
        self.github.open_prs = [{"number": 70}]
        return claim

    def accept_comments(self, module):
        run = module.run

        def transport(command, *args, **kwargs):
            if command[:3] == ["gh", "issue", "comment"]:
                self.posted.append(command[command.index("--body") + 1])
                return subprocess.CompletedProcess(command, 0, "https://example.test/issues/7#issuecomment-1", "")
            return run(command, *args, **kwargs)

        self.posted = []
        self.enterContext(mock.patch.object(module, "run", side_effect=transport))

    def test_expired_own_publication_escalates_without_worker_or_push(self):
        claim = self.expired_publication()
        module = self.dispatcher("tank")
        self.accept_comments(module)
        [issue] = module.frontier()
        self.assertNotIn("held", issue)
        with mock.patch.object(module, "push_agent") as push:
            module.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, [])
        push.assert_not_called()
        self.assertEqual(self.github.open_prs, [{"number": 70}])
        self.assertEqual(self.github.issue["assignees"], [])
        labels = {row["name"] for row in self.github.issue["labels"]}
        self.assertIn("ready-for-human", labels)
        self.assertNotIn("ready-for-agent", labels)
        self.assertNotIn("host:tank", labels)
        self.assertEqual(self.github.comments, [])
        nonce = claim["body"].split("nonce=", 1)[1].split()[0]
        self.assertIn(f"Factory requeue: nonce={nonce}", self.posted[0])
        packet = (module.FACTORY / "escalations" / "7.md").read_text()
        self.assertIn("70", packet)
        self.assertIn("a" * 40, packet)

    def test_heartbeat_after_recovery_selection_restores_open_pr_hold(self):
        claim = self.expired_publication()
        module = self.dispatcher("tank")
        [issue] = module.frontier()
        self.assertNotIn("held", issue)
        claim["body"] = claim["body"].rsplit("at=", 1)[0] + f"at={datetime.now(UTC).isoformat()}"
        before = copy.deepcopy((self.github.issue, self.github.comments))
        module.process_ticket(issue, 1, False)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_live_foreign_completed_and_requeued_publications_remain_holds(self):
        module = self.dispatcher("tank")
        for mode in ("live", "foreign", "completed", "requeued"):
            with self.subTest(mode=mode):
                self.github.comments.clear()
                claim = self.expired_publication("ultron" if mode == "foreign" else "tank")
                nonce = claim["body"].split("nonce=", 1)[1].split()[0]
                if mode == "live":
                    claim["body"] = claim["body"].rsplit("at=", 1)[0] + f"at={datetime.now(UTC).isoformat()}"
                elif mode == "completed":
                    module.record("claimed", ticket=7, claim_nonce=nonce, execution_id="completed")
                    module.record("approved", ticket=7, execution_id="completed")
                elif mode == "requeued":
                    self.github.comments.append({
                        "id": 100, "body": f"Factory requeue: nonce={nonce}\nResume",
                        "created_at": datetime.now(UTC).isoformat(), "user": {"login": "@me"},
                    })
                before = copy.deepcopy((self.github.issue, self.github.comments))
                self.assertEqual(module.frontier()[0]["held"], "open agent PR #70")
                module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
                self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_human_bot_assignment_survives_persistent_cleanup_failure_and_new_process(self):
        module = self.dispatcher("tank")
        self.github.issue["assignees"] = [{"login": "@me"}]
        run = module.run

        def unavailable(command, *args, **kwargs):
            if "--remove-label" in command and "host:tank" in command:
                raise subprocess.CalledProcessError(1, command, stderr="host label unavailable")
            return run(command, *args, **kwargs)

        with mock.patch.object(module, "run", side_effect=unavailable), \
                mock.patch.object(module, "worker_round", side_effect=FileNotFoundError("worker")), \
                self.assertRaises(subprocess.CalledProcessError):
            module.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(len(self.github.comments), 1)
        (module.FACTORY / "wt-7").rmdir()  # Empty stand-in; next module is a distinct process state.
        recovered = self.dispatcher("tank")
        self.assertEqual(recovered._ASSIGNMENTS, {})
        with mock.patch.object(recovered, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(hours=3)
            self.assertEqual(recovered.frontier()[0]["held"], "assigned")
            recovered.unclaim(7, check=True, exit_path="recovery")
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.comments, [])
        self.assertFalse(any(row.get("event") == "assignee-cleared"
                             for row in lifecycle.read_events(recovered.EVENTS)))


if __name__ == "__main__":
    unittest.main()
