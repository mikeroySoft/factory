from __future__ import annotations

import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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

    def test_timeout_parent_cleans_checkout_without_child_signal_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / reviewer_eval.CORPUS_PATH / "run_tools.py"
            script.parent.mkdir(parents=True)
            out = root / "out"
            out.mkdir()
            script.write_text(
                "import os, pathlib, signal, sys, tempfile\n"
                "private = pathlib.Path(tempfile.mkdtemp("
                "dir=os.environ['FACTORY_CAL_CHECKOUT_PARENT']))\n"
                "(private/'fixture').write_text('private')\n"
                "(pathlib.Path(sys.argv[2])/'private.txt').write_text(str(private))\n"
                "signal.pause()\n"
            )
            unrelated = root / "unrelated"
            unrelated.write_text("preserve")
            rows, timed_out = reviewer_eval._run_runner(root, out, "a" * 40, 1)
            self.assertEqual(rows, [])
            self.assertTrue(timed_out)
            private = Path((out / "private.txt").read_text())
            self.assertFalse(private.exists())
            self.assertFalse(private.parent.exists())
            self.assertEqual(unrelated.read_text(), "preserve")

    def test_same_main_rerun_cannot_clear_pending_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, old, merged, _ = self.main_history(Path(directory))
            path = reviewer_eval.baseline_path(cfg)
            baseline = self.summary(old, "flaky")
            self.assertTrue(reviewer_eval.write_baseline(path, baseline, old))
            digest = reviewer_eval.baseline_digest(reviewer_eval.load_baseline(path))
            for row in (
                {
                    "event": reviewer_eval.EVENT, "candidate": "d" * 40,
                    "status": "completed", "baseline_sha256": digest,
                    "flips": [{"case": "case", "baseline": "flaky", "head": "always-pass"}],
                },
                {"event": "merged", "head": "d" * 40, "merge_sha": merged},
            ):
                lifecycle.append(cfg.factory / "events.jsonl", row)
            saved = path.read_bytes()
            self.assertFalse(reviewer_eval.write_baseline(
                path, self.summary(old, "always-fail"), old
            ))
            self.assertEqual(path.read_bytes(), saved)

    def test_merge_without_sha_keeps_baseline_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, old, _, _ = self.main_history(Path(directory))
            path = reviewer_eval.baseline_path(cfg)
            baseline = self.summary(old, "flaky")
            self.assertTrue(reviewer_eval.write_baseline(path, baseline, old))
            digest = reviewer_eval.baseline_digest(reviewer_eval.load_baseline(path))
            for row in (
                {
                    "event": reviewer_eval.EVENT, "candidate": "d" * 40,
                    "status": "completed", "baseline_sha256": digest,
                    "flips": [{"case": "case", "baseline": "flaky", "head": "always-pass"}],
                },
                {"event": "merged", "head": "d" * 40, "merge_sha": None},
            ):
                lifecycle.append(cfg.factory / "events.jsonl", row)
            with mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                 mock.patch.object(
                     reviewer_eval, "contract_fingerprint", return_value=self.fingerprint()
                 ):
                self.assertFalse(reviewer_eval.baseline_ready(cfg))
            self.assertFalse(reviewer_eval.write_baseline(path, baseline, old))

    def test_missing_merge_metadata_recovers_from_authoritative_pr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, old, merged, new = self.main_history(Path(directory))
            path = reviewer_eval.baseline_path(cfg)
            self.assertTrue(reviewer_eval.write_baseline(path, self.summary(old, "flaky"), old))
            baseline = reviewer_eval.load_baseline(path)
            for row in (
                {
                    "event": reviewer_eval.EVENT, "candidate": "d" * 40,
                    "status": "completed",
                    "baseline_sha256": reviewer_eval.baseline_digest(baseline),
                    "flips": [{"case": "case", "baseline": "flaky", "head": "always-pass"}],
                },
                {"event": "merged", "pr": 444, "head": "d" * 40, "merge_sha": None},
            ):
                lifecycle.append(cfg.factory / "events.jsonl", row)
            saved = path.read_bytes()
            real_run = subprocess.run
            responses = [
                subprocess.CompletedProcess([], 1, "", "temporarily unavailable"),
                subprocess.CompletedProcess(
                    [], 0, json.dumps({"state": "MERGED", "mergeCommit": {"oid": merged}}), ""
                ),
            ]

            def run(command, **kwargs):
                if command[0] == "gh":
                    self.assertEqual(command, [
                        "gh", "pr", "view", "444", "--json", "state,mergeCommit",
                    ])
                    return responses.pop(0)
                return real_run(command, **kwargs)

            with mock.patch.object(subprocess, "run", side_effect=run):
                self.assertFalse(reviewer_eval.write_baseline(
                    path, self.summary(new, "always-pass"), new, root=cfg.root
                ))
                self.assertEqual(path.read_bytes(), saved)
                self.assertTrue(reviewer_eval.write_baseline(
                    path, self.summary(new, "always-pass"), new, root=cfg.root
                ))
            self.assertEqual(responses, [])
            self.assertEqual(reviewer_eval.load_baseline(path)["main_sha"], new)
            # The candidate is not an ancestor of main (as with a squash merge).
            self.assertFalse(reviewer_eval._is_ancestor(cfg.root, "d" * 40, new))
            with mock.patch.object(reviewer_eval, "corpus_version", return_value=2), \
                 mock.patch.object(
                     reviewer_eval, "contract_fingerprint", return_value=self.fingerprint()
                 ):
                self.assertTrue(reviewer_eval.baseline_ready(cfg))

    def test_recovered_merge_must_be_merged_and_contained_in_evaluated_main(self) -> None:
        for evidence in (
            {"state": "OPEN", "mergeCommit": {"oid": "e" * 40}},
            {"state": "MERGED", "mergeCommit": None},
            {"state": "MERGED", "mergeCommit": {"oid": "invalid"}},
            {"state": "MERGED", "mergeCommit": {"oid": "e" * 40}},
            None,
        ):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as directory:
                cfg, old, merged, new = self.main_history(Path(directory))
                path = reviewer_eval.baseline_path(cfg)
                self.assertTrue(reviewer_eval.write_baseline(path, self.summary(old, "flaky"), old))
                digest = reviewer_eval.baseline_digest(reviewer_eval.load_baseline(path))
                for row in (
                    {
                        "event": reviewer_eval.EVENT, "candidate": old,
                        "status": "completed", "baseline_sha256": digest,
                        "flips": [{"case": "case", "baseline": "flaky", "head": "always-pass"}],
                    },
                    {"event": "merged", "pr": 444, "head": old, "merge_sha": None},
                ):
                    lifecycle.append(cfg.factory / "events.jsonl", row)
                saved = path.read_bytes()
                real_run = subprocess.run

                def run(command, **kwargs):
                    if command[0] == "gh":
                        return subprocess.CompletedProcess([], 0, json.dumps(evidence), "")
                    return real_run(command, **kwargs)

                with mock.patch.object(subprocess, "run", side_effect=run):
                    self.assertFalse(reviewer_eval.write_baseline(
                        path, self.summary(new, "always-pass"), new, root=cfg.root
                    ))
                self.assertEqual(path.read_bytes(), saved)
                # Even valid recovered evidence must not clear an older-main rerun.
                with mock.patch.object(subprocess, "run", side_effect=lambda command, **kwargs: (
                    subprocess.CompletedProcess([], 0, json.dumps({
                        "state": "MERGED", "mergeCommit": {"oid": merged},
                    }), "") if command[0] == "gh" else real_run(command, **kwargs)
                )):
                    self.assertFalse(reviewer_eval.write_baseline(
                        path, self.summary(old, "always-pass"), old, root=cfg.root
                    ))
                self.assertEqual(path.read_bytes(), saved)

    def test_cli_refused_main_refresh_is_nonzero_but_candidate_only_passes(self) -> None:
        for accepted in (True, False):
            with self.subTest(accepted=accepted), tempfile.TemporaryDirectory() as directory:
                cfg, old, _, new = self.main_history(Path(directory))
                path = reviewer_eval.baseline_path(cfg)
                self.assertTrue(reviewer_eval.write_baseline(path, self.summary(new, "always-pass"), new))
                saved = path.read_bytes()
                corpus = cfg.root / reviewer_eval.CORPUS_PATH
                oracle = corpus / "cases/case/oracle.json"
                oracle.parent.mkdir(parents=True)
                oracle.write_text(json.dumps({"expected_verdict": "APPROVE", "findings": []}))
                (corpus / "splits.json").write_text(json.dumps({
                    "version": 2, "splits": {"train": ["case"], "lockbox": []},
                }))
                rows = [{
                    "case": "case", "returncode": 0, "prompt_sha256": "unchanged",
                    "output": '```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE',
                }] * 3
                output = io.StringIO()
                with mock.patch.object(config, "load", return_value=cfg), \
                     mock.patch.object(config, "git", side_effect=lambda root, *args: (
                         str(cfg.root) if args == ("rev-parse", "--show-toplevel") else old
                     )), \
                     mock.patch.object(reviewer_eval, "_candidate_accepted", return_value=accepted), \
                     mock.patch.object(reviewer_eval, "_run_runner", return_value=(rows, False)), \
                     mock.patch.object(reviewer_eval, "_judge", return_value=(set(), {})), \
                     mock.patch.object(
                         reviewer_eval, "_checkout_settings", return_value=self.fingerprint()
                     ), \
                     mock.patch.object(
                         reviewer_eval, "contract_fingerprint", return_value=self.fingerprint()
                     ), redirect_stdout(output):
                    status = reviewer_eval.main([])
                self.assertEqual(status, 1 if accepted else 0)
                self.assertEqual(path.read_bytes(), saved)
                summary = reviewer_eval.result_for_head(
                    old, lifecycle.read_events(cfg.factory / "events.jsonl")
                )
                self.assertEqual(summary["verdict"], "block" if accepted else "pass")
                if accepted:
                    self.assertFalse(summary["baseline_updated"])
                    self.assertIn("refused", summary["baseline_update_error"].lower())
                    self.assertIn("refused", output.getvalue().lower())

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
