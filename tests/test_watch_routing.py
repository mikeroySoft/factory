"""#311: engine failures go to one engine repo from every install, deduped across installs;
reader coverage gaps never file; triage never posts as anyone but the dispatcher."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, decisions, dispatch, lifecycle, onboard, runtime_events, triage, watch
from factory.config import LABEL_AGENT, LABEL_TRIAGE

ENGINE = config.ENGINE_REPO
REAL_RUNTIME_ERRORS = watch.runtime_errors


def ago(delta: timedelta) -> str:
    return (datetime.now(UTC) - delta).isoformat().replace("+00:00", "Z")


def failure_row(reason: str = "KeyError: 'state'") -> dict:
    at = lifecycle._now()
    return {"event": "lifecycle", "kind": "exit", "stage": "manage", "outcome": "mechanism_failure",
            "reason": reason, "at": at, "execution_id": f"exec-{at}", "ticket": None, "attempt": None}


class GitHub:
    """Every repository on one fake GitHub; records each gh call."""

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        repo = cmd[cmd.index("--repo") + 1]
        value = lambda flag: cmd[cmd.index(flag) + 1]
        out = ""
        if cmd[1:3] == ["issue", "create"]:
            number = 101 + len(self.issues)
            self.issues[number] = {"repo": repo, "state": "OPEN", "closedAt": None, "body": value("--body"),
                                   "comments": [], "assignees": [], "labels": [{"name": "bug"}, {"name": LABEL_TRIAGE}]}
            out = f"https://github.com/{repo}/issues/{number}\n"
        elif cmd[1:3] == ["issue", "list"]:
            out = json.dumps([{"number": n, "body": i["body"]} for n, i in self.issues.items()
                              if i["repo"] == repo and i["state"] == "OPEN"])
        else:
            issue = self.issues[int(cmd[3])]
            assert issue["repo"] == repo, cmd
            if cmd[1:3] == ["issue", "view"]:
                out = json.dumps(issue)
            elif cmd[1:3] == ["issue", "comment"]:
                issue["comments"].append({"body": value("--body"), "createdAt": lifecycle._now()})
            elif cmd[1:3] == ["issue", "edit"]:
                issue["body"] = value("--body")
            elif cmd[1:3] == ["issue", "close"]:
                issue.update(state="CLOSED", closedAt=lifecycle._now())
            else:
                raise AssertionError(f"unexpected command {cmd}")
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def ops(self, op: str) -> list[list[str]]:
        return [c for c in self.calls if c[1:3] == ["issue", op]]


class Install:
    """One factory install: its own checkout, journal and repository."""

    def __init__(self, test: unittest.TestCase, repo: str) -> None:
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), repo)
        self.cfg.factory.mkdir()

    def journal(self, *rows: dict) -> None:
        for row in rows:
            lifecycle.append(self.cfg.factory / "events.jsonl", row)

    @mock.patch.object(watch, "orphaned_claims", new=lambda cfg: [])
    def run(self, **patches) -> list[dict]:
        dispatch.configure(self.cfg)
        with mock.patch.object(watch, "failed_units", return_value=[]), \
             mock.patch.object(watch, "runtime_errors", patches.get("runtime_errors", lambda cfg: [])):
            return watch.run_pass(self.cfg)

    def watch_rows(self) -> list[dict]:
        return [r for r in lifecycle.read_events(self.cfg.factory / "events.jsonl") if r.get("event") == "watch"]


class RoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.github = GitHub()
        patch = mock.patch.object(dispatch, "run", self.github)
        patch.start()
        self.addCleanup(patch.stop)

    def test_downstream_install_files_engine_failure_into_the_engine_repo_only(self) -> None:
        rocm = Install(self, "acme/rocm-cli")
        rocm.journal(failure_row())
        self.assertEqual(len(rocm.run()), 1)
        self.assertEqual([c[c.index("--repo") + 1] for c in self.github.calls], [ENGINE, ENGINE])
        self.assertEqual(self.github.issues[101]["repo"], ENGINE)
        self.assertIn("- `acme/rocm-cli`", self.github.issues[101]["body"])
        self.assertEqual([(r["issue"], r["repo"]) for r in rocm.watch_rows()], [(101, ENGINE)])

    def test_configured_engine_repo_is_the_destination(self) -> None:
        install = Install(self, "acme/rocm-cli")
        install.cfg.watch_engine_repo = "acme/engine"
        install.journal(failure_row())
        install.run()
        self.assertEqual({c[c.index("--repo") + 1] for c in self.github.calls}, {"acme/engine"})

    def test_second_install_comments_on_the_first_filing_instead_of_opening(self) -> None:
        factory, gadgets = Install(self, ENGINE), Install(self, "acme/gadgets")
        factory.journal(failure_row())
        factory.run()
        gadgets.journal(failure_row())
        self.assertEqual(len(gadgets.run()), 1)

        self.assertEqual(len(self.github.ops("create")), 1, "second install must not open a duplicate")
        issue = self.github.issues[101]
        self.assertEqual(len(issue["comments"]), 1)
        note = issue["comments"][0]["body"]
        self.assertIn("acme/gadgets", note)
        self.assertIn("KeyError: 'state'", note, "the comment carries the newest evidence")
        observed = issue["body"].split(watch.OBSERVED, 1)[1].split("##", 1)[0]
        self.assertIn(f"- `{ENGINE}`", observed)
        self.assertIn("- `acme/gadgets`", observed)
        self.assertEqual([(r["issue"], r.get("commented")) for r in gadgets.watch_rows()], [(101, True)])

        gadgets.journal(failure_row())
        gadgets.run()
        self.assertEqual(len(issue["comments"]), 1, "at most one comment per install per 24h")
        self.assertEqual(len(self.github.ops("create")), 1)

    def test_install_comments_again_once_its_last_report_is_a_day_old(self) -> None:
        factory, gadgets = Install(self, ENGINE), Install(self, "acme/gadgets")
        factory.journal(failure_row())
        factory.run()
        fp = factory.watch_rows()[0]["fingerprint"]
        gadgets.journal({"event": "watch", "fingerprint": fp, "issue": 101, "repo": ENGINE, "commented": True,
                         "latest": ago(timedelta(hours=26)), "at": ago(timedelta(hours=25))},
                        failure_row())
        gadgets.run()
        self.assertEqual(len(self.github.issues[101]["comments"]), 1)
        self.assertEqual(len(self.github.ops("edit")), 1, "a newly observing install joins the Observed by list")

    def test_opener_does_not_close_while_another_install_still_reports(self) -> None:
        factory, gadgets = Install(self, ENGINE), Install(self, "acme/gadgets")
        factory.journal(failure_row())
        factory.run()
        gadgets.journal(failure_row())
        gadgets.run()
        fp = factory.watch_rows()[0]["fingerprint"]
        factory.journal({"event": "watch", "fingerprint": fp, "issue": 101, "repo": ENGINE,
                         "latest": ago(timedelta(days=3)), "at": ago(timedelta(days=3))})
        with mock.patch.object(watch, "signals", return_value=[]):
            factory.run()
            self.assertEqual(self.github.ops("close"), [], "gadgets reported it within the quiet period")
            self.assertEqual(gadgets.run(), [])
            self.assertEqual(self.github.ops("close"), [], "only the install that opened it closes it")
            self.github.issues[101]["comments"][0]["createdAt"] = ago(timedelta(hours=25))
            factory.run()
        self.assertEqual(len(self.github.ops("close")), 1)


class CoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.github = GitHub()
        patch = mock.patch.object(dispatch, "run", self.github)
        patch.start()
        self.addCleanup(patch.stop)
        self.install = Install(self, "acme/rocm-cli")
        self.journal = self.install.cfg.factory / "events.jsonl"

    def runtime_json(self, payload: dict | None = None, returncode: int = 0, stderr: str = ""):
        """`runtime_errors` against the reader's real output for this install's journal."""
        payload = payload or runtime_events.project(self.journal)
        proc = subprocess.CompletedProcess([], returncode, json.dumps(payload), stderr)
        with mock.patch.object(subprocess, "run", return_value=proc):
            return REAL_RUNTIME_ERRORS(self.install.cfg)

    def test_reader_limits_and_clipped_windows_are_coverage_not_failures(self) -> None:
        execution = lifecycle.Execution(self.journal, "worker", ticket=4)
        execution.emit("enter")
        waiting = execution.wait("capacity_reached", mode="admission", active=2, max_active=2)
        with self.journal.open("wb") as handle:
            handle.write(b" " * (2 * 1024 * 1024) + b"\n")  # byte_limit: the window starts mid-execution
            handle.write(b'{"pad": "' + b"x" * runtime_events.ROW_LIMIT + b'"}\n')  # row_limit
            handle.write(json.dumps({"event": "claimed", "ticket": 4}).encode() + b"\n")  # unsupported_record
            handle.write(json.dumps(waiting).encode() + b"\n")
        data = runtime_events.project(self.journal)
        self.assertTrue({"byte_limit", "row_limit", "unsupported_record"} <= {e["code"] for e in data["errors"]})
        self.assertEqual(data["failures"], [])
        self.assertEqual(self.runtime_json(), [])

    def test_an_unclassified_reader_code_never_files(self) -> None:
        payload = {"errors": [{"source": "events.jsonl", "scope": "history", "code": "some_future_limit"},
                              {"source": "events.jsonl", "scope": "executions", "code": "missing_enter"},
                              {"source": "proc", "scope": "executions", "code": "descendants_not_scanned"},
                              {"source": "unit", "scope": "systemd", "code": "transitioning"}]}
        errors = self.runtime_json(payload)
        self.assertEqual(errors, [])
        self.assertEqual(self.install.run(runtime_errors=lambda cfg: errors), [])
        self.assertEqual(self.github.calls, [])

    def test_journal_failure_files_the_offending_row_not_just_a_code(self) -> None:
        execution = lifecycle.Execution(self.journal, "worker", ticket=4)
        execution.emit("enter")
        with self.journal.open("ab") as handle:
            handle.write(b'{"torn": "row-from-a-broken-writer"\n')
        errors = self.runtime_json()
        self.assertEqual([e["code"] for e in errors], ["invalid_json"])
        self.install.run(runtime_errors=lambda cfg: errors)
        body = self.github.issues[101]["body"]
        self.assertIn('{"torn": "row-from-a-broken-writer"', body)

    def test_broken_reader_files_its_stderr(self) -> None:
        errors = self.runtime_json({}, returncode=1, stderr="Traceback\nKeyError: 'history'\n")
        self.install.run(runtime_errors=lambda cfg: errors)
        self.assertIn("KeyError: 'history'", self.github.issues[101]["body"])


class TriageIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: "", "GH_CONFIG_DIR": "/owner"}))
        self.enterContext(mock.patch.dict(triage.__dict__))
        self.cfg = config.Config(Path(temporary.name), ENGINE)
        self.cfg.factory.mkdir()
        triage.configure(self.cfg)
        self.calls: list[tuple] = []
        logins = lambda env: {"/agent": "factory-bot", "/owner": "owner"}.get(env.get("GH_CONFIG_DIR"), "")
        self.enterContext(mock.patch.object(onboard, "gh_login", side_effect=logins))

    def gh(self, *args, execution=None) -> str:
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": 7}])
        return json.dumps(self.issue("factory-bot")) if args[:2] == ("issue", "view") else ""

    @staticmethod
    def issue(author: str) -> dict:
        return {"number": 7, "title": "factory runtime failure: lifecycle manage KeyError: 'state'",
                "body": "Fingerprint: abc123def456\n\nevidence", "comments": [], "labels": [{"name": "bug"}],
                "author": {"login": author}, "state": "OPEN", "updatedAt": lifecycle._now()}

    def execute(self) -> int:
        args = argparse.Namespace(issue=None, replay=None, dry_run=False)
        with mock.patch.object(triage, "gh", self.gh), redirect_stderr(io.StringIO()) as err, \
             mock.patch.object(triage, "call_llm", side_effect=AssertionError("model called")):
            code = triage.execute(args, None)
        self.stderr = err.getvalue()
        return code

    def test_triage_refuses_to_post_as_anyone_but_the_dispatcher(self) -> None:
        self.cfg.install["dispatch_env"] = {"GH_CONFIG_DIR": "/agent"}
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.calls, [], "no read or post under the owner's credentials")
        self.assertIn("owner", self.stderr)
        self.assertIn("factory-bot", self.stderr)

    def test_dispatcher_identity_posts(self) -> None:
        self.cfg.install["dispatch_env"] = {"GH_CONFIG_DIR": "/agent"}
        os.environ["GH_CONFIG_DIR"] = "/agent"
        self.assertEqual(self.execute(), 0)
        self.assertIn(("issue", "edit", "7", "--remove-label", LABEL_TRIAGE, "--add-label", LABEL_AGENT), self.calls)

    def test_watch_filing_routed_from_another_install_skips_the_model(self) -> None:
        os.environ["GH_CONFIG_DIR"] = "/agent"
        self.assertEqual(triage.triage_issue(self.issue("factory-bot"))["brief"], triage.WATCH_BRIEF)
        self.assertFalse(triage.watch_filed(self.issue("owner")), "a copied format by another author is triaged")

    def test_dashboard_triage_runs_as_the_dispatcher(self) -> None:
        # The dashboard keeps the human identity; its "Run LLM triage" step must not trip the guard.
        self.cfg.install["dispatch_env"] = {"GH_CONFIG_DIR": "/agent"}
        seen: list[dict] = []
        run = lambda argv, env, **kwargs: seen.append(env) or subprocess.CompletedProcess(argv, 0, "", "")
        step = {"action": "triage", "request": 0, "argv": ["factory", "triage", "--issue", "7"]}
        with mock.patch.object(decisions, "_run", side_effect=run):
            decisions._mutation(self.cfg, step, deadline=float("inf"))
        with mock.patch.dict(os.environ, seen[0], clear=True):
            self.assertIsNone(triage.identity_mismatch())


if __name__ == "__main__":
    unittest.main()
