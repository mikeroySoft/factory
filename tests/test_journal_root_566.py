"""#566: real selected discovery must not inherit another repository's journal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle

ROOT = Path(__file__).resolve().parents[1]


class RepositoryJournalIsolationTest(unittest.TestCase):
    def test_selected_real_suite_cannot_write_inherited_journal(self) -> None:
        # Unlike package discovery or the full suite, this supported unittest
        # invocation imports neither tests/__init__.py nor test_factory.py.
        # Keep the complete real tree: no synthetic suite or missing bootstrap.
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "events.jsonl"
            before = b'{"event":"sentinel"}\n'
            journal.write_bytes(before)
            context = json.dumps({
                "schema_version": 1, "path": str(journal), "ticket": 566,
                "root_execution_id": "operator-root", "parent_execution_id": "operator-worker",
            })
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_dispatch_identity.py", "-k", "test_reviewer_gets_no_github_credential"],
                cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
                env={**os.environ, lifecycle.CONTEXT_ENV: context,
                     "XDG_CONFIG_HOME": directory, "PYTHONPATH": str(ROOT)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(journal.read_bytes(), before)

    def test_configuring_same_root_preserves_worker_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ), \
                patch.dict(dispatch.__dict__):
            cfg = config.Config(root=Path(directory), repo="local/same-root")
            journal = cfg.factory / "events.jsonl"
            context = json.dumps({
                "schema_version": 1, "path": str(journal), "ticket": 566,
                "root_execution_id": "operator-root", "parent_execution_id": "operator-worker",
            })
            os.environ[lifecycle.CONTEXT_ENV] = context
            dispatch.configure(cfg)
            with lifecycle.scope(journal, "review"):
                pass
            rows = lifecycle.read_events(journal)
            self.assertEqual([r["kind"] for r in rows], ["enter", "exit"])
            self.assertEqual({r["parent_execution_id"] for r in rows}, {"operator-worker"})
            self.assertEqual({r["ticket"] for r in rows}, {566})
            self.assertEqual(os.environ[lifecycle.CONTEXT_ENV], context)
