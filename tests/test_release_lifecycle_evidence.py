"""Promotion consumes real lifecycle-produced, host-attributed dispatcher passes."""
from __future__ import annotations

import unittest
from datetime import timedelta
from unittest import mock

from factory import lifecycle, release
from tests import test_release_boundaries as fixtures


class ReleaseLifecycleEvidenceTest(unittest.TestCase):
    setUp = fixtures.ReleaseBoundaryTest.setUp
    new_repo = fixtures.ReleaseBoundaryTest.new_repo
    git = fixtures.ReleaseBoundaryTest.git
    write_release_docs = fixtures.ReleaseBoundaryTest.write_release_docs

    def emit_pass(self, at, *, host=None, commit=None, outcome="completed") -> None:
        with mock.patch.object(lifecycle, "_now", return_value=at.isoformat()), \
                mock.patch.object(lifecycle, "_host_id", return_value=host or self.host_id):
            with lifecycle.scope(
                self.cfg.factory / "events.jsonl", "dispatcher", dispatcher=True, lazy=True,
                engine_version=self.latest["tag"][1:], engine_commit=commit or self.latest["sha"],
            ) as execution:
                execution.outcome = outcome
                # Real lazy dispatcher scopes persist when the pass does work.
                execution.commit()

    def test_real_lifecycle_passes_qualify_on_tank_and_retain_exact_attribution(self) -> None:
        self.emit_pass(fixtures.NOW - timedelta(hours=24))
        self.emit_pass(fixtures.NOW)
        hold, proof = release._soak(self.cfg, self.latest)
        self.assertIsNone(hold)
        self.assertEqual((proof["hours"], proof["passes"], proof["host_id"]), (24, 2, self.host_id))

    def test_foreign_failed_or_other_revision_real_passes_cannot_qualify(self) -> None:
        for options in ({"host": "other-host"}, {"commit": "b" * 40},
                        {"outcome": "mechanism_failure"}):
            with self.subTest(options=options):
                (self.cfg.factory / "events.jsonl").unlink(missing_ok=True)
                self.emit_pass(fixtures.NOW - timedelta(hours=24), **options)
                self.emit_pass(fixtures.NOW)
                hold, proof = release._soak(self.cfg, self.latest)
                self.assertIn("24h", hold)
                self.assertEqual((proof["hours"], proof["passes"]), (0, 1))

    def test_host_identity_is_captured_not_overridden_by_event_fields(self) -> None:
        with mock.patch.object(lifecycle, "_host_id", return_value=self.host_id):
            with lifecycle.scope(
                self.cfg.factory / "events.jsonl", "dispatcher", dispatcher=True,
                host_id="spoofed-host", engine_version=self.latest["tag"][1:],
                engine_commit=self.latest["sha"],
            ) as execution:
                with mock.patch.object(lifecycle, "_host_id", return_value="other-host"):
                    row = execution.emit("result", host_id="spoofed-host")
        self.assertEqual(row["host_id"], self.host_id)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertTrue(all(row["host_id"] == self.host_id for row in rows))


if __name__ == "__main__":
    unittest.main()
