"""#535: argv owns worker identity; reported usage cannot rewrite it."""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, dispatch, onboard
from tests import test_refresh_conflict as refresh_suite
from tests import test_worker_health as health_suite


class WorkerModelConfigTest(unittest.TestCase):
    def test_builtin_argv_and_model_agree_for_every_profile(self) -> None:
        cfg = config.Config(Path("/root"), "acme/widgets")
        self.assertEqual(config.DEFAULT_WORKER_MODEL, "openai-codex/gpt-6.1-sol")
        for profile in cfg.workers:
            with self.subTest(profile=profile):
                argv = cfg.worker_command(profile, Path("/p"), Path("/w"))
                self.assertEqual(argv, cfg.worker_spec(profile, Path("/p"), Path("/w")).argv)
                self.assertIsNotNone(cfg.worker_model(profile))
                self.assertEqual(config.command_model(argv), cfg.worker_model(profile))
                self.assertIn("--model", cfg.workers[profile])

    def test_empty_or_missing_flag_value_is_not_a_model(self) -> None:
        for argv in (["omp", "--model"], ["omp", "--model", ""],
                     ["droid", "--model="], ["omp", "--model", "--cwd", "/w"]):
            with self.subTest(argv=argv):
                self.assertIsNone(config.command_model(argv))

    def test_doctor_rejects_implicit_models_even_with_a_runtime_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / config.CONFIG_NAME).touch()
            cfg = config.Config(root, "acme/widgets", signoff=False)
            cfg.install = {"host": "127.0.0.1", "env": {}}
            cfg.workers = {
                "default": ["omp", "-p", "@{prompt}"],
                "chore": ["droid", "exec", "-f", "{prompt}"],
                "reported": ["droid", "exec"],
                "pinned": ["droid", "exec", "--model", "gpt-6-sol"],
            }
            cfg.worker_usage = {"reported": config.usage_spec("reported", {"model": "result.model"})}

            def command(argv, **kwargs):
                output = "WRITE" if argv[:3] == ["gh", "repo", "view"] else "[]" if argv[:3] == ["gh", "label", "list"] else ""
                return subprocess.CompletedProcess(argv, 0, output, "")

            output = io.StringIO()
            with mock.patch.object(config, "load", return_value=cfg), \
                    mock.patch.object(config, "host_config", return_value={}), \
                    mock.patch.object(config, "engine_drift", return_value=None), \
                    mock.patch("factory.claims.repository_host_problem", return_value=None), \
                    mock.patch.object(onboard, "unit_dir", return_value=root / "units"), \
                    mock.patch.object(onboard, "sh", side_effect=command), \
                    mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                    mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")), \
                    mock.patch("factory.triage.probe_endpoint", return_value=(True, "alive")), \
                    redirect_stdout(output):
                self.assertEqual(onboard.doctor(["--json"]), 1)
            rows = {row["label"]: row for row in json.loads(output.getvalue())["rows"]}
            for profile in ("default", "chore", "reported"):
                self.assertEqual(rows[f"worker `{profile}` model"]["status"], "FAIL")
                self.assertIn("model is not explicit", rows[f"worker `{profile}` model"]["detail"])
            self.assertEqual(rows["worker `pinned` model"]["status"], "PASS")


class WorkerModelJournalTest(unittest.TestCase):
    setUp = refresh_suite.RefreshConflictTest.setUp
    patched = refresh_suite.RefreshConflictTest.patched
    events = refresh_suite.RefreshConflictTest.events
    ticket = health_suite.WorkerHealthTest.ticket
    process = health_suite.WorkerHealthTest.process

    def test_claim_and_every_attempt_path_keep_the_pinned_model(self) -> None:
        self.ticket()
        script = self.cfg.workers["default"]
        self.cfg.workers = {
            "default": [*script, "--model", "openai-codex/gpt-6.1-sol"],
            "chore": [*script, "--model", "gpt-6-sol"],
        }
        self.process()
        [claim] = self.events("claimed")
        self.assertEqual(claim["worker_model"], "openai-codex/gpt-6.1-sol")
        wt = dispatch.FACTORY / "wt-9"
        for profile in self.cfg.workers:
            for fired, deadline in ((None, float("inf")), ("idle_timeout", float("inf")), (None, 0)):
                with self.subTest(profile=profile, fired=fired, deadline=deadline):
                    def worker(argv, cwd, logfile, *_args, fired=fired):
                        logfile.write_text("")
                        return 1, fired

                    with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                            mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                            mock.patch.object(dispatch, "_launch_failure", return_value=None), \
                            mock.patch.object(dispatch, "commit_leftovers"), \
                            mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
                        dispatch.worker_round(9, wt, {profile}, "change", "", 2, deadline)
                    attempt, usage = self.events("attempt")[-1], self.events("llm-usage")[-1]
                    for row in (attempt, usage):
                        self.assertEqual((row["worker"], row["worker_model"]),
                                         (profile, self.cfg.worker_model(profile)))
                    self.assertEqual(self.events("worker-model-mismatch"), [])

    def test_reported_model_is_only_a_cross_check_and_preserves_provider_locality(self) -> None:
        self.ticket()
        script = self.cfg.workers["default"]
        self.cfg.workers["default"] = [*script, "--model", "ollama/qwen3:30b"]
        self.process()
        self.cfg.worker_transcript = {"default": "omp-session"}
        wt = dispatch.FACTORY / "wt-9"
        cases = [("qwen3:30b", "ollama", False), ("qwen3:30b", "other", True),
                 ("different", "ollama", True), ("qwen3:30b", None, False),
                 ("qwen3:30b", "", False), ("qwen3:30b", 7, False),
                 ("ollama/qwen3:30b", None, False)]
        for model, provider, mismatch in cases:
            with self.subTest(model=model, provider=provider):
                before = len(self.events("worker-model-mismatch"))
                def worker(argv, cwd, logfile, *_args, model=model, provider=provider):
                    logfile.write_text("")
                    session = Path(argv[argv.index("--session-dir") + 1])
                    message = {"role": "assistant", "model": model,
                               "usage": {"input": 4, "output": 2}}
                    if provider is not None:
                        message["provider"] = provider
                    (session / "fixture.jsonl").write_text(json.dumps({"type": "message", "message": message}) + "\n")
                    return 0, None

                with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                        mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                        mock.patch.object(dispatch, "commit_leftovers"), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
                    dispatch.worker_round(9, wt, set(), "change", "", 2, float("inf"))
                usage = self.events("llm-usage")[-1]
                self.assertEqual((usage["worker_model"], usage["model"], usage["prompt_tokens"]),
                                 ("ollama/qwen3:30b", model, 4))
                self.assertIs(usage["local"], model == self.cfg.llm_model)
                if not isinstance(provider, str) or not provider:
                    self.assertNotIn("provider", usage)
                if mismatch:
                    event = self.events("worker-model-mismatch")[-1]
                    self.assertEqual((event["worker_model"], event["reported_model"], event["reported_provider"]),
                                     ("ollama/qwen3:30b", model, provider))
                self.assertEqual(len(self.events("worker-model-mismatch")) - before, int(mismatch))
                self.assertEqual(self.events("attempt")[-1]["worker_model"], "ollama/qwen3:30b")

    def test_structured_non_omp_model_is_a_cross_check_even_on_failed_exit(self) -> None:
        self.ticket()
        script = self.cfg.workers["default"]
        self.cfg.workers["chore"] = [*script, "--model", "gpt-6-sol"]
        self.cfg.worker_usage["chore"] = config.usage_spec("chore", {"model": "result.model"})
        self.process()
        wt = dispatch.FACTORY / "wt-9"

        def worker(argv, cwd, logfile, *_args):
            logfile.write_text(json.dumps({"result": {"model": "different-model"}}) + "\n")
            return 1, None

        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
            dispatch.worker_round(9, wt, {"chore"}, "change", "", 2, float("inf"))
            with mock.patch.object(dispatch, "worker_usage", return_value={
                "model": "gpt-6-sol", "provider": "openai", "cost": None,
            }):
                dispatch.worker_round(9, wt, {"chore"}, "change", "", 3, float("inf"))
        self.assertEqual(len(self.events("worker-model-mismatch")), 1)
        event = self.events("worker-model-mismatch")[-1]
        self.assertEqual((event["worker"], event["worker_model"], event["reported_model"]),
                         ("chore", "gpt-6-sol", "different-model"))
        attempt = self.events("attempt")[-1]
        self.assertEqual((attempt["worker_model"], attempt["worker_exit"]), ("gpt-6-sol", 1))




def setUpModule() -> None:
    from unittest.mock import patch

    abi = patch("factory.darwin_observer.abi_facts",
                return_value=("darwin", "arm64", 8, "little", (27, 2)))
    abi.start()
    unittest.addModuleCleanup(abi.stop)


if __name__ == "__main__":
    unittest.main()
