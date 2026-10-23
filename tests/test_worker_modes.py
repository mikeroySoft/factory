from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, score, stats, triage


class WorkerModesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("fixture\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=self.root, check=True,
        )
        self.write_config()
        subprocess.run(["git", "add", config.CONFIG_NAME], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "config"], cwd=self.root, check=True)
        self.cfg = config.load(self.root)
        dispatch.configure(self.cfg)
        dispatch.LOGS.mkdir(parents=True)

    def write_config(self, worker_transcript: str = 'transcript = "omp-session"\n', review_mode: str = "json_mode = true\n") -> None:
        (self.root / config.CONFIG_NAME).write_text(
            '[repo]\nslug = "acme/widgets"\n'
            '[workers.default]\ncommand = ["renamed-worker", "@{prompt}"]\n'
            + worker_transcript
            + '[workers.silent]\ncommand = ["silent-worker", "{prompt}"]\n'
            '[review]\ncommand = ["renamed-reviewer", "--mode", "text", "{prompt}"]\n'
            + review_mode
        )

    @staticmethod
    def transcript() -> str:
        def message(role: str, text: str, **fields: object) -> str:
            return json.dumps({"type": "message", "message": {
                "role": role, "content": [{"type": "text", "text": text}], **fields,
            }})

        return "\n".join([
            json.dumps({"type": "session", "version": 3}),
            message("user", "## Scope\nUse renamed worker.\n\n## Exit gate\nIt passes."),
            message("assistant", "Done.", model="worker-model",
                    usage={"input": 10, "output": 2, "cacheRead": 3, "cacheWrite": 1}),
        ]) + "\n"

    def run_worker_round(self, ticket: int, label: str) -> dict:
        dispatch.record("claimed", ticket=ticket, labels=[label])

        def worker(argv: list[str], _wt: Path, logfile: Path, *_args: object) -> tuple[int, None]:
            logfile.write_text("worker output\n")
            if argv[0] == "renamed-worker":
                self.assertEqual(argv[1:3], ["--session-dir", str(dispatch.FACTORY / "transcripts" / str(ticket))])
                Path(argv[2], "session.jsonl").write_text(self.transcript())
            else:
                self.assertNotIn("--session-dir", argv)
            return 0, None

        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
             mock.patch.object(dispatch, "run_worker", side_effect=worker), \
             mock.patch.object(dispatch, "commit_leftovers"), \
             mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")), \
             mock.patch.object(time, "time", return_value=time.time() - 2):
            dispatch.worker_round(ticket, self.root, {label}, "ticket", "", 1, float("inf"))
        return next(row for row in lifecycle.read_events(dispatch.EVENTS)
                    if row.get("event") == "attempt" and row.get("ticket") == ticket)

    def test_declared_modes_support_renamed_worker_and_reviewer_end_to_end(self) -> None:
        attempt = self.run_worker_round(7, "default")
        self.assertIsNotNone(attempt["transcript"])
        usage = next(row for row in lifecycle.read_events(dispatch.EVENTS)
                     if row.get("event") == "llm-usage" and row.get("stage") == "worker")
        self.assertEqual((usage["model"], usage["prompt_tokens"], usage["completion_tokens"], usage["cached_tokens"]),
                         ("worker-model", 14, 2, 3))

        dispatch.record("merged", ticket=7)
        with mock.patch.object(triage, "call_llm", return_value="1"):
            [scored] = score.run(self.cfg, dry_run=True)
        self.assertNotIn("skipped", scored)
        self.assertEqual((scored["ticket"], scored["gate_first_pass"]), (7, True))

        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
                              capture_output=True, check=True).stdout.strip()
        real_run = dispatch.run
        review_output = json.dumps({"type": "message_end", "message": {
            "role": "assistant", "model": "review-model",
            "content": [{"type": "text", "text": "VERDICT: APPROVE"}],
            "usage": {"input": 3, "output": 1},
        }})

        def run(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess:
            if argv[0] == "renamed-reviewer":
                self.assertEqual(argv[1:3], ["--mode", "json"])
                return subprocess.CompletedProcess(argv, 0, review_output, "")
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run):
            verdict, findings = dispatch.review(self.root, 7, "PASS", head)
        self.assertEqual((verdict, findings), ("APPROVE", "VERDICT: APPROVE"))

    def test_undeclared_worker_reports_not_reported_in_by_worker_stats(self) -> None:
        self.run_worker_round(7, "default")
        silent = self.run_worker_round(8, "silent")
        self.assertIsNone(silent["transcript"])

        out = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), contextlib.redirect_stdout(out):
            self.assertEqual(stats.main(["--by-worker"]), 0)
        lines = out.getvalue().splitlines()
        self.assertIn("prompt tokens", lines[0])
        self.assertIn("14", next(line for line in lines if line.startswith("default")))
        self.assertIn(stats.NOT_REPORTED, next(line for line in lines if line.startswith("silent")))

    def test_rejects_unknown_or_mistyped_modes(self) -> None:
        for transcript, json_mode, message in (
            ('transcript = "other"\n', "json_mode = true\n", "workers.default.transcript"),
            ('transcript = "omp-session"\n', 'json_mode = "yes"\n', "review.json_mode"),
        ):
            with self.subTest(message=message):
                self.write_config(transcript, json_mode)
                with self.assertRaisesRegex(config.ConfigError, message):
                    config.load(self.root)


if __name__ == "__main__":
    unittest.main()
