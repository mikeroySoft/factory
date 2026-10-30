from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from factory import config

ROOT = Path(__file__).resolve().parents[1]


class ReviewerCalibrationGitlessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = ROOT / "experiments" / "reviewer-calibration" / "run_calibration.py"
        spec = importlib.util.spec_from_file_location("calibration_gitless", path)
        cls.calibration = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.calibration)

    def rows(self) -> list[dict]:
        return [
            {
                "case": case,
                "sample": sample,
                "passed": True,
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
            for sample in range(1, self.calibration.REPEATS + 1)
            for case in self.calibration.CASES
        ]

    def test_injected_runner_writes_report_without_git_metadata(self) -> None:
        runner = mock.Mock(return_value=(self.rows(), 1.25))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "report"
            with (
                mock.patch.object(self.calibration, "REPO", root),
                mock.patch.object(self.calibration, "_grade_runs", return_value="always-pass"),
            ):
                status = self.calibration.main(
                    [str(out), "--models", "fixture/model"],
                    matrix_runner=runner,
                    prices={},
                )
            report = json.loads((out / "report.json").read_text())
            markdown = (out / "report.md").read_text()
            runner.assert_called_once_with("fixture/model", out / "model-1")
        self.assertEqual(status, 0)
        model = report["models"][0]
        self.assertEqual(model["model"], "fixture/model")
        self.assertEqual(model["train"]["mean"], 1.0)
        self.assertEqual(model["lockbox"]["mean"], 1.0)
        self.assertEqual(model["wall_seconds"], 1.25)
        self.assertEqual(model["prompt_tokens"], len(self.rows()) * 10)
        self.assertEqual(model["verdict"], "viable")
        self.assertIn("fixture/model", markdown)

    def test_real_runner_still_requires_git_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(self.calibration, "REPO", root),
                self.assertRaisesRegex(config.ConfigError, "rev-parse HEAD"),
            ):
                self.calibration.main(
                    [str(root / "report"), "--models", "fixture/model"],
                    prices={},
                )
            self.assertFalse((root / "report" / "report.json").exists())

    def test_real_runner_receives_resolved_head(self) -> None:
        head = "a" * 40
        run_experiment = mock.Mock(return_value=self.rows())
        tools = types.SimpleNamespace(run_experiment=run_experiment)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "report"
            with (
                mock.patch.object(self.calibration.config, "git", return_value=head) as git,
                mock.patch.object(self.calibration, "_grade_rows", side_effect=lambda rows: rows),
                mock.patch.object(self.calibration, "_grade_runs", return_value="always-pass"),
                mock.patch.dict(sys.modules, {"run_tools": tools}),
            ):
                status = self.calibration.main(
                    [str(out), "--models", "fixture/model"],
                    prices={},
                )
            git.assert_called_once_with(self.calibration.REPO, "rev-parse", "HEAD")
            run_experiment.assert_called_once_with(
                out / "model-1", self.calibration.REPEATS, [head], "fixture/model"
            )
            self.assertTrue((out / "report.json").is_file())
        self.assertEqual(status, 0)
