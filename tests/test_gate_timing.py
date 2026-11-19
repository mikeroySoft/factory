"""Gate timing rows are paired, attributed, and retained in the root journal."""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import gate, lifecycle


class GateTimingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.old_cwd = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.old_cwd)
        self.environment = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.root / "config"),
            lifecycle.CONTEXT_ENV: "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "-q", "-b", "agent/41")
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "local/gate-timing"\n[leak_scan]\npattern = ""\n'
        )
        self.git("add", ".factory.toml")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-q", "-m", "initial")
        self.events = self.repo / ".factory" / "events.jsonl"

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def timing_rows(self, path: Path | None = None) -> list[dict]:
        return [row for row in lifecycle.read_events(path or self.events)
                if row.get("gate_event") in {"gate-start", "gate-run"}]

    def assert_pair(self, start: dict, end: dict) -> None:
        self.assertEqual((start["gate_event"], end["gate_event"]), ("gate-start", "gate-run"))
        self.assertEqual(start["run_id"], end["run_id"])
        for key in ("started_at", "ticket", "head", "host", "tier", "rerun"):
            self.assertEqual(start[key], end[key])
        self.assertIsNone(start["ended_at"])
        self.assertIsNone(start["duration_seconds"])
        self.assertEqual((start["event"], start["kind"]), ("lifecycle", "result"))
        self.assertEqual((end["event"], end["kind"]), ("lifecycle", "result"))
        self.assertTrue(math.isfinite(end["duration_seconds"]))
        self.assertGreaterEqual(end["duration_seconds"], 0)
        started = datetime.fromisoformat(start["started_at"])
        ended = datetime.fromisoformat(end["ended_at"])
        self.assertEqual((started.tzinfo, ended.tzinfo), (UTC, UTC))
        self.assertGreaterEqual(ended, started)

    def test_public_main_tracks_repeat_attempt_and_first_pr_across_heads(self) -> None:
        with mock.patch.object(gate, "execute", return_value=0), \
                mock.patch("socket.gethostname", side_effect=["socket-a", "socket-b", "socket-c"]):
            self.assertEqual(gate.main([]), 0)
            lifecycle.append(self.events, {
                "event": "attempt", "ticket": 41, "head": self.git("rev-parse", "HEAD"),
                "gate": "PASS",
            })
            (self.repo / "change.txt").write_text("next\n")
            self.git("add", "change.txt")
            self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                     "commit", "-q", "-m", "next")
            self.assertEqual(gate.main([]), 0)
            self.assertEqual(gate.main(["--tier", "pr"]), 0)

        rows = self.timing_rows()
        self.assertEqual(len(rows), 6)
        pairs = [rows[index:index + 2] for index in range(0, len(rows), 2)]
        for pair in pairs:
            self.assert_pair(*pair)
        starts = [pair[0] for pair in pairs]
        self.assertEqual([row["ticket"] for row in starts], [41, 41, 41])
        self.assertEqual([row["tier"] for row in starts], ["attempt", "attempt", "pr"])
        self.assertEqual([row["rerun"] for row in starts], [False, True, False])
        self.assertNotEqual(starts[0]["head"], starts[1]["head"])
        self.assertEqual(starts[1]["head"], starts[2]["head"])
        self.assertEqual([row["host"] for row in starts], ["socket-a", "socket-b", "socket-c"])
        execution_ids = {
            row["execution_id"] for row in lifecycle.read_events(self.events)
            if row.get("event") == "lifecycle" and row.get("kind") == "enter"
            and row.get("stage") == "gate"
        }
        self.assertEqual({row["run_id"] for row in starts}, execution_ids)

    def test_inherited_root_and_ticket_win_while_cli_ticket_can_override(self) -> None:
        inherited = self.root / "root-journal" / "events.jsonl"
        context = json.dumps({
            "schema_version": 1, "path": str(inherited), "ticket": 73, "attempt": 2,
        })
        loaded = gate.config.load()
        loaded.host_name = "configured-host"
        with mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: context}), \
                mock.patch.object(gate.config, "load", return_value=loaded), \
                mock.patch.object(gate, "execute", return_value=0):
            self.assertEqual(gate.main([]), 0)
            self.assertEqual(gate.main(["--ticket", "74"]), 0)

        self.assertFalse(self.events.exists())
        rows = self.timing_rows(inherited)
        self.assertEqual(len(rows), 4)
        self.assert_pair(rows[0], rows[1])
        self.assert_pair(rows[2], rows[3])
        self.assertEqual((rows[0]["ticket"], rows[2]["ticket"]), (73, 74))
        self.assertEqual({row["host"] for row in rows}, {"configured-host"})

    def test_exception_still_completes_with_unknown_head(self) -> None:
        with mock.patch.object(gate, "execute", side_effect=RuntimeError("broken")), \
                mock.patch.object(gate, "_git_value", return_value=None), \
                self.assertRaisesRegex(RuntimeError, "broken"):
            gate.main(["--ticket", "99"])

        rows = self.timing_rows()
        self.assertEqual(len(rows), 2)
        self.assert_pair(*rows)
        self.assertEqual((rows[0]["ticket"], rows[0]["head"], rows[0]["rerun"]),
                         (99, None, False))

    def test_legacy_untyped_gate_is_unknown_until_typed_evidence_exists(self) -> None:
        lifecycle.append(self.events, {
            "at": "2026-01-01T00:00:00Z", "event": "attempt", "ticket": 41,
            "gate": "PASS",
        })
        lifecycle.append(self.events, {
            "at": "2026-01-01T00:00:01Z", "event": "lifecycle", "kind": "enter",
            "stage": "gate", "ticket": 42,
        })
        with mock.patch.object(gate, "execute", return_value=0):
            for ticket in (41, 41, 42, 42):
                self.assertEqual(gate.main(["--ticket", str(ticket)]), 0)

        starts = [row for row in self.timing_rows() if row["gate_event"] == "gate-start"]
        by_ticket = {
            ticket: [row["rerun"] for row in starts if row["ticket"] == ticket]
            for ticket in (41, 42)
        }
        self.assertEqual(by_ticket, {41: [None, True], 42: [None, True]})

    def test_failed_git_query_with_stdout_does_not_attribute_ticket(self) -> None:
        real_run = subprocess.run

        def run(command, *args, **kwargs):
            if command == ["git", "symbolic-ref", "--quiet", "--short", "HEAD"]:
                return subprocess.CompletedProcess(command, 1, "agent/900\n", "")
            return real_run(command, *args, **kwargs)

        with mock.patch.object(gate.subprocess, "run", side_effect=run), \
                mock.patch.object(gate, "execute", return_value=0):
            self.assertEqual(gate.main([]), 0)

        start, end = self.timing_rows()
        self.assert_pair(start, end)
        self.assertEqual((start["ticket"], start["rerun"]), (None, None))

    def test_large_ticket_ignores_unrelated_lifecycle_before_rerun(self) -> None:
        lifecycle.append(self.events, {
            "event": "lifecycle", "kind": "enter", "stage": "dispatch",
            "tier": None, "ticket": 1000, "execution_id": "unrelated",
        })
        with mock.patch.object(gate, "execute", return_value=0):
            self.assertEqual(gate.main(["--ticket", "1000"]), 0)
            self.assertEqual(gate.main(["--ticket", "1000"]), 0)

        starts = [row for row in self.timing_rows() if row["gate_event"] == "gate-start"]
        self.assertEqual([row["ticket"] for row in starts], [1000, 1000])
        self.assertEqual([row["rerun"] for row in starts], [False, True])

    def test_duration_uses_elapsed_monotonic_seconds(self) -> None:
        clock = mock.Mock(return_value=10.0)

        def advance_clock(*_args) -> int:
            clock.return_value = 12.5
            return 0

        with mock.patch("time.monotonic", clock), \
                mock.patch.object(gate, "execute", side_effect=advance_clock):
            self.assertEqual(gate.main([]), 0)

        _start, end = self.timing_rows()
        self.assertEqual(end["duration_seconds"], 2.5)

    def test_non_agent_branch_leaves_ticket_and_rerun_unknown(self) -> None:
        self.git("checkout", "-q", "-b", "agent/41-extra")
        with mock.patch.object(gate, "execute", return_value=0):
            self.assertEqual(gate.main([]), 0)
        start, end = self.timing_rows()
        self.assert_pair(start, end)
        self.assertEqual((start["ticket"], start["rerun"]), (None, None))


if __name__ == "__main__":
    unittest.main()
