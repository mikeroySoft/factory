"""Codebase-history contracts against disposable Git repositories."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from factory import codebase

try:
    GRAPHIFY_AVAILABLE = metadata.version("graphifyy") == codebase.GRAPHIFY_VERSION
except metadata.PackageNotFoundError:
    GRAPHIFY_AVAILABLE = False


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def repository(parent: Path) -> Path:
    root = parent / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "history@example.com")
    git(root, "config", "user.name", "History Test")
    (root / ".git/info/exclude").write_text(".factory/\n")
    return root


def commit(root: Path, subject: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", subject)
    return git(root, "rev-parse", "HEAD")


def by_path(snapshot: dict) -> dict[str, dict]:
    return {item["path"]: item for item in snapshot["files"]}


class GitIdentityTest(unittest.TestCase):
    def test_default_ref_and_lineage_are_stable_across_windows_and_readdition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = repository(Path(directory))
            (root / "old.py").write_text("def value():\n    return 1\n")
            first = commit(root, "add")
            git(root, "mv", "old.py", "new.py")
            renamed = commit(root, "rename")
            (root / "new.py").unlink()
            deleted = commit(root, "delete")
            (root / "new.py").write_text("def other():\n    return 2\n")
            readded = commit(root, "readd")

            self.assertEqual(codebase.default_ref(root, "main"), "main")
            git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
            self.assertEqual(codebase.default_ref(root, "main"), "origin/main")

            wanted = {
                first: {"old.py"},
                renamed: {"new.py"},
                deleted: set(),
                readded: {"new.py"},
            }
            identities = codebase._identity_maps(root, readded, wanted, None)
            self.assertEqual(identities[first]["old.py"], identities[renamed]["new.py"])
            self.assertNotEqual(identities[renamed]["new.py"], identities[readded]["new.py"])
            window = codebase._identity_maps(root, readded, {renamed: {"new.py"}}, None)
            self.assertEqual(window[renamed]["new.py"], identities[renamed]["new.py"])

    def test_inventory_is_regular_bounded_and_explicit_about_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = repository(Path(directory))
            (root / "safe.py").write_text("def safe():\n    return 1\n")
            (root / "escape.py").symlink_to("/etc/passwd")
            (root / "vendor").mkdir()
            (root / "vendor/ignored.py").write_text("ignored\n")
            (root / "large.py").write_bytes(b"x" * (codebase.MAX_FILE_BYTES + 1))
            sha = commit(root, "inventory")

            entries, warnings, _ = codebase._inventory(root, sha)
            self.assertEqual([item["path"] for item in entries], ["safe.py"])
            text = " ".join(warnings).lower()
            self.assertIn("symlink", text)
            self.assertIn("vendored", text)
            self.assertIn("larger", text)


@unittest.skipUnless(GRAPHIFY_AVAILABLE, "graphifyy 0.9.56 optional dependency is absent")
class HistoryExtractionTest(unittest.TestCase):
    def test_coverage_exclusion_is_not_a_file_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = repository(Path(directory))
            path = root / "module.py"
            path.write_text("def value():\n    return 1\n")
            commit(root, "mapped")
            path.write_bytes(b"#" * (codebase.MAX_FILE_BYTES + 1))
            commit(root, "beyond extraction size limit")
            (root / "vendor").mkdir()
            git(root, "mv", "module.py", "vendor/module.py")
            commit(root, "move outside coverage")
            (root / "vendor/module.py").unlink()
            commit(root, "actually delete")
            history = codebase.build_history(
                root, "main", "acme/widgets", root / ".factory/codebase", 4
            )
            mapped, oversized, moved, deleted = history["snapshots"]
            identity = mapped["files"][0]["id"]
            self.assertIn(identity, oversized.get("unmapped", []))
            self.assertIn(identity, moved.get("unmapped", []))
            self.assertNotIn(identity, deleted.get("unmapped", []))

    def test_empty_manifests_remain_inventory_but_parse_errors_preserve_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = repository(Path(directory))
            (root / "Cargo.toml").write_text("[workspace]\nmembers = []\n")
            (root / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
            commit(root, "manifests without a package")
            cache = root / ".factory/codebase"
            history = codebase.build_history(root, "main", "acme/widgets", cache, 1)
            snapshot = history["snapshots"][0]
            files = by_path(snapshot)
            self.assertEqual(set(files), {"Cargo.toml", "pyproject.toml"})
            self.assertEqual(snapshot["edges"], [])
            warnings = " ".join(snapshot["warnings"])
            for path, item in files.items():
                self.assertEqual(item["symbols"], [])
                self.assertIn(path, warnings)

            published = (cache / "history.json").read_bytes()
            (root / "Cargo.toml").write_text("[workspace\n")
            commit(root, "malformed manifest")
            with self.assertRaises(RuntimeError):
                codebase.build_history(root, "main", "acme/widgets", cache, 1)
            self.assertEqual((cache / "history.json").read_bytes(), published)

    def test_real_history_tracks_edit_move_delete_cache_and_publication_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = repository(Path(directory))
            (root / "pkg").mkdir()
            (root / "pkg/a.py").write_text("def value():\n    return 1\n")
            (root / "use.py").write_text(
                "from pkg.a import value\n\ndef use():\n    return value()\n"
            )
            commit(root, "add modules")
            (root / "pkg/a.py").write_text("def value():\n    return 2\n")
            commit(root, "equal-size edit")
            (root / "lib").mkdir()
            git(root, "mv", "pkg/a.py", "lib/b.py")
            (root / "use.py").write_text(
                "from lib.b import value\n\ndef use():\n    return value()\n"
            )
            commit(root, "move module")
            (root / "use.py").unlink()
            commit(root, "delete caller")

            cache = root / ".factory/codebase"
            history = codebase.build_history(root, "refs/heads/main", "acme/widgets", cache, 4)
            self.assertEqual(
                set(history),
                {
                    "schema",
                    "extractor",
                    "repo",
                    "ref",
                    "tip",
                    "generated_at",
                    "truncated",
                    "snapshots",
                    "slots",
                },
            )
            self.assertEqual(history["schema"], 1)
            self.assertFalse(history["truncated"])
            self.assertEqual(
                [row["subject"] for row in history["snapshots"]],
                [
                    "add modules",
                    "equal-size edit",
                    "move module",
                    "delete caller",
                ],
            )

            initial, edited, moved, deleted = history["snapshots"]
            initial_files, edited_files = by_path(initial), by_path(edited)
            moved_files, deleted_files = by_path(moved), by_path(deleted)
            lineage = initial_files["pkg/a.py"]["id"]
            self.assertEqual(lineage, edited_files["pkg/a.py"]["id"])
            self.assertEqual(lineage, moved_files["lib/b.py"]["id"])
            self.assertEqual(moved_files["lib/b.py"]["group"], "pkg")
            self.assertEqual(initial_files["pkg/a.py"]["lines"], edited_files["pkg/a.py"]["lines"])
            self.assertNotEqual(initial_files["pkg/a.py"]["blob"], edited_files["pkg/a.py"]["blob"])
            self.assertNotIn("use.py", deleted_files)
            self.assertIn(
                "value",
                {symbol["name"] for symbol in initial_files["pkg/a.py"]["symbols"]},
            )

            caller = initial_files["use.py"]["id"]
            dependency = next(
                edge
                for edge in initial["edges"]
                if edge["source"] == caller and edge["target"] == lineage
            )
            self.assertEqual(
                set(dependency), {"source", "target", "relation", "confidence", "path", "line"}
            )
            self.assertEqual(dependency["path"], "use.py")
            self.assertIsInstance(dependency["line"], int)
            self.assertIn(dependency["confidence"], {"EXTRACTED", "INFERRED"})

            slot = next(item for item in history["slots"] if item["id"] == lineage)

            def must_not_extract(*_args, **_kwargs):
                raise AssertionError("commit cache was not reused")

            with patch.object(codebase, "_load_graphify", return_value=must_not_extract):
                limited = codebase.build_history(root, "refs/heads/main", "acme/widgets", cache, 2)
            self.assertTrue(limited["truncated"])
            self.assertIn("limited", " ".join(limited["snapshots"][0]["warnings"]).lower())
            limited_slot = next(item for item in limited["slots"] if item["id"] == lineage)
            self.assertEqual(limited_slot, slot)

            (root / "src").mkdir()
            git(root, "mv", "lib/b.py", "src/c.py")
            commit(root, "move again")
            latest = codebase.build_history(root, "refs/heads/main", "acme/widgets", cache, 1)
            latest_file = by_path(latest["snapshots"][0])["src/c.py"]
            self.assertEqual((latest_file["id"], latest_file["group"]), (lineage, "pkg"))
            self.assertEqual(next(item for item in latest["slots"] if item["id"] == lineage), slot)

            published = (cache / "history.json").read_bytes()
            (root / "broken.py").write_text("def broken():\n    return 1\n")
            commit(root, "extractor failure")

            def failed_extract(paths, **_kwargs):
                return {"nodes": [], "edges": [], "failed_sources": [str(paths[0])]}

            with (
                patch.object(codebase, "_load_graphify", return_value=failed_extract),
                self.assertRaisesRegex(RuntimeError, "failed to extract"),
            ):
                codebase.build_history(root, "refs/heads/main", "acme/widgets", cache, 1)
            self.assertEqual((cache / "history.json").read_bytes(), published)
            self.assertEqual(json.loads(published)["tip"], latest["tip"])

    def test_git_object_snapshot_excludes_links_runtime_and_host_preprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = repository(parent)
            marker = parent / "source-executed"
            (root / "runner.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed')\n\n"
                "def safe():\n    return 1\n"
            )
            (root / "runner.py").chmod(0o755)
            (root / "escape.py").symlink_to("/etc/passwd")
            (root / "unsafe.F90").write_text('#include "/etc/passwd"\nprogram p\nend program p\n')
            (root / "vendor").mkdir()
            (root / "vendor/ignored.py").write_text("raise RuntimeError('not source')\n")
            commit(root, "safety corpus")
            committed_blob = git(root, "rev-parse", "HEAD:runner.py")
            (root / "runner.py").write_text("dirty worktree must survive\n")

            history = codebase.build_history(
                root, "refs/heads/main", "acme/widgets", root / ".factory/codebase", 1
            )
            snapshot = history["snapshots"][0]
            files = by_path(snapshot)
            self.assertEqual(set(files), {"runner.py", "unsafe.F90"})
            self.assertEqual(files["runner.py"]["blob"], committed_blob)
            self.assertEqual(files["unsafe.F90"]["symbols"], [])
            warnings = " ".join(snapshot["warnings"]).lower()
            self.assertIn("symlink", warnings)
            self.assertIn("vendored", warnings)
            self.assertIn("host cpp", warnings)
            self.assertFalse(marker.exists())
            self.assertEqual((root / "runner.py").read_text(), "dirty worktree must survive\n")


if __name__ == "__main__":
    unittest.main()
