"""Merged promotions finish against their own source and bounded soak evidence."""
from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle, release
from tests import test_release_revision as fixtures


class ReleaseCompletionTest(unittest.TestCase):
    setUp = fixtures.ReleaseRevisionTest.setUp
    new_repo = fixtures.ReleaseRevisionTest.new_repo
    git = fixtures.ReleaseRevisionTest.git
    write_release_docs = fixtures.ReleaseRevisionTest.write_release_docs
    tag_and_advance = fixtures.ReleaseRevisionTest.tag_and_advance
    land = fixtures.ReleaseRevisionTest.land
    soak = fixtures.ReleaseRevisionTest.soak
    experimental = fixtures.ReleaseRevisionTest.experimental

    def merged_promotion(self) -> tuple[str, datetime]:
        self.experimental()
        self.assertIn("opened release PR", release.run(self.cfg, promote=True))
        sha = self.land("0.0.4")
        merged_at = datetime.now(UTC) - timedelta(seconds=30)
        self.pulls.return_value = [{"merged_at": merged_at.isoformat(), "merge_commit_sha": sha}]
        return sha, merged_at

    def advance_main(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": datetime.now(UTC).isoformat()}):
            (self.repo / "work.txt").write_text("unsoaked implementation after promotion merge\n")
            self.git(self.repo, "add", "work.txt")
            self.git(self.repo, "commit", "-qm", "later implementation")
            self.git(self.repo, "push", "-q", "origin", "main")

    def test_merged_promotion_finishes_after_main_advances_while_ci_runs(self) -> None:
        sha, merged_at = self.merged_promotion()
        with mock.patch.object(release, "ci_passes", return_value=False):
            self.assertIn("waiting for release commit CI", release.run(self.cfg, promote=True))
        self.advance_main()
        self.assertIn("pending promotion", release.run(self.cfg))
        self.assertIn("released v0.0.4", release.run(self.cfg, promote=True))
        self.assertEqual(self.git(self.origin, "rev-parse", "v0.0.4^{}"), sha)
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), sha)
        self.assertNotEqual(self.git(self.origin, "rev-parse", "main"), sha)
        annotation = self.git(self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.0.4")
        soak = json.loads(annotation.split("```soak\n", 1)[1].split("```", 1)[0])
        self.assertEqual(soak["checked_at"], merged_at.isoformat())
        self.assertEqual(soak["hours"], 24)

    def test_post_merge_escalations_and_escapes_do_not_extend_the_soak_window(self) -> None:
        sha, merged_at = self.merged_promotion()
        after = (merged_at + timedelta(seconds=1)).isoformat()
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "escalate", "ticket": 99, "at": after,
        })
        self.escapes.return_value = [{
            "status": "candidates", "closed_at": after, "bug": 99,
            "candidates": [{"ticket": 1}],
        }]
        self.assertIn("released v0.0.4", release.run(self.cfg, promote=True))
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), sha)

    def test_escalation_at_merge_still_blocks_completion(self) -> None:
        _, merged_at = self.merged_promotion()
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "escalate", "ticket": 99, "at": merged_at.isoformat(),
        })
        self.assertIn("escalations during soak", release.run(self.cfg, promote=True))
        self.assertNotIn("v0.0.4", self.git(self.origin, "tag", "--list"))

    def test_post_merge_open_watch_bug_still_blocks_completion(self) -> None:
        self.merged_promotion()
        self.filings.return_value = [[{"number": 99, "body": "Fingerprint: regression\n"}]]
        self.assertIn("watch-filed engine bug", release.run(self.cfg, promote=True))
        self.assertNotIn("v0.0.4", self.git(self.origin, "tag", "--list"))

    def test_late_passes_cannot_backfill_insufficient_soak_before_merge(self) -> None:
        self.experimental()
        self.assertIn("opened release PR", release.run(self.cfg, promote=True))
        sha = self.land("0.0.4")
        self.pulls.return_value = [{
            "merged_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "merge_commit_sha": sha,
        }]
        self.assertIn("24h", release.run(self.cfg, promote=True))
        self.assertNotIn("v0.0.4", self.git(self.origin, "tag", "--list"))

    def test_unsoaked_source_in_merged_commit_is_not_excused_by_provenance(self) -> None:
        self.experimental()
        self.advance_main()
        # Simulate a merged promotion that included unsoaked source.
        self.git(self.repo, "fetch", "origin")
        release._update_release_files(self.cfg, self.repo, "0.0.3", "0.0.4")
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md", "README.md", "docs/index.html")
        self.git(self.repo, "commit", "-qm", "Release 0.0.4 with unsoaked source")
        self.git(self.repo, "push", "-q", "origin", "main")
        sha = self.git(self.repo, "rev-parse", "HEAD")
        self.pulls.return_value = [{"merged_at": datetime.now(UTC).isoformat(), "merge_commit_sha": sha}]
        with self.assertRaisesRegex(release.ReleaseError, "release commit contains unsoaked source"):
            release.run(self.cfg, promote=True)
        self.assertNotIn("v0.0.4", self.git(self.origin, "tag", "--list"))


if __name__ == "__main__":
    unittest.main()
