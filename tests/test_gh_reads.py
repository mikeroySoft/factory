"""#828: a failed GitHub read skips its ticket, not the whole pass."""
from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, watch


class PassReadTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), "acme/widgets")
        dispatch.configure(self.cfg)
        self.cfg.watch_engine_repo = self.cfg.repo
        self.signals = [
            {"fingerprint": f"signal-{n}", "latest": "2026-11-16T12:00:00Z", "kind": "host",
             "key": f"failure-{n}", "stage": "dispatch", "rows": [], "log": None, "excerpt": None}
            for n in range(1, 7)
        ]
        self.issues = {n: {"state": "OPEN", "body": "", "labels": [{"name": "needs-triage"}],
                           "assignees": [{"login": "owner"}]} for n in range(1, 7)}
        self.before = copy.deepcopy(self.issues)
        for n, signal in enumerate(self.signals, 1):
            lifecycle.append(dispatch.EVENTS, {
                "event": "watch", "fingerprint": signal["fingerprint"], "issue": n,
                "repo": self.cfg.repo, "at": "2026-11-01T00:00:00Z",
            })
        self.calls = []
        self.failures = {2}
        self.stderr = "error connecting to api.github.com"
        self.remaining = 5000
        self.enterContext(mock.patch.object(config, "engine_commit", return_value=None))
        self.reset = 4102444800
        self.enterContext(mock.patch.object(watch, "signals", return_value=self.signals))
        self.enterContext(mock.patch.object(dispatch, "_run", side_effect=self.command))
        self.enterContext(mock.patch("subprocess.run", side_effect=self.probe))

    def probe(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.assertEqual(cmd[:3], ["gh", "api", "rate_limit"])
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": {
            key: {"remaining": self.remaining, "reset": self.reset} for key in ("core", "graphql")
        }}), "")

    def command(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "view"]:
            n = int(cmd[3])
            if n in self.failures:
                raise subprocess.CalledProcessError(1, cmd, "", self.stderr)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.issues[n]), "")
        if cmd[:3] == ["gh", "issue", "comment"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["gh", "issue", "edit"]:
            self.issues[int(cmd[3])]["body"] = cmd[cmd.index("--body") + 1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)

    def rows(self, event):
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == event]

    def test_middle_read_failure_continues_without_touching_failed_ticket(self):
        filed = watch.run_pass(self.cfg)
        self.assertEqual([s["fingerprint"] for s in filed], [f"signal-{n}" for n in (1, 3, 4, 5, 6)])
        self.assertEqual(self.issues[2], self.before[2])
        self.assertFalse(any(cmd[:3] in (["gh", "issue", "edit"], ["gh", "issue", "comment"])
                             and cmd[3] == "2" for cmd in self.calls))
        [skip] = self.rows("gh-read-skipped")
        self.assertEqual((skip["code"], skip["ticket"]), ("gh_read_failed", 2))
        [summary] = self.rows("pass-summary")
        self.assertEqual((summary["failed_reads"], summary["skipped"]), (1, 1))

    def test_rate_limit_defers_next_pass_until_reported_reset(self):
        self.failures = {2, 3, 4, 5, 6}
        self.stderr = "GraphQL: API rate limit exceeded"
        self.remaining = 0
        watch.run_pass(self.cfg)
        self.assertFalse(any(cmd[:4] == ["gh", "issue", "view", "3"] for cmd in self.calls))
        [skip] = self.rows("gh-read-skipped")
        self.assertEqual((skip["code"], skip["reset"]), ("rate_limited", self.reset))
        self.calls.clear()
        watch.run_pass(self.cfg)
        self.assertEqual(self.calls, [], "backoff must survive the next pass without more GitHub reads")
        self.assertEqual(self.rows("pass-summary")[-1]["reason"], "rate_limited")

    def test_majority_read_failures_end_pass_cleanly(self):
        self.failures = {1, 2, 3, 4, 5, 6}
        self.assertEqual(watch.run_pass(self.cfg), [])
        views = [cmd for cmd in self.calls if cmd[:3] == ["gh", "issue", "view"]]
        self.assertEqual(len(views), 3)
        [summary] = self.rows("pass-summary")
        self.assertEqual((summary["reason"], summary["failed_reads"], summary["skipped"]),
                         ("gh_reads_unavailable", 3, 3))
        self.assertEqual(self.issues, self.before)


class SharedReadBoundaryTest(unittest.TestCase):
    def setUp(self):
        from factory import gh_reads

        self.reads = gh_reads
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.journal = Path(temporary.name) / "events.jsonl"
        self.probe = self.enterContext(mock.patch("factory.gh_reads.subprocess.run"))
        self.probe.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps({"resources": {
                name: {"remaining": 0, "reset": 4102444800} for name in ("core", "graphql")
            }}), "",
        )

    def test_network_failure_is_not_rate_limit_even_with_exhausted_budget(self):
        cmd = ["gh", "issue", "view", "7", "--json", "state"]
        with self.reads.scope(self.journal, "dispatch"), self.reads.item(7):
            self.reads.run(cmd, lambda: subprocess.CompletedProcess(cmd, 1, "", "TLS handshake timeout"))
        [skip] = [r for r in lifecycle.read_events(self.journal) if r["event"] == "gh-read-skipped"]
        self.assertEqual(skip["code"], "gh_read_failed")
        self.assertIsNone(skip["reset"])
        self.assertEqual(skip["rate_limit"]["graphql"]["remaining"], 0)
        self.probe.assert_called_once()

    def test_read_failure_never_enters_write_error_fallback(self):
        cmd = ["gh", "api", "repos/acme/widgets/issues/7/comments"]
        changed = []
        with self.reads.scope(self.journal, "dispatch"):
            with self.reads.item(7):
                try:
                    self.reads.run(cmd, lambda: subprocess.CompletedProcess(cmd, 1, "", "connection refused"))
                except Exception:  # noqa: BLE001 - emulate the engine's mutation-on-error fallback
                    changed.append("escalated")
            # Later stages must not mutate a ticket skipped earlier in this pass.
            with self.reads.item(7):
                self.reads.run(["gh", "issue", "edit", "7"], lambda: changed.append("label changed"))
        self.assertEqual(changed, [])

    def test_write_and_programming_errors_are_not_read_skips(self):
        for cmd, error in (
            (["gh", "api", "repos/acme/widgets/issues/7/comments", "--method", "POST"],
             subprocess.CalledProcessError(1, ["gh"], stderr="write denied")),
            (["gh", "issue", "view", "7"], ValueError("programming defect")),
        ):
            with self.subTest(cmd=cmd), self.reads.scope(self.journal, "dispatch"), self.assertRaises(type(error)):
                self.reads.run(cmd, mock.Mock(side_effect=error))
        self.probe.assert_not_called()
        self.assertEqual(lifecycle.read_events(self.journal), [])

    def test_pending_or_failing_checks_are_successful_reads(self):
        cmd = ["gh", "pr", "checks", "7", "--json", "bucket"]
        with self.reads.scope(self.journal, "dispatch") as state:
            for code, bucket in ((1, "fail"), (8, "pending")):
                result = subprocess.CompletedProcess(cmd, code, json.dumps([{"bucket": bucket}]), "")
                self.assertEqual(self.reads.run(cmd, lambda result=result: result, check=False).returncode, code)
            self.assertEqual(state.failed_reads, 0)
        self.probe.assert_not_called()


class DispatchClaimReadTest(unittest.TestCase):
    def test_admission_read_failure_preserves_labels_assignee_and_host_claim(self):
        from tests import test_host_claims as fixture

        owner = fixture.HostClaimRaceTest()
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        module = owner.dispatcher("tank")
        from datetime import UTC, datetime

        owner.github.add_claim("tank", datetime.now(UTC))
        before = copy.deepcopy((owner.github.issue, owner.github.comments))
        issue = copy.deepcopy(owner.github.issue)

        def fail_read(args):
            cmd = ["gh", *args]
            return module.gh_reads.run(cmd, lambda: subprocess.CompletedProcess(cmd, 1, "", "network unavailable"))

        with (
            mock.patch.object(module, "gh_json", side_effect=fail_read),
            mock.patch.object(module.runner, "preflight", return_value=None),
            mock.patch("factory.gh_reads.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"resources": {
                    name: {"remaining": 5000, "reset": 4102444800} for name in ("core", "graphql")
                }}), "",
            )),
        ):
            module.process_ticket(issue, 1, False)
        self.assertEqual((owner.github.issue, owner.github.comments), before)
        self.assertEqual(owner.launched, [])
        rows = lifecycle.read_events(module.EVENTS)
        self.assertFalse(any(row.get("event") == "escalate" for row in rows))
        self.assertTrue(any(row.get("event") == "gh-read-skipped" and row["ticket"] == 7 for row in rows))
