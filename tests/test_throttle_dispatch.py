"""Modes affect real admission, review bounces and manager limits, not global config."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, handoff, lifecycle, manage, throttle, trace
from tests import test_risk_merge as pipeline
from tests.test_schedule import REPO, ticket
from tests.test_trace import decision


class SchedulingHoldTraceTest(unittest.TestCase):
    AT = "2026-10-23T10:00:00Z"

    def test_scheduling_holds_preserve_pipeline_frontier_and_coverage(self):
        triage = decision(
            7, "triage", "ready-for-agent", self.AT,
            causes=[{"ticket": 3, "relation": "blocked_by"}],
        )
        for stage, chosen, expected in (
            ("binding", "unbound", "blocker"),
            ("claim", "claimed", "plan"),
            ("review", "APPROVE", "merge eligibility"),
            ("merge", "ci-infra", "merge eligibility"),
            ("merge", "merge now", None),
        ):
            rows = [triage, decision(7, stage, chosen, self.AT)]
            baseline = trace.explain(rows, 7)
            for mode, reason in (
                ("normal", "assigned"),
                ("normal", "blocked by #3"),
                ("maintenance", "maintenance mode: not a bug/chore ticket"),
            ):
                with self.subTest(stage=stage, chosen=chosen, mode=mode, reason=reason):
                    hold = {
                        "event": "scheduling", "mode": mode, "at": self.AT,
                        "explained": [{"ticket": 7, "decision": "held", "reason": reason}],
                    }
                    graph = trace.explain([*rows, hold], 7)
                    self.assertEqual(graph["frontier_stage"], expected)
                    self.assertEqual(graph["frontier"], baseline["frontier"])
                    self.assertEqual(graph["coverage"], baseline["coverage"])
                    self.assertEqual(graph["edges"], baseline["edges"])
                    self.assertIn(reason, graph["nodes"][-1]["rationale"])

    def test_repeated_holds_keep_latest_reason_without_evicting_decisions(self):
        review = decision(7, "review", "APPROVE", self.AT)
        holds = [
            {
                "event": "scheduling", "mode": "normal", "at": self.AT,
                "explained": [{"ticket": 7, "decision": "held", "reason": f"assigned pass {index}"}],
            }
            for index in range(100)
        ]
        with patch.object(trace.runtime_events, "BYTE_LIMIT", 4096):
            graph = trace.explain([review, *holds], 7)
        self.assertIn(review, graph["nodes"])
        self.assertEqual(graph["frontier"], review["decision_id"])
        self.assertEqual(graph["frontier_stage"], "merge eligibility")
        self.assertEqual(
            [node["rationale"] for node in graph["nodes"] if node["chosen"] == "held"],
            ["normal: assigned pass 99"],
        )


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        pipeline.isolate_journal(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.cfg = config.Config(Path(temp.name), REPO, max_active=2)
        dispatch.configure(self.cfg)

    def state(self, mode, feature=None):
        self.cfg.factory.mkdir(exist_ok=True)
        (self.cfg.factory / "mode.json").write_text(json.dumps({"mode": mode, "feature": feature}))

    def test_maintenance_claims_only_maintenance_and_explains_held_ticket(self):
        self.state("maintenance")
        self.cfg.throttle = config.throttle_settings({"pace": {"low": {"max_active": 2}}})
        rows = [ticket(3, "1", labels=("enhancement",)), ticket(1, "2", labels=("bug",)),
                ticket(2, "3", labels=("chore",))]
        with patch.object(dispatch, "frontier", return_value=rows), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(90, False)
        self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [2, 1])
        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(all(row["mode"] == "maintenance" for row in events if row["event"] == "scheduling"))
        graph = trace.explain(events, 3)
        self.assertEqual(graph["nodes"][-1]["chosen"], "held")
        self.assertIn("maintenance mode: not a bug/chore ticket", graph["nodes"][-1]["rationale"])
        self.assertEqual(rows[0]["labels"], [{"name": "enhancement"}])

    def test_feature_reservation_survives_per_pick_ranking(self):
        self.state("feature-drive", 9)
        rows = [ticket(10, "1", body="unbound"), ticket(11, "2", body="unbound"), ticket(3, "3")]
        dispatch.record("issue-created", ticket=10, parent=9)
        dispatch.record("issue-created", ticket=11, parent=9)
        with patch.object(dispatch, "frontier", return_value=rows), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(90, False)
        self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [10, 3])
        self.assertTrue(process.call_args_list[0].args[0]["_factory_feature_drive"])
        self.assertFalse(process.call_args_list[1].args[0]["_factory_feature_drive"])

    def test_low_capacity_is_clamped_and_zero_does_not_claim(self):
        self.state("maintenance")
        self.cfg.throttle = config.throttle_settings({"pace": {"low": {"max_active": 3}}})
        self.cfg.manager_max_active_cap = 1
        with patch.object(dispatch, "frontier", return_value=[ticket(1, "1", labels=("bug",))]), \
                patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(90, False)
        self.assertEqual(process.call_count, 1)
        self.cfg.throttle["pace"]["low"]["max_active"] = 0
        with patch.object(dispatch, "frontier") as frontier, patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(90, False)
        frontier.assert_not_called()
        process.assert_not_called()

    def test_zero_pace_still_runs_open_work_stages_and_preserves_pause(self):
        self.cfg.throttle = config.throttle_settings({"pace": {"normal": {"max_active": 0}}})
        self.cfg.factory.mkdir(exist_ok=True)
        pause = self.cfg.factory / "pause.json"
        pause.write_text('{"paused": false}')
        with patch.object(config, "load", return_value=self.cfg), \
                patch.object(dispatch, "pin_root", return_value=[]), \
                patch.object(dispatch, "github_preflight", return_value=None), \
                patch.object(dispatch, "_sweep_stale_factory_tempdirs"), \
                patch.object(config, "engine_commit", return_value=None), \
                patch.object(config, "engine_target", return_value=None), \
                patch.object(dispatch, "worker_health_pass"), \
                patch("factory.results.prune", return_value={"status": "complete"}), \
                patch.object(dispatch, "land_pass") as land, \
                patch.object(dispatch, "review_intake_pass") as review, \
                patch.object(manage, "manage_pass") as manager, \
                patch("factory.learn.learn_pass"), \
                patch.object(dispatch, "process_ticket") as process:
            self.assertEqual(dispatch.main([]), 0)
        land.assert_called_once_with(False)
        review.assert_called_once_with(False)
        manager.assert_called_once_with(False)
        process.assert_not_called()
        self.assertEqual(pause.read_text(), '{"paused": false}')


class AggressionTest(unittest.TestCase):
    def setUp(self):
        pipeline.isolate_journal(self)
        pipeline.HighPipelineBoundaryTest.setUp(self)

    patch = pipeline.HighPipelineBoundaryTest.patch
    github = pipeline.HighPipelineBoundaryTest.github
    command_result = pipeline.HighPipelineBoundaryTest.command_result
    work = pipeline.HighPipelineBoundaryTest.work

    def test_real_review_loop_uses_feature_limits_without_leaking(self):
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        (self.cfg.factory / "mode.json").write_text('{"mode":"feature-drive","feature":7}')
        self.patch(dispatch.risk, "classify", return_value=("Medium", []))
        self.patch(dispatch, "run_gate", return_value=(True, "- test: PASS\n"))
        finding = '```json\n' + json.dumps({"verdict": "REVISE", "findings": [{
            "severity": "important", "pass": "bug", "path": "factory/dispatch.py", "line": 10,
            "summary": "Missing boundary check"}]}) + '\n```\nVERDICT: REVISE'
        self.patch(dispatch, "review", return_value=("REVISE", finding))
        self.patch(dispatch, "_actionable_review", return_value=True)
        self.patch(dispatch, "_review_rounds_used", return_value=0)
        self.patch(dispatch.time, "monotonic", return_value=0)
        for number, rounds, budget in ((7, 5, 150), (8, 1, 90)):
            with self.subTest(number=number):
                self.worker.reset_mock()
                self.issue["number"] = number
                dispatch.process_ticket(self.issue, self.cfg.budget_min, False, forced=True)
                self.assertEqual(self.worker.call_count, rounds + 1)
                self.assertEqual(self.worker.call_args_list[0].args[6], budget * 60)
        self.assertEqual((self.cfg.review_rounds, self.cfg.budget_min), (1, 90))

    def test_worker_attempt_limit_is_ticket_local(self):
        (self.cfg.factory / "mode.json").write_text('{"mode":"feature-drive","feature":7}')
        self.worker.side_effect = None
        self.worker.return_value = (False, "- test: FAIL\n", self.root / "worker.log", pipeline.HEAD)
        for number, attempts in ((7, 4), (8, 3)):
            self.worker.reset_mock()
            self.issue["number"] = number
            dispatch.process_ticket(self.issue, self.cfg.budget_min, False, forced=True)
            self.assertEqual(self.worker.call_count, attempts)
            self.assertTrue(self.worker.call_args.kwargs["fixups"])

    def test_stale_maintenance_labels_are_rechecked_before_claim(self):
        (self.cfg.factory / "mode.json").write_text('{"mode":"maintenance","feature":null}')
        dispatch.process_ticket(self.issue, self.cfg.budget_min, False, forced=True)
        self.worker.assert_not_called()
        self.assertFalse(any(
            call.args[0][:3] == ["gh", "issue", "edit"] and "--add-assignee" in call.args[0]
            for call in self.command.call_args_list
        ))

    def test_explicit_ticket_does_not_bypass_zero_pace(self):
        self.cfg.throttle = config.throttle_settings({"pace": {"normal": {"max_active": 0}}})
        dispatch.process_ticket(self.issue, self.cfg.budget_min, False, forced=True)
        self.worker.assert_not_called()


class ManagerLimitTest(unittest.TestCase):
    def test_same_frontier_uses_feature_only_manager_rounds(self):
        pipeline.isolate_journal(self)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.Config(Path(tmp), REPO, manager=["manager"], manager_rounds=1, manager_review="all")
            dispatch.configure(cfg)
            cfg.factory.mkdir()
            (cfg.factory / "mode.json").write_text('{"mode":"feature-drive","feature":7}')
            prs = [{"number": 70 + n, "headRefName": f"agent/{n}", "baseRefName": "main",
                    "isCrossRepository": False, "isDraft": False, "reviewDecision": "",
                    "headRefOid": "new", "updatedAt": "2999-01-01T00:00:00Z", "labels": []} for n in (7, 8)]
            for n in (7, 8):
                dispatch.record("manage", ticket=n, pr=70 + n, head="old", round=1)
            def github(args):
                if args[:2] == ["pr", "list"]:
                    return prs
                return {"number": int(args[2]), "state": "OPEN", "labels": [], "title": "ticket", "body": ""}
            with patch.object(dispatch, "gh_json", side_effect=github), \
                    patch.object(manage, "observe_pr", return_value={"owner": {"relation": "factory_issue"}}), \
                    patch.object(manage, "undelivered", return_value=[]), \
                    patch.object(dispatch, "pr_checks", return_value=[]), \
                    patch.object(dispatch, "_head_evidence_matches", return_value=True), \
                    patch.object(dispatch, "manager_approval", return_value=False), \
                    patch.object(dispatch, "log") as log:
                manage.frontier_pass(dry_run=True)
            messages = [call.args[0] for call in log.call_args_list]
            self.assertTrue(any("#77: would request manager approval" in row for row in messages))
            self.assertTrue(any("#78: would escalate (manager rounds exhausted)" in row for row in messages))
            packet = cfg.factory / "packet.md"
            packet.write_text("packet")
            escalation = {"round": 2, "packet": str(packet), "at": "0"}
            self.assertIsNone(handoff.terminal(throttle.effective(cfg, 7),
                                              [{"event": "comment", "kind": "escalation", "at": "1"}], escalation))
            self.assertIn("exhausted", handoff.terminal(throttle.effective(cfg, 8), [], escalation))


class RefreshFailureIsolationTest(unittest.TestCase):
    def test_refresh_errors_preserve_mode_and_continue_dispatch_stages(self):
        import subprocess

        pipeline.isolate_journal(self)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), REPO, release_auto=True)
            throttle.set_mode(cfg, "feature-drive", 9, who="alice")
            before = (cfg.factory / "mode.json").read_bytes()
            foreign = [[{
                "number": 12, "repository_url": "https://api.github.com/repos/acme/other",
            }]]
            stages = []
            for failure in (
                config.ConfigError("GitHub unavailable"),
                subprocess.TimeoutExpired(["gh", "api"], 30),
                OSError("cannot execute gh"),
                foreign,
            ):
                with self.subTest(failure=failure):
                    stages.clear()

                    def github(_cfg, endpoint, failure=failure, **_kwargs):
                        if isinstance(failure, BaseException):
                            raise failure
                        return foreign if "/sub_issues" in endpoint else {"number": 9, "state": "open"}

                    with patch.object(config, "load", return_value=cfg), \
                            patch.object(dispatch, "pin_root", return_value=[]), \
                            patch.object(dispatch, "github_preflight", return_value=None), \
                            patch.object(dispatch, "_sweep_stale_factory_tempdirs"), \
                            patch.object(config, "engine_commit", return_value=None), \
                            patch.object(config, "engine_target", return_value=None), \
                            patch.object(dispatch, "worker_health_pass"), \
                            patch("factory.results.prune", return_value={"status": "complete"}), \
                            patch.object(throttle, "_github", side_effect=github), \
                            patch("factory.release.run", side_effect=lambda *_a, **kw: stages.append(
                                "release-open" if kw["open_pr"] else "release-land")), \
                            patch.object(dispatch, "land_pass", side_effect=lambda _: stages.append("land")), \
                            patch.object(dispatch, "review_intake_pass", side_effect=lambda _: stages.append("review")), \
                            patch.object(manage, "manage_pass", side_effect=lambda _: stages.append("manager")), \
                            patch("factory.learn.learn_pass", side_effect=lambda _: stages.append("learn")), \
                            patch.object(dispatch, "frontier", return_value=[ticket(9, "1")]), \
                            patch.object(dispatch, "process_ticket", side_effect=lambda *_a, **_kw: stages.append("ticket")), \
                            patch.object(dispatch, "log") as log:
                        self.assertEqual(dispatch.main([]), 0)
                    self.assertEqual(stages, ["release-land", "land", "review", "manager",
                                              "ticket", "learn", "release-open"])
                    self.assertEqual((cfg.factory / "mode.json").read_bytes(), before)
                    self.assertEqual(
                        [row["mode"] for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                         if row.get("event") == "mode"],
                        ["feature-drive"],
                    )
                    self.assertTrue(any("mode refresh" in call.args[0] for call in log.call_args_list))
