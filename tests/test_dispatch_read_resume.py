"""Retained dispatch claims recover without weakening assignment ownership."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from datetime import UTC, datetime
import sys
from unittest import mock

from factory import config, dispatch, lifecycle, reads
from tests import test_host_claims


class DispatchReadResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        test_host_claims.HostClaimRaceTest.setUp(self)

    dispatcher = test_host_claims.HostClaimRaceTest.dispatcher
    launch = test_host_claims.HostClaimRaceTest.launch

    @staticmethod
    def failure(stderr: str = "HTTP 500: provider unavailable") -> reads.ReadFailure:
        command = ["gh", "issue", "view", "7"]
        return reads.ReadFailure(
            "gh_read_failed",
            subprocess.CompletedProcess(command, 1, "", stderr),
        )

    def fail_claimed_worker_once(self, module) -> list[tuple]:
        attempts = []

        def worker(*args, **kwargs):
            attempts.append((args, kwargs))
            if len(attempts) == 1:
                self.remote_at_failure = copy.deepcopy((self.github.issue, self.github.comments))
                raise self.failure()
            return self.launch(module.cfg.host_name)

        self.enterContext(mock.patch.object(module, "worker_round", side_effect=worker))
        with reads.pass_scope(module.EVENTS, "dispatch"):
            module.process_ticket(module.frontier()[0], 1, False)
        return attempts

    def test_default_host_retained_assignment_resumes_on_next_pass(self) -> None:
        single = self.dispatcher(None)
        attempts = self.fail_claimed_worker_once(single)

        self.assertEqual((self.github.issue, self.github.comments), self.remote_at_failure)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])

        with reads.pass_scope(single.EVENTS, "dispatch"):
            single.ticket_pass(1, False)

        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.launched, [None])

    def test_retained_journal_does_not_resume_a_foreign_assignment(self) -> None:
        single = self.dispatcher(None)
        attempts = self.fail_claimed_worker_once(single)
        self.github.issue["assignees"] = [{"login": "other-owner"}]
        before = copy.deepcopy((self.github.issue, self.github.comments))

        with reads.pass_scope(single.EVENTS, "dispatch"):
            single.ticket_pass(1, False)

        self.assertEqual(len(attempts), 1)
        self.assertEqual((self.github.issue, self.github.comments), before)
        self.assertEqual(self.launched, [])

    def test_heartbeat_renews_with_dispatch_read_circuit_already_stopped(self) -> None:
        tank = self.dispatcher("tank", claim_ttl="1s")
        self.github.add_claim("tank", datetime.now(UTC))
        [owned] = tank.host_claims(7)
        original = tank.gh_json

        def classified_read(args):
            command = ["gh", *args]
            result = reads.run(
                command,
                lambda: subprocess.CompletedProcess(command, 0, json.dumps(original(args)), ""),
            )
            return json.loads(result.stdout)

        with reads.pass_scope(tank.EVENTS, "dispatch") as state, \
                mock.patch.object(tank, "gh_json", side_effect=classified_read):
            state.stopped = True
            state.code = "gh_read_failed"
            heartbeat = tank.HostClaim(7, owned)
            heartbeat.thread.start()
            self.addCleanup(heartbeat.stop)
            self.assertTrue(self.github.comment_patched.wait(timeout=0.7))

        self.assertEqual(self.github.patch_count, 1)

    def test_unchecked_dependency_error_never_reaches_claim_admission(self) -> None:
        issue = {
            "number": 7, "title": "Blocked status unavailable", "body": "", "state": "OPEN",
            "labels": [{"name": "ready-for-agent"}], "assignees": [], "comments": [],
            "createdAt": "2026-01-01T00:00:00Z",
        }
        cfg = config.Config(
            self.root / "admission",
            "local/claims",
            workers={"default": [sys.executable, "{prompt}"]},
        )
        dispatch.configure(cfg)

        for status in (500, 401):
            with self.subTest(status=status):
                before = copy.deepcopy(issue)
                commands = []

                def command(argv, *_args):
                    commands.append(argv)
                    if argv[1:3] == ["issue", "list"]:
                        return subprocess.CompletedProcess(argv, 0, json.dumps([issue]), "")
                    if argv[1:3] == ["api", "repos/local/claims/issues/7/dependencies/blocked_by"]:
                        return subprocess.CompletedProcess(
                            argv, 1, "", f"HTTP {status}: provider unavailable",
                        )
                    raise AssertionError(argv)

                with mock.patch.object(dispatch, "_run", side_effect=command), \
                        mock.patch.object(dispatch, "active_tickets", return_value=[]), \
                        mock.patch.object(dispatch, "process_ticket") as process, \
                        mock.patch.object(reads.subprocess, "run",
                                          return_value=subprocess.CompletedProcess([], 1, "", "")), \
                        reads.pass_scope(dispatch.EVENTS, "dispatch"):
                    dispatch.ticket_pass(1, False)

                process.assert_not_called()
                self.assertEqual(issue, before)
                self.assertFalse(any("--add-assignee" in argv or "--add-label" in argv
                                     for argv in commands))
                skips = [row for row in lifecycle.read_events(dispatch.EVENTS)
                         if row.get("event") == "read-skip" and row.get("ticket") == 7]
                self.assertEqual(skips[-1]["code"], "gh_read_failed")


if __name__ == "__main__":
    unittest.main()
