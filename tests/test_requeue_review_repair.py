"""Releases close retained review PRs before making tickets worker-eligible."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import actions, config, decisions, dispatch, manage


class RequeueReviewRepairTest(unittest.TestCase):
    @staticmethod
    def completed(argv: list[str], code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, out, err)

    def test_every_manager_release_closes_the_pr_before_relabeling(self) -> None:
        cases = {
            "RETRY": ("Try again", None),
            "REWRITE": ("Replacement body", None),
            "AUTHORIZE": ("", {"justification": "Required", "guidance": "Change it"}),
            "ROUTE": ("", {"add": ["chore"], "remove": [], "guidance": "Use chore"}),
        }
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            dispatch.configure(cfg)
            packet = Path(directory) / "packet.txt"
            packet.write_text("packet")
            for decision, (body, data) in cases.items():
                with self.subTest(decision=decision):
                    issue = {"title": "Fix parser", "body": "Original body",
                             "labels": [{"name": config.LABEL_HUMAN}]}
                    prs = [{"number": 70}]

                    def run(argv: list[str], *, issue=issue, prs=prs, **kwargs):
                        if argv[:3] == ["gh", "pr", "close"]:
                            prs.clear()
                        if argv[:3] == ["gh", "issue", "edit"] and "--add-label" in argv:
                            added = argv[argv.index("--add-label") + 1]
                            if added == config.LABEL_AGENT:
                                self.assertEqual(prs, [], "release must not strand an open PR")
                                issue["labels"] = [{"name": config.LABEL_AGENT}]
                        return self.completed(argv, out="https://github.com/acme/widgets/issues/7#issuecomment-1\n")

                    def read(argv: list[str], *, issue=issue, prs=prs):
                        if argv[:2] == ["pr", "list"]:
                            return list(prs)
                        if argv[:2] == ["issue", "view"]:
                            return dict(issue)
                        raise AssertionError(argv)

                    with (
                        mock.patch.object(dispatch, "run", side_effect=run),
                        mock.patch.object(dispatch, "gh_json", side_effect=read),
                        mock.patch.object(dispatch, "comment_receipt"),
                    ):
                        manage.apply(7, issue, decision, body, data, packet)
                    self.assertEqual(prs, [])
                    self.assertEqual(issue["labels"], [{"name": config.LABEL_AGENT}])

    def test_manager_close_failure_does_not_release_the_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            dispatch.configure(cfg)
            issue = {"title": "Fix parser", "body": "Body", "labels": [{"name": config.LABEL_HUMAN}]}
            prs = [{"number": 70}]

            def run(argv: list[str], **kwargs):
                if argv[:3] == ["gh", "pr", "close"]:
                    return self.completed(argv, 1, err="close unavailable")
                if argv[:3] == ["gh", "issue", "edit"]:
                    issue["labels"] = [{"name": config.LABEL_AGENT}]
                return self.completed(argv, out="https://github.com/acme/widgets/issues/7#issuecomment-1\n")

            with (
                mock.patch.object(dispatch, "run", side_effect=run),
                mock.patch.object(dispatch, "gh_json", return_value=prs),
                mock.patch.object(dispatch, "comment_receipt"),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                manage.apply(7, issue, "RETRY", "Try again", None, Path(directory) / "packet")
            self.assertEqual(prs, [{"number": 70}])
            self.assertEqual(issue["labels"], [{"name": config.LABEL_HUMAN}])

    def _human_requeue(self, intent: str, *, fail_close: bool = False) -> tuple[dict, dict, list[dict]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cfg = config.Config(root=root, repo="acme/widgets")
        issue = {
            "number": 7,
            "title": "Fix parser",
            "body": "Body",
            "state": "OPEN",
            "labels": [{"name": config.LABEL_HUMAN}],
            "assignees": [{"login": "alice"}, {"login": "bob"}],
            "updatedAt": "2026-11-25T00:00:01Z",
            "url": "https://github.com/acme/widgets/issues/7",
        }
        open_prs = [{"number": 70}, {"number": 71}]

        def decision_transport(argv: list[str], **kwargs):
            if argv[:2] == ["gh", "api"]:
                return self.completed(argv, out="alice\n")
            if argv[:3] == ["gh", "issue", "view"]:
                return self.completed(argv, out=json.dumps({key: issue[key] for key in argv[-1].split(",")}))
            if argv[:3] == ["gh", "pr", "list"]:
                return self.completed(argv, out=json.dumps(open_prs))
            if argv[:3] == ["gh", "pr", "close"]:
                if fail_close:
                    return self.completed(argv, 1, err="close failed")
                open_prs[:] = [pr for pr in open_prs if str(pr["number"]) != argv[3]]
                return self.completed(argv)
            if argv[:3] == ["gh", "issue", "edit"]:
                labels = {label["name"] for label in issue["labels"]}
                labels.add(config.LABEL_AGENT)
                labels.discard(config.LABEL_HUMAN)
                issue["labels"] = [{"name": label} for label in sorted(labels)]
                issue["assignees"] = [row for row in issue["assignees"] if row["login"] != "alice"]
                issue["updatedAt"] = "2026-11-25T00:00:02Z"
                return self.completed(argv, out="ok")
            raise AssertionError(argv)

        with mock.patch.object(decisions, "_run", side_effect=decision_transport):
            preview = actions.prepare(
                cfg, "requeue_agent", {"number": 7, "intent": intent}, caller="dashboard",
            )
            receipt = decisions.apply(cfg, preview["proposal_id"], preview["confirmation"])
        return receipt, issue, open_prs

    def test_named_human_requeue_closes_open_pr_for_resume_and_fresh(self) -> None:
        for intent in ("resume", "fresh"):
            with self.subTest(intent=intent):
                receipt, issue, prs = self._human_requeue(intent)
                self.assertEqual(receipt["status"], "success")
                self.assertEqual({label["name"] for label in issue["labels"]}, {config.LABEL_AGENT})
                self.assertEqual(issue["assignees"], [{"login": "bob"}])
                self.assertEqual(prs, [])

    def test_named_human_requeue_close_failure_keeps_human_routing_and_assignees(self) -> None:
        receipt, issue, prs = self._human_requeue("resume", fail_close=True)
        self.assertEqual(receipt["status"], "uncertain")
        self.assertEqual(prs, [{"number": 70}, {"number": 71}])
        self.assertEqual({label["name"] for label in issue["labels"]}, {config.LABEL_HUMAN})
        self.assertEqual(issue["assignees"], [{"login": "alice"}, {"login": "bob"}])


if __name__ == "__main__":
    unittest.main()
