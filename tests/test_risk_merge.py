from __future__ import annotations

import contextlib
import importlib
import inspect
import io
import itertools
import subprocess
import sys
import tempfile
import types
import typing
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, gate, manage, onboard
from scripts import mutation

HEAD = "a" * 40
NEW_HEAD = "b" * 40


def high_events(*, head: str = HEAD, second: bool = True, mutation: str = "PASS", distinct: bool = True):
    rows = [
        {"event": "risk", "ticket": 7, "head": head, "tier": "High", "matched_rules": ["high:risk policy"]},
        {"event": "attempt", "ticket": 7, "head": head, "actual_head": head, "gate": "PASS",
         "worker_model": "worker/model"},
        {"event": "review", "ticket": 7, "head": head, "actual_head": head, "accepted": True,
         "verdict": "APPROVE", "role": "primary", "model": "primary/model"},
        {"event": "risk-check", "ticket": 7, "head": head, "mutation": mutation,
         "pr_gate": "PASS" if mutation in {"PASS", "SKIP"} else "FAIL"},
    ]
    if second:
        rows.append({
            "event": "review", "ticket": 7, "head": head, "actual_head": head, "accepted": True,
            "verdict": "APPROVE", "role": "second",
            "model": "primary/model" if not distinct else "second/model",
        })
    return rows


class HighEvidenceTest(unittest.TestCase):
    def test_high_requires_two_distinct_reviews_and_blocking_mutation(self) -> None:
        self.assertTrue(dispatch._head_evidence_matches(high_events(), 7, HEAD))
        self.assertFalse(dispatch._head_evidence_matches(high_events(second=False), 7, HEAD))
        self.assertFalse(dispatch._head_evidence_matches(high_events(distinct=False), 7, HEAD))
        self.assertFalse(dispatch._head_evidence_matches(high_events(mutation="FAIL"), 7, HEAD))
        self.assertTrue(dispatch._head_evidence_matches(high_events(mutation="SKIP"), 7, HEAD))

    def test_new_head_invalidates_all_high_evidence(self) -> None:
        self.assertFalse(dispatch._head_evidence_matches(high_events(), 7, NEW_HEAD))

    def test_regression_approve_refuses_high_without_second_review(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        dispatch.configure(cfg)
        with mock.patch.object(dispatch.lifecycle, "read_events", return_value=high_events(second=False)), \
                mock.patch.object(dispatch, "gh_json") as github:
            self.assertFalse(dispatch.approve_pr(7, HEAD))
        github.assert_not_called()

    def test_missing_risk_row_cannot_hide_a_high_path(self) -> None:
        events = [
            row for row in high_events()
            if row["event"] not in {"risk", "risk-check"} and row.get("role") != "second"
        ]
        self.assertFalse(dispatch._head_evidence_matches(
            events, 7, HEAD, changed_paths=["factory/dispatch.py"],
        ))

    def test_merge_stage_removes_approval_when_high_path_has_no_risk_row(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        dispatch.configure(cfg)
        events = [
            {"event": "attempt", "ticket": 7, "head": HEAD, "actual_head": HEAD, "gate": "PASS"},
            {"event": "review", "ticket": 7, "head": HEAD, "actual_head": HEAD,
             "accepted": True, "verdict": "APPROVE", "role": "primary"},
            {"event": "approved", "ticket": 7, "pr": 70, "head": HEAD,
             "gate_head": HEAD, "review_head": HEAD},
        ]
        pr = {
            "number": 70, "headRefName": "agent/7", "headRefOid": HEAD,
            "baseRefName": "main", "isDraft": False,
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
            "state": "OPEN", "title": "change dispatcher",
            "files": [{"path": "factory/dispatch.py"}],
        }

        def github(args):
            if args[:2] == ["pr", "list"]:
                return [pr]
            if args[:2] == ["pr", "view"]:
                return pr
            if args[0] == "api":
                return {"behind_by": 0}
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                mock.patch.object(dispatch, "triage_cancelled", side_effect=lambda _p, checks, *_: checks), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch.lifecycle, "read_events", return_value=events), \
                mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                mock.patch.object(dispatch, "escalate") as escalate, \
                mock.patch("factory.learn.promote_pass"):
            dispatch.merge_pass_locked(False)

        self.assertFalse(any(call.args[0][:3] == ["gh", "pr", "merge"] for call in run.call_args_list))
        self.assertIn(
            ["gh", "pr", "edit", "70", "--repo", "acme/widgets",
             "--remove-label", config.LABEL_APPROVED],
            [call.args[0] for call in run.call_args_list],
        )
        self.assertEqual(escalate.call_args.kwargs["code"], "approval_missing")


class ReviewPolicyTest(unittest.TestCase):
    def test_low_skips_pr_checks_and_runs_one_primary_review(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(cfg)
        with mock.patch.object(dispatch, "risk_for_head", return_value=("Low", ["low:docs/**"])), \
                mock.patch.object(dispatch, "run_gate") as gate, \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")) as review:
            self.assertEqual(
                dispatch.pr_gate_and_review(mock.Mock(), 7, "attempt report", HEAD, {}),
                ("APPROVE", "ok", False),
            )
        gate.assert_not_called()
        self.assertEqual(review.call_count, 1)

    def test_low_revise_uses_medium_checks_on_next_head(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(cfg)
        runs = {}
        with mock.patch.object(dispatch, "risk_for_head", return_value=("Low", ["low:docs/**"])), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "pr report")) as gate, \
                mock.patch.object(
                    dispatch, "review",
                    side_effect=[("REVISE", "fix"), ("APPROVE", "ok")],
                ):
            self.assertEqual(
                dispatch.pr_gate_and_review(mock.Mock(), 7, "attempt", HEAD, runs)[0],
                "REVISE",
            )
            self.assertEqual(
                dispatch.pr_gate_and_review(mock.Mock(), 7, "attempt", NEW_HEAD, runs)[0],
                "APPROVE",
            )
        gate.assert_called_once()

    def test_stale_disagreement_is_cleared_before_a_new_head_policy(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        dispatch.configure(cfg)
        dispatch._PENDING_RISK[7] = {
            "head": HEAD, "disagreement": True, "packet": "old findings",
        }
        with mock.patch.object(dispatch, "risk_for_head", return_value=("Medium", ["medium:other path"])), \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")):
            result = dispatch.pr_gate_and_review(Path("."), 7, "attempt", NEW_HEAD, {})
        self.assertEqual(result, ("APPROVE", "ok", False))
        self.assertNotIn(7, dispatch._PENDING_RISK)

    def test_second_disagreement_only_escalates_on_the_next_head(self) -> None:
        first = {"head": HEAD, "disagreement": True}
        second = {"head": NEW_HEAD, "disagreement": True}
        active, repeated = dispatch.risk_disagreement_transition(None, first, HEAD)
        self.assertEqual((active, repeated), (HEAD, False))
        self.assertEqual(
            dispatch.risk_disagreement_transition(active, second, NEW_HEAD),
            (NEW_HEAD, True),
        )
        self.assertEqual(
            dispatch.risk_disagreement_transition(HEAD, first, NEW_HEAD),
            (None, False),
        )

    def test_refresh_disagreement_is_pending_worker_context(self) -> None:
        rows = [{
            "event": "risk-requeue", "ticket": 7, "pr": 70, "head": HEAD,
            "round": 1, "packet": "second reviewer: REVISE",
        }]
        cfg = config.Config(Path("."), "acme/widgets")
        dispatch.configure(cfg)
        with mock.patch.object(dispatch.lifecycle, "read_events", return_value=rows):
            self.assertEqual(dispatch.pending_risk_requeue_head(7), HEAD)
            context = dispatch.refresh_requeue_context(7)
        self.assertIn("High-risk disagreement, round 1", context)
        self.assertIn("second reviewer: REVISE", context)

    def test_high_without_changed_factory_code_records_mutation_skip(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        dispatch.configure(cfg)

        def fake_review(*_args, role="primary", **_kwargs):
            target = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
            target[7] = {"verdict": "APPROVE", "model": f"{role}/model"}
            return "APPROVE", f"{role} ok"

        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:workflow"])), \
                mock.patch.object(dispatch, "review", side_effect=fake_review), \
                mock.patch.object(dispatch, "_worker_model_for_head", return_value="worker/model"), \
                mock.patch.object(
                    dispatch, "run",
                    return_value=subprocess.CompletedProcess([], 0, "M\t.github/workflows/ci.yml\n", ""),
                ):
            verdict, _, _ = dispatch.pr_gate_and_review(Path("."), 7, "attempt", HEAD, {})

        self.assertEqual(verdict, "APPROVE")
        self.assertEqual(dispatch._PENDING_RISK[7]["mutation"], "SKIP")

    def test_mutation_blocking_fails_and_lists_changed_line_survivor(self) -> None:
        class TestOutcome(Enum):
            SURVIVED = "survived"
            INCOMPETENT = "incompetent"
            KILLED = "killed"

        class WorkerOutcome(Enum):
            NORMAL = "normal"

        spec = SimpleNamespace(
            module_path=Path("factory/example.py"),
            operator_name="NumberReplacer",
            occurrence=0,
            start_pos=(1, 0),
            end_pos=(2, 9),
        )
        unrelated = SimpleNamespace(
            module_path=Path("factory/example.py"),
            operator_name="NumberReplacer",
            occurrence=1,
            start_pos=(1, 0),
            end_pos=(1, 9),
        )
        survivor = SimpleNamespace(
            worker_outcome=WorkerOutcome.NORMAL,
            test_outcome=TestOutcome.SURVIVED,
            diff="-value = 1\n+value = 2\n",
            output="",
        )
        attrs = types.ModuleType("attrs")
        attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
        cosmic_ray = types.ModuleType("cosmic_ray")
        cosmic_ray.__path__ = []
        mutating = types.ModuleType("cosmic_ray.mutating")
        mutating.mutate_and_test = mock.Mock(return_value=survivor)
        work_item = types.ModuleType("cosmic_ray.work_item")
        work_item.TestOutcome = TestOutcome
        modules = {
            "attrs": attrs,
            "cosmic_ray": cosmic_ray,
            "cosmic_ray.mutating": mutating,
            "cosmic_ray.work_item": work_item,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "factory").mkdir()
            (root / "factory/example.py").write_text("enabled = True\nvalue = 1\n")
            with contextlib.chdir(root), mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(mutation, "changed_lines", return_value={"factory/example.py": {2}}), \
                    mock.patch.object(mutation, "sample", return_value=([spec], 1)) as sampler, \
                    mock.patch.object(
                        mutation.subprocess, "run",
                        return_value=subprocess.CompletedProcess([], 0, "OK\n", ""),
                    ):
                report_only = io.StringIO()
                with contextlib.redirect_stdout(report_only):
                    self.assertEqual(mutation.main([]), 0)
                blocking = io.StringIO()
                with contextlib.redirect_stdout(blocking):
                    self.assertEqual(mutation.main(["--blocking"]), 1)
                incomplete = io.StringIO()
                clock = itertools.count(0, 10)
                with mock.patch.object(mutation, "gate_timeout", return_value=1), \
                        mock.patch.object(mutation.time, "monotonic", side_effect=lambda: next(clock)), \
                        contextlib.redirect_stdout(incomplete):
                    self.assertEqual(mutation.main(["--blocking"]), 1)
                untested = SimpleNamespace(
                    worker_outcome=WorkerOutcome.NORMAL, test_outcome=None, diff="", output="no test",
                )
                with mock.patch.object(mutating, "mutate_and_test", return_value=untested), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mutation.main(["--blocking"]), 1)
                killed = SimpleNamespace(
                    worker_outcome=WorkerOutcome.NORMAL, test_outcome=TestOutcome.KILLED,
                    diff="", output="",
                )
                sampler.return_value = ([spec, unrelated], 1)
                mutating.mutate_and_test.reset_mock(return_value=True)
                mutating.mutate_and_test.return_value = killed
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mutation.main(["--blocking"]), 0)
                self.assertEqual(mutating.mutate_and_test.call_count, 1)

        self.assertIn("factory/example.py:1: NumberReplacer", blocking.getvalue())
        self.assertIn("1 survived", report_only.getvalue())
        self.assertIn("incomplete: 1 mutants not started", incomplete.getvalue())
        self.assertEqual(
            gate._check_status("mutation", True, "Installed 28 packages\nno mutable files\n", True),
            "SKIP (no changed factory code)",
        )

    def test_high_disagreement_packet_contains_bound_verdicts(self) -> None:
        packet = dispatch.risk_disagreement_packet(
            HEAD, ["high:risk policy"], "APPROVE", "primary ok",
            "REVISE", "second finding", "PASS", "mutation ok",
        )
        self.assertIn(HEAD, packet)
        self.assertIn("high:risk policy", packet)
        self.assertIn("Primary reviewer: APPROVE", packet)
        self.assertIn("Second reviewer: REVISE", packet)
        self.assertIn("second finding", packet)

    def test_disagree_cases_and_agreement_are_distinct(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(cfg)

        cases = (
            ("split", ("APPROVE", "REVISE"), "PASS", "REVISE", True),
            ("mutation", ("APPROVE", "APPROVE"), "FAIL", "REVISE", True),
            ("approve", ("APPROVE", "APPROVE"), "PASS", "APPROVE", False),
            ("revise", ("REVISE", "REVISE"), "PASS", "REVISE", False),
        )
        for name, verdicts, mutation_status, expected, disagreement in cases:
            with self.subTest(name=name):
                dispatch.configure(cfg)

                def fake_review(*_args, role="primary", expected=verdicts, **_kwargs):
                    verdict = expected[role == "second"]
                    target = (
                        dispatch._PENDING_SECOND_REVIEWS
                        if role == "second" else dispatch._PENDING_REVIEWS
                    )
                    target[7] = {
                        "verdict": verdict,
                        "model": f"{role}/model",
                    }
                    return verdict, f"{role} findings"

                report = f"- mutation: {mutation_status}\n"
                with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                        mock.patch.object(dispatch, "run_gate", return_value=(mutation_status != "FAIL", report)), \
                        mock.patch.object(dispatch, "review", side_effect=fake_review), \
                        mock.patch.object(dispatch, "_worker_model_for_head", return_value="worker/model"), \
                        mock.patch.object(dispatch, "run", return_value=mock.Mock(stdout="M factory/risk.py\n")):
                    result = dispatch.pr_gate_and_review(Path("."), 7, "attempt", HEAD, {})
                self.assertEqual(result[0], expected)
                self.assertEqual(dispatch._PENDING_RISK[7]["disagreement"], disagreement)


class ConfigTest(unittest.TestCase):
    def test_second_reviewer_default_is_distinct_from_primary(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        self.assertNotEqual(config.command_model(cfg.reviewer), config.command_model(cfg.second_reviewer))

    def test_doctor_reports_missing_launcher_and_same_model(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        self.assertIn(
            "cannot launch",
            onboard.second_reviewer_problem(cfg, lambda _: None),
        )
        cfg.second_reviewer = list(cfg.reviewer)
        self.assertIn(
            "same model",
            onboard.second_reviewer_problem(cfg, lambda _: "/usr/bin/omp"),
        )

    def test_doctor_reports_worker_model_missing_or_not_distinct(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.workers = {"default": ["omp", "-p", "@{prompt}"]}
        self.assertIn(
            "worker `default` model is not explicit",
            onboard.second_reviewer_problem(cfg, lambda _: "/usr/bin/omp"),
        )
        cfg.workers = {
            "default": ["omp", "-p", "--model", config.command_model(cfg.second_reviewer), "@{prompt}"],
        }
        self.assertIn(
            "same model as second reviewer",
            onboard.second_reviewer_problem(cfg, lambda _: "/usr/bin/omp"),
        )


    def test_builtin_worker_has_a_pinned_distinct_model(self) -> None:
        cfg = config.Config(Path("."), "acme/widgets")
        model = cfg.worker_model("default")
        self.assertEqual(model, config.DEFAULT_WORKER_MODEL)
        self.assertNotIn(model, {
            config.command_model(cfg.reviewer),
            config.command_model(cfg.second_reviewer),
        })
class HighPrGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cfg = config.Config(self.root, "acme/widgets")
        cfg.checks = [
            config.Check("mutation", ["mutation"], when="pr"),
            config.Check("extra", ["extra"], when="pr"),
        ]
        dispatch.configure(cfg)

    def test_high_other_pr_check_failure_is_ordinary_gate_failure(self) -> None:
        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                mock.patch.object(dispatch, "run_gate", return_value=(
                    False, "- mutation: PASS\n- extra: FAIL\n",
                )), mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")) as reviewer:
            result = dispatch.pr_gate_and_review(self.root, 7, "attempt", HEAD, {})
        self.assertEqual(result, ("REVISE", "- mutation: PASS\n- extra: FAIL\n", True))
        reviewer.assert_not_called()
        self.assertNotIn(7, dispatch._PENDING_RISK)
        row = dispatch.lifecycle.read_events(dispatch.EVENTS)[-1]
        self.assertEqual((row["event"], row["head"], row["pr_gate"]), ("risk-check", HEAD, "FAIL"))

    def test_low_recorded_tier_skips_pr_checks_without_relying_on_interned_strings(self) -> None:
        dispatch.record("risk", ticket=7, head=HEAD, tier="Low", matched_rules=["low:markdown"])
        with mock.patch.object(dispatch, "run_gate") as gate, \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")):
            self.assertEqual(
                dispatch.pr_gate_and_review(self.root, 7, "attempt", HEAD, {}),
                ("APPROVE", "ok", False),
            )
        gate.assert_not_called()

    def test_high_approval_requires_successful_current_head_pr_gate(self) -> None:
        for status in ("FAIL", None):
            with self.subTest(status=status):
                events = high_events()
                for row in events:
                    if row["event"] == "risk-check":
                        row["pr_gate"] = status
                self.assertFalse(dispatch._head_evidence_matches(events, 7, HEAD))
                with mock.patch.object(dispatch.lifecycle, "read_events", return_value=events), \
                        mock.patch.object(dispatch, "run") as run:
                    self.assertFalse(dispatch.approve_pr(7, HEAD))
                run.assert_not_called()
        stale = [row for row in high_events() if row["event"] == "risk-check"]
        current = [row for row in high_events(head=NEW_HEAD) if row["event"] != "risk-check"]
        self.assertFalse(dispatch._head_evidence_matches(stale + current, 7, NEW_HEAD))

    def test_high_pr_gate_success_is_persisted(self) -> None:
        def review(*_args, role="primary", **_kwargs):
            pending = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
            pending[7] = {"verdict": "APPROVE", "model": f"{role}/model"}
            return "APPROVE", "ok"

        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                mock.patch.object(dispatch, "run_gate", return_value=(
                    True, "- mutation: PASS\n- extra: PASS\n",
                )), mock.patch.object(dispatch, "review", side_effect=review), \
                mock.patch.object(dispatch, "_worker_model_for_head", return_value="worker/model"), \
                mock.patch.object(dispatch, "run", return_value=mock.Mock(stdout="M\tfactory/risk.py\n")):
            self.assertEqual(dispatch.pr_gate_and_review(self.root, 7, "attempt", HEAD, {})[0], "APPROVE")
        rows = dispatch.lifecycle.read_events(dispatch.EVENTS)
        check = next(row for row in rows if row.get("event") == "risk-check")
        self.assertEqual(check["pr_gate"], "PASS")

    def test_high_pr_check_output_cannot_overwrite_failed_gate_summary(self) -> None:
        report = (
            "- mutation: FAIL\n- extra: FAIL\n\n## extra failure\n"
            "```\n- extra: PASS\n```\n"
        )
        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                mock.patch.object(dispatch, "run_gate", return_value=(False, report)), \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "ok")) as reviewer:
            self.assertEqual(
                dispatch.pr_gate_and_review(self.root, 7, "attempt", HEAD, {}),
                ("REVISE", report, True),
            )
        reviewer.assert_not_called()


class HighPipelineBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.review_rounds = 1
        self.cfg.signoff = False
        self.cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(self.cfg)
        self.wt = dispatch.FACTORY / "wt-7"
        self.wt.mkdir(parents=True)
        self.issue = {
            "number": 7, "title": "change dispatcher", "body": "", "comments": [],
            "labels": [{"name": config.LABEL_AGENT}], "state": "OPEN", "assignees": [],
        }
        self.pr = {
            "number": 70, "headRefName": "agent/7", "headRefOid": HEAD,
            "baseRefName": "main", "isDraft": False, "state": "OPEN",
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
            "title": "change dispatcher", "files": [{"path": "factory/dispatch.py"}],
        }
        self.heads = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.command = self.patch(dispatch, "run", side_effect=self.command_result)
        self.patch(dispatch, "gh_json", side_effect=self.github)
        self.patch(dispatch, "initiative_kind", return_value=None)
        self.patch(dispatch, "admit_plan", return_value=None)
        self.patch(dispatch, "ensure_worktree", return_value=self.wt)
        self.patch(dispatch, "agent_lease", return_value=HEAD)
        self.patch(dispatch, "refresh_requeue_context", return_value="")
        self.patch(dispatch, "pending_risk_requeue_head", return_value=None)
        self.patch(dispatch, "sync_staged", return_value=None)
        self.patch(dispatch, "push_and_pr", return_value=True)
        self.patch(dispatch, "push_agent", return_value=NEW_HEAD)
        self.patch(dispatch, "pr_comment")
        self.patch(dispatch, "retain_handoff", return_value=True)
        self.patch(dispatch, "cleanup_after_merge")
        self.patch(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}])
        self.patch(dispatch, "triage_cancelled", side_effect=lambda _p, checks, *_: checks)
        self.patch(dispatch.risk, "classify", return_value=(
            "High", ["high:approval, gate, and dispatch control"],
        ))
        self.stack.enter_context(mock.patch("factory.learn.promote_pass"))
        self.stack.enter_context(mock.patch("factory.schedule.priority", return_value=("normal", "test")))
        self.escalate = self.patch(dispatch, "escalate")
        self.worker = self.patch(dispatch, "worker_round", side_effect=self.work)

    def patch(self, target, name, **kwargs):
        return self.stack.enter_context(mock.patch.object(target, name, **kwargs))

    def github(self, args):
        if args[:2] == ["issue", "view"]:
            return self.issue
        if args[:2] == ["pr", "list"]:
            return [self.pr]
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[0] == "api":
            return {"behind_by": 0}
        raise AssertionError(args)

    def command_result(self, args, **_kwargs):
        if args[:3] == ["git", "diff", "--name-status"]:
            output = "M\tfactory/dispatch.py\n"
        elif args[:3] == ["git", "branch", "--show-current"]:
            output = "agent/7\n"
        elif args[:3] == ["git", "rev-parse", "HEAD"]:
            output = self.pr["headRefOid"]
        else:
            output = "1\n"
        return subprocess.CompletedProcess(args, 0, output, "")

    def work(self, *_args, **_kwargs):
        head = HEAD if not self.heads else NEW_HEAD
        self.heads.append(head)
        self.pr["headRefOid"] = head
        dispatch.record(
            "attempt", ticket=7, head=head, actual_head=head, gate="PASS",
            worker_model="worker/model",
        )
        return True, "- test: PASS\n", self.root / "worker.log", head

    def reviews(self, verdicts):
        def review(_wt, n, _report, head, *, role="primary", **_kwargs):
            verdict = verdicts[role == "second"]
            row = {
                "ticket": n, "head": head, "actual_head": head, "accepted": True,
                "verdict": verdict, "role": role, "model": f"{role}/model",
            }
            dispatch.record("review", **row)
            pending = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
            pending[n] = row
            return verdict, f"{role} dissent" if verdict == "REVISE" else f"{role} ok"
        self.patch(dispatch, "review", side_effect=review)

    def test_disagree_ticket_loop_reworks_once_then_escalates_at_next_head(self) -> None:
        for verdicts, mutation_status in (
            (("APPROVE", "REVISE"), "PASS"),
            (("APPROVE", "APPROVE"), "FAIL"),
        ):
            with self.subTest(verdicts=verdicts):
                self.heads.clear()
                self.worker.reset_mock()
                self.escalate.reset_mock()
                self.reviews(verdicts)
                self.patch(dispatch, "run_gate", return_value=(
                    mutation_status == "PASS", f"- mutation: {mutation_status}\nsurvivor findings",
                ))
                dispatch.process_ticket(self.issue, 10, False, forced=True)
                self.assertEqual(self.heads, [HEAD, NEW_HEAD])
                self.assertIn("High-risk disagreement", self.worker.call_args.args[4])
                self.escalate.assert_called_once()
                self.assertEqual(self.escalate.call_args.kwargs["code"], "risk_disagreement")
                packet = self.escalate.call_args.kwargs["extra"]
                for field in (
                    NEW_HEAD, "What changed", "factory/dispatch.py", "Why High",
                    f"Primary reviewer: {verdicts[0]}", f"Second reviewer: {verdicts[1]}",
                    f"Blocking mutation: {mutation_status}",
                    "second dissent" if verdicts[1] == "REVISE" else "survivor findings",
                ):
                    self.assertIn(field, packet)

    def test_high_both_revise_follows_medium_budget_without_risk_disagreement(self) -> None:
        self.reviews(("REVISE", "REVISE"))
        self.patch(dispatch, "run_gate", return_value=(True, "- mutation: PASS\n"))
        dispatch.process_ticket(self.issue, 10, False, forced=True)
        self.assertEqual(self.heads, [HEAD, NEW_HEAD])
        self.escalate.assert_called_once()
        self.assertEqual(self.escalate.call_args.kwargs["code"], "review_revise")

    def test_high_disagree_checks_all_agree_approves_and_merges_without_human(self) -> None:
        self.reviews(("APPROVE", "APPROVE"))
        self.patch(dispatch, "run_gate", return_value=(True, "- mutation: PASS\n"))
        dispatch.process_ticket(self.issue, 10, False, forced=True)
        dispatch.merge_pass_locked(False)
        commands = [call.args[0] for call in self.command.call_args_list]
        self.assertTrue(any(command[:3] == ["gh", "pr", "merge"] for command in commands))
        self.assertTrue(any("--add-label" in command and config.LABEL_APPROVED in command for command in commands))
        self.assertFalse(any("--add-label" in command and config.LABEL_HUMAN in command for command in commands))
        self.escalate.assert_not_called()
        self.assertEqual(self.heads, [HEAD])

    def test_regression_high_dispatch_only_missing_second_cannot_approve_or_merge(self) -> None:
        for row in high_events(second=False):
            dispatch.record(row["event"], **{k: v for k, v in row.items() if k != "event"})
        dispatch.record("approved", ticket=7, pr=70, head=HEAD, gate_head=HEAD, review_head=HEAD)
        self.assertFalse(dispatch.approve_pr(7, HEAD))
        dispatch.merge_pass_locked(False)
        commands = [call.args[0] for call in self.command.call_args_list]
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in commands))
        self.assertFalse(any("--add-label" in command and config.LABEL_APPROVED in command for command in commands))
        self.assertTrue(any("--remove-label" in command and config.LABEL_APPROVED in command for command in commands))

    def test_high_mutation_pass_with_other_pr_check_failed_refuses_merge(self) -> None:
        for row in high_events():
            row = dict(row)
            if row["event"] == "risk-check":
                row["pr_gate"] = "FAIL"
            dispatch.record(row.pop("event"), **row)
        dispatch.record("approved", ticket=7, pr=70, head=HEAD, gate_head=HEAD, review_head=HEAD)
        self.assertFalse(dispatch.approve_pr(7, HEAD))
        dispatch.merge_pass_locked(False)
        self.assertFalse(any(call.args[0][:3] == ["gh", "pr", "merge"] for call in self.command.call_args_list))

    def test_high_manager_fix_records_risk_and_cannot_ignore_other_pr_failure(self) -> None:
        self.cfg.checks.append(config.Check("extra", ["extra"], when="pr"))
        self.reviews(("APPROVE", "APPROVE"))
        self.patch(dispatch, "run_gate", return_value=(False, "- mutation: PASS\n- extra: FAIL\n"))
        packet = self.root / "packet.md"
        packet.write_text("fix it")
        approve = self.patch(dispatch, "approve_pr")
        manage.apply(7, self.issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
        approve.assert_not_called()
        self.escalate.assert_called_once()
        self.assertEqual(self.escalate.call_args.kwargs["code"], "pr_checks_failed")
        rows = dispatch.lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(any(row.get("event") == "risk" and row.get("head") == HEAD for row in rows))
        self.assertTrue(any(
            row.get("event") == "decision" and row.get("stage") == "risk" for row in rows
        ))

    def test_new_head_reruns_high_gate_and_both_reviewers_before_merge(self) -> None:
        self.reviews(("APPROVE", "APPROVE"))
        run_gate = self.patch(dispatch, "run_gate", return_value=(True, "- mutation: PASS\n"))
        dispatch.process_ticket(self.issue, 10, False, forced=True)
        self.pr["headRefOid"] = NEW_HEAD
        self.assertFalse(dispatch.approve_pr(7, NEW_HEAD))
        self.work()
        dispatch.record_risk(self.wt, 7, NEW_HEAD)
        self.assertEqual(
            dispatch.pr_gate_and_review(self.wt, 7, "- test: PASS", NEW_HEAD, {})[0],
            "APPROVE",
        )
        self.assertTrue(dispatch.approve_pr(7, NEW_HEAD))
        dispatch.merge_pass_locked(False)
        self.assertEqual(run_gate.call_count, 2)
        reviews = [
            (row["head"], row["role"]) for row in dispatch.lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "review"
        ]
        self.assertEqual(reviews, [
            (HEAD, "primary"), (HEAD, "second"), (NEW_HEAD, "primary"), (NEW_HEAD, "second"),
        ])


class MutationExecutionTest(unittest.TestCase):
    def test_risk_changed_modules_have_resolvable_function_annotations(self) -> None:
        for name in (
            "config", "dashboard", "dispatch", "gate", "manage",
            "onboard", "risk", "stats", "trace", "watch",
        ):
            module = importlib.import_module(f"factory.{name}")
            for function_name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ == module.__name__:
                    with self.subTest(module=name, function=function_name):
                        typing.get_type_hints(function)

    def test_risk_model_parser_handles_trailing_option_and_preserves_equals(self) -> None:
        self.assertIsNone(config.command_model(["omp", "--model"]))
        self.assertEqual(config.command_model(["omp", "--model=provider/name=variant"]), "provider/name=variant")

    def test_mutation_deletions_only_have_no_changed_factory_lines(self) -> None:
        diff = "+++ b/factory/dispatch.py\n@@ -2,2 +1,0 @@\n-old\n-code\n"
        with mock.patch.object(mutation.subprocess, "run", return_value=mock.Mock(stdout=diff)):
            self.assertEqual(mutation.changed_lines(), {})

    def test_mutation_runner_prioritizes_related_tests_without_omitting_others(self) -> None:
        executed = []

        class Cases(unittest.TestCase):
            def test_unrelated(self):
                executed.append("unrelated")

            def test_related(self):
                executed.append("related")

        unrelated, related = Cases("test_unrelated"), Cases("test_related")
        unrelated.__class__.__module__ = "test_other"
        # Distinct classes keep unittest's module/class fixtures grouped.
        class Related(Cases):
            pass
        related = Related("test_related")
        Related.__module__ = "test_risk_merge"
        suite = unittest.TestSuite([unrelated, related])
        with mock.patch.object(mutation.unittest.defaultTestLoader, "discover", return_value=suite), \
                mock.patch.object(mutation.Path, "read_text", return_value=(
                    "| PR risk | `factory/risk.py` | `tests/test_risk_merge.py` | test |\n"
                )), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mutation.run_tests("factory/risk.py"), 0)
        self.assertEqual(executed, ["related", "unrelated"])

    def test_mutation_runner_stops_as_soon_as_a_test_kills_mutant(self) -> None:
        executed = []

        class Cases(unittest.TestCase):
            def test_kill(self):
                self.fail("mutant killed")

            def test_later(self):
                executed.append("later")

        suite = unittest.TestSuite([Cases("test_kill"), Cases("test_later")])
        with mock.patch.object(mutation.unittest.defaultTestLoader, "discover", return_value=suite), \
                mock.patch.object(mutation.Path, "read_text", return_value=""), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mutation.run_tests("factory/example.py"), 1)
        self.assertEqual(executed, [])



class WorkerModelProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(self.root, "acme/widgets")
        dispatch.configure(self.cfg)

    def high_review(self, head: str) -> tuple[str | None, str, bool]:
        def review(*_args, role="primary", **_kwargs):
            pending = dispatch._PENDING_SECOND_REVIEWS if role == "second" else dispatch._PENDING_REVIEWS
            pending[7] = {"verdict": "APPROVE", "model": f"{role}/model"}
            return "APPROVE", f"{role} ok"

        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                mock.patch.object(dispatch, "review", side_effect=review), \
                mock.patch.object(
                    dispatch, "run",
                    return_value=subprocess.CompletedProcess([], 0, "M\t.github/workflows/ci.yml\n", ""),
                ):
            return dispatch.pr_gate_and_review(self.root, 7, "- mutation: PASS\n", head, {})

    def test_builtin_chore_model_survives_requeue_and_post_decision_claim(self) -> None:
        model = self.cfg.worker_model(config.LABEL_CHORE)
        dispatch.record(
            "attempt", ticket=7, attempt=1, head=HEAD, actual_head=HEAD,
            gate="PASS", worker=config.LABEL_CHORE, worker_model=model,
        )
        self.assertEqual(self.high_review(HEAD)[0], "APPROVE")

        dispatch.record("risk-requeue", ticket=7, head=HEAD, round=1)
        dispatch.record("human-decision", ticket=7, status="success", decision_id="owner-choice")
        dispatch.record("claimed", ticket=7, worker=config.LABEL_CHORE)
        dispatch.record(
            "attempt", ticket=7, attempt=1, head=NEW_HEAD, actual_head=NEW_HEAD,
            gate="PASS", worker=config.LABEL_CHORE, worker_model=model,
        )
        self.assertEqual(self.high_review(NEW_HEAD)[0], "APPROVE")
        self.assertEqual(model, "gpt-6-sol")
        checks = [
            row for row in dispatch.lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "risk-check"
        ]
        self.assertEqual([row["worker_model"] for row in checks], [model, model])

    def test_missing_worker_provenance_blocks_review_with_machine_code(self) -> None:
        with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "primary ok")) as review:
            verdict, findings, _ = dispatch.pr_gate_and_review(
                self.root, 7, "- mutation: PASS\n", HEAD, {},
            )
        self.assertIsNone(verdict)
        review.assert_not_called()
        self.assertEqual(
            dispatch._PENDING_REVIEWS[7]["failure_code"],
            "missing_worker_provenance",
        )
        self.assertIn("missing worker model provenance", findings)

if __name__ == "__main__":
    unittest.main()
