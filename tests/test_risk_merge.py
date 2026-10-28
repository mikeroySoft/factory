from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, onboard

HEAD = "a" * 40
NEW_HEAD = "b" * 40


def high_events(*, head: str = HEAD, second: bool = True, mutation: str = "PASS", distinct: bool = True):
    rows = [
        {"event": "risk", "ticket": 7, "head": head, "tier": "High", "matched_rules": ["high:risk policy"]},
        {"event": "attempt", "ticket": 7, "head": head, "actual_head": head, "gate": "PASS",
         "worker_model": "worker/model"},
        {"event": "review", "ticket": 7, "head": head, "actual_head": head, "accepted": True,
         "verdict": "APPROVE", "role": "primary", "model": "primary/model"},
        {"event": "risk-check", "ticket": 7, "head": head, "mutation": mutation},
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
        for name, verdicts, mutation, expected, disagreement in cases:
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

                report = f"- mutation: {mutation}\n"
                with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                        mock.patch.object(dispatch, "run_gate", return_value=(mutation != "FAIL", report)), \
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

if __name__ == "__main__":
    unittest.main()
