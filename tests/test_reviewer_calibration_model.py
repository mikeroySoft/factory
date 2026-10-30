from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments" / "reviewer-calibration"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EXP / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReviewerCalibrationModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(EXP))
        cls.addClassCleanup(lambda: sys.path.remove(str(EXP)))
        cls.calibration = load_module("reviewer_calibration_model", "run_calibration.py")
        cls.tools = load_module("reviewer_calibration_tools_model", "run_tools.py")

    def test_default_model_comes_from_factory_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".factory.toml").write_text(
                '[repo]\nslug = "acme/widgets"\n'
                '[review]\ncommand = ["reviewer", "--model", "fixture/reviewer", "{prompt}"]\n'
            )
            self.assertEqual(
                self.calibration.configured_reviewer_model(root), "fixture/reviewer"
            )
            self.assertEqual(self.tools.configured_reviewer_model(root), "fixture/reviewer")
        self.assertFalse(hasattr(self.calibration, "MODEL"))
        self.assertFalse(hasattr(self.tools, "MODEL"))

    def test_run_config_records_the_model_passed_to_client(self) -> None:
        called = []

        def client(contract: str, case: str, sample: int, model: str) -> dict:
            called.append(model)
            return {
                "returncode": 0,
                "seconds": 0.1,
                "output": "VERDICT: APPROVE",
                "stderr": "",
                "usage": {"model": model, "prompt_tokens": 2, "completion_tokens": 1},
            }

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            rows = self.calibration.run_experiment(
                out, 1, ["issue36"], "fixture/model", client
            )
            recorded = json.loads((out / "config.json").read_text())
        self.assertTrue(rows)
        self.assertEqual(recorded["model"], "fixture/model")
        self.assertEqual(set(called), {"fixture/model"})
        self.assertEqual({row["model"] for row in rows}, {"fixture/model"})

    @staticmethod
    def _split_file(root: Path, version: int = 2) -> Path:
        path = root / "splits.json"
        path.write_text(json.dumps({
            "version": version,
            "splits": {"train": ["train-case"], "lockbox": ["lockbox-case"]},
        }))
        return path

    def test_models_matrix_writes_quality_cost_and_wall_report(self) -> None:
        calls = []

        def runner(model: str, out: Path) -> tuple[list[dict], float]:
            calls.append(model)
            passed = model == "baseline/model"
            rows = [
                {
                    "case": case,
                    "sample": sample,
                    "passed": passed,
                    "seconds": 0.1,
                    "usage": {"model": model, "prompt_tokens": 10, "completion_tokens": 2},
                }
                for sample in range(1, 4)
                for case in self.calibration.CASES
            ]
            return rows, 1.25

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            with mock.patch.object(
                self.calibration.config, "git", return_value="fixture-head"
            ), mock.patch.object(
                self.calibration,
                "_grade_runs",
                side_effect=lambda rows: (
                    "always-pass" if all(row["passed"] for row in rows) else "always-fail"
                ),
            ):
                status = self.calibration.main(
                    [str(out), "--models", "baseline/model,candidate/model"],
                    matrix_runner=runner,
                    prices={"baseline/model": {"prompt": 1.0, "completion": 2.0}},
                )
            report = json.loads((out / "report.json").read_text())
            markdown = (out / "report.md").read_text()
        self.assertEqual(status, 0)
        self.assertEqual(calls, ["baseline/model", "candidate/model"])
        self.assertEqual([row["model"] for row in report["models"]], calls)
        for row in report["models"]:
            self.assertEqual(set(row), {
                "model", "train", "lockbox", "stable_flips", "missing_cases",
                "prompt_tokens", "completion_tokens", "cost", "unpriced",
                "wall_seconds", "verdict",
            })
            self.assertEqual(set(row["train"]), {"mean", "pstdev"})
            self.assertEqual(set(row["lockbox"]), {"mean", "pstdev"})
        self.assertIsNotNone(report["models"][0]["cost"])
        self.assertFalse(report["models"][0]["unpriced"])
        self.assertIsNone(report["models"][1]["cost"])
        self.assertTrue(report["models"][1]["unpriced"])
        self.assertEqual(
            report["models"][1]["stable_flips"], self.calibration.CASES
        )
        self.assertIn("baseline/model", markdown)
        self.assertIn("candidate/model", markdown)

    def test_verdict_boundaries(self) -> None:
        verdict = self.calibration.verdict
        common = {
            "train_pstdev": 0.0,
            "lockbox_mean": 0.6,
            "lockbox_pstdev": 0.0,
            "baseline_train_mean": 1.0,
            "baseline_lockbox_mean": 0.6,
            "stable_flips": 0,
        }
        self.assertEqual(verdict(train_mean=0.9167, **common), "viable")
        self.assertEqual(verdict(train_mean=0.9166, **common), "worse")
        self.assertEqual(
            verdict(train_mean=0.9167, **{**common, "stable_flips": 1}), "worse"
        )
        self.assertEqual(
            verdict(train_mean=0.9167, **{**common, "train_pstdev": 0.0834}),
            "inconclusive",
        )

    def test_noisy_baseline_makes_every_candidate_inconclusive(self) -> None:
        common = {
            "train_mean": 1.0,
            "train_pstdev": 0.0,
            "lockbox_mean": 1.0,
            "lockbox_pstdev": 0.0,
            "baseline_train_mean": 1.0,
            "baseline_lockbox_mean": 1.0,
            "stable_flips": 0,
        }
        self.assertEqual(
            self.calibration.verdict(
                **common, baseline_train_pstdev=0.0834, baseline_lockbox_pstdev=0.0
            ),
            "inconclusive",
        )
        self.assertEqual(
            self.calibration.verdict(
                **common, baseline_train_pstdev=0.0, baseline_lockbox_pstdev=0.0834
            ),
            "inconclusive",
        )

    def test_missing_runs_are_reported_without_stable_flips(self) -> None:
        def runner(model: str, out: Path) -> tuple[list[dict], float]:
            missing = model == "offline/model"
            rows = [
                {
                    "case": case,
                    "sample": sample,
                    "returncode": 1 if missing else 0,
                    "output": "" if missing else "VERDICT: APPROVE",
                    "passed": not missing,
                    "grade_missing": missing,
                    "seconds": 0.1,
                    "usage": {"model": model, "prompt_tokens": 1, "completion_tokens": 1},
                }
                for sample in range(1, 4)
                for case in ("train-case", "lockbox-case")
            ]
            return rows, 0.5

        def grade_runs(rows: list[dict]) -> str:
            if any(row.get("grade_missing") for row in rows):
                return "missing"
            return "always-pass" if all(row["passed"] for row in rows) else "always-fail"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_file = self._split_file(root)
            with mock.patch.object(
                self.calibration, "_grade_runs", side_effect=grade_runs
            ) as shared_grader:
                report = self.calibration.replay_models(
                    ["baseline/model", "offline/model"],
                    root / "run",
                    runner,
                    {},
                    split_file,
                )
        candidate = report["models"][1]
        self.assertEqual(candidate["missing_cases"], ["train-case", "lockbox-case"])
        self.assertEqual(candidate["stable_flips"], [])
        self.assertEqual(candidate["verdict"], "inconclusive")
        self.assertEqual(shared_grader.call_count, 4)

    def test_missing_baseline_makes_candidate_inconclusive(self) -> None:
        def runner(model: str, out: Path) -> tuple[list[dict], float]:
            missing = model == "baseline/model"
            return [
                {
                    "case": case,
                    "sample": sample,
                    "passed": not missing,
                    "grade_missing": missing,
                    "usage": {"model": model, "prompt_tokens": 1, "completion_tokens": 1},
                }
                for sample in range(1, 4)
                for case in ("train-case", "lockbox-case")
            ], 0.5

        def grade_runs(rows: list[dict]) -> str:
            if any(row["grade_missing"] for row in rows):
                return "missing"
            return "always-pass"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                self.calibration, "_grade_runs", side_effect=grade_runs
            ):
                report = self.calibration.replay_models(
                    ["baseline/model", "candidate/model"],
                    root / "run",
                    runner,
                    {},
                    self._split_file(root),
                )

        self.assertEqual(report["models"][1]["verdict"], "inconclusive")

    def test_wall_seconds_exclude_grading_latency(self) -> None:
        def runner(model: str, out: Path) -> tuple[list[dict], float]:
            rows = [
                {
                    "case": case,
                    "sample": sample,
                    "passed": True,
                    "seconds": 0.25,
                    "usage": {"model": model, "prompt_tokens": 1, "completion_tokens": 1},
                }
                for sample in range(1, 4)
                for case in ("train-case", "lockbox-case")
            ]
            time.sleep(0.02)  # Simulated grading after the measured model calls.
            return rows, 1.5

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_file = self._split_file(root)
            with mock.patch.object(
                self.calibration, "_grade_runs", return_value="always-pass"
            ):
                report = self.calibration.replay_models(
                    ["baseline/model"], root / "run", runner, {}, split_file
                )
        self.assertEqual(report["models"][0]["wall_seconds"], 1.5)

    def test_split_version_fails_before_runner(self) -> None:
        called = False

        def runner(model: str, out: Path) -> tuple[list[dict], float]:
            nonlocal called
            called = True
            return [], 0.0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_file = self._split_file(root, version=1)
            with self.assertRaisesRegex(ValueError, "splits.json version 2 required"):
                self.calibration.replay_models(
                    ["a", "b"], root / "run", runner, {}, split_file
                )
        self.assertFalse(called)

    def test_replay_does_not_invoke_gh_or_change_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_file = self._split_file(root)
            config_file = root / ".factory.toml"
            config_file.write_text("[repo]\nslug = \"acme/widgets\"\n")
            before = config_file.read_bytes()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "gh-called"
            gh = bin_dir / "gh"
            gh.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n")
            gh.chmod(0o755)

            def runner(model: str, out: Path) -> tuple[list[dict], float]:
                rows = [
                    {"case": case, "sample": sample, "passed": True, "seconds": 0.1,
                     "usage": {"model": model, "prompt_tokens": 1, "completion_tokens": 1}}
                    for sample in range(1, 4)
                    for case in ("train-case", "lockbox-case")
                ]
                return rows, 0.5

            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir}:{old_path}"
            try:
                with mock.patch.object(
                    self.calibration, "_grade_runs", return_value="always-pass"
                ):
                    self.calibration.replay_models(
                        ["a", "b"], root / "run", runner, {}, split_file
                    )
            finally:
                os.environ["PATH"] = old_path
            self.assertFalse(marker.exists())
            self.assertEqual(config_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
