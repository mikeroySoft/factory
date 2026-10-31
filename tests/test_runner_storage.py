"""Runner storage preflights decline work and run-scoped temp artifacts are removed."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, manage, storage
from tests import test_dispatch_lifecycle as dispatch_test
from tests.test_gate import gate, repo_with_test


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
        result = self.cli("dispatch", "--ticket", "7")
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

    def test_dashboard_surfaces_low_storage_as_a_host_problem(self) -> None:
        self.set_runner_threshold("min_free_gb", 10**9)
        snapshot = json.loads(self.cli("dashboard", "--json").stdout)
        self.assertEqual(snapshot["host_problems"][0]["code"], "runner_storage_low")

    def test_storage_is_checked_at_each_ticket_claim_boundary(self) -> None:
        dispatch.configure(config.load(self.repo))
        issue = {
            "number": 7, "title": "local change",
            "body": "Acceptance: make the local change",
            "labels": [{"name": "ready-for-agent"}], "assignees": [],
        }
        low = storage.RunnerStorageLow("claim", storage.status(dispatch.cfg))
        with mock.patch.object(dispatch, "gh_json", return_value={
            **issue, "state": "OPEN", "comments": [],
        }), mock.patch.object(storage, "require", side_effect=low), \
                mock.patch.object(dispatch, "run", side_effect=AssertionError("claim started")) as run:
            with self.assertRaises(storage.RunnerStorageLow):
                dispatch.process_ticket(issue, 1, False, forced=True)
        run.assert_not_called()

    def test_gate_storage_deferral_keeps_committed_worker_work(self) -> None:
        dispatch.configure(config.load(self.repo))
        issue = {
            "number": 7, "title": "local change",
            "body": "Acceptance: make the local change",
            "labels": [{"name": "ready-for-agent"}], "assignees": [],
        }

        def defer(_n, wt, *_args, **_kwargs):
            (wt / "preserved.txt").write_text("worker result")
            self.git("-C", str(wt), "add", "preserved.txt")
            self.git("-C", str(wt), "commit", "-qm", "worker result")
            raise storage.RunnerStorageLow("gate", storage.status(dispatch.cfg))

        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "worker_round", side_effect=defer):
            with self.assertRaises(storage.RunnerStorageLow):
                dispatch.process_ticket(issue, 1, False, forced=True)

        wt = self.repo / ".factory" / "wt-7"
        self.assertEqual((wt / "preserved.txt").read_text(), "worker result")
        events = lifecycle.read_events(self.events)
        self.assertFalse(any(row.get("event") in {"attempt", "escalate"} for row in events))

    def test_manager_storage_retry_gates_saved_head_without_rerunning_worker(self) -> None:
        dispatch.configure(config.load(self.repo))
        wt = self.repo / ".factory" / "wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", str(wt), "-b", "agent/7")
        lease = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        (wt / "saved.txt").write_text("worker result")
        self.git("-C", str(wt), "add", "saved.txt")
        self.git("-C", str(wt), "commit", "-qm", "saved worker result")
        head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        packet = self.repo / ".factory" / "packet.md"
        packet.write_text("fix it")
        issue = {
            "title": "local change", "body": "", "comments": [],
            "labels": [{"name": "ready-for-human"}],
        }
        pr = {
            "state": "OPEN", "headRefName": "agent/7", "headRefOid": lease,
            "baseRefName": "main", "reviewDecision": "",
        }
        with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                mock.patch.object(dispatch, "worker_round") as worker, \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate, \
                mock.patch.object(dispatch, "push_agent", return_value=head), \
                mock.patch.object(dispatch, "record_risk"), \
                mock.patch.object(dispatch, "pr_gate_and_review",
                                  return_value=("APPROVE", "approved", False)), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True):
            manage.apply(
                7, issue, "FIX", "", {"worker": "default", "guidance": "fix"},
                packet, resume_head=head,
            )
        worker.assert_not_called()
        gate.assert_called_once()


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

    def test_low_storage_declines_gate_before_checks_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            marker = base / "check-ran"
            command = f"open({str(marker)!r}, 'w').close()"
            toml = (
                "[runner]\nmin_free_gb = 1000000000\n"
                "[[gate.check]]\nname = \"code\"\n"
                f"run = {json.dumps([sys.executable, '-c', command])}\n"
            )
            repo = repo_with_test(base, toml)
            code, output, report = gate(repo)
            self.assertEqual(code, 75, output)
            self.assertEqual(report, "")
            self.assertFalse(marker.exists())
            events = lifecycle.read_events(repo / ".factory" / "events.jsonl")
            self.assertEqual([(row.get("event"), row.get("phase")) for row in events],
                             [("runner_storage_low", "gate")])



class StorageRecoveryTest(unittest.TestCase):
    def test_statvfs_error_is_reported_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(storage.os, "statvfs", side_effect=OSError("I/O error")):
            value = storage.status(config.Config(Path(directory), "acme/widgets"))
        self.assertFalse(value["ok"])
        self.assertTrue(all(row["low"] == ["unavailable"] for row in value["filesystems"]))

    def test_next_preflight_removes_temp_root_abandoned_by_crashed_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            record = base / "abandoned"
            script = base / "crash.py"
            script.write_text(
                "import os\n"
                "from pathlib import Path\n"
                "from factory import storage\n"
                "with storage.temporary_environment('factory-worker-crash-'):\n"
                f"    Path({str(record)!r}).write_text(os.environ['TMPDIR'])\n"
                "    os._exit(7)\n"
            )
            proc = subprocess.run(
                [sys.executable, str(script)], check=False,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            )
            self.assertEqual(proc.returncode, 7)
            abandoned = Path(record.read_text())
            self.assertTrue(abandoned.is_dir())

            storage.require(config.Config(base, "acme/widgets", runner_min_free_gb=0,
                                          runner_min_free_inodes=0), "claim")
            self.assertFalse(abandoned.exists())

    def test_gate_tempfail_is_a_storage_deferral_not_a_gate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(root, "acme/widgets", runner_min_free_gb=0,
                                runner_min_free_inodes=0)
            dispatch.configure(cfg)
            completed = subprocess.CompletedProcess([], storage.EX_TEMPFAIL, "", "runner_storage_low")

            def run(command, **_kwargs):
                if command[:3] == ["git", "rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                return completed

            with mock.patch.object(dispatch, "run", side_effect=run), \
                    self.assertRaises(storage.RunnerStorageLow):
                dispatch.run_gate(root, 7)
            self.assertFalse((root / ".factory" / "gate-report-7.md").exists())


class ManagerStorageRetryTest(unittest.TestCase):
    def test_manager_fix_retries_saved_decision_without_rerunning_manager_or_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(
                root, "acme/widgets", manager=["manager"], manager_rounds=1,
                workers={"default": ["worker"]},
            )
            dispatch.configure(cfg)
            packet = cfg.factory / "escalations" / "7.md"
            packet.parent.mkdir(parents=True)
            packet.write_text("gate failed")
            lifecycle.append(dispatch.EVENTS, {
                "event": "escalate", "ticket": 7, "at": "2026-01-01T00:00:00Z",
                "round": 1, "packet": str(packet),
            })
            (cfg.factory / "wt-7").mkdir()
            issue = {
                "number": 7, "title": "Fix gate", "body": "Acceptance: pass",
                "labels": [{"name": "ready-for-human"}],
            }
            manager_runs = 0

            def run(command, **_kwargs):
                nonlocal manager_runs
                if command[0] == "manager":
                    manager_runs += 1
                    return subprocess.CompletedProcess(
                        command, 0,
                        'DECISION: FIX\n{"worker":"default","guidance":"fix"}\n', "",
                    )
                if command[:3] == ["git", "rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "b" * 40 + "\n", "")
                raise AssertionError(command)

            low = storage.RunnerStorageLow("gate", storage.status(cfg))
            with mock.patch.object(dispatch, "gh_json", return_value=[issue]), \
                    mock.patch.object(dispatch, "initiative_kind", return_value=False), \
                    mock.patch.object(manage, "human_activity", return_value=False), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(manage, "apply", side_effect=[low, None]) as apply:
                manage.escalation_pass()
                manage.escalation_pass()

            self.assertEqual(manager_runs, 1)
            self.assertEqual(apply.call_count, 2)
            self.assertEqual(apply.call_args.kwargs["resume_head"], "b" * 40)

if __name__ == "__main__":
    unittest.main()
