from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dispatch, lifecycle, triage


class EndpointLocalityTest(unittest.TestCase):
    def test_loopback_and_private_addresses_are_local(self) -> None:
        for url in ("http://127.0.0.1:11434/v1", "http://[::1]:8000", "http://10.1.2.3/v1",
                    "https://172.20.0.2", "http://192.168.1.9", "http://localhost:9000"):
            with self.subTest(url=url):
                self.assertTrue(config.local_endpoint(url))
        for url in ("https://api.openai.com/v1", "http://8.8.8.8", "not a url"):
            with self.subTest(url=url):
                self.assertFalse(config.local_endpoint(url))

    def test_worker_usage_marks_the_configured_local_model(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dispatch.cfg = config.Config(root, "acme/widgets")
        dispatch.cfg.llm_model = "local-model"
        log = root / "worker.log"
        log.write_text("")
        transcript = root / "session.jsonl"
        transcript.write_text(json.dumps({
            "type": "message",
            "message": {"role": "assistant", "model": "local-model", "usage": {
                "input": 4, "output": 2, "cacheRead": 0, "cacheWrite": 0,
            }},
        }) + "\n")

        usage = dispatch.worker_usage(log, None, str(transcript))

        self.assertIs(dispatch._usage_local(usage), True)


    def test_worker_usage_does_not_mark_hosted_configured_model_local(self) -> None:
        dispatch.cfg = config.Config(Path("/tmp"), "acme/widgets")
        dispatch.cfg.llm_url = "https://api.openai.com/v1"
        dispatch.cfg.llm_model = "shared-model"

        self.assertIs(dispatch._usage_local({"model": "shared-model"}), False)

    def test_triage_usage_records_endpoint_locality(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "events.jsonl"
        execution = SimpleNamespace(ticket=7, path=path, execution_id="e", root_execution_id="r",
                                    commit=mock.Mock())
        with mock.patch.object(triage, "LLM_URL", "http://192.168.1.2/v1/chat/completions"):
            triage._record_usage(execution, {"prompt_tokens": 4, "completion_tokens": 2})

        [row] = lifecycle.read_events(path)
        self.assertIs(row["local"], True)
        execution.commit.assert_called_once_with()


class WorkerModelJournalTest(unittest.TestCase):
    def test_default_expanded_argv_and_model_agree(self) -> None:
        cfg = config.Config(Path("/repo"), "acme/widgets")
        command = cfg.worker_command("default", Path("/prompt"), Path("/worktree"))
        self.assertEqual(config.command_model(command), "openai-codex/gpt-6.1-sol")
        self.assertEqual(cfg.worker_model("default"), config.command_model(command))
        self.assertEqual(command[-3:], ["--cwd", "/worktree", "@/prompt"])

    def test_worker_model_identity_for_every_profile_and_attempt_exit(self) -> None:
        import os
        import subprocess

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "user.name", "Test"],
            ["config", "user.email", "test@example.invalid"],
            ["commit", "-q", "--allow-empty", "-m", "fixture"],
            ["update-ref", "refs/remotes/origin/main", "HEAD"],
        ):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        cfg = config.Config(root, "acme/widgets")
        cfg.workers = {**cfg.workers, "pilot": ["omp", "-p", "--model=pilot/model", "@{prompt}"]}
        cfg.worker_usage["chore"] = config.usage_spec("chore", {
            "model": "result.model", "match": {"type": "result"},
        })
        dispatch.configure(cfg)
        dispatch.LOGS.mkdir(parents=True)

        def worker(_argv, _cwd, logfile, *_args):
            logfile.write_text(output)
            return 0, timeout

        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
            for profile, model in (
                ("default", "openai-codex/gpt-6.1-sol"),
                ("chore", "gpt-6-sol"),
                ("pilot", "pilot/model"),
            ):
                for output in ("", '{"type":"result","result":{"model":"chore/model"}}\n',
                               json.dumps({"type": "result", "result": {"model": model}}) + "\n"):
                    for ticket, (timeout, deadline) in enumerate((
                        (None, float("inf")), ("idle_timeout", float("inf")), (None, 0),
                    ), 1):
                        with self.subTest(profile=profile, exit=timeout, deadline=deadline, output=output):
                            dispatch.worker_round(
                                ticket, root, {profile}, "ticket", "", 1, deadline,
                            )
                            rows = [
                                row for row in lifecycle.read_events(dispatch.EVENTS)
                                if row.get("event") in {"attempt", "llm-usage"}
                            ][-2:]
                            self.assertEqual(
                                [(row["event"], row["worker"], row["worker_model"]) for row in rows],
                                [("llm-usage", profile, model), ("attempt", profile, model)],
                            )
                            self.assertEqual(
                                rows[0].get("model_mismatch", False),
                                profile == "chore" and "chore/model" in output,
                            )

    def test_claim_records_the_selected_explicit_model(self) -> None:
        import os
        import subprocess

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        cfg = config.Config(root, "acme/widgets")
        dispatch.configure(cfg)
        for profile, model in (("default", "openai-codex/gpt-6.1-sol"), ("chore", "gpt-6-sol")):
            issue = {"number": 7, "title": "ticket",
                     "labels": [{"name": "ready-for-agent"}, {"name": profile}]}
            fresh = {**issue, "state": "OPEN", "assignees": [], "body": "", "comments": []}
            with mock.patch.object(dispatch, "gh_json", return_value=fresh), \
                    mock.patch.object(dispatch, "admit_plan", return_value=None), \
                    mock.patch.object(dispatch, "ensure_worktree", return_value=root), \
                    mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess([], 0, "head", "")), \
                    mock.patch.object(dispatch, "agent_lease", return_value="head"), \
                    mock.patch.object(dispatch, "sync_staged", return_value=None), \
                    mock.patch.object(dispatch, "worker_round", side_effect=FileNotFoundError("fixture worker")):
                dispatch.process_ticket(issue, 1, False, forced=True)
            claim = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "claimed"][-1]
            self.assertEqual(claim["worker"], profile)
            self.assertEqual(claim["worker_model"], model)

    def test_transcript_model_cross_check_preserves_provider_identity(self) -> None:
        import os
        import subprocess

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "user.name", "Test"],
            ["config", "user.email", "test@example.invalid"],
            ["commit", "-q", "--allow-empty", "-m", "fixture"],
            ["update-ref", "refs/remotes/origin/main", "HEAD"],
        ):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        cfg = config.Config(root, "acme/widgets")
        cfg.workers["prime"] = ["factory-prime-agent", "--model", "openai-codex/gpt-6.1-sol"]
        cfg.worker_transcript["prime"] = "omp-session"
        dispatch.configure(cfg)
        dispatch.LOGS.mkdir(parents=True)
        transcript = root / ".factory" / "session.jsonl"

        def worker(_argv, _cwd, logfile, *_args):
            logfile.write_text("")
            return 0, None

        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "_transcript", return_value=str(transcript)), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
            for provider, model, reported, mismatch in (
                ("openai-codex", "gpt-6.1-sol", "openai-codex/gpt-6.1-sol", False),
                ("anthropic", "gpt-6.1-sol", "anthropic/gpt-6.1-sol", True),
                ("openai-codex", "gpt-5.6-sol", "openai-codex/gpt-5.6-sol", True),
                ("openai-codex", "openai-codex/gpt-6.1-sol", "openai-codex/gpt-6.1-sol", False),
                (None, "openai-codex/gpt-6.1-sol", "openai-codex/gpt-6.1-sol", False),
            ):
                with self.subTest(provider=provider, model=model):
                    transcript.write_text(json.dumps({
                        "type": "message", "message": {
                            "role": "assistant", "provider": provider, "model": model,
                            "usage": {"input": 4, "output": 2},
                        },
                    }) + "\n")
                    dispatch.worker_round(7, root, {"prime"}, "ticket", "", 1, float("inf"))
                    usage, attempt = [
                        row for row in lifecycle.read_events(dispatch.EVENTS)
                        if row.get("event") in {"attempt", "llm-usage"}
                    ][-2:]
                    self.assertEqual(usage["model"], reported)
                    self.assertEqual(usage.get("model_mismatch", False), mismatch)
                    self.assertEqual(usage["worker_model"], "openai-codex/gpt-6.1-sol")
                    self.assertEqual(attempt["worker_model"], "openai-codex/gpt-6.1-sol")


if __name__ == "__main__":
    unittest.main()
