"""Release source, version, and channel boundary guards."""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from factory import release
from tests import test_release as fixtures


class ReleaseSourceGuardsTest(unittest.TestCase):
    new_repo = fixtures.ReleaseTest.new_repo
    git = fixtures.ReleaseTest.git
    write_release_docs = fixtures.ReleaseTest.write_release_docs
    tag_and_advance = fixtures.ReleaseTest.tag_and_advance
    release_commit = fixtures.ReleaseTest.release_commit
    events = fixtures.ReleaseTest.events

    def setUp(self) -> None:
        self.new_repo()

    def test_promotion_source_requires_exact_version_replacement_and_no_source_changes(self) -> None:
        cases = (
            ("exact replacement", '__version__ = "0.2.4"\n', None, True),
            ("lexically lower version", '__version__ = "0.2.2"\n', None, False),
            ("lexically greater version", '__version__ = "0.2.5"\n', None, False),
            ("extra edit in allowed version file", '__version__ = "0.2.4"\n# arbitrary\n', None, False),
            ("source change", '__version__ = "0.2.4"\n', "factory/runtime.py", False),
        )
        for label, after, source, accepted in cases:
            with self.subTest(label=label):
                self.new_repo()
                version = self.repo / "factory" / "__init__.py"
                version.write_text('__version__ = "0.2.3"\n')
                self.git(self.repo, "add", "factory/__init__.py")
                self.git(self.repo, "commit", "-q", "-m", "baseline")
                baseline = self.git(self.repo, "rev-parse", "HEAD")

                version.write_text(after)
                if source:
                    (self.repo / source).write_text("changed = True\n")
                self.git(self.repo, "add", "factory")
                self.git(self.repo, "commit", "-q", "-m", label)
                candidate = self.git(self.repo, "rev-parse", "HEAD")

                latest = {"sha": baseline, "version": (0, 2, 3)}
                self.assertIs(release._promotion_source(self.cfg, latest, candidate), accepted)

    def test_release_pr_query_selects_repository_owner_not_repository_name(self) -> None:
        expected = [{"number": 42, "head": {"ref": "release/v0.2.3"}}]
        for state in ("open", "closed"):
            endpoint = (f"repos/acme/widgets/pulls?state={state}&base=main"
                        "&head=acme:release/v0.2.3")
            with self.subTest(state=state), mock.patch.object(
                subprocess, "run", side_effect=lambda command, endpoint=endpoint, **kw: subprocess.CompletedProcess(
                    command, 0, json.dumps(expected if command[-1] == endpoint else []), ""),
            ):
                self.assertEqual(release._pulls(self.cfg, "release/v0.2.3", state=state), expected)

    def test_publish_channel_uses_patch_parity_not_minor_parity(self) -> None:
        cases = (
            ("v0.2.3", ("--prerelease", "--latest=false")),
            ("v0.3.4", ("--latest",)),
        )
        for tag, channel in cases:
            with self.subTest(tag=tag), \
                    mock.patch.object(
                        release,
                        "_gh_release",
                        side_effect=(
                            subprocess.CompletedProcess([], 1, "", "not found"),
                            subprocess.CompletedProcess([], 0, "", ""),
                        ),
                    ) as gh, \
                    mock.patch.object(release, "_notes", return_value="notes"), \
                    mock.patch.object(release, "_record"):
                release._publish(self.cfg, tag)

                self.assertEqual(
                    gh.call_args_list,
                    [
                        mock.call(self.cfg, "view", tag),
                        mock.call(
                            self.cfg,
                            "create",
                            tag,
                            "--verify-tag",
                            *channel,
                            "--title",
                            tag,
                            "--notes-file",
                            "-",
                            notes="notes",
                        ),
                    ],
                )

    def test_release_file_updates_use_patch_parity_not_minor_parity(self) -> None:
        cases = (("0.2.2", "0.2.3", False), ("0.3.3", "0.3.4", True))
        for old, new, public in cases:
            with self.subTest(old=old, new=new):
                self.new_repo()
                version = self.repo / "factory" / "__init__.py"
                readme = self.repo / "README.md"
                page = self.repo / "docs" / "index.html"
                version.write_text(f'__version__ = "{old}"\n')
                changelog = self.repo / "CHANGELOG.md"
                changelog.write_text(
                    f"# Changelog\n\n## Unreleased\n\n## {old} — 2026-11-01\n\n- prior notes\n",
                )
                readme.write_text(readme.read_text().replace("0.0.1", old))
                page.write_text(page.read_text().replace("0.0.1", old))
                original_docs = (readme.read_text(), page.read_text())

                release._update_release_files(self.cfg, self.repo, old, new)

                self.assertEqual(version.read_text(), f'__version__ = "{new}"\n')
                self.assertIn(f"## {new} — ", (self.repo / "CHANGELOG.md").read_text())
                section = changelog.read_text().split(f"## {new} — ", 1)[1].split("\n## ", 1)[0]
                self.assertEqual("- prior notes" in section, public)
                if public:
                    self.assertIn(f"factory@v{new}   # a specific release", readme.read_text())
                    self.assertIn(f"factory@v{new}</code>", page.read_text())
                    self.assertIn(f"Latest release: v{new} · ", page.read_text())
                else:
                    self.assertEqual((readme.read_text(), page.read_text()), original_docs)

    def test_even_baseline_opens_next_odd_with_patch_beyond_two(self) -> None:
        version = self.repo / "factory" / "__init__.py"
        version.write_text('__version__ = "0.0.4"\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-qm", "public release")
        bar = {"human_rate": 0.0, "first_pass": 1.0, "tickets": 1}
        self.git(self.repo, "tag", "-a", "v0.0.4", "-m", f"Release\n```json\n{json.dumps(bar)}\n```")
        self.git(self.repo, "push", "-q", "origin", "main", "v0.0.4")
        (self.repo / "work.txt").write_text("new implementation\n")
        self.git(self.repo, "add", "work.txt")
        self.git(self.repo, "commit", "-qm", "implementation")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.events()
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                redirect_stdout(io.StringIO()):
            self.assertIn("would open release PR for v0.0.5", release.run(self.cfg, dry_run=True))

    def test_baseline_version_file_must_equal_tag_above_or_below(self) -> None:
        bar = {"human_rate": 0.0, "first_pass": 1.0, "tickets": 1}
        for tag in ("v0.0.0", "v0.0.2"):
            with self.subTest(tag=tag):
                self.new_repo()
                self.git(
                    self.repo,
                    "tag",
                    "-a",
                    tag,
                    "-m",
                    f"Release\n\n```json\n{json.dumps(bar)}\n```",
                )
                self.git(self.repo, "push", "-q", "origin", "main", tag)

                with redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                    release.ReleaseError, rf"baseline {tag} does not match its version file"
                ):
                    release.run(self.cfg, dry_run=True)

    def test_merged_release_pr_requires_equal_sha_not_lexical_order(self) -> None:
        bar = {"human_rate": 0.0, "first_pass": 1.0, "tickets": 1}
        for wrong_sha in ("0" * 40, "f" * 40):
            with self.subTest(wrong_sha=wrong_sha):
                self.new_repo()
                self.tag_and_advance(bar)
                commit = self.release_commit("0.0.3")
                self.git(self.repo, "push", "-q", "origin", "HEAD:main")
                self.assertNotEqual(commit, wrong_sha)

                pulls = [{
                    "merged_at": "2026-01-01T00:00:00Z",
                    "merge_commit_sha": wrong_sha,
                }]
                with mock.patch.object(release, "_pulls", return_value=pulls), \
                        redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                            release.ReleaseError, "has no matching merged release PR"
                        ):
                    release.run(self.cfg, dry_run=True)

    def test_pending_pr_rejects_both_lower_and_higher_version_files(self) -> None:
        for version in ("0.0.2", "0.0.4"):
            with self.subTest(version=version):
                self.new_repo()
                self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
                self.events()
                (self.repo / "factory" / "__init__.py").write_text(f'__version__ = "{version}"\n')
                self.git(self.repo, "add", "factory/__init__.py")
                self.git(self.repo, "commit", "-qm", "wrong release PR version")
                sha = self.git(self.repo, "rev-parse", "HEAD")
                pull = {"number": 12, "head": {"sha": sha}}
                with mock.patch.object(release, "_pulls", return_value=[pull]), \
                        mock.patch.object(release, "_pull", return_value=pull), \
                        self.assertRaisesRegex(release.ReleaseError, "version mismatch in release PR"):
                    release.run(self.cfg)

    def test_provenanced_release_commit_must_match_expected_version_in_both_directions(self) -> None:
        for wrong in ("0.0.2", "0.0.4"):
            with self.subTest(version=wrong):
                self.new_repo()
                self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
                version_file = self.repo / "factory" / "__init__.py"
                version_file.write_text(f'__version__ = "{wrong}"\n# target 0.0.3\n')
                self.git(self.repo, "add", "factory/__init__.py")
                self.git(self.repo, "commit", "-qm", "purported release with wrong version")
                purported = self.git(self.repo, "rev-parse", "HEAD")
                version_file.write_text('__version__ = "0.0.3"\n')
                self.git(self.repo, "add", "factory/__init__.py")
                self.git(self.repo, "commit", "-qm", "later version repair")
                self.git(self.repo, "push", "-q", "origin", "main")
                pulls = [{"merged_at": "2026-01-01T00:00:00Z", "merge_commit_sha": purported}]
                with mock.patch.object(release, "_pulls", return_value=pulls), \
                        self.assertRaisesRegex(release.ReleaseError, "version mismatch at release commit"):
                    release.run(self.cfg)

    def test_minimum_ticket_boundary_uses_counts_not_integer_identity(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.cfg.release_min_tickets = 257
        self.events(tickets=257)
        with mock.patch.object(release, "_pulls", return_value=[]), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                redirect_stdout(io.StringIO()):
            self.assertIn("would open release PR for v0.0.3", release.run(self.cfg, dry_run=True))

    def test_promotion_source_changes_only_the_first_version_occurrence(self) -> None:
        version_file = self.repo / "factory" / "__init__.py"
        version_file.write_text('__version__ = "0.0.1"\n# previous 0.0.1\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-qm", "baseline with a historical version")
        baseline = self.git(self.repo, "rev-parse", "HEAD")
        version_file.write_text('__version__ = "0.0.2"\n# previous 0.0.1\n')
        self.git(self.repo, "add", "factory/__init__.py")
        self.git(self.repo, "commit", "-qm", "promotion")
        candidate = self.git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(release._promotion_source(
            self.cfg, {"sha": baseline, "version": (0, 0, 1)}, candidate,
        ))

    def test_experimental_legacy_bar_reset_is_retained_in_the_next_tag(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        self.events()
        sha = self.release_commit("0.0.3")
        self.git(self.repo, "push", "-q", "origin", "main")
        pulls = [{"merged_at": "2026-01-01T00:00:00Z", "merge_commit_sha": sha}]
        with mock.patch.object(release, "_pulls", return_value=pulls), \
                mock.patch.object(release, "ci_passes", return_value=True), \
                mock.patch.object(release, "_gh_release", return_value=subprocess.CompletedProcess(
                    [], 0, "", "",
                )):
            self.assertIn("released v0.0.3", release.run(self.cfg))
        annotation = self.git(self.origin, "for-each-ref", "--format=%(contents)", "refs/tags/v0.0.3")
        bar = release._bar(annotation)
        self.assertEqual(bar["human_rate"], self.cfg.release_bar_floor_human_rate)
        self.assertEqual(bar["first_pass"], self.cfg.release_bar_floor_first_pass)

    def test_automatic_pass_recognizes_promotion_after_first_odd_patch(self) -> None:
        self.tag_and_advance({"human_rate": 0.0, "first_pass": 1.0, "tickets": 1})
        sha = self.release_commit("0.0.2")
        self.git(self.repo, "push", "-q", "origin", "main")
        pulls = [{"merged_at": "2026-01-01T00:00:00Z", "merge_commit_sha": sha}]
        with mock.patch.object(release, "_pulls", return_value=pulls):
            self.assertIn("pending promotion of v0.0.1", release.run(self.cfg, open_pr=False))
        self.assertNotIn("v0.0.2", self.git(self.origin, "tag", "--list"))


if __name__ == "__main__":
    unittest.main()
