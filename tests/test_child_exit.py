"""Child completion evidence retains exit status without leaking command secrets."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import dispatch, gate, lifecycle, triage
from factory.config import Config

FAKE_OMP = """#!{python}
import json, os, sys
from pathlib import Path
Path(os.environ["REVIEW_ARGV"]).write_text(json.dumps(sys.argv[1:]))
text = '```json\\n{{"verdict":"APPROVE","findings":[]}}\\n```\\nVERDICT: APPROVE'
print(json.dumps({{"type":"message_end","message":{{"role":"assistant","content":text}}}}))
"""


class ChildExitTest(unittest.TestCase):
    def fake_omp(self, root: Path) -> Path:
        executable = root / "omp"
        executable.write_text(FAKE_OMP.format(python=sys.executable))
        executable.chmod(0o755)
        return executable

    def test_default_ticket_reviewers_keep_prompt_out_of_child_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True,
            )
            (repo / "README").write_text("base\n")
            subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", head], check=True,
            )
            self.fake_omp(root)
            events = root / "events.jsonl"
            cfg = Config(root=repo, repo="local/safety")
            path = str(root) + os.pathsep + os.environ["PATH"]
            with mock.patch.object(dispatch, "cfg", cfg, create=True), \
                    mock.patch.dict(os.environ, {"PATH": path}):
                for role, reviewer in (("primary", None), ("second", cfg.second_reviewer)):
                    with self.subTest(role=role):
                        capture = root / f"{role}.json"
                        password = f"{role}-review-password"
                        prompt = f"Review this packet.\ncredentials:\n  password: {password}"
                        with lifecycle.scope(events, "review", ticket=7):
                            result = dispatch.run_reviewer(
                                repo, prompt, head, reviewer=reviewer,
                                env={"PATH": path, "REVIEW_ARGV": str(capture)},
                            )
                        self.assertTrue(result["accepted"], result)
                        argv = json.loads(capture.read_text())
                        self.assertEqual(argv[-1], prompt)
                        exit_row = next(
                            row for row in reversed(lifecycle.read_events(events))
                            if row.get("kind") == "child_exit"
                            and row.get("command", [None])[0] == "omp"
                        )
                        self.assertEqual(exit_row["returncode"], 0)
                        self.assertEqual(exit_row["command"][:-1], ["omp", *argv[:-1]])
                        self.assertEqual(exit_row["command"][-1], "[REDACTED]")
                        self.assertNotIn(prompt, events.read_text())
                        self.assertNotIn(password, events.read_text())

    def test_default_external_reviewer_keeps_diff_prompt_out_of_child_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fake_omp(root)
            cfg = Config(root=root, repo="local/safety")
            cfg.factory.mkdir()
            capture = root / "external.json"
            password = "external-review-password"
            diff = f"diff --git a/config.yml b/config.yml\n+credentials:\n+  password: {password}"
            real_run = dispatch.run

            def run(command, **kwargs):
                if command[0] == "gh":
                    output = diff if any("/compare/" in arg for arg in command) else ""
                    return subprocess.CompletedProcess(command, 0, output, "")
                return real_run(command, **kwargs)

            path = str(root) + os.pathsep + os.environ["PATH"]
            with mock.patch.multiple(
                    dispatch, create=True, cfg=cfg, ROOT=root, EVENTS=cfg.factory / "events.jsonl",
                    REPO=cfg.repo, run=run,
            ), mock.patch.dict(
                os.environ, {"PATH": path, "REVIEW_ARGV": str(capture)},
            ):
                dispatch.review_external_pr(17, "base", "head")
            argv = json.loads(capture.read_text())
            self.assertIn(diff, argv[-1])
            [exit_row] = [
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("kind") == "child_exit"
            ]
            self.assertEqual(exit_row["returncode"], 0)
            self.assertEqual(exit_row["command"][:-1], ["omp", *argv[:-1]])
            self.assertEqual(exit_row["command"][-1], "[REDACTED]")
            journal = (cfg.factory / "events.jsonl").read_text()
            self.assertNotIn(argv[-1], journal)
            self.assertNotIn(password, journal)

    def test_dispatch_child_failure_records_redacted_command_and_returncode(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            secret = "ghp_" + "a" * 32
            command = [sys.executable, "-c", "raise SystemExit(19)", "--token", "private-value", secret]
            with lifecycle.scope(events, "ticket", ticket=7):
                result = dispatch.run(command, check=False)
            self.assertEqual(result.returncode, 19)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 19)
            self.assertEqual(exit_row["command"][:3], command[:3])
            self.assertNotIn(secret, events.read_text())
            self.assertNotIn("private-value", events.read_text())
            self.assertIn("[REDACTED]", exit_row["command"])

    def test_timeout_records_killed_child_status(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            with lifecycle.scope(events, "ticket", ticket=7), self.assertRaises(subprocess.TimeoutExpired):
                dispatch.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertLess(exit_row["returncode"], 0)
            self.assertEqual(exit_row["command"][0], sys.executable)

    def test_gate_child_records_real_status(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            with lifecycle.scope(events, "gate", ticket=7):
                result = gate.timed([sys.executable, "-c", "raise SystemExit(23)"])
            self.assertEqual(result.returncode, 23)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 23)
            self.assertEqual(exit_row["command"][0], sys.executable)

    def test_triage_child_records_status(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text("#!/bin/sh\nexit 17\n")
            gh.chmod(0o755)
            events = root / "events.jsonl"
            with mock.patch.object(triage, "cfg", Config(root=root, repo="local/safety"), create=True), \
                    mock.patch.dict("os.environ", {"PATH": str(root)}), \
                    lifecycle.scope(events, "triage") as execution, self.assertRaises(SystemExit):
                triage.gh("issue", "list", execution=execution)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 17)
            self.assertEqual(exit_row["command"][:3], ["gh", "issue", "list"])

    def test_api_comment_body_is_redacted_in_child_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text("#!/bin/sh\nexit 17\n")
            gh.chmod(0o755)
            events = root / "events.jsonl"
            body = "Factory requeue: nonce=private-context\nWorker handoff details"
            command = [str(gh), "api", "repos/local/safety/issues/7/comments",
                       "--method", "POST", "-f", f"body={body}"]
            with lifecycle.scope(events, "ticket", ticket=7):
                result = dispatch.run(command, check=False)
            self.assertEqual(result.returncode, 17)
            [exit_row] = [row for row in lifecycle.read_events(events) if row.get("kind") == "child_exit"]
            self.assertEqual(exit_row["returncode"], 17)
            self.assertEqual(exit_row["command"][:-1], command[:-1])
            self.assertEqual(exit_row["command"][-1], "body=[REDACTED]")
            self.assertNotIn("private-context", events.read_text())
            self.assertNotIn("Worker handoff details", events.read_text())
