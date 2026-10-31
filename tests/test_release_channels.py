"""Odd experimental and even public release channels (#458)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, release


class ReleaseChannelsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.addCleanup(mock.patch.dict(os.environ, {
            "HOME": str(self.base / "home"),
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_DATA_HOME": str(self.base / "data"),
        }).stop)
        mock.patch.dict(os.environ, {
            "HOME": str(self.base / "home"),
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_DATA_HOME": str(self.base / "data"),
        }).start()
        self.origin = self.base / "origin.git"
        self.repo = self.base / "repo"
        self.git(self.base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(self.base, "clone", "-q", str(self.origin), str(self.repo))
        self.git(self.repo, "config", "user.name", "Release Test")
        self.git(self.repo, "config", "user.email", "release@example.invalid")
        (self.repo / "factory").mkdir()
        (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.4.6"\n')
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Unreleased\n\n- experimental work\n\n"
            "## 0.4.6 — 2026-10-23\n\n- public work\n"
        )
        (self.repo / "README.md").write_text(
            "uv tool install git+https://github.com/mikeroySoft/factory@stable   # stable\n"
            "uv tool install git+https://github.com/mikeroySoft/factory@v0.4.6   # a specific release\n"
        )
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "index.html").write_text(
            "<code>uv tool install git+https://github.com/mikeroySoft/factory@v0.4.6</code>\n"
            '<p>Latest release: v0.4.6 · October 23, 2026</p>\n'
            '<ol class="history">\n<li class="release"><span class="chip chip-success">Latest release</span>'
            '<a href="/v0.4.6">v0.4.6</a></li>\n</ol>\n'
        )
        (self.repo / ".factory.toml").write_text('[repo]\nslug = "acme/widgets"\n')
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-q", "-m", "initial")
        bar = {"human_rate": 1.0, "first_pass": 0.0, "escape_rate": 1.0, "tickets": 30}
        self.git(self.repo, "tag", "-a", "v0.4.5", "-m", "Experimental release 0.4.5")
        self.git(self.repo, "tag", "-a", "v0.4.6", "-m", f"Release 0.4.6\n\n```json\n{json.dumps(bar)}\n```")
        self.git(self.repo, "push", "-q", "origin", "main", "v0.4.5", "v0.4.6")
        (self.repo / "work.txt").write_text("change\n")
        self.git(self.repo, "add", "work.txt")
        self.git(self.repo, "commit", "-q", "-m", "work")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.cfg = config.Config(
            root=self.repo,
            repo="acme/widgets",
            release_min_tickets=1,
            release_bar_min_tickets=1,
            release_window_tickets=2,
            release_soak_hours=0,
            release_experimental_min_interval_hours=0,
        )

    @staticmethod
    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()

    def event(self, **row: object) -> None:
        lifecycle.append(self.cfg.factory / "events.jsonl", row)

    @staticmethod
    def gh_result(code: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, "", stderr)
    def make_odd(self) -> str:
        release._update_experimental_files(self.cfg, self.repo, "0.4.6", "0.4.7")
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.4.7")
        sha = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "tag", "-a", "v0.4.7", "-m", "Experimental release 0.4.7")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main", "v0.4.7")
        at = lifecycle._now()
        self.event(at=at, event="lifecycle", stage="dispatcher", kind="enter", engine_commit=sha)
        self.event(at=at, event="attempt", ticket=1, attempt=1, gate="PASS")
        self.event(at=at, event="merged", ticket=1)
        return sha


    def test_config_defaults_and_window_guard(self) -> None:
        loaded = config.load(self.repo)
        self.assertEqual(
            (loaded.release_experimental_min_interval_hours, loaded.release_window_tickets,
             loaded.release_soak_hours, loaded.release_bar_floor_escape_rate),
            (6, 30, 24, 0.05),
        )
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n[release]\nwindow_tickets = 19\nbar_min_tickets = 20\n'
        )
        with self.assertRaisesRegex(config.ConfigError, "window_tickets.*bar_min_tickets"):
            config.load(self.repo)

    def test_rolling_window_ages_out_touches_and_counts_escapes(self) -> None:
        for ticket, day in ((1, 1), (2, 2), (3, 3)):
            at = f"2026-10-0{day}T00:00:00Z"
            self.event(at=at, event="attempt", ticket=ticket, attempt=1, gate="PASS")
            self.event(at=at, event="merged", ticket=ticket)
        self.event(at="2026-10-01T00:00:01Z", event="manage", ticket=1, decision="HUMAN")
        escapes = [{"candidates": [{"ticket": 2}]}]
        with mock.patch("factory.stats.escapes", return_value=escapes):
            metrics = release.measure_window(self.cfg)
        self.assertEqual(metrics["tickets"], 2)
        self.assertEqual((metrics["human_touches"], metrics["human_rate"]), (0, 0.0))
        self.assertEqual((metrics["escapes"], metrics["escape_rate"]), (1, 0.5))

    def test_odd_cut_ignores_failing_bar_and_keeps_public_pins(self) -> None:
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 12}), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch("factory.stats.escapes", return_value=[]):
            reason = release.run(self.cfg)
        self.assertIn("opened experimental release PR #12 for v0.4.7", reason)
        self.assertIn('__version__ = "0.4.7"', self.git(
            self.origin, "show", "release/v0.4.7:factory/__init__.py"))
        self.assertIn("factory@v0.4.6", self.git(self.origin, "show", "release/v0.4.7:README.md"))
        self.assertIn("Latest release: v0.4.6", self.git(
            self.origin, "show", "release/v0.4.7:docs/index.html"))

    def test_odd_tag_is_prerelease_without_stable(self) -> None:
        release._update_experimental_files(self.cfg, self.repo, "0.4.6", "0.4.7")
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.4.7")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        with mock.patch.object(release, "_gh_release", side_effect=[
            self.gh_result(1), self.gh_result(0),
        ]) as gh, mock.patch("factory.stats.escapes", return_value=[]):
            reason = release.run(self.cfg)
        self.assertIn("released v0.4.7", reason)
        self.assertTrue(self.git(self.origin, "rev-parse", "v0.4.7^{}"))
        self.assertNotIn("refs/heads/stable", self.git(self.origin, "show-ref"))
        self.assertEqual(gh.call_args_list[1].args[1:6], (
            "create", "v0.4.7", "--verify-tag", "--prerelease", "--latest=false"))

    def test_even_promotion_tags_latest_advances_stable_and_opens_carryback(self) -> None:
        self.make_odd()
        with mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch("factory.stats.escapes", return_value=[]):
            built = release.run(self.cfg)
        self.assertIn("built promotion branch release/v0.4.8", built)
        self.assertIn('__version__ = "0.4.8"', self.git(
            self.origin, "show", "release/v0.4.8:factory/__init__.py"))
        self.assertIn("factory@v0.4.8", self.git(
            self.origin, "show", "release/v0.4.8:README.md"))

        with mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_gh_release", side_effect=[
                    self.gh_result(1), self.gh_result(0),
                ]) as gh, mock.patch("factory.stats.escapes", return_value=[]):
            tagged = release.run(self.cfg)
        self.assertIn("released v0.4.8", tagged)
        public = self.git(self.origin, "rev-parse", "v0.4.8^{}")
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), public)
        self.assertIn("--latest", gh.call_args_list[1].args)
        annotation = self.git(
            self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.4.8")
        recorded = release._bar(annotation)
        self.assertFalse(
            {"soak_hours", "watch_filed_bugs", "escalation_rate", "escape_rate"}
            - set(recorded)
        )

        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 18}) as opened, \
                mock.patch("factory.stats.escapes", return_value=[]):
            carried = release.run(self.cfg)
        self.assertIn("opened promotion PR #18 for v0.4.8", carried)
        self.assertEqual(opened.call_args.args[1:3], ("release/v0.4.8", "Promote v0.4.8"))
        self.assertIn('__version__ = "0.4.7"', self.git(
            self.origin, "show", "release/v0.4.8:factory/__init__.py"))

    def test_escape_rate_blocks_even_promotion(self) -> None:
        self.make_odd()
        strict = {
            "human_rate": 1.0, "first_pass": 0.0, "escape_rate": 0.0, "tickets": 30,
            "pending_tickets": 0, "pending_human_touches": 0,
            "pending_first_passes": 0, "pending_attempts": 0,
        }
        escapes = [{"candidates": [{"ticket": 1}]}]
        with mock.patch.object(release, "_bar", return_value=strict), \
                mock.patch.object(release, "gate_passes") as gate, \
                mock.patch("factory.stats.escapes", return_value=escapes):
            held = release.run(self.cfg)
        self.assertIn("escape_rate 1.0 is worse than 0.0", held)
        gate.assert_not_called()

    def test_experimental_interval_and_in_flight_pr_hold(self) -> None:
        self.cfg.release_experimental_min_interval_hours = 10_000
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_open_pull") as opened, \
                mock.patch("factory.stats.escapes", return_value=[]):
            reason = release.run(self.cfg)
        self.assertIn("experimental interval", reason)
        opened.assert_not_called()

        self.cfg.release_experimental_min_interval_hours = 0
        pull = {"number": 9, "head": {"sha": "abc"}, "mergeable": None, "mergeable_state": "unknown"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 9}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "_open_pull") as opened, \
                mock.patch("factory.stats.escapes", return_value=[]):
            reason = release.run(self.cfg)
        self.assertIn("waiting for GitHub mergeability", reason)
        opened.assert_not_called()

    def test_soak_gate_checks_age_watch_bugs_and_escalation_regression(self) -> None:
        odd = {"sha": "a" * 40, "date": "2026-10-01T00:00:00Z", "tag": "v0.4.7"}
        previous = {"sha": "b" * 40, "date": "2026-09-01T00:00:00Z", "tag": "v0.4.5"}
        self.event(at="2026-10-01T00:00:00Z", event="lifecycle", stage="dispatcher", kind="enter",
                   engine_commit=odd["sha"])
        early = release.soak_metrics(self.cfg, odd, previous, {"escalation_rate": 0.5},
                                     now=datetime(2026, 10, 1, 12, tzinfo=UTC))
        self.assertLess(early["soak_hours"], 24)
        self.event(at="2026-10-02T01:00:00Z", event="watch", issue=10, fingerprint="runtime:x")
        self.event(at="2026-10-02T02:00:00Z", event="escalate", ticket=1)
        self.event(at="2026-10-02T03:00:00Z", event="merged", ticket=2)
        dirty = release.soak_metrics(self.cfg, odd, previous, {"escalation_rate": 0.25},
                                     now=datetime(2026, 10, 2, 4, tzinfo=UTC))
        self.assertEqual(dirty["watch_filed_bugs"], 1)
        self.assertGreater(dirty["escalation_rate"], dirty["previous_escalation_rate"])

    def test_dry_run_reports_both_channels_without_writes(self) -> None:
        output = io.StringIO()
        real_git = release._git
        calls: list[tuple[str, ...]] = []

        def observed_git(root: Path, *args: str, **kwargs: object):
            calls.append(args)
            return real_git(root, *args, **kwargs)

        with mock.patch.object(release, "_git", side_effect=observed_git), \
                mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch("factory.stats.escapes", return_value=[]), redirect_stdout(output):
            release.run(self.cfg, dry_run=True)
        report = json.loads(output.getvalue())
        self.assertEqual(set(report), {"odd", "even"})
        self.assertFalse(any(
            args and (
                args[0] in {"push", "worktree", "branch", "commit", "add"}
                or (args[0] == "tag" and any(flag in args for flag in ("-a", "-d")))
            )
            for args in calls
        ))


if __name__ == "__main__":
    unittest.main()
