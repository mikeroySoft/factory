from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, manage
from factory.domain import EscalationCode, ManagerDecision, ReviewVerdict


class ManagerEnumTest(unittest.TestCase):
    def test_escalation_parser_returns_every_accepted_decision_as_enum(self) -> None:
        workers = {"worker": {}}
        bodies = {
            ManagerDecision.RETRY: "Try again with this guidance.",
            ManagerDecision.REWRITE: "Replacement issue body.",
            ManagerDecision.HUMAN: "A human decision is required.",
            ManagerDecision.CLOSE: "The request should not proceed.",
            ManagerDecision.RESOLVE: "Already present on main at abc123.",
            ManagerDecision.SPLIT: '[{"title": "Child", "body": "Work"}]',
            ManagerDecision.ROUTE: '{"add": ["worker"], "remove": [], "guidance": "Route it."}',
            ManagerDecision.AUTHORIZE: '{"justification": "Required", "guidance": "Edit the file."}',
            ManagerDecision.FIX: '{"worker": "worker", "guidance": "Correct the defect."}',
        }
        for expected, body in bodies.items():
            with self.subTest(decision=expected):
                decision, _, _ = manage.parse(f"DECISION: {expected}\n{body}", workers)
                self.assertIs(decision, expected)

    def test_approval_context_accepts_approve_and_rejects_escalation_only_decisions(self) -> None:
        decision, _, _ = manage.parse(
            "DECISION: APPROVE\nGate and independent review passed.", {}, approval=True
        )
        self.assertIs(decision, ManagerDecision.APPROVE)
        self.assertIs(
            manage.parse("DECISION: APPROVE\nNot valid here.", {})[0],
            ManagerDecision.HUMAN,
        )
        for rejected in (ManagerDecision.RESOLVE, ManagerDecision.AUTHORIZE):
            with self.subTest(decision=rejected):
                body = (
                    "Already present on main."
                    if rejected == ManagerDecision.RESOLVE
                    else '{"justification": "Required", "guidance": "Edit it."}'
                )
                self.assertIs(
                    manage.parse(f"DECISION: {rejected}\n{body}", {}, approval=True)[0],
                    ManagerDecision.HUMAN,
                )

    def test_invalid_known_decision_body_keeps_safe_handoff(self) -> None:
        decision, body, data = manage.parse("DECISION: FIX\n{}", {"worker": {}})
        self.assertIs(decision, ManagerDecision.HUMAN)
        self.assertIn("Unparseable manager output", body)
        self.assertIsNone(data)

    def test_unknown_decision_is_rejected_at_parse_boundary(self) -> None:
        with self.assertRaises(ValueError):
            manage.parse("DECISION: DEPLOY\nDo it now.", {})

    def test_command_boundary_turns_unknown_decision_into_safe_handoff(self) -> None:
        decision, body, data = manage._parse_command("DECISION: DEPLOY\nDo it now.", {})
        self.assertIs(decision, ManagerDecision.HUMAN)
        self.assertIn("Unparseable manager output", body)
        self.assertIsNone(data)

    def test_curate_is_rejected_as_a_manager_decision(self) -> None:
        output = "DECISION: CURATE\n--- a/AGENTS.md\n+++ b/AGENTS.md"
        with self.assertRaises(ValueError):
            manage.parse(output, {})
        self.assertIs(manage._parse_command(output, {})[0], ManagerDecision.HUMAN)

    def test_manager_fix_treats_reviewer_error_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cfg = config.Config(Path(temporary), "acme/widgets")
            cfg.factory.mkdir()
            (cfg.factory / "wt-7").mkdir()
            packet = cfg.factory / "packet.md"
            packet.write_text("evidence")
            dispatch.configure(cfg)
            pr = {
                "state": "OPEN",
                "headRefName": "agent/7",
                "headRefOid": "abc",
                "baseRefName": cfg.main,
                "reviewDecision": "",
            }

            def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
                stdout = "agent/7\n" if argv[1:3] == ["branch", "--show-current"] else "abc\n"
                return subprocess.CompletedProcess(argv, 0, stdout=stdout)

            with (
                mock.patch.object(dispatch, "gh_json", side_effect=[pr, {"baseRefName": cfg.main}]),
                mock.patch.object(dispatch, "run", side_effect=run),
                mock.patch.object(dispatch, "worker_round", return_value=(True, "gate", None, "abc")),
                mock.patch.object(dispatch, "push_agent"),
                mock.patch.object(dispatch, "record_risk"),
                mock.patch.object(
                    dispatch,
                    "pr_gate_and_review",
                    return_value=(ReviewVerdict.ERROR, "review unavailable", False),
                ),
                mock.patch.object(dispatch, "pr_comment"),
                mock.patch.object(dispatch, "escalate") as escalate,
                mock.patch.object(dispatch, "approve_pr") as approve,
            ):
                manage.apply(
                    7,
                    {"title": "Fix", "body": "", "labels": []},
                    ManagerDecision.FIX,
                    '{"worker": "worker", "guidance": "repair"}',
                    {"worker": "worker", "guidance": "repair"},
                    packet,
                )

            escalate.assert_called_once()
            self.assertEqual(
                escalate.call_args.kwargs["code"],
                EscalationCode.REVIEWER_UNAVAILABLE,
            )
            approve.assert_not_called()

    def test_decision_recording_keeps_contextual_types_and_wire_spellings(self) -> None:
        from factory import lifecycle

        with tempfile.TemporaryDirectory() as temporary:
            cfg = config.Config(Path(temporary), "acme/widgets")
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            packet = cfg.factory / "packet.md"
            packet.write_text("evidence")

            manage.record_manager_decision(
                7, "BUILD", "useful", None, "VERDICT: BUILD", {}, packet,
                viability=True,
            )
            manage.record_manager_decision(
                7,
                ManagerDecision.HUMAN,
                "needs a human",
                None,
                "DECISION: HUMAN\nneeds a human",
                {},
                packet,
            )

            viability, manager = lifecycle.read_events(dispatch.EVENTS)
            self.assertEqual(viability["chosen"], "BUILD")
            self.assertIs(manager["chosen"], ManagerDecision.HUMAN)
            wire = dispatch.EVENTS.read_text()
            self.assertIn('"chosen":"BUILD"', wire)
            self.assertIn('"chosen":"HUMAN"', wire)



if __name__ == "__main__":
    unittest.main()
