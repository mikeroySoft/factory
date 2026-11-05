"""Storage admission retains its infrastructure diagnosis in runtime projections."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import dispatch, lifecycle, runtime_events
from factory.config import Config


class RunnerRuntimeTest(unittest.TestCase):
    def test_storage_refusal_preserves_infra_outcome_and_reason_for_consumers(self):
        for field in ("f_bavail", "f_favail"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                scratch = root / "tmp"
                scratch.mkdir()
                cfg = Config(root=root, repo="local/preflight")
                journal = cfg.factory / "events.jsonl"
                values = dict(f_bavail=10**12, f_frsize=4096, f_favail=10**9, f_files=10**9)
                values[field] = 0
                with patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}), \
                        patch.object(tempfile, "tempdir", str(scratch)), \
                        patch.multiple(dispatch, create=True, cfg=cfg, ROOT=root, REPO=cfg.repo,
                                       FACTORY=cfg.factory, EVENTS=journal), \
                        patch("os.statvfs", return_value=SimpleNamespace(**values)), \
                        patch.object(dispatch, "acquire_host_claim") as claim, \
                        patch.object(dispatch, "gh_json") as github:
                    dispatch.process_ticket({"number": 7, "title": "local", "labels": []}, 1, False)
                claim.assert_not_called()
                github.assert_not_called()

                data = runtime_events.project(journal)
                self.assertEqual(
                    [(row["ticket"], row["outcome"], row["reason"]) for row in data["executions"]],
                    [(7, "infra", "runner_storage_low")],
                )
                self.assertEqual(
                    [(row["wait"]["mode"], row["wait"]["reason"])
                     for row in data["events"] if row["kind"] == "wait"],
                    [("retry_next_pass", "runner_storage_low")],
                )
                self.assertFalse(any(row.get("event") in {"claimed", "attempt", "escalate"}
                                     for row in lifecycle.read_events(journal)))
