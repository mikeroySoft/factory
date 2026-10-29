from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, reviewer_eval

ROOT = Path(__file__).resolve().parents[1]


def review_output(verdict: str = "REVISE", marker: str = "") -> str:
    findings = (
        [{"severity": "important", "pass": "bug", "path": "change.py", "line": 1,
          "summary": "Primary defect."}]
        if verdict == "REVISE" else []
    )
    prefix = f"{marker}\n" if marker else ""
    return (
        prefix
        + "```json\n"
        + json.dumps({"verdict": verdict, "findings": findings})
        + f"\n```\nVERDICT: {verdict}"
    )


def run(
    *, passed: bool, output: str | None = None, returncode: int = 0, timeout: bool = False
) -> dict:
    return {
        "passed": passed,
        "output": review_output() if output is None else output,
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
            [run(passed=True), run(passed=True, output="VERDICT: REVISE"), run(passed=True)],
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
        row = {"returncode": 0, "output": review_output("REVISE")}
        self.assertTrue(reviewer_eval.grade_output(row, oracle, {"D1"})["passed"])
        self.assertFalse(reviewer_eval.grade_output(row, oracle, set())["passed"])
        self.assertFalse(
            reviewer_eval.grade_output(
                {"returncode": 0, "output": review_output("APPROVE")}, oracle, {"D1"}
            )["passed"]
        )
        self.assertIsNone(
            reviewer_eval.grade_output(
                {"returncode": 0, "output": "VERDICT: REVISE"}, oracle, {"D1"}
            )["verdict"]
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
        outputs = [
            review_output("REVISE", "one"),
            review_output("APPROVE", "two"),
            review_output("REVISE", "three"),
        ]
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
        self.assertEqual(summary["regressions"], ["case-1"])
        self.assertEqual(summary["flips"], [{
            "case": "case-1", "baseline": "always-pass", "head": "always-fail",
        }])
        self.assertEqual(summary["verdict"], "block")
        self.assertIn("case-1", reviewer_eval.format_report(summary))
        improved = reviewer_eval.summarize(
            ["case-1"],
            [{"case": "case-1", **run(passed=True)} for _ in range(3)],
            {"statuses": {"case-1": "always-fail"}},
            candidate="b" * 40,
            corpus_version=2,
        )
        self.assertEqual(improved["flips"], [{
            "case": "case-1", "baseline": "always-fail", "head": "always-pass",
        }])
        self.assertEqual(improved["verdict"], "pass")
        self.assertIn("always-fail → always-pass", reviewer_eval.format_report(improved))

    def test_flaky_source_tags(self) -> None:
        mixed = (True, False, True)
        different = [
            {
                "case": "agent",
                **run(passed=value, output=review_output("REVISE", f"output-{index}")),
            }
            for index, value in enumerate(mixed)
        ]
        identical = [
            {"case": "judge", **run(passed=value, output=review_output("REVISE", "same"))}
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

    def test_exact_head_and_current_baseline_gate_evidence(self) -> None:
        head = "a" * 40
        baseline = {"main_sha": "m" * 40, "statuses": {"case": "always-pass"}}
        digest = reviewer_eval.baseline_digest(baseline)
        rows = [{
            "event": "reviewer-eval-summary", "candidate": head,
            "status": "completed", "verdict": "pass", "baseline_sha256": digest,
        }]
        self.assertTrue(reviewer_eval.merge_allowed(True, head, rows, digest))
        self.assertFalse(reviewer_eval.merge_allowed(True, "b" * 40, rows, digest))
        self.assertFalse(reviewer_eval.merge_allowed(True, head, rows, "0" * 64))
        self.assertFalse(reviewer_eval.merge_allowed(True, head, [{**rows[0], "verdict": "block"}], digest))
        self.assertTrue(reviewer_eval.merge_allowed(False, "b" * 40, [], None))

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
                 mock.patch.object(
                     reviewer_eval, "baseline_ready", side_effect=FileNotFoundError("missing corpus")
                 ), \
                 mock.patch.object(dispatch, "run"):
                dispatch.merge_pass_locked(False)

            waits = [
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "lifecycle" and row.get("kind") == "wait"
            ]
            self.assertEqual(waits[-1]["wait"]["reason"], "reviewer_eval_required")
            command = waits[-1]["wait"]["details"]["command"]
            self.assertIn(str(root), command)
            self.assertIn(str(cfg.factory / "wt-7"), command)
            self.assertIn("-m factory reviewer-eval", command)
            baseline = {
                "main_sha": "m" * 40,
                "corpus_version": 2,
                "statuses": {"case-1": "always-pass"},
            }
            reviewer_eval.baseline_path(cfg).write_text(json.dumps(baseline))
            digest = reviewer_eval.baseline_digest(baseline)

            lifecycle.append(cfg.factory / "events.jsonl", {
                "event": reviewer_eval.EVENT,
                "candidate": head,
                "status": "completed",
                "verdict": "block",
                "baseline_sha256": digest,
                "flips": [{
                    "case": "case-1", "baseline": "always-pass", "head": "always-fail",
                }],
                "regressions": ["case-1"],
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
        expected = config.command_model(config.load(ROOT, worktree=True).reviewer)
        self.assertIsNotNone(expected)
        for module in modules:
            self.assertFalse(hasattr(module, "MODEL"))
            self.assertEqual(module.configured_reviewer_model(ROOT), expected)

    def test_historical_config_model_fallback_and_secure_checkout_root(self) -> None:
        calibration = ROOT / "experiments/reviewer-calibration"
        sys.path.insert(0, str(calibration))
        self.addCleanup(lambda: sys.path.remove(str(calibration)))
        spec = importlib.util.spec_from_file_location(
            "test_run_calibration_legacy", calibration / "run_calibration.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory)
            package = legacy / "factory"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "config.py").write_text(
                'DEFAULT_REVIEWER = ["omp", "-p", "--model", "historical/reviewer", "{prompt}"]\n'
                "def load(root):\n"
                "    raise AssertionError('legacy fallback should use DEFAULT_REVIEWER')\n"
            )
            self.assertEqual(
                module.configured_reviewer_model(legacy), "historical/reviewer"
            )
        self.assertNotEqual(module.CHECKOUT_ROOT, Path("/tmp/cal-checkout"))
        self.assertEqual(stat.S_IMODE(module.CHECKOUT_ROOT.stat().st_mode), 0o700)

    def test_calibration_subprocesses_drop_lifecycle_context(self) -> None:
        context = '{"schema_version":1,"path":"/tmp/live-events.jsonl"}'
        settings = subprocess.CompletedProcess(
            [], 0, json.dumps({"model": "reviewer/model", "review_policy": "REVIEW.md"}), ""
        )
        hashes = subprocess.CompletedProcess([], 0, '{"case":"hash"}', "")
        with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: context}), \
             mock.patch.object(subprocess, "run", side_effect=[settings, hashes]) as called, \
             mock.patch.object(config, "git", return_value="a" * 40):
            reviewer_eval.contract_fingerprint(ROOT)
        for call in called.call_args_list:
            self.assertNotIn(lifecycle.CONTEXT_ENV, call.kwargs["env"])

    def test_trigger_rejects_checkout_not_at_pr_head(self) -> None:
        with mock.patch.object(config, "git", return_value="b" * 40):
            with self.assertRaisesRegex(RuntimeError, "PR head"):
                reviewer_eval.trigger_for_checkout(
                    ["factory/dispatch.py"], ROOT, ROOT, "REVIEW.md", expected_head="a" * 40
                )


    def test_contract_fingerprint_uses_revision_prompt_model_and_policy(self) -> None:
        def clone(name: str, parent: Path) -> Path:
            checkout = parent / name
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", str(ROOT), str(checkout)],
                check=True,
            )
            subprocess.run(
                ["git", "remote", "set-url", "origin", "https://github.com/mikeroySoft/factory"],
                cwd=checkout,
                check=True,
            )
            for relative in (
                "experiments/reviewer-calibration/capture_contract.py",
                "experiments/reviewer-calibration/run_calibration.py",
                "experiments/reviewer-calibration/run_tools.py",
            ):
                checkout.joinpath(relative).write_bytes(ROOT.joinpath(relative).read_bytes())
            subprocess.run(["git", "config", "user.name", "Reviewer Eval Test"], cwd=checkout, check=True)
            subprocess.run(
                ["git", "config", "user.email", "reviewer-eval@example.invalid"],
                cwd=checkout,
                check=True,
            )
            return checkout

        def commit(checkout: Path, *paths: str) -> str:
            subprocess.run(["git", "add", "--", *paths], cwd=checkout, check=True)
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "test reviewer contract"],
                cwd=checkout,
                check=True,
            )
            return subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            xdg = parent / "xdg"
            xdg.mkdir()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}):
                base = reviewer_eval.contract_fingerprint(ROOT)

                prompt_checkout = clone("prompt", parent)
                dispatch_path = prompt_checkout / "factory/dispatch.py"
                dispatch_text = dispatch_path.read_text()
                changed = dispatch_text.replace(
                    "Review the diff in `./review.diff`",
                    "Review the complete diff in `./review.diff`",
                    1,
                )
                self.assertNotEqual(dispatch_text, changed)
                dispatch_path.write_text(changed)
                commit(prompt_checkout, "factory/dispatch.py")
                prompt = reviewer_eval.contract_fingerprint(prompt_checkout)
                self.assertTrue(
                    reviewer_eval.requires_eval(
                        ["factory/dispatch.py"], base, prompt, "REVIEW.md"
                    )
                )

                model_checkout = clone("model", parent)
                config_path = model_checkout / "factory/config.py"
                config_text = config_path.read_text()
                changed = config_text.replace(
                    "anthropic/claude-fable-5-1",
                    "openai-codex/reviewer-eval-test",
                    1,
                )
                self.assertNotEqual(config_text, changed)
                config_path.write_text(changed)
                commit(model_checkout, "factory/config.py")
                model = reviewer_eval.contract_fingerprint(model_checkout)
                self.assertEqual(model["model"], "openai-codex/reviewer-eval-test")
                self.assertTrue(
                    reviewer_eval.requires_eval(
                        ["factory/config.py"], base, model, "REVIEW.md"
                    )
                )

                policy_checkout = clone("policy", parent)
                marker = "REVIEWER-EVAL-POLICY-MARKER"
                (policy_checkout / "REVIEW.md").write_text(marker + "\n")
                policy_head = commit(policy_checkout, "REVIEW.md")
                capture = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import pathlib, shutil, sys, tempfile;"
                            "root=pathlib.Path(sys.argv[1]);"
                            "sys.path.insert(0,str(root/'experiments/reviewer-calibration'));"
                            "import run_tools;"
                            "tmp=pathlib.Path(tempfile.mkdtemp());"
                            "source=tmp/'source';"
                            "run_tools.worktree('12-before',source);"
                            "print(run_tools.capture(sys.argv[2],'12-before',source));"
                            "shutil.rmtree(tmp)"
                        ),
                        str(policy_checkout),
                        policy_head,
                    ],
                    cwd=policy_checkout,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                self.assertIn(marker, capture)

    def test_baseline_ready_requires_current_main_contract(self) -> None:
        fingerprint = {"prompt_sha256": {"case": "prompt"}, "model": "reviewer/one"}
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            cfg.factory.mkdir()
            path = reviewer_eval.baseline_path(cfg)
            path.write_text(json.dumps({
                "main_sha": "a" * 40,
                "corpus_version": 2,
                "statuses": {"case": "always-pass"},
                **fingerprint,
            }))
            with mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                 mock.patch.object(reviewer_eval, "contract_fingerprint", return_value=fingerprint):
                self.assertTrue(reviewer_eval.baseline_ready(cfg))
            changed = {**fingerprint, "model": "reviewer/two"}
            with mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                 mock.patch.object(reviewer_eval, "contract_fingerprint", return_value=changed):
                self.assertFalse(reviewer_eval.baseline_ready(cfg))

    def test_failed_runner_does_not_reuse_stale_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "results.json").write_text('[{"case":"stale"}]')
            proc = mock.Mock()
            proc.communicate.return_value = ("", "crashed")
            proc.returncode = 2
            context = '{"schema_version":1,"path":"/tmp/live-events.jsonl"}'
            with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: context}), \
                 mock.patch.object(subprocess, "Popen", return_value=proc) as popen:
                rows, timed_out = reviewer_eval._run_runner(
                    ROOT, out, "a" * 40, 1.0
                )
            self.assertNotIn(lifecycle.CONTEXT_ENV, popen.call_args.kwargs["env"])
            self.assertFalse(timed_out)
            self.assertEqual(rows, [])

    def test_unaccepted_candidate_run_does_not_replace_baseline(self) -> None:
        candidate = "b" * 40
        rows = [
            {
                "case": "12-before",
                "returncode": 0,
                "output": review_output("REVISE"),
                "prompt_sha256": "prompt",
            }
            for _ in range(3)
        ]
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            cfg.factory.mkdir()
            with mock.patch.object(reviewer_eval, "case_ids", return_value=["12-before"]), \
                 mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                 mock.patch.object(reviewer_eval, "_run_runner", return_value=(rows, False)), \
                 mock.patch.object(reviewer_eval, "_judge", return_value=({"D1"}, {})), \
                 mock.patch.object(reviewer_eval, "_candidate_accepted", return_value=False), \
                 mock.patch.object(reviewer_eval, "_append"), \
                 mock.patch.object(config, "git", return_value=candidate), \
                 mock.patch.object(config, "load", return_value=cfg):
                summary = reviewer_eval.run(cfg, ROOT)
            self.assertFalse(summary["accepted"])
            self.assertFalse(reviewer_eval.baseline_path(cfg).exists())

    def test_tools_runner_named_contracts_and_host_model_override(self) -> None:
        calibration = ROOT / "experiments/reviewer-calibration"
        sys.path.insert(0, str(calibration))
        self.addCleanup(lambda: sys.path.remove(str(calibration)))
        spec = importlib.util.spec_from_file_location(
            "test_run_tools_aliases", calibration / "run_tools.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertEqual(module.contract_commit("landed"), module.CONTRACTS["landed"])

        with tempfile.TemporaryDirectory() as directory:
            host = Path(directory) / "factory"
            host.mkdir()
            host.joinpath("config.toml").write_text(
                '[defaults.review]\n'
                'command = ["omp", "-p", "--model", "host/reviewer", "{prompt}"]\n'
            )
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
                self.assertEqual(module.configured_reviewer_model(ROOT), "host/reviewer")

if __name__ == "__main__":
    unittest.main()
