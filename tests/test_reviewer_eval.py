from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, reviewer_eval

ROOT = Path(__file__).resolve().parents[1]


def run(*, passed: bool, output: str = "VERDICT: REVISE", returncode: int = 0, timeout: bool = False) -> dict:
    return {
        "passed": passed,
        "output": output,
        "returncode": returncode,
        "timeout": timeout,
    }


class ReviewerEvalTest(unittest.TestCase):
    def test_three_run_statuses(self) -> None:
        self.assertEqual(reviewer_eval.grade_runs([run(passed=True)] * 3), "always-pass")
        self.assertEqual(reviewer_eval.grade_runs([run(passed=False)] * 3), "always-fail")
        self.assertEqual(
            reviewer_eval.grade_runs([run(passed=True), run(passed=False), run(passed=True)]),
            "flaky",
        )
        missing = (
            [run(passed=True), run(passed=True, returncode=1), run(passed=True)],
            [run(passed=True), run(passed=True, output=""), run(passed=True)],
            [run(passed=True), run(passed=True, output="no verdict"), run(passed=True)],
            [run(passed=True), run(passed=True, timeout=True), run(passed=True)],
            [run(passed=True), run(passed=True)],
        )
        for rows in missing:
            with self.subTest(rows=rows):
                self.assertEqual(reviewer_eval.grade_runs(rows), "missing")

    def test_grade_requires_verdict_and_primary_defect_coverage(self) -> None:
        oracle = {
            "expected_verdict": "REVISE",
            "findings": [
                {"id": "D1", "severity": "primary"},
                {"id": "D2", "severity": "secondary"},
            ],
        }
        row = {"returncode": 0, "output": "details\nVERDICT: REVISE"}
        self.assertTrue(reviewer_eval.grade_output(row, oracle, {"D1"})["passed"])
        self.assertFalse(reviewer_eval.grade_output(row, oracle, set())["passed"])
        self.assertFalse(
            reviewer_eval.grade_output(
                {"returncode": 0, "output": "VERDICT: APPROVE"}, oracle, {"D1"}
            )["passed"]
        )

    def test_missing_and_absent_case_rows_block(self) -> None:
        baseline = {"statuses": {"case-1": "always-pass", "case-2": "always-pass"}}
        rows = [
            {"case": "case-1", **run(passed=True)},
            {"case": "case-1", **run(passed=True, returncode=1)},
            {"case": "case-1", **run(passed=True)},
        ]
        summary = reviewer_eval.summarize(
            ["case-1", "case-2"], rows, baseline, candidate="a" * 40, corpus_version=2
        )
        self.assertEqual(summary["statuses"], {"case-1": "missing", "case-2": "missing"})
        self.assertEqual(summary["verdict"], "block")
        self.assertEqual(summary["missing"], ["case-1", "case-2"])

    def test_single_failure_is_flaky_and_does_not_block(self) -> None:
        outputs = ["one\nVERDICT: REVISE", "two\nVERDICT: APPROVE", "three\nVERDICT: REVISE"]
        rows = [
            {"case": "case-1", **run(passed=value, output=output)}
            for value, output in zip((True, False, True), outputs, strict=True)
        ]
        summary = reviewer_eval.summarize(
            ["case-1"], rows, {"statuses": {"case-1": "always-pass"}},
            candidate="a" * 40, corpus_version=2,
        )
        self.assertEqual(summary["statuses"]["case-1"], "flaky")
        self.assertEqual(summary["verdict"], "pass")
        self.assertEqual(summary["flaky"], [{"case": "case-1", "tag": "agent-flaky"}])

    def test_stable_pass_to_fail_blocks_and_report_names_case(self) -> None:
        rows = [{"case": "case-1", **run(passed=False)} for _ in range(3)]
        summary = reviewer_eval.summarize(
            ["case-1"], rows, {"statuses": {"case-1": "always-pass"}},
            candidate="a" * 40, corpus_version=2,
        )
        self.assertEqual(summary["flips"], ["case-1"])
        self.assertEqual(summary["verdict"], "block")
        self.assertIn("case-1", reviewer_eval.format_report(summary))

    def test_flaky_source_tags(self) -> None:
        mixed = (True, False, True)
        different = [
            {"case": "agent", **run(passed=value, output=f"output-{index}\nVERDICT: REVISE")}
            for index, value in enumerate(mixed)
        ]
        identical = [
            {"case": "judge", **run(passed=value, output="same\nVERDICT: REVISE")}
            for value in mixed
        ]
        summary = reviewer_eval.summarize(
            ["agent", "judge"], different + identical, {"statuses": {}},
            candidate="a" * 40, corpus_version=2,
        )
        self.assertEqual(summary["flaky"], [
            {"case": "agent", "tag": "agent-flaky"},
            {"case": "judge", "tag": "judge-flaky"},
        ])

    def test_trigger_needs_contract_path_and_changed_prompt_or_model(self) -> None:
        base = {"prompt_sha256": {"case": "same"}, "model": "reviewer/one"}
        self.assertFalse(reviewer_eval.requires_eval(["factory/dispatch.py"], base, base, "REVIEW.md"))
        changed_prompt = {**base, "prompt_sha256": {"case": "different"}}
        self.assertTrue(
            reviewer_eval.requires_eval(["factory/dispatch.py"], base, changed_prompt, "REVIEW.md")
        )
        changed_model = {**base, "model": "reviewer/two"}
        self.assertTrue(
            reviewer_eval.requires_eval(["factory/config.py"], base, changed_model, "REVIEW.md")
        )
        self.assertFalse(
            reviewer_eval.requires_eval(["factory/watch.py"], base, changed_model, "REVIEW.md")
        )
        self.assertTrue(reviewer_eval.requires_eval(["REVIEW.md"], base, changed_prompt, "REVIEW.md"))

    def test_candidate_run_cannot_replace_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            original = {
                "main_sha": "a" * 40,
                "corpus_version": 2,
                "statuses": {"old": "always-pass"},
            }
            path.write_text(json.dumps(original))
            candidate = {
                "candidate": "b" * 40,
                "status": "completed",
                "corpus_version": 2,
                "statuses": {"new": "always-pass"},
                "prompt_sha256": {},
                "model": "reviewer/one",
            }
            self.assertFalse(reviewer_eval.write_baseline(path, candidate, "a" * 40))
            self.assertEqual(json.loads(path.read_text()), original)
            candidate["candidate"] = "a" * 40
            self.assertTrue(reviewer_eval.write_baseline(path, candidate, "a" * 40))
            self.assertEqual(json.loads(path.read_text())["statuses"], {"new": "always-pass"})

    def test_config_loads_reviewer_eval_budget_and_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / config.CONFIG_NAME).write_text(
                '[repo]\nslug = "acme/widgets"\n'
                '[reviewer_eval]\nmax_cases = 12\nbudget_min = 90\nschedule = true\n'
            )
            with mock.patch.object(config, "repo_root", return_value=root), \
                 mock.patch.object(config, "host_config", return_value={}):
                cfg = config.load(root)
            self.assertEqual(
                (
                    cfg.reviewer_eval_max_cases,
                    cfg.reviewer_eval_budget_min,
                    cfg.reviewer_eval_schedule,
                ),
                (12, 90, True),
            )

    def test_exact_head_gate_evidence(self) -> None:
        head = "a" * 40
        rows = [{
            "event": "reviewer-eval-summary", "candidate": head,
            "status": "completed", "verdict": "pass",
        }]
        self.assertTrue(reviewer_eval.merge_allowed(True, head, rows))
        self.assertFalse(reviewer_eval.merge_allowed(True, "b" * 40, rows))
        self.assertFalse(reviewer_eval.merge_allowed(True, head, [{**rows[0], "verdict": "block"}]))
        self.assertTrue(reviewer_eval.merge_allowed(False, "b" * 40, []))

    def test_merge_refuses_triggering_head_without_result_and_journals_command(self) -> None:
        from factory import dispatch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(root=root, repo="acme/widgets", reviewer_eval_schedule=True)
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            head = "a" * 40
            pr = {
                "number": 42, "headRefName": "agent/7", "headRefOid": head,
                "baseRefName": "main", "isDraft": False,
                "labels": [{"name": dispatch.FACTORY_APPROVED}],
                "reviewDecision": "APPROVED", "state": "OPEN", "title": "review prompt",
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
                 mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                 mock.patch.object(reviewer_eval, "trigger_for_checkout", return_value=True), \
                 mock.patch.object(reviewer_eval, "baseline_ready", return_value=True), \
                 mock.patch.object(dispatch, "run"):
                dispatch.merge_pass_locked(False)

            waits = [
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "lifecycle" and row.get("kind") == "wait"
            ]
            self.assertEqual(waits[-1]["wait"]["reason"], "reviewer_eval_required")
            command = waits[-1]["wait"]["details"]["command"]
            self.assertIn(str(cfg.factory / "wt-7"), command)
            self.assertIn("-m factory reviewer-eval", command)

            lifecycle.append(cfg.factory / "events.jsonl", {
                "event": reviewer_eval.EVENT,
                "candidate": head,
                "status": "completed",
                "verdict": "block",
                "flips": ["case-1"],
                "missing": [],
                "flaky": [],
            })
            with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                 mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                 mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                 mock.patch.object(reviewer_eval, "trigger_for_checkout", return_value=True), \
                 mock.patch.object(reviewer_eval, "baseline_ready", return_value=True), \
                 mock.patch.object(dispatch, "pr_comment") as comment, \
                 mock.patch.object(dispatch, "run"):
                dispatch.merge_pass_locked(False)
            self.assertIn("case-1", comment.call_args.args[1])

    def test_runners_use_checkout_reviewer_model(self) -> None:
        calibration = ROOT / "experiments/reviewer-calibration"
        sys.path.insert(0, str(calibration))
        self.addCleanup(lambda: sys.path.remove(str(calibration)))
        modules = []
        for name in ("run_calibration", "run_tools"):
            spec = importlib.util.spec_from_file_location(f"test_{name}", calibration / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            modules.append(module)
        expected = config.command_model(config.DEFAULT_REVIEWER)
        self.assertIsNotNone(expected)
        for module in modules:
            self.assertFalse(hasattr(module, "MODEL"))
            self.assertEqual(module.configured_reviewer_model(ROOT), expected)


if __name__ == "__main__":
    unittest.main()
