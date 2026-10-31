"""Runner storage preflights decline work and run-scoped temp artifacts are removed."""
from __future__ import annotations

import json
import test_dispatch_lifecycle as dispatch_test
import sys
import tempfile
import unittest
from pathlib import Path

from factory import lifecycle

from test_gate import gate, repo_with_test


class RunnerStorageDispatchTest(unittest.TestCase):
    setUp = dispatch_test.LocalCLI.setUp
    git = dispatch_test.LocalCLI.git
    write_config = dispatch_test.LocalCLI.write_config
    commit_config = dispatch_test.LocalCLI.commit_config
    cli = dispatch_test.LocalCLI.cli
    rows = dispatch_test.LocalCLI.rows

    def set_runner_threshold(self, setting: str, value: int) -> None:
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text() + f"\n[runner]\n{setting} = {value}\n")
        self.commit_config()

    def assert_claim_declined(self, setting: str, value: int) -> None:
        self.set_runner_threshold(setting, value)
        result = self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertIn("runner_storage_low", result.stdout + result.stderr)
        events = lifecycle.read_events(self.events)
        conditions = [row for row in events if row.get("event") == "runner_storage_low"]
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["phase"], "claim")
        self.assertFalse(any(row.get("event") in {"claimed", "attempt", "escalate"} for row in events))
        self.assertFalse(any(row.get("stage") == "gate" for row in events))
        self.assertFalse((self.repo / ".factory" / "wt-7").exists())

    def test_low_free_space_declines_claim_without_gate_or_round(self) -> None:
        self.assert_claim_declined("min_free_gb", 10**9)

    def test_low_free_inodes_declines_claim_without_gate_or_round(self) -> None:
        self.assert_claim_declined("min_free_inodes", 10**15)

    def test_failed_worker_run_removes_its_temp_directory(self) -> None:
        record = self.base / "worker-temp"
        self.worker.write_text(
            "import os\nfrom pathlib import Path\n"
            f"tmp = Path(os.environ['TMPDIR'])\nPath({str(record)!r}).write_text(str(tmp))\n"
            "(tmp / 'worker-artifact').mkdir()\n"
            + dispatch_test.WORKER
            + "\nraise SystemExit(7)\n"
        )
        self.cli("dispatch", "--ticket", "7")
        self.assertTrue(record.exists())
        self.assertFalse(Path(record.read_text()).exists())

    def test_escalated_worktree_is_kept(self) -> None:
        self.write_config(check=[sys.executable, "-c", "raise SystemExit(1)"])
        self.commit_config()
        self.cli("dispatch", "--ticket", "7")
        events = lifecycle.read_events(self.events)
        self.assertTrue(any(row.get("event") == "escalate" and row.get("code") == "gate_failed" for row in events))
        self.assertTrue((self.repo / ".factory" / "wt-7").is_dir())


class GateTempCleanupTest(unittest.TestCase):
    def test_failed_gate_and_mutation_checks_remove_their_temp_directories(self) -> None:
        for name, tier in (("code", "attempt"), ("mutation", "pr")):
            with self.subTest(check=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                record = base / "temp-path"
                script = base / "fail.py"
                script.write_text(
                    "import os, sys\nfrom pathlib import Path\n"
                    "tmp = Path(os.environ['TMPDIR'])\n"
                    "(tmp / 'check-artifact').mkdir()\n"
                    "Path(sys.argv[1]).write_text(str(tmp))\n"
                    "raise SystemExit(1)\n"
                )
                when = "" if tier == "attempt" else '\nwhen = "pr"'
                toml = (
                    "[leak_scan]\npattern = \"\"\n"
                    "[[gate.check]]\n"
                    f"name = {json.dumps(name)}\n"
                    f"run = {json.dumps([sys.executable, str(script), str(record)])}"
                    f"{when}\n"
                )
                repo = repo_with_test(base, toml)
                args = ("--skip", "conflict-markers,leak-scan") if tier == "attempt" else ("--tier", "pr")
                code, output, report = gate(repo, *args)
                self.assertEqual(code, 1, report + output)
                self.assertTrue(record.exists(), report + output)
                self.assertFalse(Path(record.read_text()).exists())


if __name__ == "__main__":
    unittest.main()
