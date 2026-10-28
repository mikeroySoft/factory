from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, gate, onboard
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

        class WorkerOutcome(Enum):
            NORMAL = "normal"

        spec = SimpleNamespace(
            module_path=Path("factory/example.py"),
            operator_name="NumberReplacer",
            occurrence=0,
            start_pos=(2, 8),
            end_pos=(2, 9),
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
                    mock.patch.object(mutation, "sample", return_value=([spec], 1)), \
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

        self.assertIn("factory/example.py:2: NumberReplacer", blocking.getvalue())
        self.assertIn("1 survived", report_only.getvalue())
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
if __name__ == "__main__":
    unittest.main()


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
