"""Repository identity accepts realpath aliases, never foreign or malformed scopes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config
from factory.workspace import Workspace


class CanonicalRootTest(unittest.TestCase):
    def test_evidence_scope_accepts_symlink_alias_and_rejects_foreign_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            (root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n')
            if sys.platform == "darwin":
                # Exercise the actual macOS temp-root alias, not a simulated mapping.
                self.assertTrue(root.is_relative_to("/private/var/folders"), str(root))
                alias = Path("/var") / root.relative_to("/private/var")
            else:
                alias = base / "alias"
                alias.symlink_to(root, target_is_directory=True)
            self.assertNotEqual(alias, root)
            self.assertEqual(alias.resolve(), root)
            request = {"schema_version": 1, "repository": "acme/widgets", "op": "capabilities"}
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(base / "xdg")}):
                proc = subprocess.run(
                    [sys.executable, "-B", "-m", "factory", "evidence", "--root", str(alias)],
                    input=json.dumps(request), capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                envelope = json.loads(proc.stdout)
                self.assertEqual(envelope["scope"], {"repository": "acme/widgets", "root": str(root)})
                workspace = Workspace(config.load(alias), codebase_monitor=SimpleNamespace(state={}))
                try:
                    for selected, reported, repository, accepted in (
                        (root, str(alias), "acme/widgets", True),
                        (alias, str(root), "acme/widgets", True),
                        (root, str(base / "other"), "acme/widgets", False),
                        (root, str(alias), "acme/foreign", False),
                        (root, "repo", "acme/widgets", False),
                        (root, "", "acme/widgets", False),
                        (root, None, "acme/widgets", False),
                        (root, 1, "acme/widgets", False),
                        (root, "\x00", "acme/widgets", False),
                    ):
                        with self.subTest(selected=selected, reported=reported, repository=repository):
                            workspace._roots["acme/widgets"] = selected
                            response = {**envelope, "scope": {"repository": repository, "root": reported}}
                            with mock.patch.object(workspace, "_run_json", return_value=(response, None, 0)):
                                value, error = workspace._read_evidence("acme/widgets", request, "capabilities")
                            if accepted:
                                self.assertIsNone(error)
                                self.assertEqual(value["scope"], response["scope"])
                            else:
                                self.assertIsNone(value)
                                self.assertEqual(error["code"], "invalid_response")
                finally:
                    workspace.close()
