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
                mock.patch.object(dispatch, "run", side_effect=AssertionError("claim started")) as run, \
                self.assertRaises(storage.RunnerStorageLow):
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
                mock.patch.object(dispatch, "worker_round", side_effect=defer), \
                self.assertRaises(storage.RunnerStorageLow):
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


class StorageRevisionDispatchTest(unittest.TestCase):
    setUp = dispatch_test.LocalCLI.setUp
    git = dispatch_test.LocalCLI.git
    write_config = dispatch_test.LocalCLI.write_config

    def test_later_launch_failure_preserves_unpublished_commit(self) -> None:
        dispatch.configure(config.load(self.repo))
        issue = {
            "number": 7, "title": "local change", "body": "Make the change.",
            "labels": [{"name": "ready-for-agent"}], "assignees": [],
        }
        saved = []

        def attempt(_n, wt, *_args, **_kwargs):
            if saved:
                raise FileNotFoundError("worker disappeared before retry")
            (wt / "unpublished.txt").write_text("keep this work")
            self.git("-C", str(wt), "add", "unpublished.txt")
            self.git("-C", str(wt), "commit", "-qm", "unpublished worker result")
            saved.append(self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip())
            return False, "gate failed", None, saved[0]

        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "worker_round", side_effect=attempt):
            self.assertFalse(dispatch.process_ticket(issue, 1, False, forced=True))
        wt = self.repo / ".factory" / "wt-7"
        self.assertTrue(wt.is_dir(), "launch failure deleted unpublished worker progress")
        self.assertEqual(self.git("rev-parse", "agent/7").stdout.strip(), saved[0])
        self.assertEqual((wt / "unpublished.txt").read_text(), "keep this work")
        self.assertFalse(any(row.get("event") == "escalate"
                             for row in lifecycle.read_events(self.events)))

    def test_storage_journal_exhaustion_still_defers_dispatch_successfully(self) -> None:
        import errno

        dispatch.configure(config.load(self.repo))
        issue = {
            "number": 7, "title": "local change", "body": "Make the change.",
            "labels": [{"name": "ready-for-agent"}], "assignees": [],
        }
        original_status, original_write = storage.status, lifecycle._write
        exhausted = False

        def low(cfg):
            nonlocal exhausted
            exhausted = True
            value = original_status(cfg)
            value["ok"] = False
            value["filesystems"][0]["low"] = ["space"]
            return value

        def write(*args):
            if exhausted:
                raise OSError(errno.ENOSPC, "journal filesystem full")
            return original_write(*args)

        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(storage, "status", side_effect=low), \
                mock.patch.object(lifecycle, "_write", side_effect=write), \
                mock.patch.object(dispatch, "_main", side_effect=lambda _args: dispatch.process_ticket(
                    issue, 1, False, forced=True)), \
                mock.patch("sys.stderr") as stderr:
            self.assertEqual(dispatch.main([]), 0)
        self.assertIn("runner_storage_low", str(stderr.write.call_args_list))
        events = lifecycle.read_events(self.events)
        self.assertFalse(any(row.get("event") in {"claimed", "attempt", "escalate"} for row in events))


class StorageRevisionRefreshTest(unittest.TestCase):
    def test_deferred_refresh_resumes_gate_and_independent_review_next_merge_pass(self) -> None:
        from tests.test_refresh_conflict import FakeGitHub, build_fork, git

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, origin = build_fork(base)
            git(origin, "checkout", "-qb", "agent/9")
            (origin / "agent.txt").write_text("agent work")
            git(origin, "add", "agent.txt")
            git(origin, "commit", "-qm", "agent work")
            remote = git(origin, "rev-parse", "HEAD")
            git(origin, "checkout", "-q", "main")
            (origin / "main.txt").write_text("new upstream work")
            git(origin, "add", "main.txt")
            git(origin, "commit", "-qm", "main moved")
            cfg = config.Config(root, "acme/widgets", runner_min_free_gb=0,
                                runner_min_free_inodes=0)
            dispatch.configure(cfg)
            github = FakeGitHub(origin, dispatch.run)
            low = storage.RunnerStorageLow("gate", storage.status(cfg))
            with mock.patch.multiple(dispatch, gh_json=github.gh_json, run=github.run), \
                    mock.patch.object(dispatch, "run_gate", side_effect=low), \
                    mock.patch.object(dispatch, "review") as review:
                # The claim preflight succeeded, but storage dropped after the rebase.
                storage.require(cfg, "claim")
                with self.assertRaises(storage.RunnerStorageLow):
                    dispatch.merge_pass_locked(False)
                review.assert_not_called()
            wt = cfg.factory / "wt-9"
            candidate = git(wt, "rev-parse", "HEAD")
            self.assertNotEqual(candidate, remote)
            self.assertEqual(github.head(), remote)
            self.assertNotIn(config.LABEL_APPROVED, github.pr_labels)
            rows = lifecycle.read_events(dispatch.EVENTS)
            self.assertFalse(any(row.get("event") in {
                "attempt", "escalate", "gate-requeue", "conflict-requeue",
            } for row in rows))

            with mock.patch.multiple(dispatch, gh_json=github.gh_json, run=github.run), \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "fresh gate")) as gate, \
                    mock.patch.object(dispatch, "record_risk"), \
                    mock.patch.object(dispatch, "risk_for_head", return_value=("Low", {})), \
                    mock.patch.object(dispatch, "review", return_value=("APPROVE", "fresh review")) as review, \
                    mock.patch.object(dispatch, "approve_pr", return_value=True) as approve:
                dispatch.merge_pass_locked(False)
            gate.assert_called_once()
            review.assert_called_once_with(wt, 9, "fresh gate", candidate)
            approve.assert_called_once_with(9, candidate)
            self.assertEqual(github.head(), candidate)
            self.assertFalse(github.merged, "refresh must not bypass the normal merge prerequisites")


class StorageRevisionFailureTest(unittest.TestCase):
    def test_gate_retains_exit_75_when_low_storage_journal_is_full(self) -> None:
        import errno
        from factory import gate as gate_module

        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", runner_min_free_gb=10**9)
            for error in (errno.ENOSPC, errno.EDQUOT):
                with self.subTest(errno=error), \
                        mock.patch.object(config, "load", return_value=cfg), \
                        mock.patch.object(lifecycle, "append", side_effect=OSError(error, "full")), \
                        mock.patch("sys.stderr") as stderr:
                    self.assertEqual(gate_module.main([]), storage.EX_TEMPFAIL)
                    self.assertIn("runner_storage_low", str(stderr.write.call_args_list))

    def test_temp_root_and_owner_exhaustion_are_host_deferrals(self) -> None:
        import errno

        for target in ("root", "owner"):
            for error in (errno.ENOSPC, errno.EDQUOT):
                with self.subTest(target=target, errno=error), tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    operation = (
                        mock.patch.object(storage.tempfile, "mkdtemp", side_effect=OSError(error, "full"))
                        if target == "root" else
                        mock.patch.object(Path, "write_text", side_effect=OSError(error, "full"))
                    )
                    with mock.patch.object(storage.tempfile, "tempdir", str(base)), operation, \
                            self.assertRaises(storage.RunnerStorageLow):
                        with storage.temporary_environment("factory-worker-retry-"):
                            self.fail("worker launched without a usable temp root")
                    self.assertEqual(list(base.iterdir()), [])

    def test_dead_owner_does_not_reclaim_a_surviving_child_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            record = base / "abandoned"
            ready = base / "child-ready"
            stop = base / "stop"
            child = (
                "import time\nfrom pathlib import Path\n"
                f"Path({str(ready)!r}).touch()\n"
                f"while not Path({str(stop)!r}).exists(): time.sleep(0.01)\n"
            )
            script = (
                "import os, subprocess, sys, time\nfrom pathlib import Path\n"
                "from factory import lifecycle, storage\n"
                f"with lifecycle.scope(Path({str(base / 'events.jsonl')!r}), 'worker') as execution:\n"
                "  with storage.temporary_environment('factory-worker-survivor-'):\n"
                f"    Path({str(record)!r}).write_text(os.environ['TMPDIR'])\n"
                f"    proc = subprocess.Popen([sys.executable, '-c', {child!r}], "
                "env=execution.env(), start_new_session=True)\n"
                "    execution.child(proc.pid)\n"
                f"    while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
                "    os._exit(7)\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script], check=False, timeout=15,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            )
            self.assertEqual(proc.returncode, 7)
            abandoned = Path(record.read_text())
            try:
                storage.cleanup_abandoned()
                self.assertTrue(abandoned.exists(), "surviving child's TMPDIR was reclaimed")
            finally:
                stop.touch()
            # Observe the child's recorded identity, not a background-job poll.
            child_identity = next(
                row["child_process"] for row in lifecycle.read_events(base / "events.jsonl")
                if row.get("kind") == "child_start"
            )
            import time
            deadline = time.monotonic() + 5
            while lifecycle._process_state(child_identity) == "alive" and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(lifecycle._process_state(child_identity), "dead")
            storage.cleanup_abandoned()
            self.assertFalse(abandoned.exists())
