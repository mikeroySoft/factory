from __future__ import annotations

import contextlib
import functools
import hashlib
import importlib
import inspect
import io
import itertools
import json
import os
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


def journal_state(directory: Path) -> tuple[int, str] | None:
    path = directory / ".factory" / "events.jsonl"
    if not path.exists():
        return None
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


# Every binding `dispatch.configure` writes (factory/dispatch.py:80-101): 11 rebound globals,
# 7 containers it clears in place, and the functools.cache it clears.
_UNSET = object()
DISPATCH_REBOUND = (
    "cfg", "ROOT", "REPO", "UPSTREAM", "UPSTREAM_REPO", "FACTORY", "LOGS",
    "SYNC_LOG", "EVENTS", "MAX_ACTIVE", "MAX_ATTEMPTS",
)
DISPATCH_CLEARED = (
    "_PENDING_REVIEWS", "_PENDING_SECOND_REVIEWS", "_PENDING_RISK", "_LOW_FALLBACK",
    "UNAVAILABLE_WORKERS", "VERIFIED_WORKERS", "_ESCALATED",
)
DISPATCH_CACHED = "host_claim_login"
DISPATCH_NAMES = (*DISPATCH_REBOUND, *DISPATCH_CLEARED, DISPATCH_CACHED)
# Points where isolate_journal can be made to fail, in order; each precedes one mutation.
ISOLATION_STEPS = (
    "tempdir", *(f"fresh:{name}" for name in DISPATCH_CLEARED), f"fresh:{DISPATCH_CACHED}",
    "chdir", "done",
)


def dispatch_bindings() -> dict[str, object]:
    """Each binding configure() touches, by identity; `_UNSET` when the attribute is absent."""
    return {name: getattr(dispatch, name, _UNSET) for name in DISPATCH_NAMES}


def container_contents(bindings: dict[str, object]) -> dict[str, object]:
    return {name: type(bindings[name])(bindings[name]) for name in DISPATCH_CLEARED}


# What each restore operation in restore_dispatch can raise; anything else is a bug and
# propagates at once. ValueError (NUL in path) is excluded: cwd always comes from os.getcwd().
RESTORE_ERRORS = (
    # os.chdir(cwd): the saved cwd was removed (FileNotFoundError), replaced by a file
    # (NotADirectoryError) or lost its search permission (PermissionError).
    OSError,
    # setattr/delattr on the module: a read-only descriptor (property without setter or
    # deleter) or, for delattr, a name that vanished between hasattr() and delattr().
    AttributeError,
)


def restore_dispatch(
    bindings: dict[str, object], cwd: str, target: types.ModuleType = dispatch,
) -> None:
    """Put back cwd and the exact original objects. Nothing original is cleared or refilled."""
    errors: list[Exception] = []

    def attempt(action, *args) -> None:
        try:
            action(*args)
        except RESTORE_ERRORS as exc:  # keep restoring; the first error is raised below
            errors.append(exc)

    attempt(os.chdir, cwd)
    for name, value in bindings.items():
        if value is not _UNSET:
            attempt(setattr, target, name, value)
        elif hasattr(target, name):
            attempt(delattr, target, name)
    if errors:
        raise errors[0]


def _no_fault(step: str) -> None:
    return None


def isolate_journal(
    test: unittest.TestCase, fault: typing.Callable[[str], None] = _no_fault,
) -> tuple[Path, dict[Path, tuple[int, str] | None]]:
    """Run one test whose Config(Path(".")) dispatch writes events in a private cwd.

    configure() only ever sees fresh containers and a fresh host_claim_login cache, so the
    originals are never cleared. Cleanups are LIFO: temp.cleanup is registered first, so the
    restore (cwd and every original binding) runs before the directory is removed. Each
    cleanup is registered before the mutation it undoes, so a partial setup is undone too.
    """
    cwd = os.getcwd()
    checkouts = {Path(cwd).resolve(), Path(__file__).resolve().parents[1]}
    before = {checkout: journal_state(checkout) for checkout in checkouts}
    originals = dispatch_bindings()
    fault("tempdir")
    temp = tempfile.TemporaryDirectory()
    test.addCleanup(temp.cleanup)
    test.addCleanup(restore_dispatch, originals, cwd)
    for name in DISPATCH_CLEARED:
        fault(f"fresh:{name}")
        setattr(dispatch, name, type(originals[name])())
    fault(f"fresh:{DISPATCH_CACHED}")
    setattr(dispatch, DISPATCH_CACHED, functools.cache(originals[DISPATCH_CACHED].__wrapped__))
    root = Path(temp.name).resolve()
    fault("chdir")
    os.chdir(root)
    fault("done")
    return root, before


def assert_journal_isolated(
    test: unittest.TestCase, root: Path, before: dict[Path, tuple[int, str] | None],
) -> None:
    for checkout, state in before.items():
        test.assertEqual(journal_state(checkout), state, checkout)
    test.assertTrue(dispatch.EVENTS.resolve().is_relative_to(root), dispatch.EVENTS)
    written = journal_state(root)
    test.assertIsNotNone(written)
    test.assertGreater(written[0], 0)


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
        journal_root, journal_before = isolate_journal(self)
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
        assert_journal_isolated(self, journal_root, journal_before)


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
        journal_root, journal_before = isolate_journal(self)
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
        assert_journal_isolated(self, journal_root, journal_before)

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
        journal_root, journal_before = isolate_journal(self)
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
        assert_journal_isolated(self, journal_root, journal_before)

    def test_json_parsed_high_review_verdicts_drive_result_and_actionability(self) -> None:
        journal_root, journal_before = isolate_journal(self)
        cfg = config.Config(Path("."), "acme/widgets")
        cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        finding = {
            "severity": "important",
            "pass": "bug",
            "path": "factory/dispatch.py",
            "line": 1,
            "summary": "Fix the high-risk behavior.",
        }
        cases = (
            ("approve-revise-fix", ("APPROVE", False), ("REVISE", True), "REVISE", True),
            ("approve-revise-no-fix", ("APPROVE", False), ("REVISE", False), "REVISE", False),
            ("revise-approve-fix", ("REVISE", True), ("APPROVE", False), "REVISE", True),
            ("revise-approve-no-fix", ("REVISE", False), ("APPROVE", False), "REVISE", False),
            ("both-revise-fixes", ("REVISE", True), ("REVISE", True), "REVISE", True),
            ("both-revise-no-fixes", ("REVISE", False), ("REVISE", False), "REVISE", False),
            ("both-approve", ("APPROVE", False), ("APPROVE", False), "APPROVE", True),
            ("reject-primary", ("REJECT", True), ("APPROVE", False), "REJECT", False),
            ("reject-second", ("APPROVE", False), ("REJECT", True), "REJECT", False),
        )

        for name, primary, second, expected, actionable in cases:
            with self.subTest(name=name):
                dispatch.configure(cfg)
                reviews = tuple(
                    json.loads(json.dumps({
                        "verdict": verdict,
                        "findings": [finding] if has_fix else [],
                    }))
                    for verdict, has_fix in (primary, second)
                )
                if primary[0] == second[0]:
                    self.assertIsNot(reviews[0]["verdict"], reviews[1]["verdict"])

                def fake_review(*_args, role="primary", reviews=reviews, **_kwargs):
                    review = reviews[role == "second"]
                    target = (
                        dispatch._PENDING_SECOND_REVIEWS
                        if role == "second" else dispatch._PENDING_REVIEWS
                    )
                    target[7] = {
                        "verdict": review["verdict"],
                        "model": f"{role}/model",
                    }
                    findings = f"```json\n{json.dumps(review)}\n```\nVERDICT: {review['verdict']}"
                    return review["verdict"], findings

                with mock.patch.object(dispatch, "risk_for_head", return_value=("High", ["high:risk policy"])), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "- mutation: PASS\n")), \
                        mock.patch.object(dispatch, "review", side_effect=fake_review), \
                        mock.patch.object(dispatch, "_worker_model_for_head", return_value="worker/model"), \
                        mock.patch.object(dispatch, "run", return_value=mock.Mock(stdout="M factory/risk.py\n")):
                    result = dispatch.pr_gate_and_review(Path("."), 7, "attempt", HEAD, {})

                self.assertEqual(result[0], expected)
                self.assertIs(dispatch._PENDING_RISK[7]["actionable"], actionable)
        assert_journal_isolated(self, journal_root, journal_before)


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
            finished=True, clean=True,
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


class JournalIsolationRestoreTest(unittest.TestCase):
    """isolate_journal restores cwd and every binding configure() touches before the tempdir goes."""

    ISOLATED = (
        (HighEvidenceTest, "test_merge_stage_removes_approval_when_high_path_has_no_risk_row"),
        (ReviewPolicyTest, "test_high_without_changed_factory_code_records_mutation_skip"),
        (ReviewPolicyTest, "test_disagree_cases_and_agreement_are_distinct"),
        (ReviewPolicyTest, "test_json_parsed_high_review_verdicts_drive_result_and_actionability"),
    )

    def setUp(self) -> None:
        # The proofs use stand-in "originals": the real containers and cache are only rebound,
        # never cleared, seeded or mutated, and that is checked last.
        real, cwd = dispatch_bindings(), os.getcwd()
        real_contents = container_contents(real)
        real_cache = real[DISPATCH_CACHED].cache_info()
        self.addCleanup(self.assert_untouched, real, real_contents, real_cache, cwd)
        self.addCleanup(restore_dispatch, real, cwd)
        for name in DISPATCH_CLEARED:
            setattr(dispatch, name, type(real[name])())
        setattr(dispatch, DISPATCH_CACHED, functools.cache(real[DISPATCH_CACHED].__wrapped__))

    def assert_untouched(self, bindings, contents, cache, cwd) -> None:
        for name in DISPATCH_NAMES:
            self.assertIs(getattr(dispatch, name, _UNSET), bindings[name], name)
        self.assertEqual(container_contents(bindings), contents)
        self.assertEqual(bindings[DISPATCH_CACHED].cache_info(), cache)
        self.assertEqual(os.getcwd(), cwd)

    def seed_sentinels(self) -> None:
        dispatch.configure(config.Config(Path("sentinel-root"), "sentinel/repo"))
        dispatch._PENDING_REVIEWS[99] = {"verdict": "sentinel"}
        dispatch._PENDING_SECOND_REVIEWS[99] = {"verdict": "sentinel-second"}
        dispatch._PENDING_RISK[99] = {"head": "sentinel"}
        dispatch._LOW_FALLBACK.add(99)
        dispatch.UNAVAILABLE_WORKERS["sentinel"] = "down"
        dispatch.VERIFIED_WORKERS.add("sentinel")
        dispatch._ESCALATED.add(99)
        with mock.patch.object(dispatch, "gh_json", return_value={"login": "Sentinel"}):
            self.assertEqual(dispatch.host_claim_login(), "sentinel")
        self.assertEqual(dispatch.host_claim_login.cache_info().currsize, 1)

    def unset_bindings(self) -> None:
        for name in DISPATCH_REBOUND:
            if hasattr(dispatch, name):
                delattr(dispatch, name)

    def expect(self) -> dict[str, object]:
        bindings = dispatch_bindings()
        return {
            "bindings": bindings, "contents": container_contents(bindings),
            "cache": bindings[DISPATCH_CACHED].cache_info(), "cwd": os.getcwd(),
        }

    def recording_tempdir(self, expected: dict[str, object]):
        """A TemporaryDirectory whose cleanup records what was already restored when it ran."""
        created: list[Path] = []
        observed: list[dict[str, object]] = []

        class RecordingTemporaryDirectory(tempfile.TemporaryDirectory):
            def __init__(inner, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                created.append(Path(inner.name))

            def cleanup(inner) -> None:
                bindings = expected["bindings"]
                observed.append({
                    "dir_present": Path(inner.name).is_dir(),
                    "cwd_restored": os.getcwd() == expected["cwd"],
                    "bindings_restored": [
                        name for name in DISPATCH_NAMES
                        if getattr(dispatch, name, _UNSET) is not bindings[name]
                    ] == [],
                    "contents_unchanged": container_contents(bindings) == expected["contents"],
                    "cache_unchanged": bindings[DISPATCH_CACHED].cache_info() == expected["cache"],
                    "journal": journal_state(Path(inner.name)),
                })
                super().cleanup()

        return RecordingTemporaryDirectory, created, observed

    def run_case(self, case: unittest.TestCase, expected: dict[str, object]):
        recording, created, observed = self.recording_tempdir(expected)
        result = unittest.TestResult()
        with mock.patch.object(tempfile, "TemporaryDirectory", recording):
            case.run(result)
        self.assertEqual(result.testsRun, 1)
        return result, created, observed

    def assert_restored_first(self, created: list[Path], observed: list[dict[str, object]]) -> None:
        self.assertEqual(len(created), 1)
        self.assertEqual(len(observed), 1)
        restored = {key: value for key, value in observed[0].items() if key != "journal"}
        self.assertEqual(restored, dict.fromkeys(restored, True))
        self.assertFalse(created[0].exists())

    def assert_restored(self, expected: dict[str, object], seeded: bool) -> None:
        bindings = expected["bindings"]
        for name in DISPATCH_NAMES:
            self.assertIs(getattr(dispatch, name, _UNSET), bindings[name], name)
        for name in DISPATCH_REBOUND:
            self.assertEqual(hasattr(dispatch, name), bindings[name] is not _UNSET, name)
        self.assertEqual(container_contents(bindings), expected["contents"])
        self.assertEqual(bindings[DISPATCH_CACHED].cache_info(), expected["cache"])
        self.assertEqual(os.getcwd(), expected["cwd"])
        if seeded:
            self.assertEqual(expected["contents"]["_PENDING_RISK"], {99: {"head": "sentinel"}})
            with mock.patch.object(dispatch, "gh_json", side_effect=AssertionError("cache lost")):
                self.assertEqual(dispatch.host_claim_login(), "sentinel")

    def prepared(self):
        for prepare in (self.seed_sentinels, self.unset_bindings):
            with self.subTest(prepare=prepare.__name__):
                prepare()
                yield prepare == self.seed_sentinels

    def test_success_restores_before_tempdir_cleanup(self) -> None:
        for seeded in self.prepared():
            for cls, name in self.ISOLATED:
                with self.subTest(test=name):
                    expected = self.expect()
                    result, created, observed = self.run_case(cls(name), expected)
                    self.assertEqual((result.errors, result.failures), ([], []))
                    self.assert_restored_first(created, observed)
                    self.assertIsNotNone(observed[0]["journal"])
                    self.assert_restored(expected, seeded)

    def test_setup_failure_at_each_step_restores_before_tempdir_cleanup(self) -> None:
        for seeded in self.prepared():
            for step in ISOLATION_STEPS:
                with self.subTest(step=step):
                    expected = self.expect()

                    def fault(current: str, step: str = step) -> None:
                        if current == step:
                            raise OSError(f"setup failed at {step}")

                    class Case(unittest.TestCase):
                        def test_isolated(inner) -> None:
                            isolate_journal(inner, fault)
                            inner.fail("setup should have failed")

                    result, created, observed = self.run_case(Case("test_isolated"), expected)
                    self.assertEqual(result.failures, [])
                    self.assertEqual(len(result.errors), 1)
                    self.assertIn(f"setup failed at {step}", result.errors[0][1])
                    if step == "tempdir":
                        self.assertEqual((created, observed), ([], []))
                    else:
                        self.assert_restored_first(created, observed)
                    self.assert_restored(expected, seeded)

    def test_assertion_failure_in_body_restores_before_tempdir_cleanup(self) -> None:
        for seeded in self.prepared():
            expected = self.expect()

            class Case(unittest.TestCase):
                def test_isolated(inner) -> None:
                    isolate_journal(inner)
                    dispatch.configure(config.Config(Path("."), "acme/widgets"))
                    dispatch._PENDING_RISK[7] = {"head": HEAD}
                    dispatch._ESCALATED.add(7)
                    dispatch.VERIFIED_WORKERS.add("body")
                    with mock.patch.object(dispatch, "gh_json", return_value={"login": "Body"}):
                        dispatch.host_claim_login()
                    dispatch.record("risk", ticket=7, head=HEAD, tier="Low", matched_rules=[])
                    inner.assertEqual("test body", "failed")

            result, created, observed = self.run_case(Case("test_isolated"), expected)
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.failures), 1)
            self.assertIn("'test body' != 'failed'", result.failures[0][1])
            self.assert_restored_first(created, observed)
            self.assertIsNotNone(observed[0]["journal"])
            self.assert_restored(expected, seeded)


class _RestoreTarget(types.ModuleType):
    """A module whose `frozen` and `sealed` bindings are read-only properties: setattr/delattr
    on either really fail."""

    @property
    def frozen(self) -> str:
        return "frozen"

    @property
    def sealed(self) -> str:
        return "sealed"


class _ExplodingTarget(_RestoreTarget):
    def __setattr__(self, name: str, value: object) -> None:
        if name == "boom":
            raise RuntimeError("unexpected restore failure")
        super().__setattr__(name, value)


class RestoreFailureTest(unittest.TestCase):
    """Each restore operation fails for real; every other restoration still runs, and the
    first error is re-raised. The failing binding sits between bindings set before and
    after it, so a loop that stops at the first error leaves later ones unrestored."""

    def setUp(self) -> None:
        home = os.getcwd()
        dirs = [tempfile.TemporaryDirectory() for _ in range(3)]
        for directory in dirs:
            self.addCleanup(directory.cleanup)
        self.addCleanup(os.chdir, home)
        saved, elsewhere, scratch = (Path(d.name).resolve() for d in dirs)
        self.saved, self.elsewhere = str(saved), str(elsewhere)
        self.removed = str(scratch / "removed")
        self.not_a_dir = scratch / "file"
        self.not_a_dir.write_text("not a directory")
        os.chdir(self.elsewhere)

    def target(self, cls: type[_RestoreTarget] = _RestoreTarget) -> _RestoreTarget:
        module = cls("restore_target")
        for name in ("before", "after_set", "after_del"):
            setattr(module, name, "stale")
        return module

    @staticmethod
    def bindings(failing: dict[str, object]) -> dict[str, object]:
        return {"before": "orig-before", **failing, "after_set": "orig-after", "after_del": _UNSET}

    def assert_rest_restored(self, target: _RestoreTarget, cwd: str) -> None:
        self.assertEqual(target.before, "orig-before")
        self.assertEqual(target.after_set, "orig-after")
        self.assertFalse(hasattr(target, "after_del"))
        self.assertEqual(os.getcwd(), cwd)

    def test_each_failing_restore_op_still_runs_the_rest_and_reraises(self) -> None:
        cases = (
            ("chdir-missing", {}, self.removed, FileNotFoundError, self.removed),
            ("chdir-not-a-dir", {}, str(self.not_a_dir), NotADirectoryError, str(self.not_a_dir)),
            ("setattr-readonly", {"frozen": "x"}, self.saved, AttributeError, "has no setter"),
            ("delattr-readonly", {"frozen": _UNSET}, self.saved, AttributeError, "has no deleter"),
        )
        for op, failing, cwd, error, detail in cases:
            with self.subTest(op=op):
                os.chdir(self.elsewhere)
                target = self.target()
                with self.assertRaises(error) as raised:
                    restore_dispatch(self.bindings(failing), cwd, target)
                self.assertIs(type(raised.exception), error)
                self.assertIn(detail, str(raised.exception))
                expected_cwd = self.elsewhere if op.startswith("chdir") else self.saved
                self.assert_rest_restored(target, expected_cwd)

    def test_first_error_wins_when_several_ops_fail(self) -> None:
        target = self.target()
        # chdir fails (FileNotFoundError), setattr on `frozen` and delattr on `sealed` both
        # fail (AttributeError); the chdir error is first and must be the one raised.
        bindings = self.bindings({"frozen": "x", "sealed": _UNSET})
        with self.assertRaises(FileNotFoundError) as raised:
            restore_dispatch(bindings, self.removed, target)
        self.assertIs(type(raised.exception), FileNotFoundError)
        self.assertEqual(raised.exception.filename, self.removed)
        self.assertEqual(target.frozen, "frozen")
        self.assertEqual(target.sealed, "sealed")
        self.assert_rest_restored(target, self.elsewhere)

    def test_error_outside_the_restore_boundary_propagates_immediately(self) -> None:
        target = self.target(_ExplodingTarget)
        with self.assertRaisesRegex(RuntimeError, "unexpected restore failure"):
            restore_dispatch(self.bindings({"boom": 1}), self.saved, target)
        self.assertEqual(target.before, "orig-before")
        self.assertEqual(target.after_set, "stale")


if __name__ == "__main__":
    unittest.main()
