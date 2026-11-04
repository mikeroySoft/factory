from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import lifecycle, runtime_events
from factory.domain import EscalationCode, LifecycleOutcome, ManagerDecision, ReviewVerdict

# Sanitized bounded extraction from the root .factory/events.jsonl on 2026-11-04.
# Repository paths, ticket/head identity, model name, and reviewer prose were replaced.

FIXTURE = Path(__file__).with_name("fixtures") / "journal-enums.jsonl"


class JournalEnumTest(unittest.TestCase):
    def test_real_journal_shapes_decode_once_and_keep_wire_bytes(self) -> None:
        raw = FIXTURE.read_bytes()
        rows = lifecycle.read_events(FIXTURE)

        self.assertIs(rows[0]["decision"], ManagerDecision.FIX)
        self.assertIs(rows[1]["code"], EscalationCode.GATE_FAILED)
        self.assertIs(rows[2]["verdict"], ReviewVerdict.REQUEST_CHANGES)
        self.assertIs(rows[3]["verdict"], ReviewVerdict.APPROVE)
        self.assertIs(rows[4]["outcome"], LifecycleOutcome.APPROVED)
        self.assertIsNone(rows[0]["notes"])
        self.assertEqual(rows[3]["counts"], {"important": 0, "nit": 2, "pre_existing": 0})
        self.assertEqual(lifecycle._dump(rows), raw)

        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "events.jsonl"
            for row in rows:
                lifecycle.append(copied, row)
            self.assertEqual(copied.read_bytes(), raw)

    def test_unknown_event_and_lifecycle_kinds_are_named_read_errors(self) -> None:
        cases = (
            ({"event": "future-event"}, "future-event"),
            ({"event": 7}, "7"),
            ({}, "None"),
            ({
                **json.loads(FIXTURE.read_text().splitlines()[3]),
                "kind": "future_kind",
            }, "future_kind"),
        )
        for row, value in cases:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                journal = Path(directory) / "events.jsonl"
                journal.write_text(json.dumps(row) + "\n")
                with self.assertRaisesRegex(lifecycle.UnknownEventKindError, value):
                    lifecycle.read_events(journal)

    def test_structured_decision_domains_decode_without_rewriting_descriptive_values(self) -> None:
        manager = lifecycle.parse_event({
            "event": "decision", "stage": "manager", "chosen": "FIX",
            "options": [{"option": "FIX"}, {"option": "BUILD"}],
        })
        review = lifecycle.parse_event({
            "event": "decision", "stage": "review", "chosen": "ERROR",
            "options": [{"option": "APPROVE"}, {"option": "reviewer unavailable"}],
        })
        escalation = lifecycle.parse_event({
            "event": "decision", "stage": "escalate", "chosen": "gate_failed",
            "options": [{"option": "gate_failed"}],
        })
        legacy_null = lifecycle.parse_event({"event": "review-result", "verdict": None})
        reviewer_error = lifecycle.parse_event({"event": "review", "verdict": "ERROR"})

        self.assertIs(manager["chosen"], ManagerDecision.FIX)
        self.assertIs(manager["options"][0]["option"], ManagerDecision.FIX)
        self.assertEqual(manager["options"][1]["option"], "BUILD")
        self.assertIs(review["chosen"], ReviewVerdict.ERROR)
        self.assertIs(review["options"][0]["option"], ReviewVerdict.APPROVE)
        self.assertEqual(review["options"][1]["option"], "reviewer unavailable")
        self.assertIs(escalation["chosen"], EscalationCode.GATE_FAILED)
        self.assertIs(escalation["options"][0]["option"], EscalationCode.GATE_FAILED)
        self.assertIsNone(legacy_null["verdict"])
        self.assertIs(reviewer_error["verdict"], ReviewVerdict.ERROR)

    def test_unknown_enum_values_are_rejected_at_append_and_read_boundaries(self) -> None:
        cases = (
            {"event": "manage", "decision": "MAYBE"},
            {"event": "escalate", "code": "future_code"},
            {"event": "review", "verdict": "REJECT"},
            {
                **json.loads(FIXTURE.read_text().splitlines()[4]),
                "outcome": "future_outcome",
            },
        )
        for row in cases:
            with self.subTest(row=row), tempfile.TemporaryDirectory() as directory:
                journal = Path(directory) / "events.jsonl"
                with self.assertRaises(ValueError):
                    lifecycle.append(journal, row)
                journal.write_text(json.dumps(row) + "\n")
                with self.assertRaises(ValueError):
                    lifecycle.read_events(journal)

    def test_bounded_reader_uses_shared_unknown_kind_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            row = {
                **json.loads(FIXTURE.read_text().splitlines()[3]),
                "kind": "future_kind",
            }
            journal.write_text(json.dumps(row, separators=(",", ":")) + "\n")
            result = runtime_events.project(journal)
            self.assertIn("unsupported_kind", result["history"]["gaps"])
            self.assertEqual(result["events"], [])

    def test_real_risk_and_manager_receipt_domains_decode_without_changing_wire_bytes(self) -> None:
        source = [
            {"event": "risk-check", "primary": "APPROVE", "second": "REVISE", "pr_gate": "PASS"},
            {"event": "risk-check", "mutation": "FAIL", "pr_gate": "FAIL"},
            {"event": "comment", "kind": "manager", "decision": "HUMAN", "comment": 123},
            {"event": "viability", "kind": "issue", "verdict": "BUILD"},
        ]
        raw = b"".join(
            json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in source
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            journal.write_bytes(raw)
            rows = lifecycle.read_events(journal)

        self.assertIs(rows[0]["primary"], ReviewVerdict.APPROVE)
        self.assertIs(rows[0]["second"], ReviewVerdict.REVISE)
        self.assertNotIn("primary", rows[1])
        self.assertIs(rows[2]["decision"], ManagerDecision.HUMAN)
        self.assertEqual(rows[3]["verdict"], "BUILD")
        self.assertEqual(lifecycle._dump(rows), raw)

    def test_unknown_risk_and_manager_receipt_domains_are_rejected_at_boundaries(self) -> None:
        cases = (
            {"event": "risk-check", "primary": "REJECT"},
            {"event": "risk-check", "second": "future_verdict"},
            {"event": "comment", "kind": "manager", "decision": "DEPLOY"},
        )
        for row in cases:
            with self.subTest(row=row), tempfile.TemporaryDirectory() as directory:
                journal = Path(directory) / "events.jsonl"
                with self.assertRaises(ValueError):
                    lifecycle.append(journal, row)
                journal.write_text(json.dumps(row) + "\n")
                with self.assertRaises(ValueError):
                    lifecycle.read_events(journal)


if __name__ == "__main__":
    unittest.main()
