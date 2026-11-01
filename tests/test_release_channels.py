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

from factory import config, lifecycle, release, stats


class ReleaseChannelsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.base / "home"),
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_DATA_HOME": str(self.base / "data"),
        })
        env.start()
        self.addCleanup(env.stop)
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

    def test_measure_window_configures_escape_scan(self) -> None:
        self.event(at="2026-10-01T00:00:00Z", event="attempt", ticket=1, attempt=1, gate="PASS")
        self.event(at="2026-10-01T00:00:00Z", event="merged", ticket=1)

        def escapes(*, read_only: bool = False) -> list[dict]:
            self.assertFalse(read_only)
            self.assertIs(stats.cfg, self.cfg)
            self.assertEqual(stats.REPOSITORY, self.cfg.repo)
            return []

        with mock.patch.object(stats, "escapes", side_effect=escapes):
            self.assertEqual(release.measure_window(self.cfg)["escape_rate"], 0.0)

    def test_pending_legacy_bar_gains_escape_floor(self) -> None:
        old = {
            "human_rate": 0.1, "first_pass": 0.9, "tickets": 30,
            "pending_tickets": 4, "pending_human_touches": 0,
            "pending_first_passes": 4, "pending_attempts": 4,
        }
        migrated, changed = release._reset_legacy_bar(old, self.cfg)
        self.assertTrue(changed)
        self.assertEqual(migrated["escape_rate"], self.cfg.release_bar_floor_escape_rate)
        self.assertEqual(migrated["pending_escapes"], 0)

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

    def test_held_promotion_does_not_block_next_odd_cut(self) -> None:
        self.make_odd()
        (self.repo / "next.txt").write_text("next experiment\n")
        self.git(self.repo, "add", "next.txt")
        self.git(self.repo, "commit", "-q", "-m", "next experiment")
        self.git(self.repo, "push", "-q", "origin", "main")
        strict = {
            "human_rate": 1.0, "first_pass": 0.0, "escape_rate": 0.0, "tickets": 30,
            "pending_tickets": 0, "pending_human_touches": 0,
            "pending_first_passes": 0, "pending_attempts": 0, "pending_escapes": 0,
        }
        escapes = [{"candidates": [{"ticket": 1}]}]
        with mock.patch.object(release, "_bar", return_value=strict), \
                mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 13}), \
                mock.patch.object(release, "_record_hold"), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=escapes):
            reason = release.run(self.cfg)
        self.assertIn("escape_rate 1.0 is worse than 0.0", reason)
        self.assertIn("opened experimental release PR #13 for v0.4.9", reason)

    def test_built_promotion_hold_does_not_block_next_odd_cut(self) -> None:
        self.make_odd()
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=[]):
            self.assertIn("built promotion branch release/v0.4.8", release.run(self.cfg))
        (self.repo / "next.txt").write_text("next experiment\n")
        self.git(self.repo, "add", "next.txt")
        self.git(self.repo, "commit", "-q", "-m", "next experiment")
        self.git(self.repo, "push", "-q", "origin", "main")
        strict = {
            "human_rate": 1.0, "first_pass": 0.0, "escape_rate": 0.0, "tickets": 30,
            "pending_tickets": 0, "pending_human_touches": 0,
            "pending_first_passes": 0, "pending_attempts": 0, "pending_escapes": 0,
        }
        with mock.patch.object(release, "_bar", return_value=strict), \
                mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 13}), \
                mock.patch.object(release, "_record_hold"), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=[
                    {"candidates": [{"ticket": 1}]}
                ]):
            reason = release.run(self.cfg)
        self.assertIn("escape_rate 1.0 is worse than 0.0", reason)
        self.assertIn("opened experimental release PR #13 for v0.4.9", reason)

    def test_promotion_scans_escapes_once(self) -> None:
        self.make_odd()
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=[]) as escapes:
            self.assertIn("built promotion branch release/v0.4.8", release.run(self.cfg))
        escapes.assert_called_once_with(read_only=False)

    def test_odd_cut_does_not_scan_escapes(self) -> None:
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 12}), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=[]) as escapes:
            self.assertIn("opened experimental release PR #12", release.run(self.cfg))
        escapes.assert_not_called()

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
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch("factory.stats.escapes", return_value=[]):
            built = release.run(self.cfg)
        self.assertIn("built promotion branch release/v0.4.8", built)
        self.assertIn('__version__ = "0.4.8"', self.git(
            self.origin, "show", "release/v0.4.8:factory/__init__.py"))
        self.assertIn("factory@v0.4.8", self.git(
            self.origin, "show", "release/v0.4.8:README.md"))

        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
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

        changelog = self.repo / "CHANGELOG.md"
        changelog.write_text(changelog.read_text().replace(
            "## Unreleased\n\n", "## Unreleased\n\n- landed during soak\n\n", 1,
        ))
        self.git(self.repo, "add", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "land during promotion")
        self.git(self.repo, "push", "-q", "origin", "main")

        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 18}) as opened, \
                mock.patch("factory.stats.escapes", return_value=[]):
            carried = release.run(self.cfg)
        self.assertIn("opened promotion PR #18 for v0.4.8", carried)
        self.assertEqual(opened.call_args.args[1:3], ("release/v0.4.8", "Promote v0.4.8"))
        self.assertIn('__version__ = "0.4.7"', self.git(
            self.origin, "show", "release/v0.4.8:factory/__init__.py"))
        self.assertIn("- landed during soak", self.git(
            self.origin, "show", "release/v0.4.8:CHANGELOG.md"))

        (self.repo / "later.txt").write_text("later experiment\n")
        self.git(self.repo, "add", "later.txt")
        self.git(self.repo, "commit", "-q", "-m", "later experiment")
        self.git(self.repo, "push", "-q", "origin", "main")
        pending_pull = {
            "number": 18, "head": {"sha": "abc"},
            "mergeable": None, "mergeable_state": "unknown",
        }

        def pulls(_cfg: config.Config, branch: str) -> list[dict]:
            return [{"number": 18}] if branch == "release/v0.4.8" else []

        with mock.patch.object(release, "_pulls", side_effect=pulls), \
                mock.patch.object(release, "_pull", return_value=pending_pull), \
                mock.patch.object(release, "_open_pull", return_value={"number": 19}), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=[]):
            progressed = release.run(self.cfg)
        self.assertIn("waiting for GitHub mergeability", progressed)
        self.assertNotIn("opened experimental release PR", progressed)

    def test_public_tag_replaces_divergent_stable_atomically(self) -> None:
        self.git(self.repo, "checkout", "-q", "-b", "old-public", "v0.4.6")
        (self.repo / "public.txt").write_text("old public\n")
        self.git(self.repo, "add", "public.txt")
        self.git(self.repo, "commit", "-q", "-m", "old public")
        self.git(self.repo, "push", "-q", "origin", "HEAD:stable")
        old_stable = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "checkout", "-q", "main")
        new_public = self.git(self.repo, "rev-parse", "HEAD")
        metrics = {
            "tickets": 1, "human_touches": 0, "human_ticket_rate": 0.0,
            "first_pass": 1.0, "first_passes": 1, "attempts": 1,
            "escape_rate": 0.0, "escapes": 0, "soak_hours": 24.0,
            "soak_passes": 2, "watch_filed_bugs": 0, "escalation_rate": 0.0,
            "previous_escalation_rate": 0.0,
        }
        comparison = {"human_rate": 1.0, "first_pass": 0.0, "escape_rate": 1.0}
        with mock.patch.object(release, "_finish", return_value="released"), \
                mock.patch.object(release, "_publish", return_value="published"):
            reason = release._tag_public(
                self.cfg, {"tag": "v0.4.7"}, {"tag": "v0.4.6"}, "0.4.8",
                new_public, metrics, comparison, comparison,
            )
        self.assertIn("published", reason)
        self.assertNotEqual(old_stable, new_public)
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), new_public)

    def test_escape_rate_blocks_even_promotion(self) -> None:
        self.make_odd()
        strict = {
            "human_rate": 1.0, "first_pass": 0.0, "escape_rate": 0.0, "tickets": 30,
            "pending_tickets": 0, "pending_human_touches": 0,
            "pending_first_passes": 0, "pending_attempts": 0,
        }
        escapes = [{"candidates": [{"ticket": 1}]}]
        with mock.patch.object(release, "_bar", return_value=strict), \
                mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "gate_passes") as gate, \
                mock.patch.object(stats, "escapes", return_value=escapes):
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

    def test_soak_gate_requires_candidate_pass_span_and_blocks_regressions(self) -> None:
        odd = {"sha": "a" * 40, "date": "2026-10-01T00:00:00Z", "tag": "v0.4.7"}
        previous = {"sha": "b" * 40, "date": "2026-09-01T00:00:00Z", "tag": "v0.4.5"}
        self.event(at="2026-10-01T00:00:00Z", event="lifecycle", stage="dispatcher", kind="enter",
                   engine_commit=odd["sha"])
        one_pass = release.soak_metrics(
            self.cfg, odd, previous, {"escalation_rate": 0.5},
            now=datetime(2026, 10, 3, tzinfo=UTC),
        )
        self.assertEqual(one_pass["soak_hours"], 0)

        self.event(at="2026-10-02T01:00:00Z", event="watch", issue=10, fingerprint="runtime:x")
        self.event(at="2026-10-02T02:00:00Z", event="escalate", ticket=1)
        self.event(at="2026-10-02T03:00:00Z", event="merged", ticket=2)
        self.event(at="2026-10-02T04:00:00Z", event="lifecycle", stage="dispatcher", kind="enter",
                   engine_commit=odd["sha"])
        dirty = release.soak_metrics(
            self.cfg, odd, previous, {"escalation_rate": 0.25},
            now=datetime(2026, 10, 3, tzinfo=UTC),
        )
        self.assertEqual(dirty["soak_hours"], 28)
        self.assertEqual(dirty["watch_filed_bugs"], 1)
        self.assertGreater(dirty["escalation_rate"], dirty["previous_escalation_rate"])
        self.cfg.release_soak_hours = 24

        base = {
            "tickets": 30, "human_rate": 0.0, "human_touches": 0,
            "human_ticket_rate": 0.0, "first_pass": 1.0, "first_passes": 30,
            "attempts": 30, "escape_rate": 0.0, "escapes": 0,
        }
        cases = (
            ({"soak_hours": 23.0, "soak_passes": 2, "watch_filed_bugs": 0,
              "escalation_rate": 0.0, "previous_escalation_rate": 0.0}, "below soak_hours"),
            ({"soak_hours": 24.0, "soak_passes": 2, "watch_filed_bugs": 1,
              "escalation_rate": 0.0, "previous_escalation_rate": 0.0}, "watch filed"),
            ({"soak_hours": 24.0, "soak_passes": 2, "watch_filed_bugs": 0,
              "escalation_rate": 0.5, "previous_escalation_rate": 0.25}, "rose above"),
            ({"soak_hours": 24.0, "soak_passes": 2, "watch_filed_bugs": 0,
              "escalation_rate": 0.25, "previous_escalation_rate": 0.25}, None),
        )
        even = {
            "message": "Release\n```json\n"
            '{"human_rate":1.0,"first_pass":0.0,"escape_rate":1.0,"tickets":30,'
            '"pending_tickets":0,"pending_human_touches":0,"pending_first_passes":0,'
            '"pending_attempts":0,"pending_escapes":0}\n```'
        }
        for soak, expected in cases:
            with self.subTest(expected=expected), \
                    mock.patch.object(release, "measure_window", return_value=base.copy()), \
                    mock.patch.object(release, "soak_metrics", return_value=soak):
                hold = release._promotion_evidence(self.cfg, odd, previous, even)[3]
            if expected is None:
                self.assertIsNone(hold)
            else:
                self.assertIn(expected, hold)

    def test_dry_run_reports_both_channels_without_writes(self) -> None:
        self.make_odd()
        before = (self.cfg.factory / "events.jsonl").read_bytes()
        output = io.StringIO()
        real_git = release._git
        git_calls: list[tuple[str, ...]] = []
        gh_calls: list[tuple[str, ...]] = []

        def observed_git(root: Path, *args: str, **kwargs: object):
            git_calls.append(args)
            return real_git(root, *args, **kwargs)

        def observed_gh(_cfg: config.Config, endpoint: str, *args: str) -> list:
            gh_calls.append((endpoint, *args))
            return []

        with mock.patch.object(release, "_git", side_effect=observed_git), \
                mock.patch.object(release, "_gh", side_effect=observed_gh), \
                mock.patch.object(release, "_gh_release") as gh_release, \
                mock.patch.object(release, "_record") as record, \
                mock.patch.object(stats, "escapes", return_value=[]), redirect_stdout(output):
            release.run(self.cfg, dry_run=True)
        report = json.loads(output.getvalue())
        self.assertEqual(set(report), {"odd", "even"})
        self.assertEqual(report["even"]["candidate"], "v0.4.7")
        self.assertFalse(any(
            args and (
                args[0] in {"push", "worktree", "branch", "commit", "add"}
                or (args[0] == "tag" and any(flag in args for flag in ("-a", "-d")))
            )
            for args in git_calls
        ))
        self.assertFalse(any(
            any(arg in {"POST", "PUT", "PATCH", "DELETE"} for arg in args)
            for args in gh_calls
        ))
        gh_release.assert_not_called()
        record.assert_not_called()
        self.assertEqual((self.cfg.factory / "events.jsonl").read_bytes(), before)


    def build_and_tag_public(self) -> None:
        self.make_odd()
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_gh_release", side_effect=[
                    self.gh_result(1), self.gh_result(0),
                ]), mock.patch.object(stats, "escapes", return_value=[]):
            self.assertIn("built promotion branch", release.run(self.cfg))
            self.assertIn("released v0.4.8", release.run(self.cfg))

    def test_pending_carryback_blocks_another_release(self) -> None:
        self.build_and_tag_public()
        for newer_odd in (False, True):
            with self.subTest(newer_odd=newer_odd):
                if newer_odd:
                    release._update_experimental_files(self.cfg, self.repo, "0.4.7", "0.4.9")
                    self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
                    self.git(self.repo, "commit", "-q", "-m", "Release 0.4.9")
                    self.git(self.repo, "tag", "-a", "v0.4.9", "-m", "Experimental release 0.4.9")
                    self.git(self.repo, "push", "-q", "origin", "main", "v0.4.9")
                (self.repo / "later.txt").write_text(f"next work {newer_odd}\n")
                self.git(self.repo, "add", "later.txt")
                self.git(self.repo, "commit", "-q", "-m", "next work")
                self.git(self.repo, "push", "-q", "origin", "main")
                pull = {"number": 18, "mergeable": None, "mergeable_state": "unknown"}
                with mock.patch.object(release, "_pulls", side_effect=lambda cfg, branch: (
                    [{"number": 18}] if branch == "release/v0.4.8" else []
                )), mock.patch.object(release, "_pull", return_value=pull), \
                        mock.patch.object(release, "_open_experimental_pr",
                                          return_value="unexpected odd cut") as opened, \
                        mock.patch.object(release, "_build_promotion",
                                          return_value="unexpected promotion") as built, \
                        mock.patch.object(release, "ci_passes", return_value=True), \
                        mock.patch.object(stats, "escapes", return_value=[]):
                    reason = release.run(self.cfg)
                self.assertIn("waiting for GitHub mergeability", reason)
                opened.assert_not_called()
                built.assert_not_called()

    def test_missing_carryback_recovers_original_source_after_newer_odd(self) -> None:
        self.build_and_tag_public()
        release._update_experimental_files(self.cfg, self.repo, "0.4.7", "0.4.9")
        changelog = self.repo / "CHANGELOG.md"
        changelog.write_text(changelog.read_text().replace(
            "## Unreleased\n\n", "## Unreleased\n\n- newer unpromoted work\n\n", 1,
        ))
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.4.9")
        self.git(self.repo, "tag", "-a", "v0.4.9", "-m", "Experimental release 0.4.9")
        self.git(self.repo, "push", "-q", "origin", "main", "v0.4.9")
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 18}), \
                mock.patch.object(stats, "escapes", return_value=[]):
            reason = release.run(self.cfg)
        self.assertIn("opened promotion PR #18 for v0.4.8", reason)
        carried = self.git(self.origin, "show", "release/v0.4.8:CHANGELOG.md")
        self.assertIn("- newer unpromoted work", carried)
        self.assertIn("factory@v0.4.8", self.git(
            self.origin, "show", "release/v0.4.8:README.md"))
        self.assertIn('__version__ = "0.4.9"', self.git(
            self.origin, "show", "release/v0.4.8:factory/__init__.py"))

    def test_escalations_compare_previous_odd_not_public_baseline(self) -> None:
        odd = {"tag": "v0.4.9", "sha": "candidate"}
        previous = {"tag": "v0.4.7", "sha": "previous"}
        for sha, start, end in (("previous", "01", "02"), ("candidate", "03", "04")):
            for day in (start, end):
                self.event(at=f"2026-10-{day}T00:00:00Z", event="lifecycle",
                           stage="dispatcher", kind="enter", engine_commit=sha)
            for ticket in range(20):
                self.event(at=f"2026-10-{start}T01:00:00Z", event=(
                    "escalate" if ticket < (1 if sha == "previous" else 2) else "merged"
                ), ticket=ticket + (100 if sha == "candidate" else 0))
                self.event(at=f"2026-10-{start}T01:00:00Z", event="attempt",
                           ticket=ticket, attempt=1, gate="PASS")
        even = {"message": 'Release\n```json\n{"human_rate":1,"first_pass":0,'
                '"escape_rate":1,"tickets":30,"escalation_rate":0.20}\n```'}
        with mock.patch.object(stats, "escapes", return_value=[]):
            metrics, _, _, hold = release._promotion_evidence(self.cfg, odd, previous, even)
        self.assertEqual(metrics["previous_escalation_rate"], 0.05)
        self.assertEqual(metrics["escalation_rate"], 0.10)
        self.assertIn("rose above 0.05", hold)

    def test_watch_host_filings_do_not_block_candidate_engine_bugs_do(self) -> None:
        from factory import watch

        odd = {"tag": "v0.4.7", "sha": "candidate"}
        for day in ("01", "03"):
            self.event(at=f"2026-10-{day}T00:00:00Z", event="lifecycle",
                       stage="dispatcher", kind="enter", engine_commit=odd["sha"])
        reason = "github_command_failed: unavailable"
        self.event(at="2026-10-01T01:00:00Z", event="lifecycle", kind="exit",
                   stage="triage", reason=reason, outcome="mechanism_failure",
                   engine_commit=odd["sha"])
        self.event(at="2026-10-01T02:00:00Z", event="watch", issue=10,
                   fingerprint=watch.fingerprint("host", reason))
        self.assertEqual(release.soak_metrics(
            self.cfg, odd, None, None)["watch_filed_bugs"], 0)
        self.event(at="2026-10-01T03:00:00Z", event="watch", issue=11,
                   fingerprint=watch.fingerprint("lifecycle", "KeyError"),
                   signal_kind="lifecycle", engine_commit="another-engine")
        self.assertEqual(release.soak_metrics(
            self.cfg, odd, None, None)["watch_filed_bugs"], 0)
        self.event(at="2026-10-01T04:00:00Z", event="watch", issue=12,
                   fingerprint=watch.fingerprint("lifecycle", "TypeError"),
                   signal_kind="lifecycle", engine_commit=odd["sha"])
        self.assertEqual(release.soak_metrics(
            self.cfg, odd, None, None)["watch_filed_bugs"], 1)

    def test_watch_producer_records_signal_kind_and_engine(self) -> None:
        from factory import dispatch, watch

        signal = {
            "kind": "host", "fingerprint": watch.fingerprint("host", "github_command_failed"),
            "stage": "triage", "key": "github_command_failed", "rows": [], "log": None,
            "excerpt": None, "latest": None, "command": "factory triage",
        }
        with mock.patch.dict(dispatch.__dict__), \
                mock.patch.object(watch, "signals", return_value=[signal]), \
                mock.patch.object(watch, "open_filings", return_value={}), \
                mock.patch.object(config, "engine_commit", return_value="candidate"), \
                mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess(
                    [], 0, "https://github.com/acme/engine/issues/10\n", "",
                )):
            dispatch.configure(self.cfg)
            watch.run_pass(self.cfg)
        filing = next(row for row in lifecycle.read_events(
            self.cfg.factory / "events.jsonl") if row.get("event") == "watch")
        self.assertEqual(filing["signal_kind"], "host")
        self.assertEqual(filing["engine_commit"], "candidate")

    def test_public_notes_include_promoted_changes_and_survive_carryback(self) -> None:
        self.build_and_tag_public()
        for path in ("CHANGELOG.md", "docs/index.html"):
            text = self.git(self.origin, "show", f"v0.4.8:{path}")
            if path == "CHANGELOG.md":
                text = text.split("## 0.4.8", 1)[1].split("## 0.4.7", 1)[0]
            else:
                text = text.split("<h3>v0.4.8 release notes.</h3>", 1)[1].split("</ul>", 1)[0]
            self.assertIn("experimental work", text)
        with mock.patch.object(release, "_gh_release", side_effect=[
            self.gh_result(1), self.gh_result(0),
        ]) as gh:
            release._publish(self.cfg, "v0.4.8")
        self.assertIn("- experimental work", gh.call_args.kwargs["notes"])
        changelog = self.repo / "CHANGELOG.md"
        changelog.write_text(changelog.read_text().replace(
            "## Unreleased\n\n", "## Unreleased\n\n- landed during soak\n\n", 1,
        ))
        self.git(self.repo, "add", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "more changes")
        self.git(self.repo, "push", "-q", "origin", "main")
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 18}), \
                mock.patch.object(stats, "escapes", return_value=[]):
            self.assertIn("opened promotion PR", release.run(self.cfg))
        carried = self.git(self.origin, "show", "release/v0.4.8:CHANGELOG.md")
        self.assertIn("- landed during soak", carried.split("## 0.4.8", 1)[0])
        self.assertIn("- experimental work", carried.split(
            "## 0.4.8", 1)[1].split("## 0.4.7", 1)[0])

    def test_dry_run_checks_main_ci_before_offering_odd_pr(self) -> None:
        for state, statuses in (("failure", [{"state": "failure"}]),
                                ("pending", [{"state": "pending"}]), ("pending", [])):
            with self.subTest(state=state, statuses=statuses):
                def gh(cfg: config.Config, endpoint: str, *args: str,
                       state=state, statuses=statuses):
                    if "/status?" in endpoint:
                        return {"state": state, "statuses": statuses}
                    if "/check-runs?" in endpoint:
                        return [{"check_runs": []}]
                    return []

                output = io.StringIO()
                with mock.patch.object(release, "_gh", side_effect=gh), \
                        mock.patch.object(stats, "escapes", return_value=[]), redirect_stdout(output):
                    release.run(self.cfg, dry_run=True)
                verdict = json.loads(output.getvalue())["odd"]["verdict"]
                self.assertIn("CI statuses/check-runs are not all successful", verdict)
                self.assertNotIn("would open", verdict)

    def test_dry_run_reports_pending_carryback_even_with_newer_odd(self) -> None:
        self.build_and_tag_public()
        self.git(self.repo, "tag", "-a", "v0.4.9", "-m", "Experimental release 0.4.9")
        self.git(self.repo, "push", "-q", "origin", "v0.4.9")
        output = io.StringIO()
        with mock.patch.object(release, "_pulls", side_effect=lambda cfg, branch: (
            [{"number": 18}] if branch == "release/v0.4.8" else []
        )), mock.patch.object(stats, "escapes", return_value=[]), redirect_stdout(output):
            release.run(self.cfg, dry_run=True)
        report = json.loads(output.getvalue())
        self.assertIn("carryback", report["odd"]["verdict"])
        self.assertIn("#18", report["even"]["verdict"])

    def test_dry_run_cli_is_read_only_with_remote_changes_and_legacy_tags(self) -> None:
        import shutil
        import sys

        binaries = self.base / "bin"
        binaries.mkdir()
        calls = self.base / "calls.jsonl"
        git_path = shutil.which("git")
        wrapper = binaries / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"with open({str(calls)!r}, 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "args = sys.argv[1:]\n"
            "if args[0] == '-c': args = args[2:]\n"
            "if args[0] not in {'rev-parse','tag','cat-file','for-each-ref','ls-remote',"
            "'show','merge-base','log','rev-list','diff','blame'}"
            " or (args[0] == 'tag' and '--list' not in args):\n"
            "    sys.exit('forbidden git write: ' + repr(args))\n"
            f"os.execv({git_path!r}, [{git_path!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        gh = binaries / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"with open({str(calls)!r}, 'a') as f: f.write(json.dumps(['gh', *sys.argv[1:]]) + '\\n')\n"
            "args = sys.argv[1:]\n"
            "if any(x in args for x in ['POST','PUT','PATCH','DELETE','create']):\n"
            "    sys.exit('forbidden GitHub write')\n"
            "if any('/status?' in x for x in args):\n"
            "    print(json.dumps({'state':'success','statuses':[{'state':'success'}]}))\n"
            "elif any('/check-runs?' in x for x in args):\n"
            "    print(json.dumps([{'check_runs':[]}]))\n"
            "elif args[:2] == ['issue','list'] and os.environ.get('MISSING_FIX'):\n"
            "    print(json.dumps([{'number':42,'title':'engine bug','body':''}]))\n"
            "elif args[:2] == ['pr','list'] and os.environ.get('MISSING_FIX'):\n"
            "    print(json.dumps([{'mergeCommit':{'oid':os.environ['MISSING_FIX']},"
            "'closingIssuesReferences':[{'number':42}]}]))\n"
            "else: print('[]')\n"
        )
        gh.chmod(0o755)
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\n[release]\nmin_tickets = 1\n'
            'bar_min_tickets = 1\nwindow_tickets = 2\nexperimental_min_interval_hours = 0\n'
        )
        self.event(at="2099-01-01T00:00:00Z", event="merged", ticket=1)
        self.event(at="2099-01-01T00:00:00Z", event="attempt", ticket=1, attempt=1, gate="PASS")
        # A remote-only tag must not be downloaded into the dry-run checkout.
        self.git(self.origin, "tag", "v0.4.99", "main")

        def snapshot() -> dict[str, tuple[bytes, int]]:
            return {
                str(path.relative_to(self.repo)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.repo.rglob("*") if path.is_file()
            }

        for scenario in ("initial", "candidate", "legacy"):
            with self.subTest(scenario=scenario):
                legacy = scenario == "legacy"
                if scenario == "candidate":
                    self.make_odd()
                    (self.repo / "after-odd.txt").write_text("work after odd tag\n")
                    self.git(self.repo, "add", "after-odd.txt")
                    self.git(self.repo, "commit", "-q", "-m", "next experiment")
                    self.git(self.repo, "push", "-q", "origin", "main")
                if legacy:
                    self.git(self.repo, "tag", "-d", "v0.4.6", "v0.4.7")
                    self.git(self.repo, "push", "-q", "origin",
                             ":refs/tags/v0.4.6", ":refs/tags/v0.4.7")
                    # Stay at the legacy tag's version with changes eligible for a PR.
                    (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.4.5"\n')
                    self.git(self.repo, "add", "factory/__init__.py")
                    self.git(self.repo, "commit", "-q", "-m", "legacy fixture")
                    self.git(self.repo, "push", "-q", "origin", "main")
                before = snapshot()
                env = {
                    **os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                    "PYTHONPATH": str(Path(release.__file__).resolve().parent.parent),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONSAFEPATH": "1",
                    "XDG_CACHE_HOME": str(self.base / "cache"),
                    "XDG_STATE_HOME": str(self.base / "state"),
                    "XDG_RUNTIME_DIR": str(self.base / "runtime"),
                    "MISSING_FIX": "f" * 40 if scenario == "candidate" else "",
                }
                proc = subprocess.run(
                    [sys.executable, "-m", "factory", "release", "--dry-run"],
                    cwd=self.repo, env=env, text=True, capture_output=True, check=True,
                )
                report = json.loads(proc.stdout)
                verdict = report["verdict"] if legacy else report["odd"]["verdict"]
                self.assertIn("would open", verdict, proc.stderr)
                if scenario == "candidate":
                    self.assertEqual(report["even"]["candidate"], "v0.4.7")
                    self.assertIsNone(report["even"]["metrics"]["escape_rate"])
                    self.assertEqual(report["even"]["verdict"], "escape evidence is unavailable")
                self.assertEqual(snapshot(), before)
        recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertFalse(any("fetch" in args for args in recorded))
        self.assertFalse(any(
            any(arg in {"POST", "PUT", "PATCH", "DELETE", "create"} for arg in args)
            for args in recorded if args[0] == "gh"
        ))

    def test_dry_run_escape_scan_never_fetches_missing_fix_commits(self) -> None:
        self.make_odd()
        fix = "f" * 40

        def gh(*args: str) -> list[dict]:
            if args[:2] == ("issue", "list"):
                return [{"number": 42, "title": "engine bug", "body": ""}]
            self.assertEqual(args[:2], ("pr", "list"))
            return [{"mergeCommit": {"oid": fix}, "closingIssuesReferences": [{"number": 42}]}]

        with mock.patch.object(stats, "gh", side_effect=gh), \
                mock.patch.object(stats, "present", wraps=stats.present) as present:
            metrics = release.measure_window(self.cfg, read_only=True)
        self.assertIsNone(metrics["escape_rate"])
        present.assert_called_once_with(fix, fetch=False)

    def test_public_notes_collect_all_unpromoted_odd_sections(self) -> None:
        self.make_odd()
        changelog = self.repo / "CHANGELOG.md"
        changelog.write_text(changelog.read_text().replace(
            "## Unreleased\n\n", "## Unreleased\n\n- second experiment\n\n", 1,
        ))
        release._update_experimental_files(self.cfg, self.repo, "0.4.7", "0.4.9")
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.4.9")
        sha = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "tag", "-a", "v0.4.9", "-m", "Experimental release 0.4.9")
        self.git(self.repo, "push", "-q", "origin", "main", "v0.4.9")
        self.event(at=lifecycle._now(), event="lifecycle", stage="dispatcher", kind="enter",
                   engine_commit=sha)
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True), \
                mock.patch.object(stats, "escapes", return_value=[]), \
                mock.patch.object(release, "_gh_release", side_effect=[
                    self.gh_result(1), self.gh_result(0),
                ]) as gh:
            self.assertIn("built promotion branch release/v0.4.10", release.run(self.cfg))
            self.assertIn("released v0.4.10", release.run(self.cfg))
        notes = gh.call_args.kwargs["notes"]
        self.assertIn("- experimental work", notes)
        self.assertIn("- second experiment", notes)
        self.assertNotIn("- public work", notes)

    def test_in_flight_odd_pr_blocks_building_a_public_promotion(self) -> None:
        self.make_odd()
        pull = {"number": 19, "mergeable": None, "mergeable_state": "unknown"}
        with mock.patch.object(release, "_pulls", side_effect=lambda cfg, branch: (
            [{"number": 19}] if branch == "release/v0.4.9" else []
        )), mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "_build_promotion", return_value="unexpected") as built, \
                mock.patch.object(stats, "escapes", return_value=[]):
            reason = release.run(self.cfg)
        self.assertIn("waiting for GitHub mergeability", reason)
        built.assert_not_called()


if __name__ == "__main__":
    unittest.main()
