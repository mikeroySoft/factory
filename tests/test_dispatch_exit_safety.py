"""Queue transitions against a stateful gh process, including partial writes."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from factory import dispatch, lifecycle
from factory.config import Config

GH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ["EXIT_STATE"])
s = json.loads(p.read_text())
a = sys.argv[1:]
s["calls"].append(a)
operation = "comment" if a[:2] == ["issue", "comment"] else "add" if a[:2] == ["issue", "edit"] and "--add-label" in a else "edit"
failure = s.get("failure") == operation and s.get("remaining", 0) != 0
# gh can apply removals before a failed addition in the same issue edit.
for i, value in enumerate(a[:-1] if a[:2] == ["issue", "edit"] else []):
    if value == "--remove-label":
        s["labels"] = [x for x in s["labels"] if x != a[i + 1]]
    if value == "--remove-assignee":
        s["assigned"] = False
if failure:
    s["remaining"] -= 1
elif operation == "add":
    label = a[a.index("--add-label") + 1]
    if label not in s["labels"]:
        s["labels"].append(label)
elif operation == "comment":
    s["comments"].append(a[a.index("--body") + 1])
elif a[:2] == ["pr", "merge"]:
    s["closed"] = True
s["history"].append(list(s["labels"]))
p.write_text(json.dumps(s))
if failure:
    print(s.get("error", "GraphQL: secondary rate limit; retry-after: 75"), file=sys.stderr)
    raise SystemExit(1)
if operation == "comment":
    print("https://github.com/local/safety/issues/7#issuecomment-123")
'''


class EscalationWriteSafetyTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = root / "state.json"
        self.state.write_text(json.dumps({"labels": ["ready-for-agent"], "assigned": True,
                                          "comments": [], "calls": [], "history": []}))
        gh = root / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.enterContext(mock.patch.dict(os.environ, {
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "EXIT_STATE": str(self.state), lifecycle.CONTEXT_ENV: "",
        }))
        saved = getattr(dispatch, "cfg", None)
        if saved is not None:
            self.addCleanup(dispatch.configure, saved)
        dispatch.configure(Config(root=root, repo="local/safety"))
        self.sleep = self.enterContext(mock.patch.object(dispatch.time, "sleep"))
        self.enterContext(mock.patch.object(dispatch, "_ESCALATED", set()))

    def data(self):
        return json.loads(self.state.read_text())

    def inject_failure(self, operation, remaining=-1, error=None):
        state = self.data()
        state.update(failure=operation, remaining=remaining)
        if error:
            state["error"] = error
        self.state.write_text(json.dumps(state))

    def escalate(self):
        with lifecycle.scope(dispatch.EVENTS, "ticket", ticket=7):
            dispatch.escalate(7, "gate failed", None, code="gate_failed")

    def assert_loud_failure(self, operation):
        self.inject_failure(operation)
        with self.assertRaises(subprocess.CalledProcessError):
            self.escalate()
        rows = lifecycle.read_events(dispatch.EVENTS)
        failures = [row for row in rows if row.get("event") == "github-write-failed"]
        self.assertEqual([row["attempt"] for row in failures], [1, 2])
        self.assertTrue(all(row["returncode"] == 1 for row in failures))
        self.assertEqual(rows[-1]["outcome"], "mechanism_failure")
        self.assertEqual(rows[-1]["reason"], "github_write_failed")
        self.assertEqual(self.sleep.call_args.args, (75.0,))
        self.assertEqual(self.data()["labels"], ["ready-for-agent" if operation == "add" else "ready-for-human"])
        self.assertTrue(all(labels for labels in self.data()["history"]))
        child_failures = [row for row in rows if row.get("kind") == "child_exit"
                          and row.get("returncode") == 1]
        self.assertEqual(len(child_failures), 2)
        self.assertTrue(all(row["command"][:3] == ["gh", "issue", "comment" if operation == "comment" else "edit"]
                            for row in child_failures))
        self.assertNotIn(7, dispatch._ESCALATED)

    def test_label_add_failure_retains_queue_and_is_recorded(self):
        self.assert_loud_failure("add")
        self.assertIn("gate failed", self.data()["comments"][0])

    def test_comment_failure_retains_queue_and_is_recorded(self):
        self.assert_loud_failure("comment")

    def test_rate_limit_retry_recovers_with_backoff(self):
        for error in ("GraphQL: API rate limit already exceeded", "HTTP 403: secondary rate limit"):
            with self.subTest(error=error):
                self.inject_failure("add", remaining=1, error=error)
                self.escalate()
                self.assertGreaterEqual(self.sleep.call_args.args[0], 60)
                self.assertEqual(self.data()["labels"], ["ready-for-human"])
                self.assertTrue(all(labels for labels in self.data()["history"]))

    def test_non_rate_limit_failure_is_also_retried(self):
        self.inject_failure("comment", remaining=1, error="HTTP 502: Something went wrong")
        self.escalate()
        self.assertEqual(self.sleep.call_args.args, (1,))
        self.assertEqual(self.data()["labels"], ["ready-for-human"])
        self.assertIn("gate failed", self.data()["comments"][0])

    def test_escalation_codes_all_leave_one_queue_and_reason_comment(self):
        for code in sorted(dispatch.ESCALATION_CODES):
            with self.subTest(exit=code):
                state = self.data()
                state["labels"] = ["ready-for-agent", "factory-held"]
                self.state.write_text(json.dumps(state))
                dispatch.escalate(7, f"reason for {code}", None, code=code)
                self.assertEqual(self.data()["labels"], ["ready-for-human"])
                self.assertIn(f"reason for {code}", self.data()["comments"][-1])
                self.assertTrue(all(labels for labels in self.data()["history"]))


class TicketExitMatrixTest(unittest.TestCase):
    setUp = EscalationWriteSafetyTest.setUp
    data = EscalationWriteSafetyTest.data

    def test_owned_ticket_exit_paths_have_one_queue_and_a_reason(self):
        # Enumerate the worker/gate/review/publish/approval/exception exits, not only codes.
        cases = (
            ("protected_paths", "worker edited protected paths"),
            ("budget", "wall-clock budget"),
            ("gate_failed", "gate failed"),
            ("worker_stuck", "worker stuck"),
            ("no_change", "worker committed nothing"),
            ("pr_unpublished", "PR not published"),
            ("reviewer_unavailable", "reviewer unavailable"),
            ("risk_disagreement", "checks disagreed"),
            ("pr_checks_failed", "pr checks failed"),
            ("review_revise", "REVISE verdict"),
            ("approval_drift", "approval evidence"),
            ("lease_refused", "push refused"),
            ("dispatcher_error", "dispatcher error"),
            ("worker_unavailable", "worker unavailable"),
            ("worker_launch_failed", "worker launch failed"),
            ("approved", "gate and review passed"),
            ("manager_approval", "manager approval required"),
            ("bounce_gate_failed", "gate failed after review bounce"),
            ("bounce_protected", "worker edited protected paths"),
            ("bounce_reviewer_unavailable", "reviewer unavailable"),
            ("bounce_risk_disagreement", "checks disagreed"),
        )
        root = dispatch.ROOT
        real_run = dispatch.run
        head = "a" * 40
        for exit_path, reason in cases:
            with self.subTest(exit=exit_path), ExitStack() as stack:
                cfg = Config(root=root, repo="local/safety", max_attempts=1, review_rounds=1)
                if exit_path == "manager_approval":
                    cfg.manager, cfg.manager_review = ["manager"], "all"
                dispatch.configure(cfg)
                self.state.write_text(json.dumps({
                    "labels": ["ready-for-agent"], "assigned": True,
                    "comments": [], "calls": [], "history": [],
                }))
                wt = dispatch.FACTORY / "wt-7"
                (wt / ".factory").mkdir(parents=True, exist_ok=True)
                handoff = wt / ".factory" / "handoff-7.md"
                handoff.unlink(missing_ok=True)
                if exit_path == "no_change":
                    handoff.write_text("Nothing to change")
                issue = {"number": 7, "title": "change", "body": "", "comments": [],
                         "labels": [{"name": "ready-for-agent"}], "assignees": [], "state": "OPEN"}
                pr = {"number": 70, "state": "OPEN", "headRefOid": head,
                      "baseRefName": "main", "reviewDecision": "", "files": []}

                def command(cmd, exit_path=exit_path, **kwargs):
                    if cmd[0] == "gh":
                        return real_run(cmd, **kwargs)
                    stdout = ("0" if exit_path == "no_change" else "1") if "rev-list" in cmd else head
                    return subprocess.CompletedProcess(cmd, 0, stdout, "")

                patches = {
                    "run": command,
                    "gh_json": lambda args, issue=issue, pr=pr: issue if args[0] == "issue" else pr,
                    "admit_plan": lambda *a, **kw: None,
                    "worker_missing": lambda *a: None,
                    "ensure_worktree": lambda *a, wt=wt: wt,
                    "agent_lease": lambda *a, **kw: head,
                    "sync_staged": lambda *a: None,
                    "record_risk": lambda *a: None,
                    "pr_comment": lambda *a: None,
                    "retain_handoff": lambda *a: True,
                    "_head_evidence_matches": lambda *a, exit_path=exit_path, **kw: exit_path != "approval_drift",
                }
                for name, value in patches.items():
                    stack.enter_context(mock.patch.object(dispatch, name, value))
                success = (True, "gate ok", None, head)
                failure = (False, "worker stuck" if exit_path == "worker_stuck" else "gate failed", None, head)
                protected = (False, "# Gate report\n- protected-paths: FAIL\n", None, head)
                worker = stack.enter_context(mock.patch.object(dispatch, "worker_round", return_value=success))
                if exit_path in {"gate_failed", "worker_stuck", "budget"}:
                    worker.return_value = failure
                elif exit_path == "protected_paths":
                    worker.return_value = protected
                elif exit_path == "worker_unavailable":
                    worker.side_effect = dispatch.WorkerUnavailable("host offline")
                elif exit_path == "worker_launch_failed":
                    worker.side_effect = FileNotFoundError("worker missing")
                elif exit_path == "dispatcher_error":
                    worker.side_effect = RuntimeError("pipeline broke")
                if exit_path == "budget":
                    stack.enter_context(mock.patch.object(dispatch.time, "monotonic", side_effect=[0, 61]))
                push = stack.enter_context(mock.patch.object(dispatch, "push_and_pr", return_value=True))
                if exit_path == "pr_unpublished":
                    push.return_value = False
                elif exit_path == "lease_refused":
                    push.side_effect = dispatch.LeaseRefused(subprocess.CompletedProcess(["git"], 1), "push refused")
                review = stack.enter_context(mock.patch.object(
                    dispatch, "pr_gate_and_review", return_value=("APPROVE", "", False),
                ))
                if exit_path == "reviewer_unavailable":
                    review.return_value = (None, "", False)
                elif exit_path in {"pr_checks_failed", "review_revise"}:
                    review.return_value = ("REJECT", "", exit_path == "pr_checks_failed")
                elif exit_path.startswith("bounce_"):
                    review.side_effect = [("REVISE", "", False), (None, "", False)]
                    worker.side_effect = [success, protected if exit_path == "bounce_protected" else
                                          failure if exit_path == "bounce_gate_failed" else success]
                    stack.enter_context(mock.patch.object(dispatch, "push_agent", return_value=head))
                if exit_path in {"risk_disagreement", "bounce_risk_disagreement"}:
                    stack.enter_context(mock.patch.dict(dispatch._PENDING_RISK, {7: {"packet": "disagreement"}}))
                    stack.enter_context(mock.patch.object(
                        dispatch, "risk_disagreement_transition",
                        side_effect=[(head, False), (head, True)] if exit_path.startswith("bounce_")
                        else [(head, True)],
                    ))
                if exit_path == "dispatcher_error":
                    with self.assertRaises(RuntimeError):
                        dispatch.process_ticket(issue, 1, False)
                else:
                    dispatch.process_ticket(issue, 1, False)
                expected = "ready-for-agent" if exit_path in {
                    "approved", "manager_approval", "worker_unavailable", "worker_launch_failed",
                } else "ready-for-human"
                self.assertEqual(self.data()["labels"], [expected])
                self.assertIn(reason, self.data()["comments"][-1])
                self.assertTrue(all(labels for labels in self.data()["history"]))

    def test_deferred_review_rework_replaces_human_and_held_with_agent(self):
        state = self.data()
        state["labels"] = ["ready-for-human", "factory-held"]
        self.state.write_text(json.dumps(state))
        dispatch._record_rework(7, "head", "Fix required branch", deferred=True)
        self.assertEqual(self.data()["labels"], ["ready-for-agent"])
        self.assertIn("review rework", self.data()["comments"][-1])
        self.assertTrue(all(labels for labels in self.data()["history"]))


class MergeExitSafetyTest(unittest.TestCase):
    setUp = EscalationWriteSafetyTest.setUp
    data = EscalationWriteSafetyTest.data

    def test_merge_exit_closes_ticket_and_posts_reason(self):
        head = "a" * 40
        pr = {"number": 70, "state": "OPEN", "headRefName": "agent/7", "headRefOid": head,
              "baseRefName": "main", "reviewDecision": "", "isDraft": False,
              "labels": [{"name": "factory-approved"}], "title": "change", "files": []}

        def query(args):
            if args[:2] == ["pr", "list"]:
                return [pr]
            if args[:2] == ["pr", "view"]:
                return pr
            if args[:2] == ["issue", "view"]:
                return {"labels": [{"name": "ready-for-agent"}]}
            if args[0] == "api" and "/compare/" in args[1]:
                return {"behind_by": 0}
            raise AssertionError(args)

        real_run = dispatch.run

        def command(cmd, **kwargs):
            if cmd[0] == "gh":
                return real_run(cmd, **kwargs)
            return subprocess.CompletedProcess(cmd, 0, head, "")

        dispatch.record("approved", ticket=7, pr=70, head=head, gate_head=head, review_head=head)
        with mock.patch.object(dispatch, "gh_json", side_effect=query), \
                mock.patch.object(dispatch, "run", side_effect=command), \
                mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "CI", "bucket": "pass"}]), \
                mock.patch.object(dispatch, "_head_evidence_matches", return_value=True), \
                mock.patch.object(dispatch, "cleanup_after_merge"), \
                mock.patch("factory.learn.promote_pass"):
            dispatch.merge_pass_locked(False)
        self.assertTrue(self.data()["closed"])
        self.assertIn("gate, review, approval and CI passed", self.data()["comments"][-1])


class RefreshExitMatrixTest(unittest.TestCase):
    def test_refresh_requeue_and_approval_exits_have_one_queue_and_reason(self):
        from tests import test_refresh_conflict as refresh

        for exit_path in ("conflict", "gate", "risk", "review", "approved"):
            with self.subTest(exit=exit_path), ExitStack() as stack:
                harness = refresh.RefreshConflictTest() if exit_path == "conflict" else refresh.RefreshGateFailureTest()
                stack.callback(harness.doCleanups)
                harness.setUp()
                github = harness.github
                # A forced/manager path may start human-owned; every successful
                # requeue must replace both other queue states without a gap.
                github.issue_labels = {"ready-for-human", "factory-held"}
                comments = []
                original = github.run

                def command(cmd, comments=comments, original=original, **kwargs):
                    if cmd[:3] == ["gh", "issue", "comment"]:
                        comments.append(cmd[cmd.index("--body") + 1])
                    return original(cmd, **kwargs)

                stack.enter_context(mock.patch.object(dispatch, "run", side_effect=command))
                stack.enter_context(mock.patch.object(dispatch, "gh_json", side_effect=github.gh_json))
                stack.enter_context(mock.patch.object(
                    dispatch, "run_gate", return_value=(exit_path != "gate", "gate report"),
                ))
                stack.enter_context(mock.patch.object(dispatch, "record_risk"))
                if exit_path == "risk":
                    stack.enter_context(mock.patch.object(dispatch, "risk_for_head", return_value=("High", [])))

                    def disagree(*args, **kwargs):
                        dispatch._PENDING_RISK[9] = {"head": args[3], "disagreement": True, "packet": "machine disagreement"}
                        return "REVISE", "machine disagreement", True

                    stack.enter_context(mock.patch.object(dispatch, "pr_gate_and_review", side_effect=disagree))
                else:
                    stack.enter_context(mock.patch.object(dispatch, "risk_for_head", return_value=("Low", [])))
                    findings = ('```json\n{"required_fixes":[{"summary":"Fix branch","path":"feature.txt","line":1}]}\n```'
                                if exit_path == "review" else "")
                    stack.enter_context(mock.patch.object(
                        dispatch, "review", return_value=("REVISE" if exit_path == "review" else "APPROVE", findings),
                    ))
                    if exit_path == "review":
                        stack.enter_context(mock.patch.object(dispatch, "_actionable_review", return_value=True))
                if exit_path == "approved":
                    # A ready agent ticket stays assigned while its PR waits for CI/merge.
                    github.issue_labels = {"ready-for-agent"}
                    stack.enter_context(mock.patch.object(dispatch, "_head_evidence_matches", return_value=True))
                    stack.enter_context(mock.patch.object(dispatch, "retain_handoff", return_value=True))
                dispatch.refresh_pr_branch(9, 102, False)
                self.assertEqual(github.issue_labels, {"ready-for-agent"})
                expected = {"conflict": "conflicts", "gate": "gate failed twice",
                            "risk": "checks disagreed", "review": "review rework", "approved": "gate and review passed"}
                self.assertIn(expected[exit_path], comments[-1])


class EscalationFailureCLITest(unittest.TestCase):
    def test_persistent_rate_limited_exit_writes_fail_the_real_dispatch_command(self):
        from tests import test_dispatch_lifecycle as cli

        for failure in ("add", "comment"):
            with self.subTest(failure=failure), ExitStack() as stack:
                fixture = cli.LocalCLI()
                stack.callback(fixture.doCleanups)
                fixture.setUp()
                fixture.write_config(check=[sys.executable, "-c", "raise SystemExit(1)"])
                fixture.commit_config()
                fixture.state.write_text(json.dumps({
                    "failure": failure, "remaining": -1,
                    "labels": ["ready-for-agent"], "assigned": False,
                    "comments": [], "calls": [], "history": [],
                }))
                # Keep the existing CLI fixture's reads and PR behavior, but
                # execute the stateful transition writer used above.
                (fixture.bin / "writer").write_text(GH)
                gh = fixture.bin / "gh"
                gh.write_text(
                    "#!/usr/bin/env python3\nimport os, sys\n"
                    "from pathlib import Path\n"
                    "if sys.argv[1:3] in ([\"issue\", \"edit\"], [\"issue\", \"comment\"]):\n"
                    "    exec(compile(Path(__file__).with_name('writer').read_text(), 'writer', 'exec'))\n"
                    "    raise SystemExit(0)\n"
                    + cli.GH
                )
                delays = fixture.base / "delays.jsonl"
                code = (
                    "import json, sys\nfrom pathlib import Path\nfrom factory import dispatch\n"
                    f"delays = Path({str(delays)!r})\n"
                    "def backoff(seconds):\n"
                    "    with delays.open('a') as out:\n"
                    "        out.write(json.dumps(seconds) + '\\n')\n"
                    "dispatch.time.sleep = backoff\n"
                    "raise SystemExit(dispatch.main(['--ticket', '7']))\n"
                )
                result = subprocess.run(
                    [sys.executable, "-c", code], cwd=fixture.repo,
                    env={**fixture.env, "EXIT_STATE": str(fixture.state)},
                    capture_output=True, text=True, timeout=60, check=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                state = json.loads(fixture.state.read_text())
                self.assertEqual(state["labels"], ["ready-for-agent" if failure == "add" else "ready-for-human"])
                self.assertTrue(all(state["history"]))
                self.assertEqual(sum(delay >= 60 for delay in map(json.loads, delays.read_text().splitlines())), 1)
                rows = lifecycle.read_events(fixture.events)
                [ticket_exit] = [row for row in rows if row.get("kind") == "exit" and row.get("stage") == "ticket"]
                self.assertEqual((ticket_exit["outcome"], ticket_exit["reason"]),
                                 ("mechanism_failure", "github_write_failed"))
                self.assertEqual(len([row for row in rows if row.get("event") == "github-write-failed"]), 2)
                self.assertEqual(len([row for row in rows if row.get("event") == "escalate"]), 1)
