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
        self.enterContext(patch.multiple(
            dispatch, create=True, cfg=self.cfg, ROOT=self.root, REPO=self.cfg.repo,
            UPSTREAM=None, UPSTREAM_REPO=None, FACTORY=self.cfg.factory,
            LOGS=self.cfg.factory / "logs", SYNC_LOG=self.cfg.factory / "upstream-sync.jsonl",
            EVENTS=self.cfg.factory / "events.jsonl", MAX_ACTIVE=2, MAX_ATTEMPTS=self.cfg.max_attempts,
        ))
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

    def test_every_stage_skips_without_github_or_workers(self):
        from factory import learn, manage, release, triage, watch

        self.call("pause", "--reason", "incident")
        with patch.object(manage, "manage_pass") as manager, \
                patch.object(dispatch, "worker_health_pass") as health, \
                patch.object(watch, "signals") as signals, \
                patch.object(triage, "execute") as triage_work, \
                patch.object(learn, "run") as learning, \
                patch.object(release, "run") as releasing:
            for stage in ("manage", "watch", "triage", "learn", "release"):
                with self.subTest(stage=stage):
                    code, output = self.call(stage)
                    self.assertEqual(code, 0)
                    self.assertEqual(len(output.splitlines()), 1)
                    self.assertIn("paused (incident", output)
        for action in (manager, health, signals, triage_work, learning, releasing, self.gh, self.commands):
            action.assert_not_called()
        self.assertEqual([row["event"] for row in self.rows()], ["pause"])

    def test_runtime_projection_tracks_pause_without_expiring_it(self):
        from factory import pause, runtime_local

        self.call("pause", "--reason", "maintenance", "--until", "2000-01-01T00:00:00Z")
        def local_command(operation, **kwargs):
            if operation == "schedule":
                return "[]"
            return "LoadState=loaded\nActiveState=inactive\n"

        with patch.object(runtime_local, "_command", side_effect=local_command):
            paused, _ = runtime_local.dispatcher(self.cfg, [], {})
            self.assertEqual(paused["paused"], pause.read(self.root))
            self.assertEqual(paused["paused"]["reason"], "maintenance")
            self.assertEqual([row["event"] for row in self.rows()], ["pause"])
            self.call("resume")
            running, _ = runtime_local.dispatcher(self.cfg, [], {})
            self.assertIsNone(running["paused"])

    def test_malformed_state_never_runs_a_pass(self):
        path = self.cfg.factory / "pause.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"until": "bad"}))
        with self.assertRaises((ValueError, config.ConfigError)):
            self.call("dispatch")
        self.assertFalse(self.gh.called)
        self.assertFalse(self.commands.called)

    def test_forced_ticket_stale_engine_never_runs_unrelated_stages(self):
        from factory import pause

        self.call("pause", "--reason", "maintenance")
        with patch.object(self.cfg, "engine_yield_for_update", True), \
                patch.object(config, "engine_commit", return_value="a" * 40), \
                patch.object(config, "engine_target", return_value="b" * 40), \
                patch.object(dispatch, "github_preflight", return_value=None), \
                patch.object(dispatch, "process_ticket") as process, \
                patch.object(dispatch, "land_pass") as land, \
                patch.object(dispatch, "review_intake_pass") as review, \
                patch("factory.release.run") as release:
            for options in ([], ["--dry-run"]):
                with self.subTest(options=options):
                    code, output = self.call("dispatch", "--ticket", "1", "--force", *options)
                    land.assert_not_called()
                    review.assert_not_called()
                    release.assert_not_called()
                    process.assert_not_called()
                    self.assertNotEqual(code, 0)
                    self.assertIn("engine-stale", output)
                    self.assertIsNotNone(pause.read(self.root))
                    self.assertTrue(pause.check(self.root, log=lambda _: None))

    def test_repause_expired_state_stops_dispatch_and_journals_both_transitions(self):
        from factory import pause

        self.call("pause", "--reason", "window", "--until", "2000-01-01T00:00:00Z")
        self.assertEqual(self.call("pause", "--reason", "incident")[0], 0)
        state = pause.read(self.root)
        self.assertEqual((state["reason"], state["until"]), ("incident", None))
        self.assertEqual(self.call("pause", "--reason", "ignored")[0], 0)
        self.assertEqual(pause.read(self.root), state)
        code, output = self.call("dispatch")
        self.assertEqual(code, 0)
        self.assertIn("paused (incident", output)
        self.gh.assert_not_called()
        self.commands.assert_not_called()
        rows = self.rows()
        self.assertEqual([row["event"] for row in rows], ["pause", "resume", "pause"])
        self.assertEqual((rows[1]["who"], rows[1]["reason"]), ("expiry", "window"))
        self.assertEqual((rows[2]["who"], rows[2]["reason"]),
                         ("operator (github: operator-gh)", "incident"))



class PauseClaimTests(unittest.TestCase):
    # Reuse the hermetic real ticket/host-claim fixture, not a fake process_ticket.
    from tests.test_host_claims import HostClaimRaceTest as _Harness

    setUp = _Harness.setUp
    dispatcher = _Harness.dispatcher
    launch = _Harness.launch

    def test_pause_after_worker_parks_claim_and_preserves_worktree(self):
        from factory import pause

        for host in (None, "tank"):
            with self.subTest(host=host):
                module = self.dispatcher(host)
                def worker(*args, root=module.ROOT, **kwargs):
                    pause.set_pause(root, reason="stop after worker")
                    return True, "gate passed", None, "a" * 40

                with patch.object(pause, "actor", return_value="operator"), \
                        patch.object(module, "worker_round", side_effect=worker), \
                        patch.object(module, "push_and_pr") as publish:
                    module.process_ticket(dict(self.github.issue), 1, False)
                publish.assert_not_called()
                self.assertEqual(self.github.issue["assignees"], [])
                self.assertEqual(self.github.comments, [])
                self.assertFalse(any(label["name"].startswith("host:") for label in self.github.issue["labels"]))
                self.assertTrue((module.FACTORY / "wt-7").is_dir())
                self.assertFalse(module.active_tickets())
                self.assertFalse(any(row.get("event") == "escalate" for row in lifecycle.read_events(module.EVENTS)))

    def test_resume_fix_reacquires_host_ownership_before_applying(self):
        from factory import manage

        module = self.dispatcher("tank")
        pending = {"worker": "default", "guidance": "fix boundary", "packet": "packet.md"}
        module.record("manage-pending", ticket=7, decision="FIX", **pending)

        def apply_owned(*args, **kwargs):
            self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
            self.assertIsNotNone(module.owned_host_claim(7))
            self.assertTrue(module._HOST_CLAIMS[7].thread.is_alive())

        with patch.object(manage, "dispatch", module), \
                patch.object(module, "fallback_worker", return_value="default"), \
                patch.object(manage, "apply_fix", side_effect=apply_owned) as apply:
            manage.resume_fix(7, self.github.issue, pending)
        apply.assert_called_once()
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(len(self.github.comments), 1)
        self.assertEqual(module._HOST_CLAIMS, {})
        self.assertIsNone(manage.pending_fix(lifecycle.read_events(module.EVENTS)))

    def test_resume_fix_leaves_pending_when_another_host_owns_ticket(self):
        from factory import manage

        module = self.dispatcher("tank")
        self.github.add_claim("ultron", datetime.now(UTC))
        pending = {"worker": "default", "guidance": "fix boundary", "packet": "packet.md"}
        module.record("manage-pending", ticket=7, decision="FIX", **pending)
        with patch.object(manage, "dispatch", module), \
                patch.object(module, "fallback_worker", return_value="default"), \
                patch.object(manage, "apply_fix") as apply:
            manage.resume_fix(7, self.github.issue, pending)
        apply.assert_not_called()
        self.assertEqual(len(self.github.comments), 1)
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertIsNotNone(manage.pending_fix(lifecycle.read_events(module.EVENTS)))


class PauseManagerFixTests(unittest.TestCase):
    from tests.test_frontier import FrontierTest as _Harness

    read = _Harness.read
    events = _Harness.events
    approved_head = _Harness.approved_head
    quiet_provider = _Harness.quiet_provider

    def setUp(self):
        real_run = dispatch.run
        self._Harness.setUp(self)
        self.enterContext(patch.object(dispatch, "run", real_run))
        self.enterContext(patch.object(dispatch, "_run", side_effect=self.fake_run))
        self.enterContext(patch("factory.pause.actor", return_value="operator"))
        self.local_head = self.pr["headRefOid"]
        self.assigned = True

    def gh_json(self, args):
        if args[:2] == ["issue", "list"]:
            return [self.gh_json(["issue", "view"])]
        return self._Harness.gh_json(self, args)

    def fake_run(self, cmd, *args):
        if cmd[:3] == ["git", "branch", "--show-current"]:
            output = "agent/79"
        elif cmd[:3] == ["git", "rev-parse", "HEAD"]:
            output = self.local_head
        elif cmd[:2] == ["git", "merge-base"]:
            output = ""
        elif cmd[:2] == ["git", "push"]:
            self.mutations.append(cmd)
            self.pr["headRefOid"] = self.local_head
            output = ""
        else:
            if cmd[:3] == ["gh", "issue", "edit"] and "--remove-assignee" in cmd:
                self.assigned = False
            return self._Harness.fake_run(self, cmd)
        return subprocess.CompletedProcess(cmd, 0, output, "")

    def park_and_resume(self, stage):
        from factory import manage, pause
        from tests.test_frontier import H2

        self.cfg.manager_review = "all"
        self.cfg.workers = {"droid": ["worker-stub"]}
        self.quiet_provider()
        old_head = self.pr["headRefOid"]
        self.approved_head(old_head)
        wt = self.cfg.factory / "wt-79"
        wt.mkdir()
        packet = self.cfg.factory / "packet.md"
        packet.write_text("Evidence")
        self.manager_output = 'DECISION: FIX\n{"worker": "droid", "guidance": "fix boundary"}'
        decided = {"pr": 80, "head": old_head} if stage == "frontier" else {"round": 1, "pr": None}
        if stage == "escalation":
            self.issue_labels = [config.LABEL_HUMAN]
            dispatch.record("escalate", ticket=79, round=1, packet=str(packet), reason="gate_failed")
        run_pass = manage.frontier_pass if stage == "frontier" else manage.escalation_pass

        def worker(*args, **kwargs):
            self.local_head = H2
            dispatch.record("attempt", ticket=79, attempt=1, gate="PASS", head=H2)
            if worker_mock.call_count == 1:
                pause.set_pause(self.repo, reason="incident")
            return True, "gate passed", None, H2

        with patch.object(manage, "selectable_workers", return_value=self.cfg.workers), \
                patch.object(manage, "human_activity", return_value=False), \
                patch.object(dispatch, "escalation_packet", return_value=(packet, "")), \
                patch.object(dispatch, "worker_round", side_effect=worker) as worker_mock, \
                patch.object(dispatch, "record_risk"), \
                patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "approved", False)), \
                patch.object(dispatch, "pr_comment"), \
                patch.object(dispatch, "approve_pr", return_value=True) as approve:
            self.assertTrue(run_pass())
            self.assertFalse(self.assigned, "parked manager FIX must release its assignment")
            pending = manage.pending_fix(self.events(), **decided)
            self.assertIsNotNone(pending, "parked manager FIX must remain resumable")
            self.assertEqual((pending["worker"], pending["guidance"]), ("droid", "fix boundary"))
            self.assertTrue(wt.is_dir())
            self.assertEqual(self.pr["headRefOid"], old_head)
            self.assertFalse(any(cmd[:2] == ["git", "push"] for cmd in self.mutations))
            self.assertTrue(run_pass())  # still paused: no second worker
            self.assertEqual(worker_mock.call_count, 1)
            pause.resume(self.repo)
            run_pass()
            self.assertEqual(self.pr["headRefOid"], H2)
            approve.assert_called_once_with(79, H2)
            self.assertIsNone(manage.pending_fix(self.events(), **decided))
        self.assertEqual(len([row for row in self.events() if row["event"] == "manage"]), 1)

    def test_frontier_fix_paused_before_push_releases_assignment_and_resumes(self):
        self.park_and_resume("frontier")

    def test_escalation_fix_paused_before_push_releases_assignment_and_resumes(self):
        self.park_and_resume("escalation")

    def test_resumed_frontier_fix_is_not_claimed_again_by_ticket_pass(self):
        def fresh_issue(args):
            result = self.gh_json(args)
            for issue in result if args[:2] == ["issue", "list"] else [result]:
                if args[0] == "issue":
                    issue["assignees"] = [{"login": "factory-bot"}] if self.assigned else []
            return result

        def run(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "edit"] and "--add-assignee" in cmd:
                self.assigned = True
            return self.fake_run(cmd, *args)

        with patch.object(dispatch, "gh_json", side_effect=fresh_issue), \
                patch.object(dispatch, "_run", side_effect=run):
            self.park_and_resume("frontier")
            with patch.object(dispatch, "active_tickets", return_value=[]), \
                    patch.object(dispatch, "fairness_session", return_value={}), \
                    patch.object(dispatch, "open_blockers", return_value=[]), \
                    patch.object(dispatch, "process_ticket") as process:
                dispatch.ticket_pass(1, False)
            process.assert_not_called()
            self.assertTrue(self.assigned)


if __name__ == "__main__":
    unittest.main()
