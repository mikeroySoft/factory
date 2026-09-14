from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory import binding, briefing, config, lifecycle
from factory.evidence import EvidenceError

REPO = "example/project"


def plan_body(plan: str = "ship it", *, status: str = "ready", owner: str = "alice") -> str:
    return (
        f"**Status**\n{status}\n\n"
        "**Outcome**\nobservable result\n\n"
        f"**Owner**\n{owner}\n\n"
        "**Boundaries**\nno authority expansion\n\n"
        f"**Plan**\n{plan}\n\n"
        "**Open decisions**\nnone\n\n"
        "**Success evidence**\nreal scenario\n\n"
        "**Implementation links**\n#9\n"
    )


def issue(body: object, *, labels=("initiative",), number: int = 52) -> dict:
    return {
        "number": number,
        "body": body,
        "labels": [{"name": label} for label in labels],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
    }


class BindingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = config.Config(Path(self.temp.name), REPO)

    def observe(self, body: object, *, truncated: bool = False) -> dict:
        with patch("factory.binding.github_read", return_value=(issue(body), truncated)):
            return binding.observe(self.cfg, 52)

    def test_normalizes_only_edges_and_line_endings_and_ignores_fenced_headings(self):
        body = (
            "**Outcome**\r\n  result  \r\n```md\r\n**Boundaries**\r\nInitiative: #999\r\n```\r\nend  \r\n"
            "**Boundaries**\r\n  fixed edge  \r\n"
            "**Plan**\r\nfirst  step\r\n\r\n**Example**\r\nkept plan suffix\r\n"
            "**Success evidence**\r\n proof \r\n"
        )
        observed = self.observe(body)
        self.assertEqual(observed["sections"], {
            "Outcome": "result  \n```md\n**Boundaries**\nInitiative: #999\n```\nend",
            "Boundaries": "fixed edge",
            "Plan": "first  step\n\n**Example**\nkept plan suffix",
            "Success evidence": "proof",
        })
        self.assertEqual(binding.linked("```\nInitiative: #999\n```\nInitiative: #52"), 52)

    def test_unrelated_declared_sections_do_not_change_revision_but_suffix_beyond_4000_does(self):
        first = self.observe(plan_body("x" * 4001 + "A", status="ready", owner="alice"))
        unrelated = self.observe(plan_body("x" * 4001 + "A", status="underway", owner="bob"))
        changed = self.observe(plan_body("x" * 4001 + "B", status="underway", owner="bob"))
        self.assertEqual(first["sha256"], unrelated["sha256"])
        self.assertNotEqual(first["sha256"], changed["sha256"])
        self.assertTrue(first["sections"]["Plan"].endswith("A"))

    def test_baseline_round_trip_and_invalid_or_ambiguous_declarations_fail(self):
        observed = self.observe(plan_body())
        rendered = binding.render(observed)
        self.assertEqual(binding.baseline(rendered, REPO), observed)
        self.assertIsNone(binding.admit(self.cfg, "ordinary unlinked ticket"))
        for body in (
            "Initiative: 52",
            "Initiative: #52\nInitiative: #52",
        ):
            with self.subTest(body=body), self.assertRaises(binding.BindingError):
                binding.linked(body)
        invalid = dict(observed, sha256="0" * 64)
        body = "Initiative: #52\n\n## Plan baseline\n\n```json\n" + json.dumps(invalid) + "\n```"
        with self.assertRaisesRegex(binding.BindingError, "sha256"):
            binding.baseline(body, REPO)
        with self.assertRaisesRegex(binding.BindingError, "ambiguous Plan baseline"):
            binding.baseline(rendered + "\n\n**Plan baseline**\n```json\n{}\n```", REPO)

    def test_observation_fails_closed_for_unavailable_incomplete_duplicate_and_wrong_kind(self):
        with patch(
            "factory.binding.github_read",
            side_effect=EvidenceError("github_not_found", "missing"),
        ), self.assertRaisesRegex(binding.BindingError, "source is unavailable: github_not_found"):
            binding.observe(self.cfg, 52)
        with patch(
            "factory.binding.github_read",
            side_effect=EvidenceError("response_too_large", "overflow"),
        ), self.assertRaises(binding.BindingError) as caught:
            binding.observe(self.cfg, 52)
        self.assertEqual(caught.exception.code, "incomplete_source")
        with patch("factory.binding.github_read", return_value=(issue(plan_body()), True)), \
                self.assertRaisesRegex(binding.BindingError, "source is incomplete"):
            binding.observe(self.cfg, 52)
        with patch("factory.binding.github_read", return_value=(issue(None), False)), \
                self.assertRaisesRegex(binding.BindingError, "complete issue body is missing"):
            binding.observe(self.cfg, 52)
        duplicate = plan_body() + "\n**Plan**\nanother plan\n"
        with self.assertRaisesRegex(binding.BindingError, "duplicate Plan section"):
            self.observe(duplicate)
        with patch("factory.binding.github_read", return_value=(issue(plan_body(), labels=()), False)), \
                self.assertRaisesRegex(binding.BindingError, "not an initiative"):
            binding.observe(self.cfg, 52)

    def test_admission_pins_ticket_and_drift_retains_it_when_live_is_unavailable(self):
        observed = self.observe(plan_body())
        ticket_body = binding.render(observed)
        with patch("factory.binding.github_read", return_value=(issue(plan_body()), False)):
            self.assertEqual(binding.admit(self.cfg, ticket_body), observed)
        event = {
            "at": "2026-01-02T03:04:05Z",
            "event": "plan-bound",
            "ticket": 9,
            "schema_version": 1,
            "baseline": observed,
            "issue": {"title": "Slice", "body": ticket_body, "comments": []},
        }
        lifecycle.append(self.cfg.factory / "events.jsonl", event)
        self.assertEqual(binding.accepted(self.cfg, 9), event)
        with patch(
            "factory.binding.github_read",
            side_effect=EvidenceError("github_forbidden", "private"),
        ):
            report = binding.drift(self.cfg, observed)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["baseline"], observed)
        self.assertIsNone(report["observed"])
        self.assertEqual(report["attribution"], "unknown")

        lifecycle.append(self.cfg.factory / "events.jsonl", {**event, "schema_version": 2})
        with self.assertRaisesRegex(binding.BindingError, "unsupported"):
            binding.accepted(self.cfg, 9)

    def test_large_snapshot_does_not_hide_attempt_and_handoff_evidence(self):
        observed = self.observe(plan_body("bounded plan " * 2500))
        body = "Human-approved scope\n\n" + binding.render(observed)
        event = {
            "event": "plan-bound", "ticket": 9, "schema_version": 1, "baseline": observed,
            "issue": {"title": "Slice", "body": body, "comments": []},
        }
        ledger = self.cfg.factory / "events.jsonl"
        for row in (event, {"event": "claimed", "ticket": 9},
                    {"event": "attempt", "ticket": 9, "gate": "FAIL"},
                    {"event": "escalate", "ticket": 9, "reason": "needs human decision"}):
            lifecycle.append(ledger, row)
        handoff = self.cfg.factory / "wt-9/.factory/handoff-9.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("Human question: choose the migration window.")
        ticket = {"number": 9, "title": "Slice", "body": body,
                  "url": f"https://github.com/{REPO}/issues/9"}
        sources = briefing.sources_for(self.cfg, ticket, [])
        history = next(source for source in sources if source["label"].startswith("Factory event history"))
        rows = [json.loads(line) for line in history["text"].splitlines()]
        self.assertEqual([row["event"] for row in rows], ["plan-bound", "claimed", "attempt", "escalate"])
        self.assertEqual(rows[-1]["reason"], "needs human decision")
        self.assertTrue(any("choose the migration window" in source["text"] for source in sources))
        self.assertEqual(binding.accepted(self.cfg, 9)["baseline"]["sections"], observed["sections"])


if __name__ == "__main__":
    unittest.main()
