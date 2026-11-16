"""Durable local pause admission, expiry, and safe in-flight parking (#824)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory import cli, config, dispatch, lifecycle


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(root=self.root, repo="owner/repo", max_active=2)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.object(config, "load", return_value=self.cfg))
        self.enterContext(patch.object(config, "repo_root", return_value=self.root))
        self.enterContext(patch.object(config, "engine_commit", return_value=None))
        self.enterContext(patch.object(config, "engine_target", return_value=None))
        self.enterContext(patch.object(dispatch, "pin_root", return_value=[]))
        # Entire GitHub interface is fake, including --json state; CI needs no gh login.
        self.gh = self.enterContext(patch.object(dispatch, "gh_json", side_effect=self.github))
        self.commands = self.enterContext(patch.object(dispatch, "_run", side_effect=self.command))
        self.enterContext(patch("factory.pause.actor", return_value="operator (github: operator-gh)"))
        dispatch.configure(self.cfg)

    @staticmethod
    def github(args, **kwargs):
        if args[:2] == ["api", "rate_limit"]:
            return {"resources": {kind: {"remaining": 5000} for kind in ("core", "graphql")}}
        if args[:2] == ["issue", "view"]:
            if args[-1] == "state":
                return {"state": "OPEN"}
            return {"number": 1, "state": "OPEN", "title": "Ticket", "body": "",
                    "labels": [{"name": config.LABEL_AGENT}], "assignees": [], "comments": []}
        raise AssertionError(f"Unexpected GitHub read: {args}")

    @staticmethod
    def command(cmd, *args):
        raise AssertionError(f"Unexpected external command: {cmd}")

    def call(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = cli.main(list(args))
        return code, stream.getvalue()

    def rows(self):
        return lifecycle.read_events(self.cfg.factory / "events.jsonl")

    def test_pause_dispatch_and_commands_are_idempotent(self):
        from factory import pause

        self.assertEqual(self.call("pause", "--reason", "x")[0], 0)
        initial = pause.read(self.root)
        self.assertEqual(self.call("pause", "--reason", "ignored")[0], 0)
        self.assertEqual(pause.read(self.root), initial)
        code, output = self.call("dispatch")
        self.assertEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("paused (x, since ", output)
        self.assertFalse(self.gh.called)
        self.assertFalse(self.commands.called)
        self.assertEqual(dispatch.active_tickets(), [])
        self.assertEqual(self.call("resume")[0], 0)
        self.assertEqual(self.call("resume")[0], 0)
        self.assertIsNone(pause.read(self.root))
        rows = self.rows()
        self.assertEqual([row["event"] for row in rows], ["pause", "resume"])
        for row in rows:
            self.assertEqual(row["who"], "operator (github: operator-gh)")
            self.assertEqual(row["reason"], "x")
            self.assertIsNotNone(datetime.fromisoformat(row["at"]))

    def test_explicit_ticket_requires_force_without_resuming(self):
        from factory import pause

        self.call("pause")
        self.assertNotEqual(self.call("dispatch", "--ticket", "1")[0], 0)
        self.assertFalse(self.gh.called)
        with patch.object(dispatch, "github_preflight", return_value=None), \
                patch.object(dispatch, "process_ticket") as process:
            self.assertEqual(self.call("dispatch", "--ticket", "1", "--force")[0], 0)
        process.assert_called_once()
        self.assertIsNotNone(pause.read(self.root))
        with self.assertRaises(SystemExit) as error, contextlib.redirect_stderr(io.StringIO()):
            self.call("dispatch", "--force")
        self.assertEqual(error.exception.code, 2)

    def test_expired_pause_resumes_once_and_pass_continues(self):
        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        self.call("pause", "--reason", "window", "--until", past)
        with patch.object(dispatch, "github_preflight", return_value=None), \
                patch.object(dispatch, "process_ticket") as process:
            self.assertEqual(self.call("dispatch", "--ticket", "1")[0], 0)
        process.assert_called_once()
        from factory import pause

        self.assertFalse(pause.check(self.root))
        resumes = [row for row in self.rows() if row["event"] == "resume"]
        self.assertEqual(len(resumes), 1)
        self.assertEqual((resumes[0]["who"], resumes[0]["reason"], resumes[0]["until"]),
                         ("expiry", "window", past))

    def test_concurrent_expiry_journals_only_once(self):
        from factory import pause

        self.call("pause", "--until", "2000-01-01T00:00:00Z")
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(lambda _: pause.check(self.root), range(24))), [False] * 24)
        self.assertEqual([r["who"] for r in self.rows() if r["event"] == "resume"], ["expiry"])

    def test_duration_invalid_expiry_and_independent_pace(self):
        from factory import pause

        settings = self.root / ".factory.toml"
        host = self.root / "host.toml"
        for pace in (0, 3):
            settings.write_text(f"[dispatch]\npace = {pace}\n")
            host.write_text(f"[defaults.dispatch]\npace = {pace}\n")
            before = (settings.read_bytes(), host.read_bytes())
            with patch.object(config, "host_config_path", return_value=host):
                self.assertIsNone(pause.read(self.root))
                self.call("pause", "--until", "2h")
                state = pause.read(self.root)
                seconds = (datetime.fromisoformat(state["until"]) - datetime.fromisoformat(state["at"])).total_seconds()
                self.assertAlmostEqual(seconds, 7200, delta=1)
                self.call("resume")
            self.assertEqual((settings.read_bytes(), host.read_bytes()), before)
        for value in ("nonsense", "-1h", "2026-01-01", "0s"):
            with self.subTest(value=value), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.call("pause", f"--until={value}")
            self.assertIsNone(pause.read(self.root))

    def test_pause_during_first_pick_does_not_claim_second(self):
        from factory import pause

        issue = {"number": 1, "title": "one", "body": "", "labels": [],
                 "createdAt": "2026-01-01T00:00:00Z"}
        worked = []

        def work(ticket, *_):
            worked.append(ticket["number"])
            pause.set_pause(self.root, reason="incident")
            ticket["_factory_admitted"].set()

        with patch.object(dispatch, "frontier", return_value=[issue, {**issue, "number": 2}]), \
                patch.object(dispatch, "fairness_session", return_value={}), \
                patch.object(dispatch, "process_ticket", side_effect=work):
            dispatch.ticket_pass(1, False)
        self.assertEqual(worked, [1])

    def test_publish_merge_and_claim_refuse_after_pause_but_cleanup_runs(self):
        from factory import pause

        self.call("pause")
        for cmd in (["gh", "pr", "create"], ["gh", "pr", "merge", "2"],
                    ["git", "push", "origin", "agent/1"],
                    ["gh", "issue", "edit", "1", "--add-assignee", "@me"]):
            with self.subTest(cmd=cmd), self.assertRaises(pause.Paused):
                dispatch.run(cmd)
        self.assertFalse(self.commands.called)
        with patch.object(dispatch, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            dispatch.unclaim(1)

    def test_malformed_state_never_runs_a_pass(self):
        path = self.cfg.factory / "pause.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"until": "bad"}))
        with self.assertRaises((ValueError, config.ConfigError)):
            self.call("dispatch")
        self.assertFalse(self.gh.called)
        self.assertFalse(self.commands.called)


if __name__ == "__main__":
    unittest.main()
