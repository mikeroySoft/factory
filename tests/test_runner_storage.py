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

    def test_manager_storage_retry_gates_ahead_head_without_rerunning_worker(self) -> None:
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
                packet, resume_if_ahead=True,
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
                workers={"code": ["worker"]},
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
                        'DECISION: FIX\n{"worker":"code","guidance":"fix"}\n', "",
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

    def test_worker_phase_storage_retry_reruns_worker_without_rerunning_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(
                root, "acme/widgets", manager=["manager"], manager_rounds=1,
                workers={"code": ["worker"]},
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
                        'DECISION: FIX\n{"worker":"code","guidance":"fix"}\n', "",
                    )
                raise AssertionError(command)

            low = storage.RunnerStorageLow("worker", storage.status(cfg))
            with mock.patch.object(dispatch, "gh_json", return_value=[issue]), \
                    mock.patch.object(dispatch, "initiative_kind", return_value=False), \
                    mock.patch.object(manage, "human_activity", return_value=False), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(manage, "apply", side_effect=[low, None]) as apply:
                manage.escalation_pass()
                manage.escalation_pass()

            self.assertEqual(manager_runs, 1)
            self.assertEqual(apply.call_count, 2)
            self.assertIsNone(apply.call_args.kwargs["resume_head"])

    def test_journal_exhaustion_after_manager_fix_keeps_retry_eligible(self) -> None:
        import errno

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = config.Config(
                root, "acme/widgets", manager=["manager"], manager_rounds=1,
                workers={"code": ["worker"]},
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
            storage_failed = False
            real_record = dispatch.record

            def run(command, **_kwargs):
                nonlocal manager_runs
                if command[0] == "manager":
                    manager_runs += 1
                    return subprocess.CompletedProcess(
                        command, 0,
                        'DECISION: FIX\n{"worker":"code","guidance":"fix"}\n', "",
                    )
                if command[:3] == ["git", "rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "b" * 40 + "\n", "")
                raise AssertionError(command)

            def apply(*_args, **_kwargs):
                nonlocal storage_failed
                if not storage_failed:
                    storage_failed = True
                    lifecycle.current().defer_storage()
                    raise storage.RunnerStorageLow("gate", storage.status(cfg))

            def record(event, **fields):
                if storage_failed and event == "manage-deferred":
                    raise OSError(errno.ENOSPC, "journal filesystem full")
                return real_record(event, **fields)

            with mock.patch.object(dispatch, "gh_json", return_value=[issue]), \
                    mock.patch.object(dispatch, "initiative_kind", return_value=False), \
                    mock.patch.object(manage, "human_activity", return_value=False), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(dispatch, "record", side_effect=record), \
                    mock.patch.object(manage, "apply", side_effect=apply) as apply_mock:
                manage.escalation_pass()
                storage_failed = False
                manage.escalation_pass()

            self.assertEqual(manager_runs, 1)
            self.assertEqual(apply_mock.call_count, 2)
            self.assertTrue(apply_mock.call_args.kwargs["resume_if_ahead"])

class StorageRevisionDispatchTest(unittest.TestCase):
    setUp = dispatch_test.LocalCLI.setUp
    git = dispatch_test.LocalCLI.git
    write_config = dispatch_test.LocalCLI.write_config
    commit_config = dispatch_test.LocalCLI.commit_config
    cli = dispatch_test.LocalCLI.cli

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

    def test_preflight_precedes_ticket_journal_writes_when_disk_is_already_full(self) -> None:
        import errno

        dispatch.configure(config.load(self.repo))
        issue = {
            "number": 7, "title": "local change", "body": "Make the change.",
            "labels": [{"name": "ready-for-agent"}], "assignees": [],
        }
        value = storage.status(dispatch.cfg)
        value["ok"] = False
        value["filesystems"][0]["low"] = ["space"]
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(storage, "status", return_value=value), \
                mock.patch.object(lifecycle, "_write",
                                  side_effect=OSError(errno.ENOSPC, "journal filesystem full")), \
                mock.patch.object(dispatch, "_main", side_effect=lambda _args: dispatch.process_ticket(
                    issue, 1, False, forced=True)), \
                mock.patch("sys.stderr") as stderr:
            self.assertEqual(dispatch.main([]), 0)
        self.assertIn("runner_storage_low", str(stderr.write.call_args_list))

    def test_cli_low_storage_recovers_without_a_human_gate(self) -> None:
        self.write_config(extra="[runner]\nmin_free_gb = 1000000000\n")
        self.commit_config()
        low = self.cli("dispatch", "--ticket", "7")
        self.assertIn("runner_storage_low", low.stdout + low.stderr)
        events = lifecycle.read_events(self.events)
        self.assertFalse(any(row.get("event") in {"claimed", "attempt", "escalate"} for row in events))
        self.git("checkout", "main")  # The real dispatcher pins this fixture root detached.
        self.write_config(extra="[runner]\nmin_free_gb = 0\nmin_free_inodes = 0\n")
        self.commit_config()
        recovered = self.cli("dispatch", "--ticket", "7")
        self.assertIn("done (approved at", recovered.stdout)
        events = lifecycle.read_events(self.events)
        self.assertTrue(any(row.get("event") == "approved" for row in events))
        self.assertFalse(any(row.get("event") == "escalate" for row in events))

    def test_retry_and_bounce_temp_exhaustion_defer_without_another_attempt(self) -> None:
        import errno

        dispatch.configure(config.load(self.repo))
        issue = {
            "number": 7, "title": "local change", "body": "Make the change.",
            "labels": [{"name": "ready-for-agent"}], "assignees": [],
        }
        original_round = dispatch.worker_round
        for phase in ("retry", "bounce"):
            with self.subTest(phase=phase):
                calls = 0

                def worker(n, wt, labels, title, extra, attempt, deadline, _phase=phase, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        (wt / "saved.txt").write_text(f"worker result for {_phase}")
                        self.git("-C", str(wt), "add", "saved.txt")
                        self.git("-C", str(wt), "commit", "-qm", "worker result")
                        head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
                        return _phase == "bounce", "first gate result", None, head
                    with mock.patch.object(storage.tempfile, "mkdtemp",
                                           side_effect=OSError(errno.ENOSPC, "full")):
                        return original_round(n, wt, labels, title, extra, attempt, deadline, **kwargs)

                with mock.patch.dict(os.environ, self.env), \
                        mock.patch.object(dispatch, "worker_round", side_effect=worker), \
                        mock.patch.object(dispatch, "push_and_pr", return_value=True), \
                        mock.patch.object(dispatch, "record_risk"), \
                        mock.patch.object(dispatch, "pr_gate_and_review", return_value=("REVISE", "fix", False)), \
                        mock.patch.object(dispatch, "pr_comment"), \
                        self.assertRaises(storage.RunnerStorageLow):
                    dispatch.process_ticket(issue, 1, False, forced=True)
                self.assertEqual(calls, 2)
                self.assertTrue((self.repo / ".factory" / "wt-7" / "saved.txt").exists())
                events = lifecycle.read_events(self.events)
                self.assertFalse(any(row.get("event") in {"attempt", "escalate"} for row in events))
                self.assertTrue(any(row.get("event") == "runner_storage_low" and row.get("phase") == "worker"
                                    for row in events))


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
            cfg.manager = ["manager"]
            cfg.manager_review = "all"
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
            self.assertIsNone(dispatch.pending_refresh(9, 102))


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
                            self.assertRaises(storage.RunnerStorageLow), \
                            storage.temporary_environment("factory-worker-retry-"):
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

    def test_uncertain_consumer_identity_keeps_the_abandoned_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            abandoned = root / "factory-worker-uncertain"
            abandoned.mkdir()
            owner = {**lifecycle._identity(os.getpid()), "execution_id": "run",
                     "journal": str(root / "events.jsonl")}
            (abandoned / storage.OWNER_FILE).write_text(json.dumps(owner))
            lifecycle.append(root / "events.jsonl", {
                "execution_id": "run", "kind": "child_start",
                "child_process": {"pid": 42},
            })
            with mock.patch.object(storage.tempfile, "gettempdir", return_value=str(root)), \
                    mock.patch.object(lifecycle, "_process_state", side_effect=["dead", "unknown"]):
                storage.cleanup_abandoned()
            self.assertTrue(abandoned.exists())

    def test_unrelated_journal_errors_are_not_silently_deferred(self) -> None:
        import errno

        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", runner_min_free_gb=10**9)
            with mock.patch.object(lifecycle, "append", side_effect=OSError(errno.EIO, "I/O error")), \
                    self.assertRaises(OSError) as error:
                storage.require(cfg, "claim")
            self.assertEqual(error.exception.errno, errno.EIO)

    def test_gate_temp_setup_exhaustion_returns_75_and_records_the_host_condition(self) -> None:
        import errno

        from factory import gate as gate_module

        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", runner_min_free_gb=0,
                                runner_min_free_inodes=0)
            with mock.patch.object(config, "load", return_value=cfg), \
                    mock.patch.object(storage.tempfile, "mkdtemp", side_effect=OSError(errno.EDQUOT, "full")), \
                    mock.patch("sys.stderr"):
                self.assertEqual(gate_module.main([]), storage.EX_TEMPFAIL)
            [row] = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertEqual((row["event"], row["phase"]), ("runner_storage_low", "gate"))


from tests import test_frontier as frontier_test


class StorageFrontierRetryTest(unittest.TestCase):
    setUp = frontier_test.FrontierTest.setUp
    read = frontier_test.FrontierTest.read
    gh_json = frontier_test.FrontierTest.gh_json
    fake_run = frontier_test.FrontierTest.fake_run
    events = frontier_test.FrontierTest.events
    approved_head = frontier_test.FrontierTest.approved_head
    quiet_provider = frontier_test.FrontierTest.quiet_provider

    def test_frontier_fix_storage_deferral_reuses_decision_without_charging_round(self) -> None:
        self.cfg.manager_review = "all"
        self.cfg.workers["code"] = ["worker"]
        self.quiet_provider()
        self.approved_head(frontier_test.H)
        self.manager_output = 'DECISION: FIX\n{"worker":"code","guidance":"fix"}'
        manager_runs = 0
        low = storage.RunnerStorageLow("worker", storage.status(self.cfg))

        def run(command, *args, **kwargs):
            nonlocal manager_runs
            if command[0] == "manager-stub":
                manager_runs += 1
            return self.fake_run(command, *args, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(manage, "apply", side_effect=[low, None]) as apply:
            manage.frontier_pass()
            manage.frontier_pass()

        self.assertEqual(manager_runs, 1)
        self.assertEqual(apply.call_count, 2)
        self.assertIsNone(apply.call_args.kwargs["resume_head"])

    def test_frontier_retries_pushed_candidate_before_completed_review_evidence(self) -> None:
        self.cfg.manager_review = "all"
        self.cfg.workers["code"] = ["worker"]
        self.quiet_provider()
        self.approved_head(frontier_test.H)
        self.manager_output = 'DECISION: FIX\n{"worker":"code","guidance":"fix"}'
        candidate = frontier_test.H2
        (self.cfg.factory / "wt-79").mkdir()
        calls = []

        def apply(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                dispatch.record("attempt", ticket=79, attempt=2, head=candidate,
                                actual_head=candidate, gate="PASS")
                self.pr["headRefOid"] = candidate
                raise storage.RunnerStorageLow("gate", storage.status(self.cfg))

        def run(command, *args, **kwargs):
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, candidate, "")
            return self.fake_run(command, *args, **kwargs)

        with mock.patch.object(manage, "apply", side_effect=apply), \
                mock.patch.object(dispatch, "run", side_effect=run):
            manage.frontier_pass()
            manage.frontier_pass()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["resume_head"], candidate)
        decisions = [e for e in self.events() if e.get("event") == "manage"]
        self.assertEqual([(e["head"], e["round"]) for e in decisions], [(frontier_test.H, 1)])
        applied = [e for e in self.events() if e.get("event") == "manage-applied"]
        self.assertEqual([(e["decision_head"], e["status"]) for e in applied],
                         [(frontier_test.H, "applied")])


class StorageStartupTest(unittest.TestCase):
    def test_startup_uses_runner_settings_without_loading_stale_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".factory.toml").write_text("[runner]\nmin_free_gb = 2.5\nmin_free_inodes = 123\n")
            seen = []

            def probe(cfg):
                seen.append((cfg.runner_min_free_gb, cfg.runner_min_free_inodes))
                return {"ok": False, "thresholds": {"free_bytes": 0, "free_inodes": 0}, "filesystems": []}

            with mock.patch.object(config, "repo_root", return_value=root), \
                    mock.patch.object(config, "load", side_effect=config.ConfigError("stale")) as load, \
                    mock.patch.object(storage, "status", side_effect=probe), \
                    mock.patch.object(dispatch, "pin_root") as pin:
                self.assertEqual(dispatch.main(["--ticket", "7"]), 0)
            self.assertEqual(seen, [(2.5, 123)])
            load.assert_not_called()
            pin.assert_not_called()

    def test_real_dispatch_entry_defers_before_startup_writes(self) -> None:
        import errno

        for metric in ("space", "inodes"):
            for exhausted in (False, True):
                with self.subTest(metric=metric, journal_full=exhausted), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / ".factory.toml").write_text("[runner]\nmin_free_gb = 1\nmin_free_inodes = 100\n")
                    cfg = config.Config(root, "acme/widgets")
                    value = storage.status(cfg)
                    value["ok"] = False
                    value["filesystems"][0]["low"] = [metric]
                    write = (mock.patch.object(lifecycle, "_write",
                             side_effect=OSError(errno.ENOSPC, "full"))
                             if exhausted else mock.patch.object(lifecycle, "_write", wraps=lifecycle._write))
                    with mock.patch.object(config, "repo_root", return_value=root), \
                            mock.patch.object(config, "load", return_value=cfg), \
                            mock.patch.object(storage, "status", return_value=value), write, \
                            mock.patch.object(dispatch, "pin_root",
                                              side_effect=OSError(errno.ENOSPC, "fetch wrote to full disk")) as pin, \
                            mock.patch("sys.stderr") as stderr:
                        self.assertEqual(dispatch.main([]), 0)
                    pin.assert_not_called()
                    rows = lifecycle.read_events(cfg.factory / "events.jsonl")
                    if exhausted:
                        self.assertIn("runner_storage_low", str(stderr.write.call_args_list))
                    else:
                        self.assertEqual([e["event"] for e in rows], ["runner_storage_low"])
                    self.assertFalse((cfg.factory / "wt-7").exists())

    def test_ticket_pass_preflight_precedes_admission_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", runner_min_free_gb=10**9)
            dispatch.configure(cfg)
            issue = {"number": 7, "title": "fix", "body": "", "labels": []}
            with mock.patch.object(dispatch, "active_tickets", return_value=[]), \
                    mock.patch.object(dispatch, "frontier", return_value=[issue]), \
                    mock.patch.object(dispatch, "process_ticket") as claim, \
                    self.assertRaises(storage.RunnerStorageLow):
                dispatch.ticket_pass(1, False)
            claim.assert_not_called()
            rows = lifecycle.read_events(dispatch.EVENTS)
            self.assertEqual([e["event"] for e in rows], ["runner_storage_low"])


class ManagerGateEvidenceTest(unittest.TestCase):
    setUp = dispatch_test.LocalCLI.setUp
    git = dispatch_test.LocalCLI.git
    write_config = dispatch_test.LocalCLI.write_config

    def prepare(self):
        dispatch.configure(config.load(self.repo))
        wt = self.repo / ".factory" / "wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", str(wt), "-b", "agent/7")
        lease = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        self.git("push", "origin", "agent/7")
        (wt / "saved.txt").write_text("saved worker result")
        self.git("-C", str(wt), "add", "saved.txt")
        self.git("-C", str(wt), "commit", "-qm", "worker result")
        head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        packet = wt.parent / "packet.md"
        packet.write_text("fix")
        self.state.write_text('{"reviews": 1}')
        issue = {"number": 7, "title": "fix", "body": "", "labels": [{"name": "ready-for-human"}]}
        dispatch.record("attempt", ticket=7, attempt=4, head=lease,
                        actual_head=lease, gate="FAIL", worker_model="worker-model")
        def github(args):
            value = json.loads(dispatch.run(["gh", *args], cwd=self.repo).stdout)
            if args[:2] == ["pr", "view"]:
                value["headRefOid"] = self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0]
            return value

        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=github))
        return wt, lease, head, packet, issue

    def test_deferred_manager_gate_recovery_reaches_real_approval(self) -> None:
        _wt, lease, head, packet, issue = self.prepare()
        # Only external processes are stand-ins: push, review and approval contracts run.
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "worker_round") as worker, \
                mock.patch.object(dispatch, "run_gate", side_effect=[
                    storage.RunnerStorageLow("gate", storage.status(dispatch.cfg)),
                    (True, "gate PASS"),
                ]):
            with self.assertRaises(storage.RunnerStorageLow):
                manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"},
                             packet, resume_head=head)
            manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"},
                         packet, resume_head=head)
        worker.assert_not_called()
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual([e["head"] for e in rows if e.get("event") == "approved"], [head])
        self.assertEqual([e["attempt"] for e in rows if e.get("event") == "attempt"], [4])
        self.assertTrue(dispatch._head_evidence_matches(rows, 7, head))
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], head)
        self.assertNotEqual(lease, head)

    def reject_drift(self, drift: str) -> None:
        wt, lease, head, packet, issue = self.prepare()
        if drift == "dirty-before":
            (wt / "dirty").touch()

        def gate(*args, **kwargs):
            if drift == "dirty-after":
                (wt / "dirty").touch()
            if drift == "head-after":
                self.git("-C", str(wt), "commit", "--allow-empty", "-qm", "drift")
            return True, "gate PASS"

        expected = lease if drift == "head-before" else head
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "run_gate", side_effect=gate), \
                mock.patch.object(dispatch, "worker_round") as worker, \
                mock.patch.object(dispatch, "push_agent") as push, \
                mock.patch.object(dispatch, "pr_gate_and_review") as review:
            if drift == "head-before":
                with self.assertRaises(ValueError):
                    manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"},
                                 packet, resume_head=expected)
            else:
                manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"},
                             packet, resume_head=expected)
        worker.assert_not_called()
        push.assert_not_called()
        review.assert_not_called()
        self.assertFalse(dispatch._head_evidence_matches(lifecycle.read_events(dispatch.EVENTS), 7, head))

    def test_dirty_before_resumed_gate_is_rejected(self) -> None:
        self.reject_drift("dirty-before")

    def test_dirty_after_resumed_gate_is_rejected(self) -> None:
        self.reject_drift("dirty-after")

    def test_head_changed_during_resumed_gate_is_rejected(self) -> None:
        self.reject_drift("head-after")

    def test_head_changed_before_resumed_gate_is_rejected(self) -> None:
        self.reject_drift("head-before")

    def test_worker_phase_retry_uses_next_attempt_number(self) -> None:
        _wt, _, head, packet, issue = self.prepare()
        self.git("push", "origin", "agent/7")
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "worker_round", return_value=(False, "FAIL", None, head)) as worker:
            manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
        self.assertEqual(worker.call_args.args[5], 5)

    def test_frontier_pr_gate_deferral_recovers_pushed_candidate_with_real_approval_checks(self) -> None:
        wt, _, original, _packet, issue = self.prepare()
        self.git("push", "origin", "agent/7")
        cfg = dispatch.cfg
        cfg.manager = ["manager-stub"]
        cfg.manager_review = "all"
        cfg.manager_rounds = 1
        cfg.workers["code"] = ["worker-stub"]
        cfg.checks.append(config.Check("mutation", ["mutation-stub"], when="pr"))
        dispatch.record("attempt", ticket=7, attempt=4, head=original, actual_head=original, gate="PASS")
        dispatch.record("review", ticket=7, head=original, actual_head=original,
                        verdict="APPROVE", accepted=True)
        real_run, real_github = dispatch.run, dispatch.gh_json
        candidate = []
        manager_calls = []

        def run(command, **kwargs):
            if command[0] == "manager-stub":
                manager_calls.append(command)
                return subprocess.CompletedProcess(command, 0,
                    'DECISION: FIX\n{"worker":"code","guidance":"fix"}', "")
            return real_run(command, **kwargs)

        def github(args):
            if args[:2] == ["pr", "list"]:
                return [{**frontier_test.pr_row(), **real_github(["pr", "view", "agent/7"]),
                         "isCrossRepository": False, "labels": []}]
            if args[:2] == ["issue", "view"]:
                return {**issue, "state": "OPEN", "labels": [{"name": "ready-for-agent"}]}
            return real_github(args)

        def worker(*args, **kwargs):
            (wt / "candidate.txt").write_text("fixed")
            self.git("-C", str(wt), "add", "candidate.txt")
            self.git("-C", str(wt), "commit", "-qm", "manager fix")
            candidate.append(self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip())
            dispatch.record("attempt", ticket=7, attempt=5, head=candidate[0],
                            actual_head=candidate[0], gate="PASS", worker_model="worker-model")
            return True, "attempt PASS", None, candidate[0]

        def gate(*args, **kwargs):
            if len(gate_calls) == 0:
                gate_calls.append("deferred")
                storage.record_low(cfg, "gate", storage.status(cfg))
                raise storage.RunnerStorageLow("gate", storage.status(cfg))
            gate_calls.append(kwargs.get("tier", "attempt"))
            return True, "gate PASS"

        gate_calls = []
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "pr_checks", return_value=[]), \
                mock.patch.object(manage, "observe_pr", return_value={"owner": {"relation": "factory_issue"}}), \
                mock.patch.object(manage, "undelivered", return_value=[]), \
                mock.patch.object(dispatch, "worker_round", side_effect=worker) as worker_mock, \
                mock.patch.object(dispatch, "run_gate", side_effect=gate), \
                mock.patch.object(dispatch, "approve_pr", wraps=dispatch.approve_pr) as approval:
            manage.frontier_pass()
            self.assertEqual(self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], candidate[0])
            self.assertFalse(dispatch._head_evidence_matches(lifecycle.read_events(dispatch.EVENTS), 7, candidate[0]))
            manage.frontier_pass()
        worker_mock.assert_called_once()
        self.assertEqual(len(manager_calls), 1)
        self.assertEqual(gate_calls, ["deferred", "attempt", "pr"])
        approval.assert_called_once_with(7, candidate[0])
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(dispatch._head_evidence_matches(rows, 7, candidate[0]))
        self.assertFalse(any(e.get("event") == "escalate" for e in rows))
        self.assertEqual([e["round"] for e in rows if e.get("event") == "manage"], [1])
        self.assertEqual([e["decision_head"] for e in rows if e.get("event") == "manage-applied"], [original])
        # review=all still requires a fresh final APPROVE for the new head.
        self.assertFalse(any(e.get("event") == "approved" for e in rows))


class StorageDecisionIdentityTest(unittest.TestCase):
    def test_pending_fix_requires_exact_decision_and_candidate_identity(self) -> None:
        decision = {
            "event": "manage", "ticket": 7, "pr": 80, "round": 1, "head": "b"*40,
            "decision": "FIX", "apply_pending": True, "body": "fix", "data": {"worker": "code"},
            "packet": "packet.md",
        }
        deferred = {**decision, "event": "manage-deferred", "head": "c"*40, "decision_head": "b"*40}
        pending = manage._pending_frontier_fix([decision, deferred], 7, 80, "c"*40)
        self.assertEqual(pending["resume_head"], "c"*40)
        self.assertEqual(pending["decision_head"], "b"*40)
        for field, value in (
            ("ticket", 8), ("pr", 79), ("pr", 81), ("round", 2),
            ("decision_head", "a"*40), ("decision_head", "d"*40),
            ("decision", "CLOSE"), ("body", "other"), ("data", {}), ("packet", "other.md"),
        ):
            with self.subTest(field=field, value=value):
                self.assertIsNone(manage._pending_frontier_fix(
                    [decision, {**deferred, field: value}], 7, 80, "c"*40,
                ))
        for head in ("a"*40, "d"*40):
            self.assertIsNone(manage._pending_frontier_fix([decision, deferred], 7, 80, head))
        for field, value in (("decision", "CLOSE"), ("decision", "HUMAN"), ("apply_pending", False),
                             ("ticket", 8), ("pr", 81), ("event", "review")):
            with self.subTest(decision_field=field):
                self.assertIsNone(manage._pending_frontier_fix(
                    [{**decision, field: value}, deferred], 7, 80, "c"*40,
                ))
        applied = {"event": "manage-applied", "ticket": 7, "pr": 80,
                   "round": 1, "decision_head": "b"*40}
        self.assertIsNone(manage._pending_frontier_fix([decision, deferred, applied], 7, 80, "c"*40))
        for field, value in (("ticket", 8), ("pr", 79), ("pr", 81), ("round", 2),
                             ("decision_head", "a"*40), ("event", "review")):
            with self.subTest(applied_field=field, value=value):
                self.assertIsNotNone(manage._pending_frontier_fix(
                    [decision, deferred, {**applied, field: value}], 7, 80, "c"*40,
                ))

    def test_frontier_rows_do_not_replay_or_suppress_escalation_round(self) -> None:
        for frontier_event in ("manage-deferred", "manage-applied"):
            with self.subTest(frontier_event=frontier_event), tempfile.TemporaryDirectory() as directory:
                cfg = config.Config(Path(directory), "acme/widgets", manager=["manager"],
                                    workers={"code": ["worker"]})
                dispatch.configure(cfg)
                packet = cfg.factory / "packet.md"
                packet.parent.mkdir()
                packet.write_text("new escalation")
                dispatch.record("manage", ticket=7, round=1, pr=80, head="a"*40,
                                decision="FIX", apply_pending=True)
                dispatch.record(frontier_event, ticket=7, round=1, pr=80, decision_head="a"*40,
                                decision="FIX", body="frontier", data={}, packet=str(packet))
                dispatch.record("escalate", ticket=7, round=1, packet=str(packet))
                # A legitimate escalation FIX must not be hidden by a PR's applied row.
                if frontier_event == "manage-applied":
                    dispatch.record("manage", ticket=7, round=1, decision="FIX", apply_pending=True,
                                    body="escalation", data={}, packet=str(packet))
                    dispatch.record(frontier_event, ticket=7, round=1, pr=80, decision_head="a"*40)
                issue = {"number": 7, "title": "fix", "labels": [{"name": "ready-for-human"}]}
                with mock.patch.object(dispatch, "gh_json", return_value=[issue]), \
                        mock.patch.object(dispatch, "initiative_kind", return_value=False), \
                        mock.patch.object(manage, "human_activity", return_value=False), \
                        mock.patch.object(dispatch, "run", return_value=subprocess.CompletedProcess(
                            [], 0, "DECISION: HUMAN\nnew decision", "")) as manager, \
                        mock.patch.object(manage, "apply") as apply:
                    manage.escalation_pass()
                apply.assert_called_once()
                self.assertEqual(apply.call_args.args[3],
                                 "new decision" if frontier_event == "manage-deferred" else "escalation")
                self.assertEqual(manager.call_count, 1 if frontier_event == "manage-deferred" else 0)

    def test_refresh_pending_is_bound_to_exact_pr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dispatch.configure(config.Config(Path(directory), "acme/widgets"))
            dispatch.record("refresh-pending", ticket=7, pr=80, head="a"*40)
            dispatch.record("refresh-pending", ticket=7, pr=81, head="b"*40)
            dispatch.record("refresh-pending", ticket=7, pr=79, head="c"*40)
            self.assertEqual(dispatch.pending_refresh(7, 80)["head"], "a"*40)
            self.assertIsNone(dispatch.pending_refresh(8, 80))
            self.assertIsNone(dispatch.pending_refresh(7, 82))

    def test_storage_deferral_is_a_retryable_lifecycle_outcome_even_with_full_journal(self) -> None:
        import errno

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            with lifecycle.scope(path, "dispatcher", lazy=True) as parent, \
                    lifecycle.scope(path, "gate", lazy=True) as child:
                child.defer_storage()
                self.assertTrue(parent.storage_deferred)
                self.assertTrue(child.storage_deferred)
                self.assertEqual((child.outcome, child.reason), ("not_eligible", "runner_storage_low"))
                with mock.patch.object(lifecycle, "_write", side_effect=OSError(errno.ENOSPC, "full")), \
                        mock.patch("sys.stderr") as stderr:
                    child.wait("runner_storage_low", mode="retry_next_pass")
                self.assertIn("runner_storage_low", str(stderr.write.call_args_list))
            exits = [e for e in lifecycle.read_events(path) if e.get("kind") == "exit"]
            self.assertTrue(exits)
            self.assertTrue(all(e["outcome"] == "not_eligible" for e in exits))


class StorageOwnershipBoundaryTest(unittest.TestCase):
    def test_exited_child_is_not_a_live_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = {**lifecycle._identity(os.getpid()), "execution_id": "run",
                     "journal": str(root / "events.jsonl")}
            rows = [{"execution_id": "run", "kind": "child_exit", "child_process": {"pid": 42}}]
            with mock.patch.object(lifecycle, "read_events", return_value=rows), \
                    mock.patch.object(Path, "iterdir", return_value=iter([])), \
                    mock.patch.object(lifecycle, "_process_state", return_value="alive") as state:
                self.assertTrue(storage._consumers_stopped(root, owner))
            state.assert_not_called()

    def test_unreadable_consumer_journal_keeps_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = {**lifecycle._identity(os.getpid()), "execution_id": "run",
                     "journal": str(root / "events.jsonl")}
            for error in (OSError("unreadable"), TypeError("malformed")):
                with self.subTest(error=type(error)), \
                        mock.patch.object(lifecycle, "read_events", side_effect=error):
                    self.assertFalse(storage._consumers_stopped(root, owner))

    def test_same_tick_consumer_and_malformed_process_are_not_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / "123"
            process.mkdir()
            (process / "environ").write_bytes(b"TMPDIR=" + os.fsencode(root))
            owner = lifecycle._identity(os.getpid())
            for ticks in (str(owner["start_ticks"]), "not-a-number"):
                fields = ["S", "1", "1", "1", *(["0"] * 15), ticks]
                (process / "stat").write_text("123 (child) " + " ".join(fields))
                with self.subTest(ticks=ticks), \
                        mock.patch.object(Path, "iterdir", return_value=iter([process])):
                    self.assertFalse(storage._consumers_stopped(root, owner))

    def test_space_threshold_uses_gibibytes_and_available_not_total_blocks(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets",
                                runner_min_free_gb=1, runner_min_free_inodes=100)
            info = SimpleNamespace(f_bavail=1024**2, f_frsize=1024, f_favail=100, f_files=1000)
            with mock.patch.object(storage.os, "statvfs", return_value=info):
                value = storage.status(cfg)
                self.assertTrue(value["ok"])
                self.assertEqual(value["thresholds"], {"free_bytes": 1024**3, "free_inodes": 100})
                info.f_bavail -= 1
                self.assertEqual(storage.status(cfg)["filesystems"][0]["low"], ["space"])
                info.f_bavail += 1
                info.f_favail -= 1
                self.assertEqual(storage.status(cfg)["filesystems"][0]["low"], ["inodes"])

    def test_temp_setup_failure_has_zero_capacity_thresholds(self) -> None:
        import errno

        with mock.patch.object(storage.tempfile, "mkdtemp", side_effect=OSError(errno.ENOSPC, "full")), \
                self.assertRaises(storage.RunnerStorageLow) as raised, \
                storage.temporary_environment("factory-worker-test-"):
            self.fail("started without storage")
        self.assertEqual(raised.exception.status["thresholds"], {"free_bytes": 0, "free_inodes": 0})


if __name__ == "__main__":
    unittest.main()
