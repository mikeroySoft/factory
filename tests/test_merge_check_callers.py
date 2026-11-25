"""Runner starvation remains blocking at readiness and final merge admission."""
from __future__ import annotations

import unittest
from unittest import mock

from factory import dispatch, lifecycle, runtime_events
from tests import test_merge_checks


class MergeCheckCallersTest(unittest.TestCase):
    # Reuse the merge-admission fixture without inheriting its test methods.
    setUp = test_merge_checks.MergeChecksTest.setUp
    query = test_merge_checks.MergeChecksTest.query
    run_gh = test_merge_checks.MergeChecksTest.run_gh
    merged = test_merge_checks.MergeChecksTest.merged
    decision = test_merge_checks.MergeChecksTest.decision

    def journal_escalations(self):
        self.escalate.side_effect = lambda n, reason, *_args, **kw: dispatch.record(
            "escalate", ticket=n, reason=reason, code=kw["code"])

    def starve_on_recheck(self):
        def changing_checks(cmd, **kwargs):
            if cmd[:3] == ["gh", "pr", "checks"] and "--required" in cmd:
                self.required = [self.passing] if self.check_reads == 0 else [
                    self.passing, self.gpu]
            return self.run_gh(cmd, **kwargs)
        return mock.patch.object(dispatch, "run", side_effect=changing_checks)

    def test_required_starvation_is_pending_in_readiness_with_blocking_evidence(self):
        self.required = [self.passing, self.gpu]
        history = [{"event": "review-result", "pr": 70, "head": "head", "verdict": "APPROVE"}]
        with mock.patch.object(dispatch, "record") as record:
            dispatch.review_readiness(70, "head", history)
        readiness = record.call_args.kwargs
        self.assertEqual(readiness["state"], "ci_pending")
        self.assertEqual(readiness["checks"][1]["bucket"], "infra")
        self.assertTrue(readiness["checks"][1]["required"])
        self.assertEqual(readiness["checks"][1]["disposition"], "no matching runner")
        self.assertFalse(any(cmd[:3] == ["gh", "run", "rerun"] for cmd in self.commands))

    def test_new_required_runner_outage_at_final_recheck_escalates_and_blocks(self):
        self.journal_escalations()
        with self.starve_on_recheck():
            dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.escalate.assert_called_once()
        self.assertEqual(self.escalate.call_args.kwargs["code"], "ci_runner")
        self.assertIn("blocks merge", self.escalate.call_args.args[1])
        self.assertEqual(self.decision()["checks"][1]["bucket"], "infra")
        exits = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("kind") == "exit"]
        self.assertEqual(exits[-1]["outcome"], "project_escalation")
        self.assertFalse(any(cmd[:3] == ["gh", "run", "rerun"] for cmd in self.commands))
        self.assertFalse(any("--remove-label" in cmd for cmd in self.commands))

    def test_already_escalated_final_runner_outage_has_visible_eligibility_wait(self):
        self.journal_escalations()
        self.required = [self.passing, self.gpu]
        dispatch.merge_pass_locked(False)
        self.commands.clear()
        self.check_reads = 0
        with self.starve_on_recheck():
            dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.escalate.assert_called_once()
        events = lifecycle.read_events(dispatch.EVENTS)
        waits = [row["wait"] for row in events if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_runner")
        self.assertEqual(waits[-1]["mode"], "eligibility")
        exits = [row for row in events if row.get("kind") == "exit"]
        self.assertEqual((exits[-1]["outcome"], exits[-1]["reason"]), ("not_eligible", "ci_runner"))
        projected = runtime_events.project(dispatch.EVENTS)
        waits = [row["wait"] for row in projected["events"] if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_runner")
        self.assertFalse(any(cmd[:3] == ["gh", "run", "rerun"] for cmd in self.commands))

    def test_ordinary_pending_at_final_recheck_has_ci_pending_wait(self):
        self.job["runner_id"] = 1
        with self.starve_on_recheck():
            dispatch.merge_pass_locked(False)
        self.assertFalse(self.merged())
        self.escalate.assert_not_called()
        waits = [row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("kind") == "wait"]
        self.assertEqual(waits[-1]["reason"], "ci_pending")
        self.assertEqual(waits[-1]["mode"], "eligibility")
