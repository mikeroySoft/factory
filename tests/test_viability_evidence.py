"""Bounded repository and GitHub evidence for viability recommendations."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from factory import briefing, config, evidence


class ViabilityEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("WidgetEngine repository overview\n")
        (self.root / "ROADMAP.md").write_text("WidgetEngine rollout is planned after the pilot.\n")
        (self.root / config.LESSONS_NAME).write_text("- WidgetEngine pilots need migration evidence.\n")
        (self.root / "src").mkdir()
        (self.root / "src/widget.py").write_text("class WidgetEngine:\n    pass\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Evidence Test", "-c", "user.email=evidence@example.invalid",
             "commit", "-qm", "repository evidence"],
            cwd=self.root,
            check=True,
        )
        (self.root / ".factory").mkdir()
        (self.root / ".factory/manager-7.md").write_text("Manager notes: preserve the pilot boundary.\n")
        (self.root / ".factory/manager").mkdir()
        (self.root / ".factory/manager/notes.md").write_text("Persistent manager constraints.\n")
        self.cfg = config.Config(root=self.root, repo="acme/widgets")
        self.calls: list[str] = []

    def github_read(self, endpoint: str, deadline: float, *, text: bool = False):
        self.calls.append(endpoint)
        path = endpoint.split("?", 1)[0]
        responses = {
            "repos/acme/widgets/issues/7": {"comments": 25},
            "repos/acme/widgets/issues/9": {"comments": 1},
            "repos/acme/widgets/issues/7/comments": [{
                "body": "A customer needs WidgetEngine compatibility.",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-02T00:00:00Z",
                "html_url": "https://github.com/acme/widgets/issues/7#issuecomment-1",
                "user": {"login": "customer"},
            }],
            "repos/acme/widgets/issues/9/comments": [{
                "body": "Keep the PR focused on rollout direction." + "x" * 1000,
                "created_at": "2026-09-03T00:00:00Z",
                "updated_at": "2026-09-03T00:00:00Z",
                "html_url": "https://github.com/acme/widgets/pull/9#issuecomment-2",
                "user": {"login": "maintainer"},
            }],
            "repos/acme/widgets/pulls/9/files": [{
                "filename": "src/widget.py", "status": "modified", "additions": 8,
                "deletions": 2, "changes": 10, "patch": "unbounded diff must not be copied",
            }],
            "repos/acme/widgets/pulls": [{
                "number": 5, "title": "Pilot WidgetEngine", "state": "closed",
                "body": "Earlier implementation direction", "updated_at": "2026-08-31T00:00:00Z",
                "html_url": "https://github.com/acme/widgets/pull/5", "head": {"sha": "a" * 40},
            }],
            "repos/acme/widgets/issues": [{
                "number": 5, "title": "Pilot WidgetEngine", "state": "closed",
                "body": "PR wire record", "updated_at": "2026-08-31T00:00:00Z",
                "html_url": "https://github.com/acme/widgets/pull/5", "pull_request": {},
            }, {
                "number": 3, "title": "Customer migration", "state": "closed",
                "body": "Earlier issue context", "updated_at": "2026-08-30T00:00:00Z",
                "html_url": "https://github.com/acme/widgets/issues/3",
            }],
        }
        if path == "repos/acme/widgets/issues/7/comments":
            self.assertIn("page=3", endpoint)
        return responses[path], False

    def assert_bounded(self, sources: list[dict]) -> None:
        self.assertLessEqual(sum(len(item["text"].encode()) for item in sources), briefing.CONTEXT_CAP)
        self.assertTrue(all(len(item["text"].encode()) <= briefing.SOURCE_CAP for item in sources))
        self.assertTrue(all({"id", "label", "text", "truncated"} <= item.keys() for item in sources))

    def test_issue_uses_ticket_history_and_real_repository_context(self) -> None:
        issue = {
            "number": 7, "title": "Assess `WidgetEngine` rollout", "body": "Should `WidgetEngine` ship?",
            "state": "OPEN", "labels": [{"name": "needs-viability"}],
            "url": "https://github.com/acme/widgets/issues/7", "updatedAt": "2026-09-04T00:00:00Z",
        }
        with patch.object(evidence, "github_read", side_effect=self.github_read):
            sources = evidence.viability_sources(self.cfg, issue)

        text = "\n".join(item["text"] for item in sources)
        self.assertIn("Should `WidgetEngine` ship?", text)
        self.assertIn("customer needs WidgetEngine compatibility", text)
        self.assertIn("Earlier implementation direction", text)
        self.assertIn("Earlier issue context", text)
        self.assertIn("Manager notes: preserve the pilot boundary", text)
        self.assertIn("Persistent manager constraints", text)
        self.assertIn("WidgetEngine repository overview", text)
        self.assertIn("WidgetEngine rollout is planned", text)
        self.assertIn("WidgetEngine pilots need migration evidence", text)
        self.assertIn("class WidgetEngine", text)
        self.assert_bounded(sources)

    def test_local_reads_truncate_regular_files_and_refuse_symlinks(self) -> None:
        private = self.root.parent / "private"
        private.write_text("PRIVATE SECRET")
        (self.root / "README.md").write_text("R" * (evidence.VIABILITY_LOCAL_CAP + 100))
        (self.root / "ROADMAP.md").unlink()
        (self.root / "ROADMAP.md").symlink_to(private)
        (self.root / ".factory/manager-7.md").unlink()
        (self.root / ".factory/manager/notes.md").unlink()
        (self.root / ".factory/manager/notes.md").symlink_to(private)
        (self.root / ".factory/manager-7.md").symlink_to(private)
        issue = {
            "number": 7, "title": "Assess `WidgetEngine`", "body": "Pilot direction",
            "state": "OPEN", "labels": [{"name": "needs-viability"}],
            "url": "https://github.com/acme/widgets/issues/7", "updatedAt": "2026-09-04T00:00:00Z",
        }
        with patch.object(evidence, "github_read", side_effect=self.github_read):
            sources = evidence.viability_sources(self.cfg, issue)

        self.assertNotIn("PRIVATE SECRET", json.dumps(sources))
        readme = next(item for item in sources if item.get("path") == "README.md")
        self.assertTrue(readme["truncated"])
        self.assert_bounded(sources)

    def test_pr_evidence_is_directional_and_omits_diff_patches(self) -> None:
        pr = {
            "number": 9, "title": "Adopt `WidgetEngine`", "body": "Roll out behind the pilot flag.",
            "state": "OPEN", "labels": [{"name": "needs-review"}],
            "url": "https://github.com/acme/widgets/pull/9", "updatedAt": "2026-09-04T00:00:00Z",
            "headRefOid": "b" * 40,
        }
        with patch.object(evidence, "github_read", side_effect=self.github_read):
            sources = evidence.viability_sources(self.cfg, pr, kind="pr")

        target = next(item for item in sources if item["label"] == "Target PR #9 direction")
        self.assertEqual(json.loads(target["text"])["headRefOid"], "b" * 40)
        comments = next(item for item in sources if item["label"] == "Recent comments on target pr #9")
        self.assertTrue(comments["truncated"])
        changed = next(item for item in sources if item["label"] == "PR #9 changed-file direction summary")
        self.assertEqual(json.loads(changed["text"]), [{
            "filename": "src/widget.py", "status": "modified", "additions": 8,
            "deletions": 2, "changes": 10,
        }])
        self.assertNotIn("unbounded diff", json.dumps(sources))
        self.assertTrue(any("state=all" in call for call in self.calls))
        self.assert_bounded(sources)


if __name__ == "__main__":
    unittest.main()
