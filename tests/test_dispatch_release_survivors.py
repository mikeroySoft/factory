"""Consumer-visible cleanup and admission boundaries from the mutation report."""
from __future__ import annotations

import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle
from tests import test_host_claims


class DispatchReleaseSurvivors(unittest.TestCase):
    setUp = test_host_claims.HostClaimRaceTest.setUp
    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    def add_claim(self, host: str, nonce: str):
        at = datetime.now(UTC)
        claim = self.github.add_claim(host, at)
        claim["body"] = f"Factory claim: host={host} nonce={nonce} at={at.isoformat()}"
        return claim

    def test_stale_claim_does_not_hide_a_lower_sorting_human_assignment(self):
        claim = self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3))
        claim["user"] = {"login": "zz-factory-bot"}
        self.github.issue["assignees"] = [{"login": "alice"}]
        tank = self.dispatcher("tank", claim_ttl="1s")

        with mock.patch.object(tank, "host_claim_login", return_value="zz-factory-bot"):
            [issue] = tank.frontier()
            self.assertEqual(issue.get("held"), "assigned")
            before = copy.deepcopy((self.github.issue, self.github.comments))
            tank.process_ticket(issue, 1, False)

        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_unowned_label_cleanup_failure_is_best_effort_by_default(self):
        single = self.dispatcher(None)
        run = single.run

        def fail_label_cleanup(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "edit"] and "--remove-label" in cmd:
                result = subprocess.CompletedProcess(cmd, 1, "", "label cleanup unavailable")
                if kwargs.get("check", True):
                    result.check_returncode()
                return result
            return run(cmd, *args, **kwargs)

        with mock.patch.object(single, "run", side_effect=fail_label_cleanup):
            single.unclaim(7, "--remove-label", "ready-for-agent")

        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])
        self.assertFalse(any(row.get("event") == "assignee-cleared"
                             for row in lifecycle.read_events(single.EVENTS)))

    def test_replacement_nonce_in_either_lexical_direction_keeps_its_assignment(self):
        for replacement_nonce in ("aaa-replacement", "zzz-replacement"):
            with self.subTest(replacement_nonce=replacement_nonce):
                self.setUp()
                self.add_claim("tank", "mmm-original")
                tank = self.dispatcher("tank")
                replacement = None

                def replace_owner(n, fresh, claims, *, replacement_nonce=replacement_nonce, **kwargs):
                    nonlocal replacement
                    self.github.comments.clear()
                    self.github.issue["labels"] = [{"name": "ready-for-agent"}]
                    self.github.issue["assignees"] = []
                    replacement = self.add_claim("tank", replacement_nonce)
                    return fresh

                with mock.patch.object(tank, "release_stale_claims", side_effect=replace_owner):
                    tank.unclaim(7)

                self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
                self.assertEqual(self.github.comments, [replacement])
                self.assertFalse(any(row.get("event") == "assignee-cleared"
                                     for row in lifecycle.read_events(tank.EVENTS)))

    def test_reread_equal_value_nonce_retains_current_ownership(self):
        self.add_claim("tank", "same-long-nonce-value-not-interned")
        tank = self.dispatcher("tank")

        tank.unclaim(7)

        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.comments, [])
        cleared = [row for row in lifecycle.read_events(tank.EVENTS)
                   if row.get("event") == "assignee-cleared"]
        self.assertEqual([(row["ticket"], row["login"]) for row in cleared], [(7, "@me")])

    def test_foreign_assignees_on_both_sides_of_login_never_journal_a_clear(self):
        single = self.dispatcher(None)
        self.github.issue["assignees"] = [{"login": "alice"}, {"login": "zoe"}]
        single._ASSIGNMENTS[7] = "m-factory-bot"

        single.unclaim(7)

        self.assertEqual(self.github.issue["assignees"],
                         [{"login": "alice"}, {"login": "zoe"}])
        self.assertFalse(any(row.get("event") == "assignee-cleared"
                             for row in lifecycle.read_events(single.EVENTS)))

    def test_forced_run_removes_its_high_sorting_assignment_but_keeps_the_human(self):
        single = self.dispatcher(None)
        self.github.issue["assignees"] = [{"login": "alice"}]
        run = single.run

        def resolve_at_me(cmd, *args, **kwargs):
            cmd = list(cmd)
            if cmd[:3] == ["gh", "issue", "edit"]:
                for flag in ("--add-assignee", "--remove-assignee"):
                    if flag in cmd and cmd[cmd.index(flag) + 1] == "@me":
                        cmd[cmd.index(flag) + 1] = "zz-factory-bot"
            return run(cmd, *args, **kwargs)

        with mock.patch.object(single, "host_claim_login", return_value="zz-factory-bot"), \
                mock.patch.object(single, "run", side_effect=resolve_at_me):
            single.process_ticket(copy.deepcopy(self.github.issue), 1, False, forced=True)

        self.assertEqual(self.github.issue["assignees"], [{"login": "alice"}])
        self.assertFalse(single.lock_held(single.ticket_lock(7)))
        cleared = [row for row in lifecycle.read_events(single.EVENTS)
                   if row.get("event") == "assignee-cleared"]
        self.assertEqual([(row["login"], row["exit_path"]) for row in cleared],
                         [("zz-factory-bot", "done")])


if __name__ == "__main__":
    unittest.main()
