"""Repeated or contradictory escalation rounds carry adjacent failure evidence."""
from __future__ import annotations

import gzip
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage


class ManagerRoundsTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cfg = config.Config(
            self.root, "acme/widgets",
            manager_rounds=4,
            manager=[sys.executable, "-c", (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).read_text(); print('DECISION: HUMAN\\nFixture diagnosis')"
            ), "{prompt}"],
        )
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        dispatch.configure(self.cfg)
        self.issue = {"number": 7, "title": "Fix gate", "body": "Original body"}

    def escalation(self, reason: str, **fields) -> tuple[Path, int]:
        packet, number = dispatch.escalation_packet(7, reason, None, self.cfg.factory / "wt-7")
        dispatch.record("escalate", ticket=7, reason=reason, round=number, packet=str(packet), **fields)
        return packet, number

    def prompt(self) -> str:
        with mock.patch.object(dispatch, "gh_json", return_value=[self.issue]), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(manage, "human_activity", return_value=False), \
                mock.patch.object(manage, "apply"):
            manage.escalation_pass()
        return (self.cfg.factory / "manager-prompt-7.md").read_text()

    def assert_rule(self, prompt: str) -> None:
        self.assertIn("current round's failure repeats or contradicts the previous round's failure", prompt)
        self.assertIn("DECISION: HUMAN", prompt)
        self.assertIn("cite both failures", prompt)

    def test_repeated_round_two_failure_shows_both_and_instructs_human(self) -> None:
        reason = "gate failed: test_checkout rejects the selected revision"
        self.escalation(reason)
        packet, number = self.escalation(reason)
        self.assertEqual(number, 2)
        prompt = self.prompt()
        for text in (packet.read_text(), prompt):
            self.assertIn(f"## Reason\n\n{reason}\n\n## Previous round failure\n\n{reason}\n\n", text)
        self.assert_rule(prompt)

    def test_production_review_failures_retain_details_across_rounds(self) -> None:
        reason = "REVISE verdict after 3 review round(s)"
        findings = [
            "F1: reject stale revisions\nF2: preserve the accepted baseline",
            "F1: reject stale revisions\nF2: preserve the accepted baseline",
            "F1: reject stale revisions",
            "F1: accept stale revisions",
        ]
        wt = self.cfg.factory / "wt-7"
        (wt / ".factory").mkdir(parents=True)
        review = self.cfg.factory / "review-7.md"
        packet = self.cfg.factory / "escalations" / "7.md"
        for number, finding in enumerate(findings, 1):
            with self.subTest(round=number):
                gate = f"gate evidence for round {number}"
                handoff = f"worker diagnosis for round {number}"
                extra = f"PR-tier evidence for round {number}"
                (wt / ".factory" / "gate-report-7.md").write_text(gate)
                (wt / ".factory" / "handoff-7.md").write_text(handoff)
                review.write_text(finding)
                with mock.patch.object(dispatch, "run"), mock.patch.object(dispatch, "unclaim"):
                    dispatch.escalate(7, reason, None, extra=extra, code="review_revise")
                prompt = self.prompt()
                for text in (packet.read_text(), prompt):
                    if number == 1:
                        self.assertNotIn("Previous round failure", text)
                        continue
                    previous = text.split("## Previous round failure\n\n", 1)[1].split("## Attempts", 1)[0]
                    self.assertIn(reason, previous)
                    self.assertIn(findings[number - 2], previous)
                    self.assertIn(f"gate evidence for round {number - 1}", previous)
                    self.assertIn(f"worker diagnosis for round {number - 1}", previous)
                    self.assertIn(f"PR-tier evidence for round {number - 1}", previous)
                    if number > 2:
                        self.assertNotIn(f"gate evidence for round {number - 2}", previous)
                    current = text.split("## Attempts", 1)[1]
                    for detail in (finding, gate, handoff, extra):
                        self.assertIn(detail, current)
                if number >= 2:
                    self.assert_rule(prompt)
                if number == 1:
                    with gzip.open(dispatch.EVENTS.with_name("events.jsonl.1.gz"), "wb") as stream:
                        stream.write(dispatch.EVENTS.read_bytes())
                    dispatch.EVENTS.write_text("")

    def test_oversized_failure_evidence_remains_visible_and_survives_next_round(self) -> None:
        from factory import dashboard

        wt = self.cfg.factory / "wt-7"
        (wt / ".factory").mkdir(parents=True)
        paths = {
            "Last gate report": wt / ".factory" / "gate-report-7.md",
            "Latest review findings": self.cfg.factory / "review-7.md",
            "Handoff": wt / ".factory" / "handoff-7.md",
        }
        reason = 'review failed: preserve "界" and \\revision'
        log = self.cfg.factory / "logs" / "7.log"
        head = "a" * 40
        evidence_ref = "refs/factory-evidence/" + '界"\\' * 150
        packet = self.cfg.factory / "escalations" / "7.md"
        for padding in ("x" * 40000, "界𐐷" * 4000, '"\\\n\t\0' * 4000):
            with self.subTest(padding=repr(padding[:5])):
                dispatch.EVENTS.unlink(missing_ok=True)
                markers = {title: f"previous {title}: reject stale revision" for title in paths}
                for title, path in paths.items():
                    path.write_text(padding + markers[title])
                extra = padding + "previous additional evidence: reviewers disagree"
                with lifecycle.scope(dispatch.EVENTS, "ticket", ticket=7) as execution, \
                        mock.patch.object(dispatch, "run"), mock.patch.object(dispatch, "unclaim"):
                    dispatch.escalate(
                        7, reason, log, extra=extra, code="review_revise",
                        refreshed_head=head, evidence_ref=evidence_ref,
                    )
                with mock.patch.object(dashboard, "FACTORY", self.cfg.factory, create=True):
                    rows, history = dashboard.bounded_journal_rows()
                escalations = [row for row in rows if row.get("event") == "escalate"]
                self.assertEqual(len(escalations), 1, history)
                row = escalations[0]
                self.assertNotIn("row_limit", history["gaps"])
                self.assertEqual(
                    {key: row[key] for key in (
                        "ticket", "code", "reason", "round", "log", "packet",
                        "refreshed_head", "evidence_ref", "execution_id", "dispatcher_run_id",
                    )},
                    {
                        "ticket": 7, "code": "review_revise", "reason": reason, "round": 1,
                        "log": str(log), "packet": str(packet), "refreshed_head": head,
                        "evidence_ref": evidence_ref, "execution_id": execution.execution_id,
                        "dispatcher_run_id": execution.dispatcher_run_id,
                    },
                )
                for line in dispatch.EVENTS.read_bytes().splitlines(keepends=True):
                    self.assertLessEqual(len(line), dashboard.DASHBOARD_EVENT_ROW_CAP)
                for title, marker in markers.items():
                    self.assertIn(marker, row["failure_details"][title])
                self.assertIn(
                    "previous additional evidence: reviewers disagree",
                    row["failure_details"]["Additional evidence"],
                )
                first_packet = packet.read_text()
                self.assertNotIn("Previous round failure", first_packet)
                self.assertIn(extra, first_packet)
                for path in paths.values():
                    path.write_text("current evidence: preserve accepted baseline")
                with mock.patch.object(dispatch, "run"), mock.patch.object(dispatch, "unclaim"):
                    dispatch.escalate(7, reason, log, code="review_revise")
                prompt = self.prompt()
                self.assert_rule(prompt)
                for text in (packet.read_text(), prompt):
                    previous = text.split("## Previous round failure\n\n", 1)[1].split("## Attempts", 1)[0]
                    self.assertIn(reason, previous)
                    for marker in markers.values():
                        self.assertIn(marker, previous)
                    self.assertIn("previous additional evidence: reviewers disagree", previous)
                    self.assertIn("current evidence: preserve accepted baseline", text)

    def test_oversized_metadata_does_not_erase_previous_failure_evidence(self) -> None:
        reason = "manager requires a human: " + "界" * 6000
        details = {"Latest review findings": "reject stale revision; preserve accepted baseline"}
        dispatch.record("escalate", ticket=7, reason=reason, round=1, failure_details=details)
        packet, number = self.escalation("review still rejects stale revision")
        self.assertEqual(number, 2)
        for text in (packet.read_text(), self.prompt()):
            previous = text.split("## Previous round failure\n\n", 1)[1].split("## Attempts", 1)[0]
            self.assertIn(reason, previous)
            self.assertIn(details["Latest review findings"], previous)

    def test_distinct_previous_failure_survives_rotation_and_manager_transport_failure(self) -> None:
        previous = "review: remove the revision check"
        current = "review: restore the revision check"
        self.escalation(previous)
        events = dispatch.EVENTS.read_bytes()
        with gzip.open(dispatch.EVENTS.with_name("events.jsonl.1.gz"), "wb") as stream:
            stream.write(events)
        dispatch.EVENTS.write_text("")
        dispatch.record("escalate", ticket=7, reason="manager_failed", round=1)
        dispatch.record("escalate", ticket=8, reason="another ticket", round=1)
        packet, number = self.escalation(current)
        self.assertEqual(number, 2)
        prompt = self.prompt()
        for text in (packet.read_text(), prompt):
            self.assertIn(f"## Reason\n\n{current}\n\n## Previous round failure\n\n{previous}\n\n", text)
            self.assertNotIn("another ticket", text)
            self.assertNotIn("manager_failed", text)
        self.assert_rule(prompt)

    def test_first_round_preserves_packet_and_prompt(self) -> None:
        packet, number = self.escalation("gate failed")
        self.assertEqual(number, 1)
        expected = (
            "# Escalation #7\n\n## Reason\n\ngate failed\n\n## Attempts\n\n"
            "| Attempt | Gate | Worker exit | Timeout | Seconds | Log |\n"
            "|---:|---|---:|---|---:|---|\n| — | — | — | — | — | none recorded |"
            "\n\n## Last gate report\n\n(none recorded)"
            "\n\n## Latest review findings\n\n(none recorded)"
            "\n\n## Handoff\n\n(none recorded)\n\n## Log paths\n\n- none recorded"
            f"\n\n## Worktree path\n\n`{self.cfg.factory / 'wt-7'}`\n"
        )
        self.assertEqual(packet.read_text(), expected)
        workers = manage.selectable_workers(self.cfg)
        self.assertEqual(self.prompt(), "\n\n".join([
            manage.MENU, "Worker labels:\n" + "\n".join(
                f"- {key}: {self.cfg.worker_when.get(key) or '(no when rule)'}" for key in workers),
            "Issue #7: Fix gate\n\nOriginal body", expected,
        ]))

    def test_owner_generation_does_not_compare_old_failures(self) -> None:
        self.escalation("old failure")
        dispatch.record("generation", ticket=7, generation=1, intent="resume")
        packet, number = self.escalation("new failure")
        self.assertEqual(number, 1)
        for text in (packet.read_text(), self.prompt()):
            self.assertNotIn("Previous round failure", text)
            self.assertNotIn("old failure", text)
            self.assertNotIn("cite both failures", text)

    def test_disabled_policy_keeps_evidence_without_human_rule(self) -> None:
        self.cfg.manager_human_on_repeat = False
        self.escalation("old failure")
        packet, _ = self.escalation("new failure")
        prompt = self.prompt()
        for text in (packet.read_text(), prompt):
            self.assertIn("## Previous round failure\n\nold failure", text)
        self.assertNotIn("cite both failures", prompt)

    def load(self, manager: str = "", host: dict | None = None) -> config.Config:
        (self.root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n' + manager)
        with mock.patch.object(config, "repo_root", return_value=self.root), \
                mock.patch.object(config, "host_config", return_value=host or {}):
            return config.load(self.root)

    def test_unset_rounds_default_to_four_and_repository_already_sets_four(self) -> None:
        loaded = self.load()
        self.assertEqual(loaded.manager_rounds, 4)
        self.assertTrue(loaded.manager_human_on_repeat)
        raw = tomllib.loads((Path(__file__).resolve().parents[1] / config.CONFIG_NAME).read_text())
        self.assertEqual(raw["manager"]["rounds"], 4)

    def test_policy_respects_host_and_repository_layers(self) -> None:
        host = {"defaults": {"manager": {"human_on_repeat": False, "rounds": 2}}}
        loaded = self.load(host=host)
        self.assertFalse(loaded.manager_human_on_repeat)
        self.assertEqual(loaded.manager_rounds, 2)
        host["repo"] = {"acme/widgets": {"manager": {"human_on_repeat": True, "rounds": 3}}}
        loaded = self.load(host=host)
        self.assertTrue(loaded.manager_human_on_repeat)
        self.assertEqual(loaded.manager_rounds, 3)
        loaded = self.load("[manager]\nhuman_on_repeat = false\nrounds = 5\n", host)
        self.assertFalse(loaded.manager_human_on_repeat)
        self.assertEqual(loaded.manager_rounds, 5)
        self.assertIn("human_on_repeat", config.KNOWN_KEYS["manager"])

    def test_policy_requires_boolean(self) -> None:
        for value in ('"false"', "1", "[]"):
            with self.subTest(value=value), self.assertRaisesRegex(config.ConfigError, "human_on_repeat"):
                self.load(f"[manager]\nhuman_on_repeat = {value}\n")
