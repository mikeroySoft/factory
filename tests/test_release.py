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
        self.write_release_docs()
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
        with mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_pulls", return_value=[]), redirect_stdout(output):
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
            ({"human_rate": 1.0, "first_pass": 1.0, "tickets": 1}, True, "would open", True),
            ({"human_rate": 0.5, "first_pass": 0.5, "tickets": 1}, False, "would open", True),
            (None, True, "would open", True),
        )
        for bar, human, expected, eligible in cases:
            with self.subTest(bar=bar, human=human):
                self.new_repo()
                self.tag_and_advance(bar)
                self.events(human=human)
                before = self.git(self.origin, "rev-parse", "main")
                reason, report = self.verdict()
                self.assertIn(expected, reason)
                self.assertEqual("would open" in report["verdict"], eligible)
                self.assertEqual(report["bar"], bar)
                self.assertEqual(self.git(self.origin, "rev-parse", "main"), before)
                self.assertFalse((self.cfg.factory / "events.jsonl").read_text().count('"event":"release"'))

    def test_candidate_opens_release_pr_without_moving_main(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        before = self.git(self.origin, "rev-parse", "main")
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 12}) as opened, \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg)
        self.assertIn("opened release PR #12 for v0.0.2", reason)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), before)
        self.assertIn('__version__ = "0.0.2"',
                      self.git(self.origin, "show", "release/v0.0.2:factory/__init__.py"))
        self.assertRegex(self.git(self.origin, "show", "release/v0.0.2:CHANGELOG.md"),
                         r"## Unreleased\n\n## 0\.0\.2 — \d{4}-\d{2}-\d{2}\n\n- shipped work")
        self.assertEqual(opened.call_args.args[1:3], ("release/v0.0.2", "Release 0.0.2"))

    def test_start_of_pass_call_never_opens_a_release_pr(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull") as opened, \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg, open_pr=False)
        self.assertIn("no release PR open", reason)
        opened.assert_not_called()
        self.assertNotIn("release/v0.0.2", self.git(self.origin, "branch", "--list", "release/*"))

    def test_pending_green_pr_is_squash_merged(self) -> None:
        self.tag_and_advance(None)
        pull = {"number": 7, "head": {"sha": "abc"}, "mergeable": True,
                "mergeable_state": "clean"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_merge_pull", return_value={"merged": True}) as merged:
            reason = release.run(self.cfg, open_pr=False)
        self.assertIn("merged release PR #7", reason)
        merged.assert_called_once_with(self.cfg, 7, "Release 0.0.2")

    def test_pending_behind_pr_updates_branch_without_merging(self) -> None:
        self.tag_and_advance(None)
        pull = {"number": 7, "head": {"sha": "abc"}, "mergeable": True,
                "mergeable_state": "behind"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "_gh", return_value={}) as gh, \
                mock.patch.object(release, "_merge_pull") as merged:
            reason = release.run(self.cfg)
        self.assertIn("updated release PR #7 from main", reason)
        gh.assert_called_once_with(
            self.cfg, "repos/acme/widgets/pulls/7/update-branch", "-X", "PUT")
        merged.assert_not_called()

    def test_pending_dirty_pr_is_closed_and_branch_deleted(self) -> None:
        self.tag_and_advance(None)
        pull = {"number": 7, "head": {"sha": "abc"}, "mergeable": False,
                "mergeable_state": "dirty"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "_gh", return_value={}) as gh, \
                mock.patch.object(release, "_merge_pull") as merged:
            reason = release.run(self.cfg)
        self.assertIn("closed conflicting release PR #7; next pass rebuilds it", reason)
        self.assertEqual(
            gh.call_args_list,
            [
                mock.call(self.cfg, "repos/acme/widgets/pulls/7", "-X", "PATCH",
                          "-f", "state=closed"),
                mock.call(self.cfg, "repos/acme/widgets/git/refs/heads/release/v0.0.2",
                          "-X", "DELETE"),
            ],
        )
        merged.assert_not_called()

    def test_pending_unknown_mergeability_holds_without_writes(self) -> None:
        self.tag_and_advance(None)
        pull = {"number": 7, "head": {"sha": "abc"}, "mergeable": None,
                "mergeable_state": "unknown"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "_gh") as gh, \
                mock.patch.object(release, "ci_passes") as ci, \
                mock.patch.object(release, "_merge_pull") as merged:
            reason = release.run(self.cfg)
        self.assertIn("waiting for GitHub mergeability", reason)
        gh.assert_not_called()
        ci.assert_not_called()
        merged.assert_not_called()

    def test_pending_merge_refusal_holds(self) -> None:
        self.tag_and_advance(None)
        pull = {"number": 7, "head": {"sha": "abc"}, "mergeable": True,
                "mergeable_state": "clean"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_merge_pull",
                                  side_effect=RuntimeError("HTTP 405: merge refused")):
            reason = release.run(self.cfg)
        self.assertIn("HTTP 405: merge refused", reason)

    def test_landed_tags_release_moves_stable_and_next_run_is_noop(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        release._update_release_files(self.cfg, self.repo, "0.0.1", "0.0.2")
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.0.2")
        landed = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", "-q", "origin", "HEAD:release/v0.0.2")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        self.git(self.repo, "tag", "-a", "v0.0.2", landed, "-m", "local only")
        self.enterContext(mock.patch.object(release, "_gh_release", return_value=subprocess.CompletedProcess([], 1, "", "")))
        reason = release.run(self.cfg)
        second = release.run(self.cfg)
        self.assertIn("released v0.0.2", reason)
        self.assertIn("no changes since v0.0.2", second)
        self.assertEqual(self.git(self.origin, "rev-parse", "stable"), landed)
        self.assertEqual(self.git(self.origin, "rev-parse", "v0.0.2^{}"), landed)
        annotation = self.git(self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.0.2")
        self.assertIsNotNone(release.JSON_BAR.search(annotation))

    def test_dry_run_writes_nothing_in_candidate_pending_and_landed_states(self) -> None:
        self.tag_and_advance(None)
        self.events()
        before = self.git(self.origin, "show-ref")
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True):
            self.assertIn("would open release PR", release.run(self.cfg, dry_run=True))
        pull = {"number": 8, "head": {"sha": "abc"}, "mergeable": True,
                "mergeable_state": "clean"}
        with mock.patch.object(release, "_pulls", return_value=[{"number": 8}]), \
                mock.patch.object(release, "_pull", return_value=pull), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_merge_pull") as merged:
            self.assertIn("would squash-merge", release.run(self.cfg, dry_run=True))
            merged.assert_not_called()
        (self.repo / "factory" / "__init__.py").write_text('__version__ = "0.0.2"\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.0.2")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        landed_before = self.git(self.origin, "show-ref")
        self.assertIn("would tag v0.0.2", release.run(self.cfg, dry_run=True))
        self.assertEqual(self.git(self.origin, "show-ref"), landed_before)
        self.assertNotEqual(before, landed_before)

    def test_human_rate_counts_each_touch_without_a_cap(self) -> None:
        path = self.cfg.factory / "events.jsonl"
        for ticket in (1, 1, 2):
            lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "manage",
                                    "ticket": ticket, "decision": "HUMAN"})
        lifecycle.append(path, {"at": "2099-01-01T00:00:01Z", "event": "merged", "ticket": 1})
        measured = release.measure(self.cfg, "2098-01-01T00:00:00")
        self.assertEqual((measured["human_rate"], measured["human_touches"]), (3.0, 3))

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
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg, dry_run=False)
        self.assertIn("tickets 1 below min_tickets 2", reason)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), before)
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertNotIn("release", {row["event"] for row in rows})

    def test_bar_moves_halfway_toward_wilson_bounds(self) -> None:
        old = {"human_rate": 0.2, "first_pass": 0.8, "tickets": 17}
        measured = {
            "human_rate": 0.0, "human_touches": 0, "human_ticket_rate": 0.0,
            "first_pass": 1.0, "first_passes": 20, "attempts": 20, "tickets": 20,
        }
        result = release.next_bar(old, measured, bar_min_tickets=20)
        self.assertEqual((result["human_rate"], result["first_pass"]), (0.180563, 0.819437))

    def land_release(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        release._update_release_files(self.cfg, self.repo, "0.0.1", "0.0.2")
        self.git(self.repo, "add", "factory/__init__.py", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "Release 0.0.2")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")

    def release_rows(self) -> list[tuple[str, str]]:
        return [(row["outcome"], row["tag"]) for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                if row["event"] == "release"]

    @staticmethod
    def gh_result(code: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, "", stderr)

    def test_tag_publishes_github_release_with_changelog_section(self) -> None:
        self.land_release()
        with mock.patch.object(release, "_gh_release",
                               side_effect=[self.gh_result(1, "release not found"), self.gh_result(0)]) as gh:
            reason = release.run(self.cfg)
        self.assertIn("released v0.0.2", reason)
        self.assertEqual(gh.call_args_list, [
            mock.call(self.cfg, "view", "v0.0.2"),
            mock.call(self.cfg, "create", "v0.0.2", "--verify-tag", "--latest", "--title", "v0.0.2",
                      "--notes-file", "-", notes="- shipped work"),
        ])
        self.assertEqual(self.release_rows(), [("released", "v0.0.2"), ("published", "v0.0.2")])
        with mock.patch.object(release, "_gh_release") as gh:
            self.assertIn("no changes since v0.0.2", release.run(self.cfg))
        gh.assert_not_called()

    def test_existing_github_release_is_left_alone(self) -> None:
        self.land_release()
        with mock.patch.object(release, "_gh_release", return_value=self.gh_result(0)) as gh:
            self.assertIn("released v0.0.2", release.run(self.cfg))
        gh.assert_called_once_with(self.cfg, "view", "v0.0.2")
        self.assertEqual(self.release_rows(), [("released", "v0.0.2"), ("published", "v0.0.2")])

    def test_failed_github_release_is_journaled_and_retried_next_pass(self) -> None:
        self.land_release()
        with mock.patch.object(release, "_gh_release",
                               side_effect=[self.gh_result(1), self.gh_result(1, "HTTP 502")]):
            reason = release.run(self.cfg)
        self.assertIn("released v0.0.2", reason)
        self.assertIn("HTTP 502", reason)
        self.assertTrue(self.git(self.origin, "rev-parse", "v0.0.2"))
        self.assertEqual(self.release_rows(), [("released", "v0.0.2"), ("publish_failed", "v0.0.2")])
        with mock.patch.object(release, "_gh_release",
                               side_effect=[self.gh_result(1), self.gh_result(0)]) as gh:
            self.assertIn("no changes since v0.0.2", release.run(self.cfg))
        self.assertEqual(gh.call_args_list[1].args[1:3], ("create", "v0.0.2"))
        self.assertEqual(self.release_rows()[-1], ("published", "v0.0.2"))
        with mock.patch.object(release, "_gh_release") as gh:
            release.run(self.cfg)
        gh.assert_not_called()

    def write_release_docs(self) -> None:
        (self.repo / "README.md").write_text(
            "```sh\n"
            "uv tool install git+https://github.com/mikeroySoft/factory@stable   # stable\n"
            "uv tool install git+https://github.com/mikeroySoft/factory@v0.0.1   # a specific release\n"
            "```\n"
        )
        (self.repo / "docs").mkdir(exist_ok=True)
        (self.repo / "docs" / "index.html").write_text(
            "<code>uv tool install git+https://github.com/mikeroySoft/factory@v0.0.1</code>\n"
            '<p class="release-note"><a href="#changelog">'
            "Latest release: v0.0.1 · January 1, 2026</a></p>\n"
            '<ol class="history">\n'
            '  <li class="release">\n'
            '    <span class="chip chip-success">Latest release</span>\n'
            '    <a href="/v0.0.1">v0.0.1</a>\n'
            "  </li>\n"
            "</ol>\n"
        )

    def test_release_bump_updates_and_stages_public_documentation(self) -> None:
        with (self.repo / "CHANGELOG.md").open("a") as changelog:
            changelog.write(r"- matches \d and C:\new" + "\n")
        self.git(self.repo, "add", "CHANGELOG.md")
        self.git(self.repo, "commit", "-q", "-m", "add release notes")
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "_open_pull", return_value={"number": 12}), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "gate_passes", return_value=True):
            reason = release.run(self.cfg)
        self.assertIn("opened release PR #12 for v0.0.2", reason)
        readme = self.git(self.origin, "show", "release/v0.0.2:README.md")
        page = self.git(self.origin, "show", "release/v0.0.2:docs/index.html")
        changelog = self.git(self.origin, "show", "release/v0.0.2:CHANGELOG.md")
        version = self.git(self.origin, "show", "release/v0.0.2:factory/__init__.py")
        self.assertIn('__version__ = "0.0.2"', version)
        self.assertRegex(changelog, r"## 0\.0\.2 — \d{4}-\d{2}-\d{2}")
        self.assertIn("factory@stable", readme)
        self.assertIn("factory@v0.0.2   # a specific release", readme)
        self.assertIn("factory@v0.0.2</code>", page)
        self.assertRegex(page, r"Latest release: v0\.0\.2 · [A-Z][a-z]+ \d{1,2}, \d{4}")
        self.assertLess(page.index(">v0.0.2</a>"), page.index(">v0.0.1</a>"))
        self.assertIn("<li>shipped work</li>", page)
        self.assertIn(r"<li>matches \d and C:\new</li>", page)
        self.assertEqual(page.count('<span class="chip chip-success">Latest release</span>'), 1)

    def test_release_bump_fails_when_a_documentation_spot_is_missing(self) -> None:
        missing_spots = {
            "README.md specific-release install pin": (
                "README.md", "factory@v0.0.1", "factory@main"
            ),
            "docs/index.html install-command version pin": (
                "docs/index.html", "factory@v0.0.1</code>", "factory@main</code>"
            ),
            "docs/index.html latest-release line": (
                "docs/index.html", "Latest release:", "Current release:"
            ),
            "docs/index.html changelog history": (
                "docs/index.html", '<ol class="history">', '<ol class="releases">'
            ),
        }
        for expected, (relative, present, replacement) in missing_spots.items():
            with self.subTest(expected=expected):
                self.new_repo()
                self.write_release_docs()
                path = self.repo / relative
                path.write_text(path.read_text().replace(present, replacement))
                with self.assertRaisesRegex(ValueError, expected):
                    release._update_release_files(self.cfg, self.repo, "0.0.1", "0.0.2")


if __name__ == "__main__":
    unittest.main()
