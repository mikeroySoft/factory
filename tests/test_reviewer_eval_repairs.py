from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, reviewer_eval

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "experiments/reviewer-calibration"


def calibration_module():
    spec = importlib.util.spec_from_file_location(
        "reviewer_calibration_repairs", CALIBRATION / "run_calibration.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReviewerEvalRepairsTest(unittest.TestCase):
    def test_default_calibration_cli_resolves_checkout_model(self) -> None:
        module = calibration_module()
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(module, "configured_reviewer_model", autospec=True) as model, \
             mock.patch.object(module, "run_experiment", return_value=[]) as experiment:
            model.return_value = "checkout/reviewer"
            self.assertEqual(module.main([directory]), 0)
            model.assert_called_once_with(module.REPO)
            self.assertEqual(experiment.call_args.args[-1], "checkout/reviewer")

    def test_judge_has_no_checkout_or_github_authority(self) -> None:
        oracle = {"findings": [{"id": "D1", "severity": "primary", "defect": "defect"}]}
        seen = {}

        def judge(command, **kwargs):
            env = kwargs.get("env", os.environ)
            cwd = Path(kwargs["cwd"])
            seen.update(kwargs)
            self.assertNotEqual(cwd, ROOT)
            self.assertFalse(cwd.is_relative_to(ROOT))
            self.assertTrue(cwd.is_dir())
            self.assertEqual(list(cwd.iterdir()), [])
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertFalse(any(key.startswith("GIT_") for key in env))
            self.assertNotIn(lifecycle.CONTEXT_ENV, env)
            gh_config = Path(env["GH_CONFIG_DIR"])
            self.assertTrue(gh_config.is_dir())
            self.assertEqual(list(gh_config.iterdir()), [])
            return subprocess.CompletedProcess(command, 0, '{"covered":["D1"]}', "")

        with mock.patch.dict(os.environ, {
            "GH_TOKEN": "fixture-gh",
            "GITHUB_TOKEN": "fixture-github",
            "GH_CONFIG_DIR": "/fixture/authenticated-gh",
            "GIT_DIR": "/fixture/repository",
            lifecycle.CONTEXT_ENV: "fixture-context",
        }), mock.patch.object(subprocess, "run", side_effect=judge):
            covered, evidence = reviewer_eval._judge(
                ["fixture-judge", "{prompt}"], "untrusted reviewer output", oracle, ROOT, 3
            )
        self.assertEqual(covered, {"D1"})
        self.assertEqual(evidence["returncode"], 0)
        self.assertFalse(Path(seen["cwd"]).exists())
        self.assertFalse(Path(seen["env"]["GH_CONFIG_DIR"]).exists())

    def test_sigterm_removes_only_process_private_runner_checkout(self) -> None:
        script = (
            "import pathlib, signal, sys;"
            "sys.path.insert(0,sys.argv[1]);"
            "import run_calibration as runner;"
            "(runner.CHECKOUT_ROOT/'fixture').write_text('private');"
            "print(runner.CHECKOUT_ROOT,flush=True);"
            "signal.pause()"
        )
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            unrelated = temporary / "unrelated"
            unrelated.write_text("preserve")
            env = {**os.environ, "TMPDIR": directory}
            env.pop(lifecycle.CONTEXT_ENV, None)
            proc = subprocess.Popen(
                [sys.executable, "-c", script, str(CALIBRATION)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            )
            try:
                private = Path(proc.stdout.readline().strip())
                self.assertEqual(private.parent, temporary)
                self.assertTrue((private / "fixture").exists())
                proc.send_signal(signal.SIGTERM)
                _, err = proc.communicate(timeout=10)
                self.assertNotEqual(proc.returncode, 0, err)
                self.assertFalse(private.exists())
                self.assertEqual(unrelated.read_text(), "preserve")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def test_refresh_uses_merged_ancestry_not_completion_order(self) -> None:
        for previous in ("always-fail", "flaky"):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as directory:
                cfg, old, merged, new = self.main_history(Path(directory))
                baseline = self.summary(old, previous)
                self.assertTrue(reviewer_eval.write_baseline(
                    reviewer_eval.baseline_path(cfg), baseline, old
                ))
                digest = reviewer_eval.baseline_digest(
                    reviewer_eval.load_baseline(reviewer_eval.baseline_path(cfg))
                )
                improvement = {
                    "event": reviewer_eval.EVENT,
                    "candidate": "d" * 40,
                    "status": "completed",
                    "baseline_sha256": digest,
                    "flips": [{"case": "case", "baseline": previous, "head": "always-pass"}],
                }
                for row in (
                    baseline,
                    improvement,
                    {"event": "merged", "head": "d" * 40, "merge_sha": merged},
                    baseline,  # A pre-merge run finished after the merge.
                ):
                    lifecycle.append(cfg.factory / "events.jsonl", row)
                with mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                     mock.patch.object(
                         reviewer_eval, "contract_fingerprint", return_value=self.fingerprint()
                     ):
                    self.assertFalse(reviewer_eval.baseline_ready(cfg))
                    refreshed = self.summary(new, "always-pass")
                    lifecycle.append(cfg.factory / "events.jsonl", refreshed)
                    self.assertTrue(reviewer_eval.write_baseline(
                        reviewer_eval.baseline_path(cfg), refreshed, new
                    ))
                    self.assertTrue(reviewer_eval.baseline_ready(cfg))

    def test_late_accepted_main_run_cannot_overwrite_newer_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, old, _, new = self.main_history(Path(directory))
            path = reviewer_eval.baseline_path(cfg)
            self.assertTrue(reviewer_eval.write_baseline(
                path, self.summary(new, "always-pass"), new
            ))
            saved = path.read_bytes()
            self.assertFalse(reviewer_eval.write_baseline(
                path, self.summary(old, "always-fail"), old
            ))
            self.assertEqual(path.read_bytes(), saved)

    def test_unrelated_baseline_improvement_does_not_require_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, old, merged, _ = self.main_history(Path(directory))
            baseline = self.summary(old, "always-fail")
            path = reviewer_eval.baseline_path(cfg)
            self.assertTrue(reviewer_eval.write_baseline(path, baseline, old))
            for row in (
                baseline,
                {
                    "event": reviewer_eval.EVENT, "candidate": "d" * 40,
                    "status": "completed", "baseline_sha256": "unrelated-baseline",
                    "flips": [{
                        "case": "case", "baseline": "always-fail", "head": "always-pass",
                    }],
                },
                {"event": "merged", "head": "d" * 40, "merge_sha": merged},
            ):
                lifecycle.append(cfg.factory / "events.jsonl", row)
            with mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                 mock.patch.object(
                     reviewer_eval, "contract_fingerprint", return_value=self.fingerprint()
                 ):
                self.assertTrue(reviewer_eval.baseline_ready(cfg))

    @staticmethod
    def fingerprint() -> dict:
        return {"prompt_sha256": {"case": "unchanged"}, "model": "checkout/reviewer"}

    @classmethod
    def summary(cls, candidate: str, status: str) -> dict:
        return {
            "event": reviewer_eval.EVENT, "candidate": candidate,
            "status": "completed", "accepted": True, "corpus_version": 2,
            "statuses": {"case": status}, **cls.fingerprint(),
        }

    @staticmethod
    def main_history(root: Path) -> tuple[config.Config, str, str, str]:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, check=True,
            ).stdout.strip()

        git("init", "--quiet")
        commits = []
        for message in ("old main", "merged improvement", "new main"):
            git("commit", "--quiet", "--allow-empty", "-m", message)
            commits.append(git("rev-parse", "HEAD"))
        cfg = config.Config(root=root, repo="acme/widgets")
        return cfg, *commits


if __name__ == "__main__":
    unittest.main()
