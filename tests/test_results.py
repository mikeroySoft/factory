from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from factory import binding, config, results


NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
REPO = "acme/widgets"


class ResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = config.Config(self.root, REPO)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def handoff(self, ticket: int, data: bytes) -> Path:
        path = self.cfg.factory / f"wt-{ticket}/.factory/handoff-{ticket}.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        return path

    def accepted_events(
        self, ticket: int, head: str, metadata: dict, *, source_head: str | None = None,
        verdict: str = "APPROVE", approved: bool = True,
    ) -> list[dict]:
        events = []
        if source_head is not None:
            events.append({
                "at": results._format_time(NOW - timedelta(hours=5)),
                "event": "attempt", "event_id": f"source-{ticket}",
                "execution_id": f"worker-{ticket}", "ticket": ticket, "gate": "FAIL",
                "head": source_head, "actual_head": source_head, "handoff": metadata,
            })
        events.extend([
            {
                "at": results._format_time(NOW - timedelta(hours=4)),
                "event": "attempt", "event_id": f"gate-{ticket}",
                "execution_id": f"gate-execution-{ticket}", "ticket": ticket, "gate": "PASS",
                "head": head, "actual_head": head, "handoff": metadata,
            },
            {
                "at": results._format_time(NOW - timedelta(hours=2)),
                "event": "review", "event_id": f"review-{ticket}",
                "execution_id": f"review-execution-{ticket}", "ticket": ticket,
                "verdict": verdict, "parsed": True,
                "accepted": verdict == "APPROVE", "head": head, "actual_head": head,
            },
        ])
        if approved:
            events.append({
                "at": results._format_time(NOW - timedelta(hours=1)),
                "event": "approved", "event_id": f"approved-{ticket}",
                "execution_id": f"approval-execution-{ticket}",
                "ticket": ticket, "pr": ticket + 100,
                "head": head, "gate_head": head, "review_head": head,
            })
        return events

    def retain(self, ticket: int, head: str, events: list[dict], when: datetime = NOW) -> dict:
        with mock.patch.object(results, "_now", return_value=when):
            return results.retain(self.cfg, ticket, head, events)

    def read(self, ticket: int, head: str, offset: int = 0, when: datetime = NOW) -> dict:
        with mock.patch.object(results, "_now", return_value=when):
            return results.read_result(self.cfg, ticket, head, offset)

    def test_gate_only_and_revise_are_not_acceptance(self) -> None:
        ticket, head = 7, "a" * 40
        self.handoff(ticket, b"not accepted")
        metadata = results.source_metadata(self.cfg, ticket)
        gate_only = [{
            "event": "attempt", "ticket": ticket, "gate": "PASS",
            "head": head, "actual_head": head, "handoff": metadata,
        }]
        refused = self.retain(ticket, head, gate_only)
        self.assertEqual((refused["status"], refused["cleanup_safe"]), ("unavailable", False))

        revise = self.accepted_events(ticket, head, metadata, verdict="REVISE")
        refused = self.retain(ticket, head, revise)
        self.assertEqual((refused["status"], refused["cleanup_safe"]), ("unavailable", False))
        self.assertFalse((self.cfg.factory / "results").exists())

    def test_configured_manager_approval_must_match_the_accepted_head(self) -> None:
        ticket, head = 18, "5" * 40
        self.cfg.manager = ["manager"]
        self.cfg.manager_review = "all"
        self.handoff(ticket, b"manager-approved handoff")
        metadata = results.source_metadata(self.cfg, ticket)
        events = self.accepted_events(ticket, head, metadata)
        refused = self.retain(ticket, head, events)
        self.assertEqual((refused["status"], refused["cleanup_safe"]), ("unavailable", False))
        events.insert(-1, {
            "at": results._format_time(NOW - timedelta(minutes=90)),
            "event": "manage", "ticket": ticket, "head": "6" * 40,
            "decision": "APPROVE",
        })
        self.assertEqual(self.retain(ticket, head, events)["status"], "unavailable")
        events.insert(-1, {
            "at": results._format_time(NOW - timedelta(minutes=80)),
            "event": "manage", "event_id": "manager-18", "execution_id": "manager-execution-18",
            "ticket": ticket, "head": head, "decision": "APPROVE",
        })
        retained = self.retain(ticket, head, events)
        self.assertEqual((retained["status"], retained["cleanup_safe"]), ("complete", True))
        self.assertEqual(retained["manifest"]["acceptance"]["manager"]["head"], head)

    def test_full_raw_bytes_hash_origin_and_utf8_pages_survive_source_removal(self) -> None:
        ticket = 8
        source_head, accepted_head = "b" * 40, "c" * 40
        raw = b"x" * 19_999 + "é".encode() + b"tail"
        source = self.handoff(ticket, raw)
        metadata = results.source_metadata(self.cfg, ticket)
        events = self.accepted_events(ticket, accepted_head, metadata, source_head=source_head)
        events.insert(1, {
            "at": results._format_time(NOW - timedelta(hours=4, minutes=30)),
            "event": "attempt", "ticket": ticket, "gate": "FAIL",
            "head": "d" * 40, "actual_head": "d" * 40, "handoff": metadata,
        })
        sections = {name: f"{name} contract marker" for name in binding.SECTIONS}
        baseline = {
            "schema_version": 1,
            "initiative": 80,
            "sections": sections,
            "sha256": binding._digest(sections),
            "source_url": f"https://github.com/{REPO}/issues/80",
            "observed_at": results._format_time(NOW - timedelta(days=1)),
        }
        events.insert(0, {
            "event": "plan-bound", "ticket": ticket, "schema_version": 1,
            "baseline": baseline,
            "issue": {"title": "Bound ticket", "body": binding.render(baseline), "comments": []},
        })

        build_revision = "8" * 40
        with mock.patch("factory.evidence.reader_build", return_value={
            "revision": build_revision, "verified": True,
            "evidence_schema": 1, "runtime_schema": 1,
        }):
            retained = self.retain(ticket, accepted_head, events)
        self.assertEqual((retained["status"], retained["cleanup_safe"]), ("complete", True))
        manifest = retained["manifest"]
        self.assertEqual(manifest["accepted_head"], accepted_head)
        self.assertEqual(
            (manifest["source"]["head"], manifest["source"]["head_basis"]),
            (source_head, "first_matching_attempt_observation"),
        )
        self.assertEqual(manifest["artifact"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest["contract"]["sha256"], baseline["sha256"])
        self.assertNotIn("contract marker", json.dumps(manifest))
        producer = manifest["producer"]
        self.assertEqual((producer["schema"], producer["schema_version"]),
                         ("factory.accepted-result", 1))
        self.assertEqual((producer["verified"], producer["revision"]), (True, build_revision))
        acceptance = manifest["acceptance"]
        self.assertEqual((acceptance["approved"]["event_id"], acceptance["gate"]["event_id"],
                          acceptance["review"]["event_id"]),
                         ("approved-8", "gate-8", "review-8"))
        self.assertEqual(
            (self.cfg.factory / f"results/{ticket}/{accepted_head}/handoff.md").read_bytes(), raw,
        )
        source.write_bytes(b"different handoff")
        changed = self.retain(ticket, accepted_head, events)
        self.assertEqual((changed["status"], changed["cleanup_safe"]), ("partial", False))
        self.assertEqual(
            (self.cfg.factory / f"results/{ticket}/{accepted_head}/handoff.md").read_bytes(), raw,
        )

        shutil.rmtree(source.parents[1])
        repeated = self.retain(ticket, accepted_head, events)
        self.assertEqual((repeated["status"], repeated["cleanup_safe"]), ("complete", True))
        first = self.read(ticket, accepted_head.upper())
        self.assertEqual((first["status"], first["truncated"], first["next_offset"]),
                         ("complete", True, 19_999))
        boundary = self.read(ticket, accepted_head, 20_000)
        self.assertEqual((boundary["status"], boundary["offset"]), ("unavailable", 20_000))
        second = self.read(ticket, accepted_head, first["next_offset"])
        self.assertEqual((first["text"] + second["text"]).encode(), raw)
        with mock.patch.object(results, "_now", return_value=NOW):
            self.assertEqual(results.latest_result(self.cfg, ticket)["manifest"], manifest)

    def test_oversize_and_credentials_are_withheld_without_derivatives(self) -> None:
        cases = (
            (9, b"x" * (results.ARTIFACT_CAP + 1), "oversized", "partial"),
            (10, b"token ghp_" + b"A" * 20, "privacy_withheld", "privacy_withheld"),
        )
        for ticket, raw, artifact_status, read_status in cases:
            with self.subTest(status=artifact_status):
                head = f"{ticket:x}" * 40
                self.handoff(ticket, raw)
                metadata = results.source_metadata(self.cfg, ticket)
                events = self.accepted_events(ticket, head, metadata)
                retained = self.retain(ticket, head, events)
                self.assertEqual((retained["status"], retained["cleanup_safe"]), ("partial", False))
                self.assertEqual(retained["manifest"]["artifact"]["status"], artifact_status)
                archive = self.cfg.factory / f"results/{ticket}/{head}"
                self.assertFalse((archive / "handoff.md").exists())
                self.assertEqual(self.read(ticket, head)["status"], read_status)

        ticket, head = 19, "7" * 40
        self.handoff(ticket, b"explicit derivative")
        metadata = {**results.source_metadata(self.cfg, ticket), "status": "redacted"}
        events = self.accepted_events(ticket, head, metadata)
        events.insert(0, {
            "event": "plan-bound", "ticket": ticket, "schema_version": 1,
            "baseline": {
                "schema_version": 1, "initiative": 190,
                "sections": {name: "content" for name in binding.SECTIONS},
                "sha256": "0" * 64,
                "source_url": f"https://github.com/{REPO}/issues/190",
                "observed_at": results._format_time(NOW - timedelta(days=1)),
            },
            "issue": {"title": "Malformed binding", "body": "not a baseline", "comments": []},
        })
        retained = self.retain(ticket, head, events)
        self.assertEqual((retained["status"], retained["cleanup_safe"]), ("partial", False))
        self.assertEqual(retained["manifest"]["artifact"]["status"], "redacted")
        self.assertEqual(retained["manifest"]["contract"]["status"], "unsupported")
        self.assertFalse((self.cfg.factory / f"results/{ticket}/{head}/handoff.md").exists())

    def test_missing_source_retains_body_free_metadata_and_allows_cleanup(self) -> None:
        ticket, head = 16, "3" * 40
        metadata = results.source_metadata(self.cfg, ticket)
        self.assertEqual(metadata, {"status": "missing", "bytes": None, "sha256": None})
        retained = self.retain(ticket, head, self.accepted_events(ticket, head, metadata))
        self.assertEqual((retained["status"], retained["cleanup_safe"]), ("partial", True))
        self.assertEqual(retained["manifest"]["artifact"]["status"], "missing")
        self.assertEqual(retained["manifest"]["source"]["head_basis"], "unknown")
        archive = self.cfg.factory / f"results/{ticket}/{head}"
        self.assertTrue((archive / "manifest.json").is_file())
        self.assertFalse((archive / "handoff.md").exists())

    def test_prune_enforces_payload_and_metadata_lifetimes_without_read_writes(self) -> None:
        empty_root = self.root / "empty"
        empty_root.mkdir()
        empty_cfg = config.Config(empty_root, REPO)
        self.assertEqual(results.prune(empty_cfg), {"status": "complete", "reason": None})
        self.assertFalse((empty_root / ".factory").exists())

        ticket, head = 11, "d" * 40
        self.handoff(ticket, b"expiring handoff")
        metadata = results.source_metadata(self.cfg, ticket)
        events = self.accepted_events(ticket, head, metadata)
        self.assertEqual(self.retain(ticket, head, events)["status"], "complete")
        archive = self.cfg.factory / f"results/{ticket}/{head}"

        payload_expired = NOW + timedelta(days=91)
        with mock.patch.object(results, "_now", return_value=payload_expired):
            self.assertEqual(results.prune(self.cfg), {"status": "complete", "reason": None})
        self.assertFalse((archive / "handoff.md").exists())
        self.assertTrue((archive / "manifest.json").exists())
        expired = self.read(ticket, head, when=payload_expired)
        self.assertEqual((expired["status"], expired["manifest"]["ticket"]), ("expired", ticket))

        metadata_expired = NOW + timedelta(days=366)
        hidden = self.read(ticket, head, when=metadata_expired)
        self.assertEqual((hidden["status"], hidden["manifest"]), ("expired", None))
        with mock.patch.object(results, "_now", return_value=metadata_expired):
            self.assertEqual(results.prune(self.cfg), {"status": "complete", "reason": None})
            self.assertIsNone(results.latest_result(self.cfg, ticket))
        self.assertFalse(archive.exists())

    def test_quota_never_evicts_unexpired_results(self) -> None:
        first_ticket, first_head = 12, "e" * 40
        self.handoff(first_ticket, b"first retained handoff")
        first_metadata = results.source_metadata(self.cfg, first_ticket)
        first_events = self.accepted_events(first_ticket, first_head, first_metadata)
        self.assertEqual(self.retain(first_ticket, first_head, first_events)["status"], "complete")

        second_ticket, second_head = 13, "f" * 40
        self.handoff(second_ticket, b"second retained handoff")
        second_metadata = results.source_metadata(self.cfg, second_ticket)
        second_events = self.accepted_events(second_ticket, second_head, second_metadata)
        with mock.patch.object(results, "REPOSITORY_CAP", 1):
            refused = self.retain(second_ticket, second_head, second_events)
        self.assertEqual((refused["status"], refused["cleanup_safe"]), ("unavailable", False))
        self.assertEqual(self.read(first_ticket, first_head)["text"], "first retained handoff")
        self.assertFalse((self.cfg.factory / f"results/{second_ticket}/{second_head}").exists())

    def test_orphan_staging_and_incomplete_bundle_do_not_block_other_tickets(self) -> None:
        orphan_ticket, orphan_head = 20, "8" * 40
        orphan = self.cfg.factory / f"results/{orphan_ticket}/{orphan_head}"
        orphan.mkdir(parents=True)
        staging = [
            orphan / ".handoff.md.123.0123456789abcdef.tmp",
            orphan / ".manifest.json.123.fedcba9876543210.tmp",
        ]
        for path in staging:
            path.write_bytes(b"interrupted staging")

        partial_ticket, partial_head = 21, "9" * 40
        partial = self.cfg.factory / f"results/{partial_ticket}/{partial_head}"
        partial.mkdir(parents=True)
        (partial / "handoff.md").write_bytes(b"uncommitted payload")

        malformed = self.cfg.factory / f"results/23/{'b' * 40}"
        malformed.mkdir(parents=True)
        (malformed / "manifest.json").write_bytes(b"{")
        foreign = self.cfg.factory / f"results/24/{'c' * 40}"
        foreign.mkdir(parents=True)
        (foreign / "foreign.tmp").write_bytes(b"not factory staging")

        ticket, head = 22, "a" * 40
        self.handoff(ticket, b"unrelated accepted handoff")
        metadata = results.source_metadata(self.cfg, ticket)
        retained = self.retain(ticket, head, self.accepted_events(ticket, head, metadata))

        self.assertEqual((retained["status"], retained["cleanup_safe"]), ("complete", True))
        self.assertFalse(any(path.exists() for path in staging))
        self.assertTrue((partial / "handoff.md").is_file())
        self.assertEqual(self.read(partial_ticket, partial_head)["status"], "unavailable")
        self.assertEqual(self.read(23, "b" * 40)["status"], "unavailable")
        self.assertTrue((malformed / "manifest.json").is_file())
        self.assertTrue((foreign / "foreign.tmp").is_file())
        with mock.patch.object(results, "_now", return_value=NOW):
            pruned = results.prune(self.cfg)
        self.assertEqual(pruned["status"], "unavailable")
        self.assertIn("could not be safely expired", pruned["reason"])
        self.assertEqual(self.read(ticket, head)["text"], "unrelated accepted handoff")


    def test_symlink_sources_and_tampered_payloads_fail_closed(self) -> None:
        ticket, head = 14, "1" * 40
        outside = self.root / "outside.md"
        outside.write_text("outside")
        source = self.cfg.factory / f"wt-{ticket}/.factory/handoff-{ticket}.md"
        source.parent.mkdir(parents=True)
        os.symlink(outside, source)
        metadata = results.source_metadata(self.cfg, ticket)
        self.assertEqual(metadata["status"], "unsafe")
        retained = self.retain(ticket, head, self.accepted_events(ticket, head, metadata))
        self.assertEqual((retained["status"], retained["cleanup_safe"]), ("partial", False))

        ticket, head = 15, "2" * 40
        self.handoff(ticket, b"trusted bytes")
        metadata = results.source_metadata(self.cfg, ticket)
        events = self.accepted_events(ticket, head, metadata)
        self.assertEqual(self.retain(ticket, head, events)["status"], "complete")
        payload = self.cfg.factory / f"results/{ticket}/{head}/handoff.md"
        payload.write_bytes(b"changed bytes")
        read = self.read(ticket, head)
        self.assertEqual(read["status"], "tampered")
        self.assertIn("integrity", read["reason"])

        incomplete_ticket, incomplete_head = 17, "4" * 40
        (self.cfg.factory / f"results/{incomplete_ticket}/{incomplete_head}").mkdir(parents=True)
        latest = results.latest_result(self.cfg, incomplete_ticket)
        self.assertEqual(latest["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
