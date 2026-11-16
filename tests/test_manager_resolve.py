from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, get_ident
from unittest import mock

from factory import config, dispatch, lifecycle, manage, stats


class ManagerResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        dispatch.configure(config.Config(Path(self.root.name), "acme/widgets"))

    def test_parse_is_escalation_only_and_requires_evidence(self) -> None:
        output = "DECISION: RESOLVE\nAlready shipped by PR #12 at abc123."
        self.assertEqual(manage.parse(output, {})[:2], (
            "RESOLVE", "Already shipped by PR #12 at abc123.",
        ))
        self.assertEqual(manage.parse("DECISION: RESOLVE\n", {})[0], "HUMAN")
        self.assertEqual(manage.parse(output, {}, approval=True)[0], "HUMAN")

    def test_apply_comments_before_close_and_does_not_requeue(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        no_pr = subprocess.CalledProcessError(1, ["gh"], stderr='no pull requests found for branch "agent/7"')
        no_branch = subprocess.CalledProcessError(1, ["gh"], stderr="gh: Not Found (HTTP 404)")
        with mock.patch.object(dispatch, "gh_json", side_effect=[no_pr, no_branch]), \
                mock.patch.object(dispatch, "run", return_value=result) as run, \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7, {"title": "Already fixed"}, "RESOLVE",
                "Present on main in abc123 via PR #12.", None, Path("unused"),
            )

        commands = [call.args[0] for call in run.call_args_list]
        comment = next(i for i, command in enumerate(commands)
                       if command[1:3] == ["issue", "comment"])
        close = commands.index([
            "gh", "issue", "close", "7", "--repo", dispatch.REPO,
            "--reason", "completed",
        ])
        self.assertLess(comment, close)
        self.assertIn("Factory manager: Resolved without a code change.", commands[comment][-1])
        self.assertFalse(any("--add-label" in command and config.LABEL_AGENT in command
                             for command in commands))

    def test_resolve_records_one_manager_pr_close(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        pr = {"number": 17, "state": "OPEN", "changedFiles": 0, "baseRefName": "main"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run", return_value=result), \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7, {"title": "Already fixed"}, "RESOLVE",
                "Present on main in abc123 via PR #12.", None, Path("unused"),
            )

        closes = [row for row in lifecycle.read_events(dispatch.EVENTS)
                  if row.get("event") == "pr-closed"]
        self.assertEqual([{key: row[key] for key in ("pr", "ticket", "by", "reason")}
                          for row in closes], [{
            "pr": 17, "ticket": 7, "by": "manager", "reason": "resolve",
        }])

    def test_close_records_one_manager_pr_close(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="https://example.test/comment\n")
        pr = {"number": 17, "state": "OPEN", "baseRefName": "main"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run", return_value=result), \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply(
                7, {"title": "Duplicate"}, "CLOSE",
                "Duplicate of shipped work.", None, Path("unused"),
            )

        closes = [row for row in lifecycle.read_events(dispatch.EVENTS)
                  if row.get("event") == "pr-closed"]
        self.assertEqual([{key: row[key] for key in ("pr", "ticket", "by", "reason")}
                          for row in closes], [{
            "pr": 17, "ticket": 7, "by": "manager", "reason": "close",
        }])

    def test_failed_pr_close_is_not_journaled(self) -> None:
        pr = {"number": 17, "state": "OPEN", "changedFiles": 0, "baseRefName": "main"}

        def run(command, **_kwargs):
            if command[1:3] == ["pr", "close"]:
                raise subprocess.CalledProcessError(1, command, stderr="close refused")
            return subprocess.CompletedProcess(command, 0, stdout="https://example.test/comment\n")

        for decision in ("RESOLVE", "CLOSE"):
            with self.subTest(decision=decision), \
                    mock.patch.object(dispatch, "gh_json", return_value=pr), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(dispatch, "comment_receipt"), \
                    self.assertRaises(subprocess.CalledProcessError):
                manage.apply(7, {"title": "No change"}, decision, "No change needed.", None, Path("unused"))
            self.assertEqual([
                row for row in lifecycle.read_events(dispatch.EVENTS)
                if row.get("event") == "pr-closed"
            ], [])

    def test_reconciliation_during_manager_close_preserves_attribution(self) -> None:
        real_flock = fcntl.flock
        now = datetime.now(UTC)
        pr = {"number": 17, "state": "OPEN", "changedFiles": 0, "baseRefName": "main"}
        for decision in ("RESOLVE", "CLOSE"):
            for rotate in (False, True):
                with self.subTest(decision=decision, rotate=rotate), tempfile.TemporaryDirectory() as root:
                    dispatch.configure(config.Config(Path(root), "acme/widgets"))
                    dispatch.record("pr-opened", ticket=7, pr=17)
                    rows = lifecycle.read_events(dispatch.EVENTS)
                    progress = Event()
                    reconciler = None
                    future = None

                    def flock(handle, operation):
                        if get_ident() == reconciler and operation == fcntl.LOCK_EX:
                            try:
                                return real_flock(handle, operation | fcntl.LOCK_NB)
                            except BlockingIOError:
                                # Let the manager finish only once reconciliation is
                                # blocked on its lock (or has published the wrong row).
                                progress.set()
                        return real_flock(handle, operation)

                    def reconcile():
                        nonlocal reconciler
                        reconciler = get_ident()
                        try:
                            stats.reconcile_closed_prs(dispatch.EVENTS, rows, now - timedelta(days=7))
                        finally:
                            progress.set()

                    def run(command, **_kwargs):
                        nonlocal future
                        if command[1:3] == ["pr", "close"]:
                            # GitHub has accepted the close, but the manager has not
                            # returned from gh and cannot yet append its close row.
                            future = pool.submit(reconcile)
                            self.assertTrue(progress.wait(timeout=5))
                        return subprocess.CompletedProcess(command, 0, stdout="https://example.test/comment\n")

                    closed = [{"number": 17, "closedAt": now.isoformat(), "mergedAt": None}]
                    with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                            mock.patch.object(dispatch, "run", side_effect=run), \
                            mock.patch.object(dispatch, "comment_receipt"), \
                            mock.patch.object(stats.subprocess, "run", return_value=
                                              subprocess.CompletedProcess([], 0, json.dumps(closed))), \
                            mock.patch.object(fcntl, "flock", side_effect=flock), \
                            mock.patch.object(lifecycle, "MAX_BYTES", 1 if rotate else lifecycle.MAX_BYTES), \
                            ThreadPoolExecutor(max_workers=1) as pool:
                        with lifecycle.scope(dispatch.EVENTS, "manager", ticket=7) as execution:
                            manage.apply(7, {"title": "No change"}, decision, "No change needed.", None, Path("unused"))
                        future.result(timeout=5)

                    closes = [row for row in lifecycle.read_events(dispatch.EVENTS)
                              if row.get("event") == "pr-closed"]
                    self.assertEqual([{key: row[key] for key in ("pr", "ticket", "by", "reason")}
                                      for row in closes], [{
                        "pr": 17, "ticket": 7, "by": "manager", "reason": decision.lower(),
                    }])
                    self.assertEqual(closes[0]["execution_id"], execution.execution_id)
                    self.assertEqual([row for row in rows if row.get("event") == "pr-closed"], closes)
                    report = stats.weekly_metrics(rows, now - timedelta(days=7),
                                                  datetime.now(UTC) + timedelta(seconds=1), None)
                    self.assertEqual(report["closed_unmerged_by"], {"manager": 1})

    def test_apply_refuses_open_pr_with_changes(self) -> None:
        pr = {"number": 17, "state": "OPEN", "changedFiles": 2, "baseRefName": "main"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaisesRegex(ValueError, "with changes"):
            manage.apply(7, {"title": "Not fixed"}, "RESOLVE", "Claimed present on main.", None, Path("unused"))
        run.assert_not_called()

    def test_apply_refuses_pushed_branch_without_pr(self) -> None:
        no_pr = subprocess.CalledProcessError(1, ["gh"], stderr='no pull requests found for branch "agent/7"')
        with mock.patch.object(dispatch, "gh_json", side_effect=[no_pr, {"ahead_by": 2}]), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaisesRegex(ValueError, "no PR"):
            manage.apply(7, {"title": "Already fixed"}, "RESOLVE", "Present on main.", None, Path("unused"))
        run.assert_not_called()

    def test_apply_refuses_empty_pr_on_another_base(self) -> None:
        pr = {"number": 17, "state": "OPEN", "changedFiles": 0, "baseRefName": "release"}
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaisesRegex(ValueError, "another base"):
            manage.apply(7, {"title": "Already fixed"}, "RESOLVE", "Present on main.", None, Path("unused"))
        run.assert_not_called()

    def test_apply_does_not_close_when_pr_lookup_fails_for_another_reason(self) -> None:
        error = subprocess.CalledProcessError(1, ["gh"], stderr="error connecting to api.github.com")
        with mock.patch.object(dispatch, "gh_json", side_effect=error), \
                mock.patch.object(dispatch, "run") as run, \
                self.assertRaises(subprocess.CalledProcessError):
            manage.apply(7, {"title": "Already fixed"}, "RESOLVE", "Present on main.", None, Path("unused"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
