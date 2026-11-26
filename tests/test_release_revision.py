"""#930 reviewer regressions: qualification belongs to the experimental revision."""
from __future__ import annotations

import os
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import lifecycle, release
from tests import test_release_policy as fixtures


class ReleaseRevisionTest(unittest.TestCase):
    setUp = fixtures.ReleasePolicyTest.setUp
    new_repo = fixtures.ReleasePolicyTest.new_repo
    git = fixtures.ReleasePolicyTest.git
    write_release_docs = fixtures.ReleasePolicyTest.write_release_docs
    tag_and_advance = fixtures.ReleasePolicyTest.tag_and_advance
    land = fixtures.ReleasePolicyTest.land
    soak = fixtures.ReleasePolicyTest.soak

    def experimental(self, notes: str = "- qualified implementation\n") -> dict:
        """Five real implementation commits qualify before the odd tag; none follow it."""
        self.cfg.release_min_tickets = 5
        self.tag_and_advance(self.bar)
        at = datetime.now(UTC) - timedelta(days=2)
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": at.isoformat()}):
            for ticket in range(1, 6):
                (self.repo / "work.txt").write_text(f"implementation {ticket}\n")
                self.git(self.repo, "add", "work.txt")
                self.git(self.repo, "commit", "-qm", f"agent/{ticket}: implementation")
                for event in (
                    {"event": "attempt", "attempt": 1, "gate": "PASS"},
                    {"event": "merged"},
                ):
                    lifecycle.append(self.cfg.factory / "events.jsonl", {
                        **event, "ticket": ticket, "at": at.isoformat(),
                    })
            (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n" + notes)
            self.git(self.repo, "add", "CHANGELOG.md")
            self.git(self.repo, "commit", "-qm", "document implementation")
            self.git(self.repo, "push", "-q", "origin", "main")
        tagged_at = datetime.now(UTC) - timedelta(hours=36)
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": tagged_at.isoformat()}):
            self.assertIn("opened release PR", release.run(self.cfg))
            self.land("0.0.3")
            self.assertIn("released v0.0.3", release.run(self.cfg))
        self.pulls.return_value = []
        self.soak("v0.0.3")
        latest = release._latest(self.cfg)
        self.assertEqual(release.measure(self.cfg, latest["date"])["tickets"], 0)
        return latest

    def test_promotion_reuses_qualified_experimental_window_without_new_tickets(self) -> None:
        latest = self.experimental()
        qualified_bar = release._bar(latest["message"])
        self.assertEqual(qualified_bar["tickets"], 5)
        self.assertIn("opened release PR #12 for v0.0.4", release.run(self.cfg, promote=True))
        sha = self.land("0.0.4")
        self.assertIn("released v0.0.4", release.run(self.cfg, promote=True))
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), sha)
        annotation = self.git(self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.0.4")
        self.assertEqual(release._bar(annotation), qualified_bar)
        self.assertIn('"experimental_tag":"v0.0.3"', annotation)
        self.assertIn('"hours":24.0', annotation)

    def test_automatic_pass_holds_provenanced_merged_promotion_without_false_failure(self) -> None:
        self.experimental()
        self.assertIn("opened release PR", release.run(self.cfg, promote=True))
        self.land("0.0.4")
        before = self.git(self.origin, "show-ref")
        for open_pr in (False, True):
            self.assertIn("pending promotion", release.run(self.cfg, open_pr=open_pr))
        self.assertEqual(self.git(self.origin, "show-ref"), before)
        self.assertEqual(self.pulls.call_args, mock.call(self.cfg, "release/v0.0.4", state="closed"))
        self.assertFalse(any(row.get("outcome") == "failed" for row in
                             lifecycle.read_events(self.cfg.factory / "events.jsonl")))
        self.assertIn("released v0.0.4", release.run(self.cfg, promote=True))

    def test_automatic_pass_rejects_unprovenanced_even_bump(self) -> None:
        self.experimental()
        (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.0.4"\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-qm", "manual even bump")
        self.git(self.repo, "push", "-q", "origin", "main")
        with self.assertRaisesRegex(release.ReleaseError, "merged release PR"):
            release.run(self.cfg)
        self.assertNotIn("v0.0.4", self.git(self.origin, "tag", "--list"))

    def test_promotion_preserves_literal_experimental_release_notes(self) -> None:
        notes = r"- matches \d and C:\new\1" + "\n"
        self.experimental(notes)
        self.assertIn("opened release PR", release.run(self.cfg, promote=True))
        self.land("0.0.4")
        self.assertIn("released v0.0.4", release.run(self.cfg, promote=True))
        self.assertEqual(release._notes(self.cfg, "v0.0.4"), notes.strip())
        self.assertIn(notes.strip().removeprefix("- "),
                      self.git(self.origin, "show", "v0.0.4:docs/index.html"))

    def test_repeated_promote_retries_publication_with_auto_disabled(self) -> None:
        self.experimental()
        self.cfg.release_auto = False
        self.assertIn("opened release PR", release.run(self.cfg, promote=True))
        self.land("0.0.4")
        self.published.side_effect = lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "HTTP 502")
        self.assertIn("HTTP 502", release.run(self.cfg, promote=True))
        before = self.git(self.origin, "show-ref")
        self.published.side_effect = lambda *a, **kw: subprocess.CompletedProcess(
            [], 1 if a[1] == "view" else 0, "", "")
        self.published.reset_mock()
        self.assertIn("created GitHub Release v0.0.4", release.run(self.cfg, promote=True))
        self.assertEqual(self.git(self.origin, "show-ref"), before)
        self.assertIn("--latest", self.published.call_args.args)
        self.assertNotIn("--prerelease", self.published.call_args.args)
        self.published.reset_mock()
        self.assertIn("already public", release.run(self.cfg, promote=True))
        self.published.assert_not_called()

    def test_promotion_fails_when_escape_attribution_is_partially_unavailable(self) -> None:
        self.experimental()
        for status in ("no_candidates", "no_blamed_lines", "candidates"):
            with self.subTest(status=status):
                self.escapes.return_value = [{
                    "bug": 99, "status": status, "skipped": ["unavailable-fix"],
                    "candidates": [], "closed_at": datetime.now(UTC).isoformat(),
                }]
                with self.assertRaisesRegex(release.ReleaseError, "escape evidence is incomplete"):
                    release.run(self.cfg, promote=True)
        self.assertNotIn("release/v0.0.4", self.git(self.origin, "branch", "--list"))


if __name__ == "__main__":
    unittest.main()
