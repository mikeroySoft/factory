"""Prime Agent pilot routing, environment, and lifecycle evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle


class PrimePilotConfigTest(unittest.TestCase):
    def test_label_rule_selects_versioned_worker_and_expands_contained_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "git@github.com:mikeroySoft/rocm-cli.git"],
                cwd=root,
                check=True,
            )
            (root / config.CONFIG_NAME).write_text(
                '[workers]\ndefault = ["omp", "-p", "@{prompt}"]\n'
                '[workers.prime]\n'
                'when = { label = "harness-pilot" }\n'
                'command = ["{home}/.local/bin/factory-prime-agent", "-p", "--cwd", "{cwd}", "@{prompt}", "--mode", "json"]\n'
                'transcript = "omp-session"\n'
                'version = "0.10.0"\n'
                'env = { DO_NOT_TRACK = "1", PRIME_AGENT_TELEMETRY = "0", PRIME_AGENT_CODING_AGENT_DIR = "{root}/.factory/prime-agent" }\n'
            )
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "xdg")}, clear=False):
                loaded = config.load(root)

            self.assertEqual(loaded.worker_key({"ready-for-agent", "harness-pilot"}), "prime")
            self.assertEqual(loaded.worker_key({"ready-for-agent"}), "default")
            self.assertEqual(loaded.worker_version["prime"], "0.10.0")
            self.assertEqual(
                loaded.worker({"harness-pilot"}, Path("/prompt"), Path("/worktree")),
                [str(Path.home() / ".local/bin/factory-prime-agent"), "-p", "--cwd", "/worktree", "@/prompt", "--mode", "json"],
            )
            self.assertEqual(
                loaded.worker_environment("prime", Path("/prompt"), Path("/worktree")),
                {
                    "DO_NOT_TRACK": "1",
                    "PRIME_AGENT_TELEMETRY": "0",
                    "PRIME_AGENT_CODING_AGENT_DIR": f"{root}/.factory/prime-agent",
                },
            )


class PrimePilotLifecycleTest(unittest.TestCase):
    def test_worker_environment_and_version_are_present_on_lifecycle_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / config.CONFIG_NAME).write_text('[repo]\nslug = "mikeroySoft/rocm-cli"\n')
            dispatch.configure(config.Config(root=root, repo="mikeroySoft/rocm-cli"))
            dispatch.FACTORY.mkdir()
            worktree = root / "worktree"
            worktree.mkdir()
            seen = root / "seen.json"
            probe = (
                "import json, os, sys; "
                "json.dump({k: os.environ.get(k) for k in ('DO_NOT_TRACK', 'PRIME_AGENT_TELEMETRY')}, open(sys.argv[1], 'w'))"
            )

            code, timeout = dispatch.run_worker(
                [sys.executable, "-c", probe, str(seen)],
                worktree,
                root / "worker.log",
                worktree / "handoff.md",
                env_overrides={"DO_NOT_TRACK": "1", "PRIME_AGENT_TELEMETRY": "0"},
                worker="prime",
                worker_version="0.10.0",
            )

            self.assertEqual((code, timeout), (0, None))
            self.assertEqual(json.loads(seen.read_text()), {"DO_NOT_TRACK": "1", "PRIME_AGENT_TELEMETRY": "0"})
            rows = [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("stage") == "worker"]
            enter = next(row for row in rows if row["kind"] == "enter")
            self.assertEqual((enter["worker"], enter["worker_version"]), ("prime", "0.10.0"))

    def test_missing_selected_prime_binary_waits_without_claiming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(
                root=root,
                repo="mikeroySoft/rocm-cli",
                workers={"default": ["true"], "prime": ["no-such-prime-agent-332"]},
                worker_select_label={"prime": "harness-pilot"},
            )
            dispatch.configure(cfg)
            (dispatch.FACTORY / "locks").mkdir(parents=True)
            issue = {
                "number": 7,
                "title": "pilot",
                "labels": [{"name": "ready-for-agent"}, {"name": "harness-pilot"}],
            }
            fresh = {
                "state": "OPEN",
                "labels": issue["labels"],
                "assignees": [],
                "title": "pilot",
                "body": "",
                "comments": [],
            }
            with mock.patch.object(dispatch, "gh_json", return_value=fresh), \
                    mock.patch.object(dispatch, "run") as run:
                dispatch.process_ticket(issue, 1, False)

            run.assert_not_called()
            waits = [
                row["wait"] for row in lifecycle.read_events(dispatch.EVENTS)
                if row.get("kind") == "wait"
            ]
            self.assertEqual(
                waits,
                [{
                    "reason": "worker_unavailable",
                    "mode": "retry_next_pass",
                    "resource": None,
                    "details": {"executable": "no-such-prime-agent-332"},
                }],
            )


if __name__ == "__main__":
    unittest.main()
