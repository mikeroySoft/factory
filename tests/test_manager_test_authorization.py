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
        issue = {"number": 7, "title": "Acceptance", "body": "", "labels": []}
        with mock.patch.object(manage, "request_test_edit_authorization") as authorize, \
                mock.patch.object(dispatch, "escalate") as escalate:
            dispatch._handle_protected_failure(
                issue,
                set(),
                self.repo,
                "- protected-paths: FAIL\n\n+++ b/tests/acceptance/test_flow.py",
                None,
            )

        authorize.assert_not_called()
        escalate.assert_called_once()
        self.assertEqual(escalate.call_args.kwargs["code"], "protected_paths")

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

    def test_escalation_authorize_uses_the_shared_application_path(self) -> None:
        data = {"justification": "Incidental formatting", "guidance": "Keep behavior checks"}
        with mock.patch.object(manage, "apply_test_edit_authorization") as apply_authorization:
            manage.apply(7, {"title": "test"}, "AUTHORIZE", "", data, Path("packet"))

        apply_authorization.assert_called_once_with(7, data, [], unassign=False)


if __name__ == "__main__":
    unittest.main()
