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
from tests import test_manager_close_journal as manager_fixture
from tests import test_watch as watch_fixture


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

    def test_bounded_evidence_failure_aborts_before_partial_data_fallback(self):
        import os
        import sys
        import time

        from factory import evidence

        command = self.journal.parent / "gh"
        command.write_text(
            f"#!{sys.executable}\nimport sys\n"
            "print('error connecting to api.github.com', file=sys.stderr)\nsys.exit(1)\n"
        )
        command.chmod(0o755)
        changes = []
        with (
            mock.patch.dict(os.environ, {"PATH": f"{command.parent}{os.pathsep}{os.environ['PATH']}"}),
            self.reads.scope(self.journal, "manage"),
            self.reads.item(7),
        ):
            try:
                evidence.github_read("repos/acme/widgets/issues/7", time.monotonic() + 5)
            except evidence.EvidenceError:
                changes.append("partial-data fallback")
            changes.append("label changed")
        self.assertEqual(changes, [])
        [skip] = [r for r in lifecycle.read_events(self.journal) if r["event"] == "gh-read-skipped"]
        self.assertEqual((skip["code"], skip["returncode"], skip["ticket"]), ("gh_read_failed", 1, 7))

    def test_manager_resolve_absent_branch_preserves_expected_404(self):
        from factory import manage

        dispatch.configure(config.Config(self.journal.parent, "acme/widgets"))
        calls = []

        def command(cmd, *args, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["pr", "view"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="no pull requests found for branch agent/7")
            if cmd[1] == "api":
                raise subprocess.CalledProcessError(1, cmd, stderr="gh: Not Found (HTTP 404)")
            return subprocess.CompletedProcess(cmd, 0, "https://example.test/comment", "")

        with mock.patch.object(dispatch, "_run", side_effect=command), \
                mock.patch.object(dispatch, "comment_receipt"), \
                self.reads.scope(self.journal, "manage"), self.reads.item(7):
            manage.apply(7, {"title": "Already shipped"}, "RESOLVE", "Present on main.", None, Path("unused"))
        self.assertTrue(any(cmd[1:3] == ["issue", "close"] for cmd in calls))
        self.assertTrue(any("--remove-label" in cmd for cmd in calls))
        self.assertFalse(any(row["event"] == "gh-read-skipped" for row in lifecycle.read_events(self.journal)))
        self.probe.assert_not_called()

    def test_release_absence_publishes_but_transport_failure_defers(self):
        from factory import release

        cfg = config.Config(self.journal.parent, "acme/widgets")
        dispatch.configure(cfg)
        for diagnostic, published in (("release not found", True), ("connection refused", False)):
            with self.subTest(diagnostic=diagnostic):
                calls = []

                def command(cmd, *, calls=calls, diagnostic=diagnostic, **kwargs):
                    calls.append(cmd)
                    if cmd[:3] == ["gh", "api", "rate_limit"]:
                        return self.probe.return_value
                    if cmd[1:3] == ["release", "view"]:
                        return subprocess.CompletedProcess(cmd, 1, "", diagnostic)
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                self.probe.side_effect = command
                with mock.patch.object(release, "_notes", return_value="Shipped."), \
                        self.reads.scope(self.journal, "release"):
                    release._publish(cfg, "v1.2.3")
                self.assertEqual(any(cmd[1:3] == ["release", "create"] for cmd in calls), published)
        rows = lifecycle.read_events(self.journal)
        self.assertTrue(any(row.get("outcome") == "published"
                            for row in lifecycle.read_events(cfg.factory / "events.jsonl")))
        self.assertEqual([row["code"] for row in rows if row["event"] == "gh-read-skipped"], ["gh_read_failed"])

    def test_rest_release_limit_uses_core_reset_and_persists_backoff(self):
        from factory import release

        cfg = config.Config(self.journal.parent, "acme/widgets")
        calls = []

        def command(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["gh", "api", "rate_limit"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": {
                    "core": {"remaining": 0, "reset": 4102444800},
                    "graphql": {"remaining": 5000, "reset": 4102444900},
                }}), "")
            return subprocess.CompletedProcess(cmd, 1, "", "API rate limit exceeded (HTTP 403)")

        self.probe.side_effect = command
        with self.reads.scope(self.journal, "release"):
            release._gh_release(cfg, "view", "v1.2.3")
        with self.reads.scope(self.journal, "release") as state:
            self.assertTrue(state.stopped)
            self.assertEqual(state.reset, 4102444800)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any(row["event"] == "gh-read-backoff" and row["reset"] == 4102444800
                            for row in lifecycle.read_events(self.journal)))

    def test_standalone_dry_runs_ignore_backoff_without_journaling_failures(self):
        from factory import learn, manage, release

        for stage in ("manage", "release", "learn"):
            for saved_backoff in (False, True):
                with self.subTest(stage=stage, saved_backoff=saved_backoff):
                    cfg = config.Config(self.journal.parent / f"{stage}-{saved_backoff}",
                                        "acme/widgets", manager=["unused"])
                    dispatch.configure(cfg)
                    journal = cfg.factory / "events.jsonl"
                    lifecycle.append(journal, {"event": "merged", "ticket": 7,
                                               "at": "2026-11-01T00:00:00+00:00"})
                    if saved_backoff:
                        lifecycle.append(journal, {"event": "gh-read-backoff", "reset": 4102444800})
                    before = journal.read_bytes()
                    calls = []

                    def command(cmd, *args, calls=calls, **kwargs):
                        calls.append(cmd)
                        if cmd[:3] == ["gh", "api", "rate_limit"]:
                            return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": {
                                name: {"remaining": 0, "reset": 4102444800}
                                for name in ("core", "graphql")
                            }}), "")
                        return subprocess.CompletedProcess(cmd, 1, "", "API rate limit exceeded (HTTP 403)")

                    self.probe.side_effect = command
                    with (
                        mock.patch.object(config, "load", return_value=cfg),
                        mock.patch.object(dispatch, "_run", side_effect=command),
                        mock.patch.object(release, "_git", return_value=subprocess.CompletedProcess([], 0, "2", "")),
                        mock.patch.object(release, "_latest", return_value={
                            "tag": "v1.0.0", "version": (1, 0, 0), "message": "",
                            "date": "2026-10-01T00:00:00+00:00", "sha": "old",
                        }),
                        mock.patch.object(release, "_version_at", return_value=(1, 0, 0)),
                    ):
                        if stage == "manage":
                            manage.main(["--dry-run"])
                        elif stage == "release":
                            release.main(["--dry-run"])
                        else:
                            learn.run(cfg, dry_run=True)
                    self.assertTrue(any(cmd[0] == "gh" and cmd[1:3] != ["api", "rate_limit"]
                                        for cmd in calls), calls)
                    self.assertEqual(journal.read_bytes(), before)

    def test_inaccessible_actions_job_defers_merge_without_mutations(self):
        cfg = config.Config(self.journal.parent, "acme/widgets")
        dispatch.configure(cfg)
        pr = {"number": 70, "headRefName": "agent/7", "headRefOid": "head",
              "baseRefName": "main", "isDraft": False,
              "labels": [{"name": "factory-approved"}], "reviewDecision": "APPROVED"}
        calls = []

        def command(cmd, *args, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["pr", "list"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([pr]), "")
            if cmd[1] == "api" and "/actions/jobs/" in cmd[2]:
                return subprocess.CompletedProcess(cmd, 1, "", "gh: Not Found (HTTP 404)")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            mock.patch.object(dispatch, "_run", side_effect=command),
            mock.patch.object(dispatch, "initiative_kind", return_value=None),
            mock.patch.object(dispatch, "pr_checks", return_value=[{
                "name": "compile", "bucket": "cancel",
                "link": "https://github.com/acme/widgets/actions/runs/123/job/456",
            }]),
            mock.patch.object(dispatch, "escalate") as escalate,
            mock.patch.object(dispatch, "cleanup_after_merge") as cleanup,
        ):
            dispatch.merge_pass_locked(False)
        self.assertFalse(any(self.reads.is_read(cmd) is False for cmd in calls), calls)
        escalate.assert_not_called()
        cleanup.assert_not_called()
        [skip] = [row for row in lifecycle.read_events(dispatch.EVENTS) if row["event"] == "gh-read-skipped"]
        self.assertEqual((skip["ticket"], skip["code"], skip["returncode"]), (7, "gh_read_failed", 1))

    def test_dependency_api_absence_keeps_body_blockers_but_other_failures_defer(self):
        dispatch.configure(config.Config(self.journal.parent, "acme/widgets"))
        for diagnostic, expected in (("gh: Not Found (HTTP 404)", [9]), ("connection refused", None)):
            with self.subTest(diagnostic=diagnostic):
                observed = None

                def command(cmd, *args, diagnostic=diagnostic, **kwargs):
                    if cmd[1] == "api":
                        return subprocess.CompletedProcess(cmd, 1, "", diagnostic)
                    return subprocess.CompletedProcess(cmd, 0, '{"state":"OPEN"}', "")

                with mock.patch.object(dispatch, "_run", side_effect=command), \
                        self.reads.scope(self.journal, "dispatch"), self.reads.item(7):
                    observed = dispatch.open_blockers(7, "Blocked by: #9")
                self.assertEqual(observed, expected)
        self.assertEqual([row["code"] for row in lifecycle.read_events(self.journal)
                          if row["event"] == "gh-read-skipped"], ["gh_read_failed"])

    def test_dependency_404_keeps_roadmap_evidence_complete_inside_pass(self):
        import os
        import sys

        from factory import plan, roadmap
        from tests.test_roadmap import issue

        command = self.journal.parent / "gh"
        command.write_text(
            f"#!{sys.executable}\nimport sys\n"
            "print('gh: Not Found (HTTP 404)', file=sys.stderr)\nsys.exit(1)\n"
        )
        command.chmod(0o755)
        cfg = config.Config(self.journal.parent, "acme/widgets")
        result = {"sources": [], "errors": [], "coverage": {"notices": []}}
        reader = plan.Reader(cfg, result)
        reader.cache[f"{reader.prefix}/issues/7"] = issue(7, "Blocked by: #9")
        reader.cache[f"{reader.prefix}/issues/9"] = issue(9, "Prerequisite")
        observed = None
        with mock.patch.dict(os.environ, {"PATH": f"{command.parent}{os.pathsep}{os.environ['PATH']}"}), \
                self.reads.scope(self.journal, "manage"), self.reads.item(7):
            observed = roadmap._child(reader, result, 7)
        self.assertIsNotNone(observed)
        row, body, complete = observed
        self.assertTrue(complete)
        self.assertEqual([blocker["number"] for blocker in row["blockers"]], [9])
        self.assertEqual(result["errors"], [])
        self.assertEqual(lifecycle.read_events(self.journal), [])
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


class ManagerReadRetryTest(unittest.TestCase):
    setUp = manager_fixture.ManagerCloseJournalTest.setUp
    scenario = manager_fixture.ManagerCloseJournalTest.scenario
    run_manager = manager_fixture.ManagerCloseJournalTest.run_manager

    def test_close_read_failure_retries_next_pass_without_publishing_handoff(self):
        repo, stubs, state_path = self.scenario(fail="pr view", error="connection refused")
        before = json.loads(state_path.read_text())
        rows, state = self.run_manager(repo, stubs, state_path)
        self.assertEqual(state, before)
        self.assertFalse(any(row.get("event") == "handoff" for row in rows))
        state["fail"] = None
        state_path.write_text(json.dumps(state))
        rows, state = self.run_manager(repo, stubs, state_path)
        self.assertIn({"name": config.LABEL_WONTFIX}, state["issue"]["labels"])
        self.assertEqual(state["comments"], [
            {"subject": "issue", "body": "Factory manager: No change should land."},
        ])
        self.assertFalse(any(row.get("event") == "handoff" for row in rows))
        _, after = self.run_manager(repo, stubs, state_path)
        self.assertEqual(after, state)

    def test_partial_close_write_failure_is_not_replayed(self):
        repo, stubs, state_path = self.scenario(fail="issue edit", error="write denied")
        _, state = self.run_manager(repo, stubs, state_path)
        self.assertEqual([comment for comment in state["comments"]
                          if comment["body"] == "Factory manager: No change should land."], [
            {"subject": "issue", "body": "Factory manager: No change should land."},
        ])
        state["fail"] = None
        state_path.write_text(json.dumps(state))
        _, after = self.run_manager(repo, stubs, state_path)
        self.assertEqual(after["comments"], state["comments"])
        self.assertEqual(after["issue"], state["issue"])


class WatchReadHostConditionTest(unittest.TestCase):
    setUp = watch_fixture.WatchTest.setUp
    journal = watch_fixture.WatchTest.journal

    def test_github_read_reasons_obey_host_threshold_and_recovery(self):
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        for reason in ("gh_read_failed", "rate_limited"):
            with self.subTest(reason=reason):
                self.cfg.watch_host_failure_minutes = 15
                self.journal(watch_fixture.exit_row("dispatch", "mechanism_failure", reason,
                                                   at=(now - timedelta(minutes=20)).isoformat()))
                self.assertEqual(watch.run_pass(self.cfg), [])
                self.journal(watch_fixture.exit_row("dispatch", "mechanism_failure", reason,
                                                   at=(now - timedelta(minutes=10)).isoformat()),
                             watch_fixture.exit_row("dispatch", "mechanism_failure", reason,
                                                   at=now.isoformat()))
                filed = watch.run_pass(self.cfg)
                self.assertEqual([(row["kind"], row["key"]) for row in filed], [("host", reason)])
                create = self.gh.created[-1]
                self.assertEqual(create[create.index("--label") + 1], "bug,host,needs-triage")
                self.journal(watch_fixture.exit_row("dispatch", "completed", None))
