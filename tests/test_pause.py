"""Durable repository pause: CLI, stage guards, expiry, and runtime status."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, pause, runtime_local

ROOT = Path(__file__).resolve().parents[1]


class PauseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        (self.repo / ".factory.toml").write_text('[repo]\nslug = "local/pause"\n')
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.gh_log = self.base / "gh.log"
        gh = self.bin / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$GH_LOG"\n'
            'if [ "$1 $2" = "api user" ]; then echo pause-user; exit 0; fi\n'
            'if [ "$1 $2" = "pr list" ] || [ "$1 $2" = "issue list" ]; then echo "[]"; exit 0; fi\n'
            "echo '{\"number\":7,\"state\":\"OPEN\",\"title\":\"Pause test\","
            "\"body\":\"## Scope\\nTest pause.\\n\\n## Exit gate\\nPass.\","
            "\"comments\":[],\"labels\":[{\"name\":\"ready-for-agent\"}],\"assignees\":[]}'\n"
        )
        gh.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(ROOT),
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "GH_LOG": str(self.gh_log),
            lifecycle.CONTEXT_ENV: "",
        }

    def cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "factory", *args], cwd=self.repo, env=self.env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def events(self) -> list[dict]:
        return lifecycle.read_events(self.repo / ".factory" / "events.jsonl")

    def test_commands_are_idempotent_and_journal_exactly_once(self) -> None:
        self.cli("pause", "--reason", "maintenance", "--until", "2h")
        self.cli("pause", "--reason", "ignored")
        state = pause.read(self.repo)
        self.assertEqual(state["reason"], "maintenance")
        self.assertEqual(state["who"], f"{os.environ.get('USER') or os.getlogin()} (@pause-user)")
        self.assertIsNotNone(state["until"])
        rows = [row for row in self.events() if row.get("event") in {"pause", "resume"}]
        self.assertEqual([row["event"] for row in rows], ["pause"])
        self.assertEqual({"who", "reason", "at", "until"} - rows[0].keys(), set())

        self.cli("resume")
        self.cli("resume")
        self.assertIsNone(pause.read(self.repo))
        rows = [row for row in self.events() if row.get("event") in {"pause", "resume"}]
        self.assertEqual([row["event"] for row in rows], ["pause", "resume"])
        self.assertTrue(all(row["reason"] == "maintenance" for row in rows))

    def test_paused_stages_make_no_github_calls_and_forced_ticket_runs(self) -> None:
        self.cli("pause", "--reason", "hold")
        self.assertNotIn("until", next(row for row in self.events() if row.get("event") == "pause"))
        self.gh_log.write_text("")
        for command in ("dispatch", "manage", "watch", "triage", "triage-probe", "learn", "release"):
            with self.subTest(command=command):
                result = self.cli(command)
                lines = [line for line in result.stdout.splitlines() if line]
                self.assertEqual(len(lines), 1, result.stdout)
                self.assertIn("paused (hold, since ", lines[0])
        self.assertEqual(self.gh_log.read_text(), "")

        refused = self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertIn("--force", refused.stdout + refused.stderr)
        self.assertEqual(self.gh_log.read_text(), "")
        forced = self.cli("dispatch", "--ticket", "7", "--force", "--dry-run")
        self.assertIn("would claim", forced.stdout)
        self.assertIn("issue view", self.gh_log.read_text())

    def test_expired_pause_resumes_once_and_pass_continues(self) -> None:
        self.cli("pause", "--reason", "window", "--until", "2000-01-01T00:00:00Z")
        self.gh_log.write_text("")
        result = self.cli("dispatch", "--ticket", "7", "--dry-run")
        self.assertIn("would claim", result.stdout)
        self.assertIsNone(pause.read(self.repo))
        resumes = [row for row in self.events() if row.get("event") == "resume"]
        self.assertEqual(len(resumes), 1)
        self.assertEqual((resumes[0]["who"], resumes[0]["reason"]), ("expiry", "window"))

    def test_pause_replaces_elapsed_hold(self) -> None:
        self.cli("pause", "--reason", "window", "--until", "2000-01-01T00:00:00Z")
        self.cli("pause", "--reason", "incident")

        state = pause.read(self.repo)
        self.assertEqual((state["reason"], state["until"]), ("incident", None))
        rows = [row for row in self.events() if row.get("event") in {"pause", "resume"}]
        self.assertEqual([row["event"] for row in rows], ["pause", "resume", "pause"])
        self.assertEqual(rows[1]["who"], "expiry")

    def test_pause_between_tickets_stops_the_second_claim(self) -> None:
        cfg = config.Config(self.repo, "local/pause", max_active=2)
        dispatch.configure(cfg)
        issues = [{"number": 1}, {"number": 2}]

        def process(issue, *_args, **_kwargs):
            pause.activate(self.repo, "operator hold", who="tester")

        with (
            mock.patch.object(dispatch, "active_tickets", return_value=[]),
            mock.patch.object(dispatch, "frontier", return_value=issues),
            mock.patch("factory.schedule.plan", return_value={
                "selected": issues, "explained": [], "decisions": [],
            }),
            mock.patch.object(dispatch, "process_ticket", side_effect=process) as process_ticket,
        ):
            self.assertFalse(dispatch.ticket_pass(5, True))
        process_ticket.assert_called_once()

    def test_runtime_status_exposes_pause_state(self) -> None:
        cfg = config.Config(self.repo, "local/pause")
        pause.activate(self.repo, "runtime hold", who="tester")
        with (
            mock.patch.object(runtime_local, "_command", side_effect=[
                "LoadState=loaded\nActiveState=inactive\n",
                "LoadState=loaded\nActiveState=inactive\n",
                "[]",
            ]),
            mock.patch.object(runtime_local, "_capacity", return_value=0),
        ):
            status, errors = runtime_local.dispatcher(cfg, [], {})
        self.assertEqual(errors, [])
        self.assertEqual(status["paused"]["reason"], "runtime hold")
        pause.resume(self.repo, who="tester")
        with (
            mock.patch.object(runtime_local, "_command", side_effect=[
                "LoadState=loaded\nActiveState=inactive\n",
                "LoadState=loaded\nActiveState=inactive\n",
                "[]",
            ]),
            mock.patch.object(runtime_local, "_capacity", return_value=0),
        ):
            status, _ = runtime_local.dispatcher(cfg, [], {})
        self.assertIsNone(status["paused"])

    def test_until_parser_accepts_duration_and_utc_timestamp(self) -> None:
        now = datetime(2026, 10, 27, tzinfo=UTC)
        self.assertEqual(pause.parse_until("2h", now=now), "2026-10-27T02:00:00Z")
        self.assertEqual(pause.parse_until("2026-10-28T03:04:05Z", now=now), "2026-10-28T03:04:05Z")
        with self.assertRaises(ValueError):
            pause.parse_until("tomorrow", now=now)


if __name__ == "__main__":
    unittest.main()
