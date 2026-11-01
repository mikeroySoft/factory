from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage

REAL_RUN = dispatch.run
ADD_ONLY_FAILURE = "- protected-paths: FAIL (add-only path removed or changed existing lines)"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class ManagerTestAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / ".factory.toml").write_text(
            '[gate]\nprotected_paths = ["tests/acceptance/**"]\nadd_only_paths = ["tests/**"]\n'
        )
        cfg = config.Config(
            self.repo,
            "acme/widgets",
            manager=["manager-stub", "{prompt}"],
        )
        cfg.protected_paths = ["tests/acceptance/**"]
        cfg.add_only_paths = ["tests/**"]
        dispatch.configure(cfg)
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []
        self.issue = {
            "number": 7, "title": "Keep wait behavior while polling twice",
            "body": "Poll twice while preserving ci_pending.", "state": "OPEN",
            "labels": [{"name": config.LABEL_AGENT}], "assignees": [],
            "comments": [], "updatedAt": "2026-10-31T00:00:00Z",
        }
        snapshot = mock.patch.object(dispatch, "gh_json", return_value=self.issue)
        self.issue_read = snapshot.start()
        self.addCleanup(snapshot.stop)
        timeline = mock.patch.object(manage.handoff, "timeline", return_value=[])
        self.timeline_read = timeline.start()
        self.addCleanup(timeline.stop)

    def commit_base(self, path: str, text: str) -> Path:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        git(self.repo, "add", ".factory.toml", path)
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        return target

    def fake_run(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        if args[0] == "manager-stub":
            prompt = Path(args[1]).read_text()
            self.prompts.append(prompt)
            if "query.call_count" in prompt:
                output = (
                    'DECISION: AUTHORIZE\n{"justification":"The behavior remains asserted; only the '
                    'incidental query count changes","guidance":"Keep the outcome assertion"}'
                )
            else:
                output = "DECISION: HUMAN\nThe ci_pending behavioral outcome assertion was deleted."
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[0] == "gh":
            self.commands.append(args)
            stdout = "https://example.test/comment#issuecomment-1\n" if "comment" in args else ""
            return subprocess.CompletedProcess(args, 0, stdout, "")
        return REAL_RUN(args, **kwargs)

    def request(self, report: str) -> bool:
        issue = {
            "number": 7,
            "title": "Keep wait behavior while polling twice",
            "body": "The planned behavior change doubles the query count while preserving ci_pending.",
            "labels": [{"name": config.LABEL_AGENT}],
        }
        with mock.patch.object(dispatch, "run", side_effect=self.fake_run):
            return manage.request_test_edit_authorization(7, issue, report, self.repo, None)

    def test_incidental_count_edit_is_authorized_without_human_escalation(self) -> None:
        path = self.commit_base(
            "tests/test_wait_decisions.py",
            "def test_wait():\n"
            "    assert outcome == 'ci_pending'\n"
            "    assert query.call_count == 1\n",
        )
        path.write_text("def test_wait():\n    assert outcome == 'ci_pending'\n")
        git(self.repo, "add", str(path.relative_to(self.repo)))
        git(self.repo, "commit", "-q", "-m", "accept two polls")
        report = ADD_ONLY_FAILURE + "\n" + git(self.repo, "diff", "origin/main...HEAD")

        self.assertTrue(self.request(report))

        events = lifecycle.read_events(dispatch.EVENTS)
        authorization = next(row for row in events if row.get("event") == "test-edit-authorization")
        self.assertEqual(authorization["decision"], "AUTHORIZE")
        self.assertEqual(authorization["files"], ["tests/test_wait_decisions.py"])
        self.assertIn("incidental query count", authorization["justification"])
        commands = [" ".join(command) for command in self.commands]
        self.assertTrue(any(f"--add-label {config.LABEL_ADD_ONLY_OVERRIDE}" in command for command in commands))
        self.assertFalse(any(f"--add-label {config.LABEL_HUMAN}" in command for command in commands))
        issue_comment = next(command for command in commands if "issue comment" in command)
        self.assertIn("tests/test_wait_decisions.py", issue_comment)
        self.assertIn("incidental query count", issue_comment)
        self.assertIn("pure deletion of a behavioral assertion", self.prompts[0])

    def test_later_escalation_gets_its_own_manager_round(self) -> None:
        path = self.commit_base(
            "tests/test_wait_decisions.py",
            "def test_wait():\n"
            "    assert outcome == 'ci_pending'\n"
            "    assert query.call_count == 1\n",
        )
        path.write_text("def test_wait():\n    assert outcome == 'ci_pending'\n")
        git(self.repo, "add", str(path.relative_to(self.repo)))
        git(self.repo, "commit", "-q", "-m", "accept two polls")

        self.assertTrue(self.request(ADD_ONLY_FAILURE + "\nquery.call_count"))
        with mock.patch.object(dispatch, "run", side_effect=self.fake_run):
            dispatch.escalate(7, "later gate failure", None, code="gate_failed")

        events = lifecycle.read_events(dispatch.EVENTS)
        escalation = next(row for row in events if row.get("event") == "escalate")
        self.assertEqual(escalation["round"], 1)
        self.assertFalse(any(
            row.get("event") == "manage" and row.get("round") == escalation["round"]
            for row in events
        ))

        issue = {"number": 7, "title": "Keep wait behavior", "body": "", "labels": []}
        with mock.patch.object(dispatch, "gh_json", return_value=[issue]), \
                mock.patch.object(dispatch, "initiative_kind", return_value=False), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(dispatch, "run", side_effect=self.fake_run), \
                mock.patch.object(manage, "apply") as apply:
            manage.escalation_pass()

        self.assertEqual(apply.call_args.args[0], 7)
        self.assertEqual(apply.call_args.args[2], "HUMAN")
        self.assertEqual([
            row["round"] for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "manage" and "round" in row
        ], [1])

    def test_behavioral_assertion_deletion_is_denied_and_escalated(self) -> None:
        path = self.commit_base(
            "tests/test_wait_decisions.py",
            "def test_wait():\n    assert outcome == 'ci_pending'\n",
        )
        path.write_text("def test_wait():\n    run_wait()\n")
        git(self.repo, "add", str(path.relative_to(self.repo)))
        git(self.repo, "commit", "-q", "-m", "drop outcome")
        report = ADD_ONLY_FAILURE + "\n" + git(self.repo, "diff", "origin/main...HEAD")

        self.assertFalse(self.request(report))

        events = lifecycle.read_events(dispatch.EVENTS)
        authorization = next(row for row in events if row.get("event") == "test-edit-authorization")
        self.assertEqual(authorization["decision"], "HUMAN")
        self.assertTrue(any(row.get("event") == "escalate" for row in events))
        commands = [" ".join(command) for command in self.commands]
        self.assertTrue(any(f"--add-label {config.LABEL_HUMAN}" in command for command in commands))
        self.assertFalse(any(config.LABEL_ADD_ONLY_OVERRIDE in command for command in commands))

    def test_protected_path_gap_never_enters_manager_authorization(self) -> None:
        self.commit_base(
            "tests/acceptance/test_flow.py",
            "def test_flow():\n    assert outcome == 'accepted'\n",
        )
        issue = {"title": "Acceptance", "body": "", "labels": []}
        handoff = "Test edit authorization: tests/acceptance/test_flow.py"
        with mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                mock.patch.object(dispatch, "escalate") as escalate:
            handled = dispatch._handle_test_edit_request(
                7, issue, set(), self.repo, "- test: FAIL", handoff, None,
            )

        self.assertTrue(handled)
        authorize.assert_not_called()
        escalate.assert_called_once()
        self.assertEqual(escalate.call_args.kwargs["code"], "protected_paths")

    def test_add_only_gate_gap_routes_with_ticket_number(self) -> None:
        self.commit_base(
            "tests/test_wait_decisions.py",
            "def test_wait():\n    assert outcome == 'ci_pending'\n",
        )
        issue = {"title": "Wait outcome", "body": "", "labels": []}
        with mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                mock.patch.object(dispatch, "escalate") as escalate:
            handled = dispatch._handle_protected_failure(
                7, issue, set(), self.repo, ADD_ONLY_FAILURE, None,
            )

        self.assertTrue(handled)
        authorize.assert_called_once_with(
            7, issue, ADD_ONLY_FAILURE, self.repo, None,
        )
        escalate.assert_not_called()

    def test_authorization_is_attached_to_pr_and_reviewer_context(self) -> None:
        dispatch.record(
            "test-edit-authorization",
            ticket=7,
            decision="AUTHORIZE",
            files=["tests/test_wait_decisions.py"],
            justification="Only the incidental query count changed",
            guidance="Retain the ci_pending outcome assertion",
        )

        note = dispatch.test_edit_authorization(7)
        prompt = dispatch.review_prompt(7, "- test: PASS")

        self.assertIn("tests/test_wait_decisions.py", note)
        self.assertIn("Only the incidental query count changed", note)
        self.assertIn("check the test diff against this authorization", prompt)
        self.assertIn(note, prompt)

    def test_later_authorization_keeps_the_existing_file_scope(self) -> None:
        dispatch.record(
            "test-edit-authorization",
            ticket=7,
            decision="AUTHORIZE",
            files=["tests/test_wait_decisions.py"],
            justification="First change",
            guidance="Keep wait behavior",
        )
        result = subprocess.CompletedProcess([], 0, "https://example.test/comment\n", "")
        with mock.patch.object(dispatch, "run", return_value=result), \
                mock.patch.object(dispatch, "comment_receipt"):
            manage.apply_test_edit_authorization(
                7,
                {"justification": "Second change", "guidance": "Keep retry behavior"},
                ["tests/test_retry.py"],
                unassign=True,
            )

        events = lifecycle.read_events(dispatch.EVENTS)
        authorization = [
            row for row in events
            if row.get("event") == "test-edit-authorization"
        ][-1]
        self.assertEqual(
            authorization["files"],
            ["tests/test_retry.py", "tests/test_wait_decisions.py"],
        )

    def test_escalation_authorize_uses_the_shared_application_path(self) -> None:
        data = {"justification": "Incidental formatting", "guidance": "Keep behavior checks"}
        packet = self.repo / "packet.md"
        packet.write_text(
            "Test edit authorization: tests/test_wait_decisions.py\n"
            "Replace the incidental count with the new outcome assertion.\n"
        )
        wt = dispatch.FACTORY / "wt-7"
        wt.mkdir(parents=True)
        with mock.patch.object(
            manage, "test_edit_files", return_value=["tests/test_wait_decisions.py"],
        ) as files, mock.patch.object(
            manage, "apply_test_edit_authorization",
        ) as apply_authorization:
            manage.apply(7, {"title": "test"}, "AUTHORIZE", "", data, packet)

        files.assert_called_once_with(wt, packet.read_text())
        apply_authorization.assert_called_once_with(
            7, data, ["tests/test_wait_decisions.py"], unassign=False,
        )

    def test_human_takeover_during_test_edit_inference_blocks_authorization(self) -> None:
        self.commit_base("tests/test_wait.py", "assert outcome == 'ci_pending'\n")
        request = "Test edit authorization: tests/test_wait.py\nquery.call_count"
        changes = {
            "comment": {"comments": [{"body": "Stop, I am taking over."}]},
            "label": {"labels": [{"name": config.LABEL_HUMAN}]},
            "assignment": {"assignees": [{"login": "owner"}]},
            "body": {"body": "Changed acceptance criteria"},
        }
        for change, fields in changes.items():
            for decision in ("AUTHORIZE", "HUMAN"):
                with self.subTest(change=change, decision=decision):
                    self.commands.clear()
                    self.issue_read.side_effect = [self.issue, {**self.issue, **fields}]
                    output = (
                        'DECISION: AUTHORIZE\n{"justification":"Only incidental counts change",'
                        '"guidance":"Keep behavior"}'
                        if decision == "AUTHORIZE" else "DECISION: HUMAN\nNeeds owner input."
                    )

                    def run(args, **kwargs):
                        if args[0] == "manager-stub":
                            return subprocess.CompletedProcess(args, 0, output, "")
                        return self.fake_run(args, **kwargs)

                    with mock.patch.object(dispatch, "run", side_effect=run):
                        result = manage.request_test_edit_authorization(
                            7, self.issue, "- test: PASS", self.repo, None, request=request,
                        )

                    self.assertFalse(result)
                    self.assertFalse(self.commands, "Human changes must not be overwritten or commented on")
                    self.assertFalse([
                        row for row in lifecycle.read_events(dispatch.EVENTS)
                        if row.get("event") in {"manage", "test-edit-authorization", "escalate"}
                    ])

    def test_same_second_timeline_takeover_blocks_test_edit_authorization(self) -> None:
        self.commit_base("tests/test_wait.py", "assert outcome == 'ci_pending'\n")
        request = "Test edit authorization: tests/test_wait.py\nquery.call_count"
        for event in ("commented", "labeled", "unlabeled", "assigned", "unassigned", "edited"):
            with self.subTest(event=event):
                self.commands.clear()
                self.timeline_read.side_effect = [
                    [], [{"id": 8, "event": event, "created_at": self.issue["updatedAt"]}],
                ]
                with mock.patch.object(dispatch, "run", side_effect=self.fake_run):
                    result = manage.request_test_edit_authorization(
                        7, self.issue, "- test: PASS", self.repo, None, request=request,
                    )
                self.assertFalse(result)
                self.assertFalse(self.commands)
                self.assertEqual(manage.authorized_test_edit_files(7), [])

    def test_test_edit_snapshot_failure_leaves_issue_untouched(self) -> None:
        self.commit_base("tests/test_wait.py", "assert outcome == 'ci_pending'\n")
        request = "Test edit authorization: tests/test_wait.py\nquery.call_count"
        for stage in ("before", "after"):
            with self.subTest(stage=stage):
                self.commands.clear()
                failure = subprocess.CalledProcessError(1, ["gh", "issue", "view"])
                self.issue_read.side_effect = (
                    [failure] if stage == "before" else [self.issue, failure]
                )
                with mock.patch.object(dispatch, "run", side_effect=self.fake_run):
                    result = manage.request_test_edit_authorization(
                        7, self.issue, "- test: PASS", self.repo, None, request=request,
                    )
                self.assertFalse(result)
                self.assertFalse(self.commands)
                self.assertEqual(manage.authorized_test_edit_files(7), [])

    def test_test_edit_files_rejects_paths_outside_configured_add_only_scope(self) -> None:
        paths = [
            "src/main.rs", "src/test_wait.py", "src/tests/wait.py",
            "tests/test_wait.py", "tests/acceptance/test_flow.py",
        ]
        for path in paths:
            self.commit_base(path, "assert outcome == 'ci_pending'\n")
        for path in paths:
            (self.repo / path).write_text("assert outcome == 'ready'\n")
        git(self.repo, "add", *paths)
        git(self.repo, "commit", "-qm", "update behavior")
        request = "\n".join(f"Test edit authorization: {path}" for path in paths)
        self.assertEqual(manage.test_edit_files(self.repo, request), ["tests/test_wait.py"])
        dispatch.cfg.add_only_paths = []
        self.assertEqual(manage.test_edit_files(self.repo, request), [])

    def test_out_of_scope_test_edit_request_escalates_without_manager_inference(self) -> None:
        for path in ("src/main.rs", "src/test_wait.py", "tests/acceptance/test_flow.py"):
            self.commit_base(path, "assert outcome == 'ci_pending'\n")
            with self.subTest(path=path), \
                    mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                    mock.patch.object(dispatch, "escalate") as escalate:
                handled = dispatch._handle_test_edit_request(
                    7, self.issue, set(), self.repo, "- test: PASS",
                    f"Test edit authorization: {path}", None,
                )
                self.assertTrue(handled)
                authorize.assert_not_called()
                escalate.assert_called_once()

    def test_human_overrides_clear_stale_handoffs_without_manager(self) -> None:
        self.commit_base("tests/test_wait.py", "assert outcome == 'ci_pending'\n")
        self.commit_base("tests/acceptance/test_flow.py", "assert outcome == 'accepted'\n")
        dispatch.cfg.manager = []
        cases = [
            (config.LABEL_ADD_ONLY_OVERRIDE, "tests/test_wait.py"),
            (config.LABEL_PROTECTED_OVERRIDE, "tests/test_wait.py"),
            (config.LABEL_PROTECTED_OVERRIDE, "tests/acceptance/test_flow.py"),
        ]
        for label, path in cases:
            with self.subTest(label=label, path=path), \
                    mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                    mock.patch.object(dispatch, "escalate") as escalate:
                handled = dispatch._handle_test_edit_request(
                    7, self.issue, {label}, self.repo, "- test: PASS",
                    f"Test edit authorization: {path}", None,
                )
                self.assertFalse(handled)
                authorize.assert_not_called()
                escalate.assert_not_called()

    def test_manager_add_only_authority_stays_file_scoped(self) -> None:
        for path in ("tests/test_wait.py", "tests/test_retry.py"):
            self.commit_base(path, "assert outcome == 'ci_pending'\n")
        dispatch.record(
            "test-edit-authorization", ticket=7, decision="AUTHORIZE",
            files=["tests/test_wait.py"], justification="Incidental count", guidance="Keep behavior",
        )
        with mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                mock.patch.object(dispatch, "escalate") as escalate:
            self.assertFalse(dispatch._handle_test_edit_request(
                7, self.issue, {config.LABEL_ADD_ONLY_OVERRIDE}, self.repo, "- test: PASS",
                "Test edit authorization: tests/test_wait.py", None,
            ))
            self.assertTrue(dispatch._handle_test_edit_request(
                7, self.issue, {config.LABEL_ADD_ONLY_OVERRIDE}, self.repo, "- test: PASS",
                "Test edit authorization: tests/test_retry.py", None,
            ))
            authorize.assert_called_once()
            self.assertIn("tests/test_retry.py", authorize.call_args.kwargs["request"])
            escalate.assert_not_called()

    def test_human_add_only_override_never_clears_protected_request(self) -> None:
        self.commit_base("tests/acceptance/test_flow.py", "assert outcome == 'accepted'\n")
        with mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                mock.patch.object(dispatch, "escalate") as escalate:
            self.assertTrue(dispatch._handle_test_edit_request(
                7, self.issue, {config.LABEL_ADD_ONLY_OVERRIDE}, self.repo, "- test: PASS",
                "Test edit authorization: tests/acceptance/test_flow.py", None,
            ))
            authorize.assert_not_called()
            escalate.assert_called_once()

    def test_test_edit_request_without_manager_names_human_add_only_authority(self) -> None:
        self.commit_base("tests/test_wait.py", "assert outcome == 'ci_pending'\n")
        dispatch.cfg.manager = []
        with mock.patch.object(dispatch, "escalate") as escalate:
            self.assertTrue(dispatch._handle_test_edit_request(
                7, self.issue, set(), self.repo, "- test: PASS",
                "Test edit authorization: tests/test_wait.py", None,
            ))
        guidance = escalate.call_args.kwargs["extra"]
        self.assertIn(f"`{config.LABEL_ADD_ONLY_OVERRIDE}`", guidance)
        self.assertIn("human", guidance.lower())

if __name__ == "__main__":
    unittest.main()
