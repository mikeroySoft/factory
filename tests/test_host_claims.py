"""Cross-host dispatch against a shared, in-memory GitHub repository."""
from __future__ import annotations

import copy
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config


class FakeGitHub:
    def __init__(self):
        self.issue = {
            "number": 7, "title": "Shared ticket", "body": "", "state": "OPEN",
            "labels": [{"name": "ready-for-agent"}], "assignees": [], "comments": [],
            "createdAt": "2026-01-01T00:00:00Z",
        }
        self.comments = []
        self.calls = []
        self.lock = threading.Lock()
        self.fresh_barrier = None
        self.comments_barrier = None
        self.post_barrier = None
        self.fresh_hosts = set()
        self.comment_hosts = set()
        self.next_id = 1

    def json(self, host, args):
        barrier = None
        with self.lock:
            self.calls.append((host, list(args)))
            if args[:2] == ["issue", "list"]:
                result = [copy.deepcopy(self.issue)]
            elif args[:2] == ["issue", "view"]:
                result = copy.deepcopy(self.issue)
                if host not in self.fresh_hosts:
                    self.fresh_hosts.add(host)
                    barrier = self.fresh_barrier
            elif args[0] == "api":
                endpoint = next(arg for arg in args if arg.startswith("repos/"))
                if endpoint.endswith("/comments") and "--method" not in args:
                    result = [copy.deepcopy(self.comments)] if "--slurp" in args else copy.deepcopy(self.comments)
                    if host not in self.comment_hosts:
                        self.comment_hosts.add(host)
                        barrier = self.comments_barrier
                elif endpoint.endswith("/comments") and args[args.index("--method") + 1] == "POST":
                    body = next(arg[5:] for arg in args if arg.startswith("body="))
                    result = {"id": self.next_id, "body": body,
                              "created_at": datetime.now(UTC).isoformat()}
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

    def dispatcher(self, host):
        # Separate module state and lock directories model independent host processes.
        spec = importlib.util.spec_from_file_location(f"factory._dispatch_{host}", dispatch.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = Config(root=self.root / host, repo="local/claims",
                     workers={"default": [sys.executable, "{prompt}"]})
        cfg.host_name = host
        cfg.claim_ttl = "2h"
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


if __name__ == "__main__":
    unittest.main()
