"""Cross-host dispatch against a shared, in-memory GitHub repository."""
from __future__ import annotations

import copy
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle, triage
from factory.config import Config


class FakeGitHub:
    def __init__(self):
        self.issue = {
            "number": 7, "title": "Shared ticket", "body": "", "state": "OPEN",
            "labels": [{"name": "ready-for-agent"}], "assignees": [], "comments": [],
            "createdAt": "2026-01-01T00:00:00Z",
        }
        self.comments = []
        self.open_prs = []
        self.calls = []
        self.lock = threading.Lock()
        self.fresh_barrier = None
        self.comments_barrier = None
        self.post_barrier = None
        self.fresh_hosts = set()
        self.comment_hosts = set()
        self.next_id = 1
        self.comment_patched = threading.Event()
        self.patch_count = 0

    def add_claim(self, host, at):
        nonce = f"00000000-0000-4000-8000-{self.next_id:012d}"
        comment = {
            "id": self.next_id,
            "body": f"Factory claim: host={host} nonce={nonce} at={at.isoformat()}",
            "created_at": at.isoformat(),
            "user": {"login": "@me"},
        }
        self.next_id += 1
        self.comments.append(comment)
        self.issue["assignees"] = [{"login": "@me"}]
        if not any(row["name"] == f"host:{host}" for row in self.issue["labels"]):
            self.issue["labels"].append({"name": f"host:{host}"})
        return comment

    def json(self, host, args):
        barrier = None
        with self.lock:
            self.calls.append((host, list(args)))
            if args[:2] == ["issue", "list"]:
                result = [copy.deepcopy(self.issue)]
            elif args[:2] == ["pr", "list"]:
                result = copy.deepcopy(self.open_prs)
            elif args == ["api", "user"]:
                result = {"login": "@me"}
            elif args[:2] == ["issue", "view"]:
                result = copy.deepcopy(self.issue)
                if host not in self.fresh_hosts:
                    self.fresh_hosts.add(host)
                    barrier = self.fresh_barrier
            elif args[0] == "api":
                endpoint = next(arg for arg in args if arg.startswith("repos/"))
                if endpoint.endswith("/comments") and "--method" not in args:
                    result = [copy.deepcopy(self.comments)] if "--slurp" in args else copy.deepcopy(self.comments)
                    if self.comments_barrier is not None and host not in self.comment_hosts:
                        self.comment_hosts.add(host)
                        barrier = self.comments_barrier
                elif endpoint.endswith("/comments") and args[args.index("--method") + 1] == "POST":
                    body = next(arg[5:] for arg in args if arg.startswith("body="))
                    result = {"id": self.next_id, "body": body,
                              "created_at": datetime.now(UTC).isoformat(), "user": {"login": "@me"}}
                    self.next_id += 1
                    self.comments.append(result)
                    barrier = self.post_barrier
                else:
                    raise AssertionError(args)
            else:
                raise AssertionError(args)
        if barrier:
            barrier.wait(timeout=10)
        return result

    def run(self, host, cmd, *args, **kwargs):
        if cmd[:3] == ["gh", "issue", "comment"]:
            comment = self.json(host, [
                "api", "repos/local/claims/issues/7/comments", "--method", "POST",
                "-f", f"body={cmd[cmd.index('--body') + 1]}",
            ])
            return subprocess.CompletedProcess(
                cmd, 0, f"https://github.com/local/claims/issues/7#issuecomment-{comment['id']}", "",
            )
        with self.lock:
            self.calls.append((host, list(cmd)))
            if cmd[:3] == ["gh", "issue", "edit"]:
                for flag, key in (("--add-label", "labels"), ("--remove-label", "labels"),
                                  ("--add-assignee", "assignees"), ("--remove-assignee", "assignees")):
                    if flag not in cmd:
                        continue
                    value = cmd[cmd.index(flag) + 1]
                    field = "name" if key == "labels" else "login"
                    if flag.startswith("--remove"):
                        self.issue[key] = [row for row in self.issue[key] if row[field] != value]
                    elif not any(row[field] == value for row in self.issue[key]):
                        self.issue[key].append({field: value})
            elif cmd[:2] == ["gh", "api"]:
                endpoint = next(arg for arg in cmd if arg.startswith("repos/"))
                comment_id = int(endpoint.rsplit("/", 1)[1])
                method = cmd[cmd.index("--method") + 1]
                if method == "DELETE":
                    self.comments = [row for row in self.comments if row["id"] != comment_id]
                elif method == "PATCH":
                    body = next(arg[5:] for arg in cmd if arg.startswith("body="))
                    next(row for row in self.comments if row["id"] == comment_id)["body"] = body
                    self.patch_count += 1
                    self.comment_patched.set()
                else:
                    raise AssertionError(cmd)
            elif cmd[:3] == ["gh", "label", "create"]:
                pass
            elif cmd[0] != "git":
                raise AssertionError(cmd)
        stdout = "1" if "rev-list" in cmd else "a" * 40
        return subprocess.CompletedProcess(cmd, 0, stdout, "")


class HostClaimRaceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.github = FakeGitHub()
        self.launched = []
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))

    def dispatcher(self, host, claim_ttl="2h"):
        # Separate module state and lock directories model independent host processes.
        key = host or "single"
        spec = importlib.util.spec_from_file_location(f"factory._dispatch_{key}", dispatch.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = Config(root=self.root / key, repo="local/claims",
                     workers={"default": [sys.executable, "{prompt}"]})
        cfg.host_name = host
        cfg.claim_ttl = claim_ttl
        module.configure(cfg)
        wt = cfg.factory / "wt-7"
        wt.mkdir(parents=True)
        patches = {
            "gh_json": lambda args: self.github.json(host, args),
            "run": lambda cmd, *a, **kw: self.github.run(host, cmd, *a, **kw),
            "admit_plan": lambda *a: None,
            "open_blockers": lambda *a: [],
            "ensure_worktree": lambda *a: wt,
            "agent_lease": lambda *a: "a" * 40,
            "refresh_requeue_context": lambda *a: "",
            "pending_risk_requeue_head": lambda *a: None,
            "sync_staged": lambda *a: None,
            "worker_round": lambda *a, **kw: self.launch(host),
            "push_and_pr": lambda *a, **kw: True,
            "record_risk": lambda *a: None,
            "pr_gate_and_review": lambda *a: ("APPROVE", "", False),
            "pr_comment": lambda *a: None,
            "approve_pr": lambda *a: True,
        }
        for name, value in patches.items():
            self.enterContext(mock.patch.object(module, name, value))
        return module

    def launch(self, host):
        self.launched.append(host)
        return True, "gate passed", None, "a" * 40

    def test_two_hosts_race_one_ticket_exactly_one_dispatches(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        issues = [module.frontier()[0] for module in (tank, ultron)]
        self.assertTrue(all(not issue.get("held") for issue in issues))
        self.github.fresh_barrier = threading.Barrier(2)
        self.github.comments_barrier = threading.Barrier(2)
        self.github.post_barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(module.process_ticket, issue, 1, False)
                       for module, issue in zip((tank, ultron), issues)]
            for future in futures:
                future.result(timeout=20)
        self.assertEqual(len(self.launched), 1, self.launched)
        winner = self.launched[0]
        loser = "ultron" if winner == "tank" else "tank"
        rows = lifecycle.read_events((tank if loser == "tank" else ultron).EVENTS)
        self.assertTrue(any(row.get("event") == "claim_lost" for row in rows), rows)
        self.assertEqual([row["name"] for row in self.github.issue["labels"]
                          if row["name"].startswith("host:")], [f"host:{winner}"])
        self.assertEqual(len(self.github.comments), 1)
        self.assertTrue(self.github.comments[0]["body"].startswith(f"Factory claim: host={winner} "))

    def test_stale_claim_past_ttl_is_released_and_reclaimed(self):
        now = datetime(2026, 11, 2, tzinfo=UTC)
        old = self.github.add_claim("ultron", now - timedelta(seconds=5))
        tank = self.dispatcher("tank", claim_ttl="1s")
        clock = self.enterContext(mock.patch.object(tank, "datetime", wraps=datetime))
        clock.now.return_value = now

        [stale] = tank.frontier()
        self.assertNotIn("held", stale)
        tank.process_ticket(stale, 1, False)

        self.assertEqual(self.launched, ["tank"])
        self.assertEqual(
            [row["name"] for row in self.github.issue["labels"]
             if row["name"].startswith("host:")],
            ["host:tank"],
        )
        self.assertEqual(len(self.github.comments), 1)
        self.assertTrue(self.github.comments[0]["body"].startswith("Factory claim: host=tank "))
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        released = [row for row in lifecycle.read_events(tank.EVENTS)
                    if row.get("event") == "claim-released"]
        self.assertEqual([(row["host"], row["last_heartbeat"]) for row in released],
                         [("ultron", old["created_at"])])

        thor = self.dispatcher("thor", claim_ttl="1s")
        clock = self.enterContext(mock.patch.object(thor, "datetime", wraps=datetime))
        clock.now.return_value = now
        [live] = thor.frontier()
        self.assertIn("tank", live["held"])
        thor.process_ticket(live, 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_claim_at_ttl_boundary_is_released(self):
        now = datetime.now(UTC)
        self.github.add_claim("ultron", now - timedelta(seconds=1))
        tank = self.dispatcher("tank", claim_ttl="1s")
        with mock.patch.object(tank, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            [issue] = tank.frontier()
            self.assertNotIn("held", issue)
            tank.process_ticket(issue, 1, False)
        self.assertEqual(self.launched, ["tank"])

    def test_equal_claim_timestamps_choose_lower_integer_comment_id(self):
        at = datetime.now(UTC)
        self.github.next_id = 10
        self.github.add_claim("tank", at)
        self.github.next_id = 2
        self.github.add_claim("ultron", at)

        thor = self.dispatcher("thor")
        [live] = thor.frontier()

        self.assertIn("ultron", live["held"])


    def test_unclaim_removes_only_own_host_claim(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.github.add_claim("tank", datetime.now(UTC))
        self.github.issue["labels"].append({"name": "bug"})
        labels_before = copy.deepcopy(self.github.issue["labels"])
        comments_before = copy.deepcopy(self.github.comments)

        ultron.unclaim(7, "--remove-label", "ready-for-agent", check=False)

        self.assertEqual(self.github.issue["labels"],
                         [row for row in labels_before if row["name"] != "ready-for-agent"])
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(self.github.comments, comments_before)

        tank.unclaim(7, "--remove-label", "ready-for-agent", check=False)

        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual([row["name"] for row in self.github.issue["labels"]], ["bug"])
        self.assertEqual(self.github.comments, [])

    def test_claim_heartbeat_keeps_blocked_worker_live(self):
        tank = self.dispatcher("tank", claim_ttl="1s")
        started, release = threading.Event(), threading.Event()

        def blocked_worker(*_args, **_kwargs):
            self.launched.append("tank")
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("blocked worker was not released")
            return True, "gate passed", None, "a" * 40

        self.enterContext(mock.patch.object(tank, "worker_round", blocked_worker))
        issue = copy.deepcopy(self.github.issue)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tank.process_ticket, issue, 1, False)
            try:
                self.assertTrue(started.wait(timeout=2))
                with self.github.lock:
                    initial = self.github.comments[0]["body"]
                    patches = self.github.patch_count
                    self.github.comment_patched.clear()
                self.assertTrue(self.github.comment_patched.wait(timeout=2))
                with self.github.lock:
                    refreshed = self.github.comments[0]["body"]
                    self.assertGreater(self.github.patch_count, patches)
                self.assertNotEqual(refreshed, initial)

                ultron = self.dispatcher("ultron", claim_ttl="1s")
                [live] = ultron.frontier()
                self.assertIn("tank", live["held"])
            finally:
                release.set()
            future.result(timeout=5)

        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
        self.assertEqual(
            [row["name"] for row in self.github.issue["labels"]
             if row["name"].startswith("host:")],
            ["host:tank"],
        )
        self.assertEqual(len(self.github.comments), 1)

    def test_worker_launch_failure_releases_host_claim(self):
        tank = self.dispatcher("tank")
        self.enterContext(mock.patch.object(
            tank, "worker_round", side_effect=FileNotFoundError("worker disappeared"),
        ))

        result = tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertFalse(result)
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(
            [row["name"] for row in self.github.issue["labels"]
             if row["name"].startswith("host:")],
            [],
        )
        self.assertEqual(len(self.github.comments), 1)
        self.assertIn("worker launch failed", self.github.comments[0]["body"])
        self.assertIn("worker disappeared", self.github.comments[0]["body"])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])

    def test_assignee_failure_releases_host_claim_without_worker_launch(self):
        tank = self.dispatcher("tank")
        run = tank.run

        def fail_assignment(cmd, *args, **kwargs):
            if "--add-assignee" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="assignment refused")
            return run(cmd, *args, **kwargs)

        with mock.patch.object(tank, "run", side_effect=fail_assignment):
            tank.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        self.assertEqual(self.launched, [])
        self.assertEqual(self.github.issue["assignees"], [])
        self.assertEqual(self.github.comments, [])
        self.assertEqual(self.github.issue["labels"], [{"name": "ready-for-agent"}])


class TriageHostTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = Config(root=Path(temp.name), repo="local/claims")
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "ultron"

    def test_triage_skips_on_non_triage_host(self):
        output = io.StringIO()
        with mock.patch.object(triage.config, "load", return_value=self.cfg), \
                mock.patch.object(triage, "identity_mismatch") as identity, \
                mock.patch.object(triage, "list_needs_triage") as issue_list, \
                redirect_stdout(output):
            self.assertEqual(triage.main([]), 0)
        identity.assert_not_called()
        issue_list.assert_not_called()
        self.assertEqual(output.getvalue(), "triage: skipped, triage host is ultron\n")
        self.assertFalse(self.cfg.factory.exists())

        self.cfg.host_name = None
        with mock.patch.object(triage.config, "load", return_value=self.cfg), \
                mock.patch.object(triage, "identity_mismatch", return_value="") as identity, \
                mock.patch.object(triage, "list_needs_triage", return_value=[]) as issue_list:
            self.assertEqual(triage.main([]), 0)
        identity.assert_called_once()
        issue_list.assert_called_once()


class SingleHostTest(unittest.TestCase):
    def setUp(self):
        HostClaimRaceTest.setUp(self)

    dispatcher = HostClaimRaceTest.dispatcher
    launch = HostClaimRaceTest.launch

    def test_single_host_claim_path_unchanged(self):
        single = self.dispatcher(None)

        single.process_ticket(copy.deepcopy(self.github.issue), 1, False)

        github_calls = [
            args for _, args in self.github.calls
            if args[0] in {"issue", "api", "gh"}
        ]
        self.assertEqual(github_calls, [
            [
                "issue", "view", "7", "--repo", "local/claims", "--json",
                "state,labels,assignees,title,body,comments",
            ],
            [
                "gh", "issue", "edit", "7", "--repo", "local/claims",
                "--add-assignee", "@me",
            ],
        ])
        self.assertFalse(any(
            arg in {"--add-label", "--remove-label"}
            for call in github_calls for arg in call
        ))
        self.assertFalse(any(
            call[0] == "api" or call[:3] == ["gh", "issue", "comment"]
            for call in github_calls
        ))


if __name__ == "__main__":
    unittest.main()
