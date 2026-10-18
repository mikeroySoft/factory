"""Automatic release quality bar and atomic git publication."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, release


class ReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.new_repo()

    def new_repo(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.origin = self.base / "origin.git"
        self.repo = self.base / "repo"
        self.git(self.base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(self.base, "clone", "-q", str(self.origin), str(self.repo))
        self.git(self.repo, "config", "user.name", "Release Test")
        self.git(self.repo, "config", "user.email", "release@example.invalid")
        (self.repo / "factory").mkdir()
        (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.0.1"\n')
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- shipped work\n")
        (self.repo / ".gitignore").write_text(".factory/\n")
        (self.repo / ".factory.toml").write_text('[repo]\nslug = "acme/widgets"\n')
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-q", "-m", "initial")
        self.cfg = config.Config(root=self.repo, repo="acme/widgets", release_min_tickets=1)

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                              capture_output=True).stdout.strip()

    def tag_and_advance(self, bar: dict | None) -> None:
        message = "Release 0.0.1"
        if bar is not None:
            message += f"\n\n```json\n{json.dumps(bar, separators=(',', ':'))}\n```"
        self.git(self.repo, "tag", "-a", "v0.0.1", "-m", message)
        self.git(self.repo, "push", "-q", "origin", "main", "v0.0.1")
        (self.repo / "work.txt").write_text("change\n")
        self.git(self.repo, "add", "work.txt")
        self.git(self.repo, "commit", "-q", "-m", "work")
        self.git(self.repo, "push", "-q", "origin", "main")

    def events(self, *, human: bool = False, tickets: int = 1) -> None:
        path = self.cfg.factory / "events.jsonl"
        for ticket in range(1, tickets + 1):
            lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "claimed", "ticket": ticket})
            lifecycle.append(path, {"at": "2099-01-01T00:00:01Z", "event": "attempt", "ticket": ticket,
                                    "attempt": 1, "gate": "PASS"})
            lifecycle.append(path, {"at": "2099-01-01T00:00:02Z", "event": "merged", "ticket": ticket})
        if human:
            lifecycle.append(path, {"at": "2099-01-01T00:00:03Z", "event": "manage", "ticket": 1,
                                    "decision": "HUMAN"})

    def verdict(self) -> tuple[str, dict]:
        output = io.StringIO()
        with mock.patch.object(release, "ci_passes", return_value=True), redirect_stdout(output):
            reason = release.run(self.cfg, dry_run=True)
        return reason, json.loads(output.getvalue())

    def test_release_config_defaults_and_values(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.base / "config")}):
            loaded = config.load(self.repo)
            self.assertEqual(
                (loaded.release_auto, loaded.release_version_file, loaded.release_min_tickets),
                (False, "factory/__init__.py", 5),
            )
            (self.repo / ".factory.toml").write_text(
                '[repo]\nslug = "acme/widgets"\n\n[release]\nauto = true\n'
                'version_file = "src/version.py"\nmin_tickets = 9\n'
            )
            loaded = config.load(self.repo)
            self.assertEqual(
                (loaded.release_auto, loaded.release_version_file, loaded.release_min_tickets),
                (True, "src/version.py", 9),
            )

    def test_bar_comparison_is_directional_and_missing_bar_resets_it(self) -> None:
        cases = (
            ({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1}, True, "human_rate", False),
            ({"human_rate": 1.0, "first_pass": 1.0, "tickets": 1}, True, "would release", True),
            ({"human_rate": 0.5, "first_pass": 0.5, "tickets": 1}, False, "would release", True),
            (None, True, "would release", True),
        )
        for bar, human, expected, eligible in cases:
            with self.subTest(bar=bar, human=human):
                self.new_repo()
                self.tag_and_advance(bar)
                self.events(human=human)
                before = self.git(self.origin, "rev-parse", "main")
                reason, report = self.verdict()
                self.assertIn(expected, reason)
                self.assertEqual("would release" in report["verdict"], eligible)
                self.assertEqual(report["bar"], bar)
                self.assertEqual(self.git(self.origin, "rev-parse", "main"), before)
                self.assertFalse((self.cfg.factory / "events.jsonl").read_text().count('"event":"release"'))

    def test_end_to_end_release_and_second_run_is_noop(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        with mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg, dry_run=False)
            second = release.run(self.cfg, dry_run=False)
        self.assertIn("released v0.0.2", reason)
        self.assertIn("no changes since v0.0.2", second)
        main = self.git(self.origin, "rev-parse", "main")
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), main)
        self.assertEqual(self.git(self.origin, "rev-parse", "v0.0.2^{}"), main)
        self.assertIn('__version__ = "0.0.2"', self.git(self.origin, "show", "main:factory/__init__.py"))
        changelog = self.git(self.origin, "show", "main:CHANGELOG.md")
        self.assertRegex(changelog, r"## Unreleased\n\n## 0\.0\.2 — \d{4}-\d{2}-\d{2}\n\n- shipped work")
        annotation = self.git(self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.0.2")
        match = release.JSON_BAR.search(annotation)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1)), {"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})

    def test_stale_local_tag_does_not_block_release(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        stale = self.git(self.repo, "commit-tree", "HEAD^{tree}", "-p", "v0.0.1^{commit}", "-m", "crashed release")
        self.git(self.repo, "tag", "-a", "v0.0.2", stale, "-m", "stale")
        with mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg)
        self.assertIn("released v0.0.2", reason)
        self.assertEqual(self.git(self.origin, "rev-parse", "v0.0.2^{}"),
                         self.git(self.origin, "rev-parse", "main"))

    def test_human_rate_counts_distinct_tickets_and_is_clamped(self) -> None:
        path = self.cfg.factory / "events.jsonl"
        for ticket in (1, 1, 2):
            lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "manage",
                                    "ticket": ticket, "decision": "HUMAN"})
        lifecycle.append(path, {"at": "2099-01-01T00:00:01Z", "event": "merged", "ticket": 1})
        measured = release.measure(self.cfg, "2098-01-01T00:00:00")
        self.assertEqual(measured["human_rate"], 1.0)

    def test_no_first_attempt_evidence_holds(self) -> None:
        self.tag_and_advance(None)
        lifecycle.append(self.cfg.factory / "events.jsonl",
                         {"at": "2099-01-01T00:00:00Z", "event": "merged", "ticket": 1})
        reason, report = self.verdict()
        self.assertIn("no first-attempt evidence since v0.0.1", reason)
        self.assertIsNone(report["metrics"]["first_pass"])

    def test_skipped_check_run_passes_ci(self) -> None:
        status = {"state": "success", "statuses": []}
        pages = [{"check_runs": [{"status": "completed", "conclusion": "skipped"}]}]
        with mock.patch.object(release, "_gh", side_effect=(status, pages)) as gh:
            self.assertTrue(release.ci_passes(self.cfg, "abc"))
        self.assertEqual(gh.call_args_list[1].args[2:], ("--paginate", "--slurp"))

    def test_below_minimum_tickets_holds(self) -> None:
        self.cfg.release_min_tickets = 2
        self.tag_and_advance(None)
        self.events(tickets=1)
        before = self.git(self.origin, "rev-parse", "main")
        with mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg, dry_run=False)
        self.assertIn("tickets 1 below min_tickets 2", reason)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), before)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertNotIn("release", {row["event"] for row in rows})

    def test_bar_rises_halfway_and_never_past_the_release(self) -> None:
        measured = {"human_rate": 0.2, "first_pass": 1.0, "tickets": 17}
        first = release.next_bar(None, measured)
        self.assertEqual(first, {"human_rate": 0.6, "first_pass": 0.5, "tickets": 17})
        second = release.next_bar(first, measured)
        self.assertEqual(second, {"human_rate": 0.4, "first_pass": 0.75, "tickets": 17})


if __name__ == "__main__":
    unittest.main()
