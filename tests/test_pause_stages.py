"""In-flight pause boundaries for manager, watch, and triage passes."""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, handoff, lifecycle, manage, pause, triage, watch


class PauseStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(mock.patch.dict(dispatch.__dict__))
        self.enterContext(mock.patch.dict(triage.__dict__))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".factory").mkdir()
        self.cfg = config.Config(
            self.root,
            "local/pause-stages",
            manager=["manager-stub"],
            lock=self.root / "triage.lock",
        )
        dispatch.configure(self.cfg)
        triage.configure(self.cfg)

    def test_manager_finishes_current_viability_publication_then_stops(self) -> None:
        candidates = [
            {"number": number, "labels": [{"name": config.LABEL_VIABILITY}]}
            for number in (1, 2)
        ]
        github_writes: list[list[str]] = []
        manager_calls = 0

        def gh_json(args: list[str]):
            if args[:2] == ["pr", "list"]:
                return []
            if args[:2] == ["issue", "list"]:
                return candidates
            raise AssertionError(args)

        def snapshot(_kind: str, number: int, label: str):
            return ({
                "number": number,
                "title": f"Issue {number}",
                "body": "Assess this request.",
                "state": "OPEN",
                "labels": [{"name": label}],
            }, [], number * 10)

        def run(cmd: list[str], **_kwargs):
            nonlocal manager_calls
            if cmd[0] == "manager-stub":
                manager_calls += 1
                pause.activate(self.cfg, "operator hold", who="tester")
                return subprocess.CompletedProcess(cmd, 0, "Evidence supports it [S1]\nVERDICT: BUILD", "")
            if cmd[:2] == ["gh", "issue"]:
                github_writes.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(cmd)

        with (
            mock.patch.object(dispatch, "gh_json", side_effect=gh_json),
            mock.patch.object(dispatch, "run", side_effect=run),
            mock.patch.object(manage, "viability_snapshot", side_effect=snapshot),
            mock.patch("factory.evidence.viability_sources", return_value=[{
                "id": "S1", "label": "issue", "url": "https://example.invalid/1",
            }]),
            mock.patch.object(manage, "record_manager_decision"),
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(handoff, "handoff_pass") as terminal_handoff,
        ):
            manage.manage_pass()

        self.assertEqual(manager_calls, 1)
        self.assertEqual([cmd[3] for cmd in github_writes], ["1", "1"])
        frontier.assert_not_called()
        escalation.assert_not_called()
        terminal_handoff.assert_not_called()

    def test_handoff_finishes_current_publication_then_stops(self) -> None:
        for number in (1, 2):
            lifecycle.append(dispatch.EVENTS, {
                "event": "escalate",
                "ticket": number,
                "reason": "needs a human",
                "packet": f"packet-{number}",
                "round": 1,
            })
        comments: list[list[str]] = []
        observed = {
            "route": {
                "status": "selected",
                "owner": "maintainer",
                "candidates": [],
                "source": "test",
                "provenance": [],
            },
            "pr_url": None,
        }

        def run(cmd: list[str], **_kwargs):
            comments.append(cmd)
            pause.activate(self.cfg, "operator hold", who="tester")
            return subprocess.CompletedProcess(cmd, 0, "comment", "")

        with (
            mock.patch.object(dispatch, "gh_json", return_value=[
                {"number": 1}, {"number": 2},
            ]) as github,
            mock.patch.object(dispatch, "initiative_kind", return_value=False),
            mock.patch.object(dispatch, "run", side_effect=run),
            mock.patch.object(dispatch, "comment_receipt", return_value=True),
            mock.patch.object(handoff, "terminal", return_value="recovery stopped"),
            mock.patch.object(handoff, "observe", return_value=observed) as observe,
        ):
            handoff.handoff_pass()
            handoff.handoff_pass()

        observe.assert_called_once()
        github.assert_called_once()
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0][:4], ["gh", "issue", "comment", "1"])

    def test_watch_finishes_current_filing_then_stops(self) -> None:
        signals = [
            {"fingerprint": f"fp-{number}", "kind": "lifecycle", "stage": "worker",
             "key": f"failure-{number}", "latest": None}
            for number in (1, 2)
        ]
        creates: list[list[str]] = []

        def run(cmd: list[str], **_kwargs):
            creates.append(cmd)
            pause.activate(self.cfg, "operator hold", who="tester")
            return subprocess.CompletedProcess(cmd, 0, "https://github.com/local/watch/issues/101\n", "")

        with (
            mock.patch.object(watch, "signals", return_value=signals),
            mock.patch.object(watch, "open_filings", return_value={}),
            mock.patch.object(watch, "title", side_effect=lambda signal: signal["key"]),
            mock.patch.object(watch, "body", return_value="evidence"),
            mock.patch.object(dispatch, "run", side_effect=run),
        ):
            filed = watch.run_pass(self.cfg)

        self.assertEqual([signal["fingerprint"] for signal in filed], ["fp-1"])
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][:3], ["gh", "issue", "create"])

    def test_paused_watch_does_not_record_stage_success(self) -> None:
        pause.activate(self.cfg, "operator hold", who="tester")
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(lifecycle, "record_stage_success") as success,
        ):
            self.assertEqual(watch.main([]), 0)
        success.assert_not_called()

    def test_triage_finishes_current_decision_then_stops(self) -> None:
        issue = {
            "number": 0,
            "title": "Actionable issue",
            "body": "Problem: " + "x" * 90 + "\n\nAcceptance criteria: the regression test passes.",
            "comments": [],
            "labels": [{"name": config.LABEL_TRIAGE}],
            "state": "OPEN",
            "updatedAt": "2026-11-16T00:00:00+00:00",
        }
        github_writes: list[tuple[str, ...]] = []

        def fetch(number: int, execution=None):
            return {**issue, "number": number}

        def model(_messages, execution=None):
            pause.activate(self.cfg, "operator hold", who="tester")
            return (
                '{"decision":"ready-for-agent","rationale":"Ready.",'
                '"question":"","brief":"Run the regression test."}'
            )

        def gh(*args: str, execution=None) -> str:
            github_writes.append(args)
            return ""

        args = argparse.Namespace(replay=None, issue=None, dry_run=False)
        with (
            mock.patch.object(triage, "identity_mismatch", return_value=None),
            mock.patch.object(triage, "list_needs_triage", return_value=[1, 2]),
            mock.patch.object(triage, "fetch_issue", side_effect=fetch) as fetched,
            mock.patch.object(triage, "call_llm", side_effect=model),
            mock.patch.object(triage, "gh", side_effect=gh),
            mock.patch("factory.plan.is_initiative", return_value=False),
        ):
            self.assertEqual(triage.execute(args, None), 0)

        self.assertEqual([call.args[0] for call in fetched.call_args_list], [1])
        self.assertEqual([(call[0], call[1], call[2]) for call in github_writes], [
            ("issue", "comment", "1"),
            ("issue", "edit", "1"),
        ])


if __name__ == "__main__":
    unittest.main()
