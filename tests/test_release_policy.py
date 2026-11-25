"""#930: experimental tags, revision-bound public promotion, and release failures."""
from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import config, lifecycle, release, stats, watch
from tests import test_release as fixtures


class ReleasePolicyTest(unittest.TestCase):
    new_repo = fixtures.ReleaseTest.new_repo
    git = fixtures.ReleaseTest.git
    write_release_docs = fixtures.ReleaseTest.write_release_docs
    tag_and_advance = fixtures.ReleaseTest.tag_and_advance
    events = fixtures.ReleaseTest.events

    def setUp(self) -> None:
        self.enterContext(mock.patch.dict(os.environ, {
            "GIT_COMMITTER_DATE": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
        }))
        self.new_repo()
        self.cfg.host_name = "tank"
        self.cfg.watch_engine_repo = self.cfg.repo
        self.enterContext(mock.patch.object(release, "ci_passes", return_value=True))
        self.enterContext(mock.patch.object(release, "gate_passes", return_value=True))
        self.pulls = self.enterContext(mock.patch.object(release, "_pulls", return_value=[]))
        self.opened = self.enterContext(mock.patch.object(release, "_open_pull", return_value={"number": 12}))
        self.published = self.enterContext(mock.patch.object(
            release, "_gh_release", side_effect=lambda *a, **kw: subprocess.CompletedProcess([], 0, "", "")
        ))
        self.filings = self.enterContext(mock.patch.object(watch, "open_filings", return_value={}))
        self.escapes = self.enterContext(mock.patch.object(stats, "escapes", return_value=[]))
        self.enterContext(mock.patch.object(stats, "configure"))
        self.bar = {"human_rate": 0.1, "first_pass": 0.9, "tickets": 20,
                    **dict.fromkeys(release.PENDING, 0)}

    def baseline(self, bar: dict | None = None) -> None:
        self.tag_and_advance(self.bar if bar is None else bar)
        self.events()

    def land(self, version: str) -> str:
        self.git(self.repo, "fetch", "origin")
        self.git(self.repo, "merge", "--ff-only", f"origin/release/v{version}")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        sha = self.git(self.repo, "rev-parse", "HEAD")
        self.pulls.return_value = [{"merged_at": "2026-01-01T00:00:00Z", "merge_commit_sha": sha}]
        return sha

    def soak(self, tag: str = "v0.0.1", *, span: float = 24, host: str | None = None,
             commit: str | None = None, outcome: str = "completed") -> None:
        now = datetime.now(UTC) - timedelta(minutes=1)
        sha = commit or self.git(self.repo, "rev-parse", f"{tag}^{{}}")
        for index, at in enumerate((now - timedelta(hours=span), now)):
            common = {"schema_version": 1, "stage": "dispatcher", "execution_id": f"soak-{index}",
                      "host_id": host or lifecycle._host_id(), "at": at.isoformat()}
            lifecycle.append(self.cfg.factory / "events.jsonl", {
                **common, "event": "lifecycle", "kind": "enter", "engine_version": tag[1:],
                "engine_commit": sha,
            })
            lifecycle.append(self.cfg.factory / "events.jsonl", {
                **common, "event": "lifecycle", "kind": "exit", "outcome": outcome,
            })

    def test_auto_release_skips_even_and_bumps_tagged_commit_without_moving_stable(self) -> None:
        self.baseline()
        public_docs = self.git(self.origin, "show", "main:README.md")
        reason = release.run(self.cfg)
        self.assertIn("opened release PR #12 for v0.0.3", reason)
        self.assertEqual(self.opened.call_args.args[1], "release/v0.0.3")
        sha = self.land("0.0.3")
        reason = release.run(self.cfg)
        self.assertIn("released v0.0.3", reason)
        self.assertEqual(self.git(self.origin, "rev-parse", "v0.0.3^{}"), sha)
        self.assertIn('"0.0.3"', self.git(self.origin, "show", "v0.0.3:factory/__init__.py"))
        self.assertEqual(self.git(self.origin, "show", "v0.0.3:README.md"), public_docs)
        self.assertNotIn("refs/heads/stable", self.git(self.origin, "show-ref"))
        self.assertIn("--prerelease", self.published.call_args.args)
        self.assertNotIn("--latest", self.published.call_args.args)

    def test_public_promotion_records_soak_and_version_in_even_tag(self) -> None:
        self.baseline()
        # Promotion requires the exact experimental source, not unsoaked main changes.
        self.git(self.repo, "tag", "-d", "v0.0.1")
        self.git(self.repo, "tag", "-a", "v0.0.1", "-m", f"Release\n```json\n{json.dumps(self.bar)}\n```" )
        self.git(self.repo, "push", "-q", "--force", "origin", "v0.0.1")
        self.soak()
        self.assertIn("opened release PR #12 for v0.0.2", release.run(self.cfg, promote=True))
        self.land("0.0.2")
        self.assertIn("released v0.0.2", release.run(self.cfg, promote=True))
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"),
                         self.git(self.origin, "rev-parse", "v0.0.2^{}"))
        self.assertIn('"0.0.2"', self.git(self.origin, "show", "v0.0.2:factory/__init__.py"))
        annotation = self.git(self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.0.2")
        self.assertIn("```soak", annotation)
        self.assertIn('"experimental_tag":"v0.0.1"', annotation)
        self.assertIn('"hours":24.0', annotation)
        self.assertIn("--latest", self.published.call_args.args)
        self.assertNotIn("--prerelease", self.published.call_args.args)

    def test_promotion_refuses_unsoaked_main_changes(self) -> None:
        self.baseline()
        self.soak()
        self.assertIn("unsoaked changes", release.run(self.cfg, promote=True))
        self.opened.assert_not_called()

    def test_promotion_needs_real_revision_bound_tank_passes_spanning_24_hours(self) -> None:
        self.baseline()
        for options in ({"span": 23}, {"host": "other-host"}, {"commit": "not-the-tag"},
                        {"outcome": "mechanism_failure"}):
            with self.subTest(options=options):
                (self.cfg.factory / "events.jsonl").unlink()
                self.events()
                self.soak(**options)
                self.assertIn("24h", release.run(self.cfg, promote=True))
        self.cfg.host_name = "ultron"
        self.soak()
        self.assertIn("Tank", release.run(self.cfg, promote=True))
        self.opened.assert_not_called()

    def test_promotion_rechecks_watch_bugs_escalations_and_escapes(self) -> None:
        self.baseline()
        self.soak()
        self.filings.return_value = {"fingerprint": {"number": 99}}
        self.assertIn("watch-filed", release.run(self.cfg, promote=True))
        self.filings.return_value = {}
        self.escapes.return_value = [{"status": "candidates", "closed_at": datetime.now(UTC).isoformat(),
                                      "candidates": [{"ticket": 1}], "bug": 99}]
        self.assertIn("escape", release.run(self.cfg, promote=True))
        self.escapes.return_value = []
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "event": "escalate", "ticket": 1, "code": "gate_failed", "at": datetime.now(UTC).isoformat(),
        })
        self.assertIn("escalation", release.run(self.cfg, promote=True))
        self.opened.assert_not_called()

    def test_missing_bar_is_a_failed_check_not_an_eligible_release(self) -> None:
        self.tag_and_advance(None)
        self.events()
        with self.assertRaisesRegex(RuntimeError, "bar"):
            release.run(self.cfg)
        self.opened.assert_not_called()
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertTrue(any(row.get("outcome") == "failed" for row in rows))

    def test_malformed_bar_and_nonfinite_or_out_of_range_metrics_fail_closed(self) -> None:
        for message in ("```json\n{bad}\n```", "no bar", *(
            f"```json\n{json.dumps({**self.bar, **change})}\n```" for change in (
                {"human_rate": float("nan")}, {"first_pass": 1.1}, {"tickets": -1},
                {"pending_attempts": -1},
            )
        )):
            with self.subTest(message=message), self.assertRaises(ValueError):
                release._bar(message)

    def test_version_mismatch_fails_instead_of_tagging_arbitrary_bump(self) -> None:
        self.baseline()
        (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.0.99"\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-qm", "unrelated version drift")
        self.git(self.repo, "push", "-q", "origin", "main")
        with self.assertRaisesRegex(RuntimeError, "version"):
            release.run(self.cfg)
        self.assertNotIn("v0.0.99", self.git(self.origin, "tag", "--list"))

    def test_expected_bump_without_merged_release_pr_is_a_mismatch(self) -> None:
        self.baseline()
        (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.0.3"\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-qm", "manual bump")
        self.git(self.repo, "push", "-q", "origin", "main")
        with self.assertRaisesRegex(RuntimeError, "merged release PR"):
            release.run(self.cfg)

    def test_cli_failure_is_nonzero_and_dry_run_is_read_only(self) -> None:
        self.tag_and_advance(None)
        before = self.git(self.origin, "show-ref")
        with mock.patch.object(config, "load", return_value=self.cfg), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(release.main(["--dry-run"]), 1)
        self.assertIn("release check failed", json.loads(out.getvalue())["verdict"])
        self.assertEqual(self.git(self.origin, "show-ref"), before)
        self.assertFalse((self.cfg.factory / "events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
