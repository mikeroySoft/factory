"""Deterministic contracts for restart-safe human decisions; no live mutations."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest import mock

from factory import briefing, config, decisions, lifecycle, outcomes


class DecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.cfg = config.Config(root=self.root, repo="acme/widgets")
        self.actor = "alice"
        self.clock = 1
        self.issue = {
            "number": 7,
            "title": "Fix parser",
            "body": "Complete issue body",
            "state": "OPEN",
            "labels": [{"name": config.LABEL_HUMAN}],
            "assignees": [],
            "updatedAt": "2026-10-09T00:00:01Z",
            "url": "https://github.com/acme/widgets/issues/7",
        }
        self.pr = {
            "number": 17,
            "title": "Fix parser",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "agent/7",
            "headRefOid": "a" * 40,
            "baseRefName": "main",
            "labels": [],
            "reviewDecision": "",
            "updatedAt": "2026-10-09T00:00:01Z",
        }
        self.calls: list[list[str]] = []
        self.fail_action: str | None = None
        self.ambiguous_action: str | None = None
        self.applied_then_nonzero_action: str | None = None
        self.branch = False
        self.branch_sha = "a" * 40
        self.worktree_head = self.branch_sha
        self.worktree_dirty = False
        self.guard = threading.Lock()
        self.real_run = decisions._run
        self.run_patch = mock.patch.object(decisions, "_run", side_effect=self.transport)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    @staticmethod
    def completed(argv: list[str], code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, out, err)

    def transport(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        with self.guard:
            if argv[:5] == ["gh", "api", "--hostname", "github.com", "--method"]:
                return self.completed(argv, out=self.actor + "\n")
            if argv[:3] == ["gh", "issue", "view"]:
                return self.completed(argv, out=json.dumps({key: self.issue[key] for key in argv[-1].split(",")}))
            if argv[:3] == ["gh", "pr", "view"]:
                return self.completed(argv, out=json.dumps(self.pr))
            if argv[:4] == ["git", "-C", str(self.root), "branch"] and "--list" in argv:
                return self.completed(argv, out=self.branch_sha + "\n" if self.branch else "")
            worktree = self.cfg.factory / "wt-7"
            if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "status":
                status = (
                    f"# branch.oid {self.worktree_head}\n"
                    "# branch.head agent/7\n"
                    + ("1 .M N... 100644 100644 100644 abc abc tracked.txt\n" if self.worktree_dirty else "")
                )
                return self.completed(argv, out=status)

            action = argv[2] if argv[0] == "gh" else (
                "cleanup_worktree" if "worktree" in argv else "cleanup_branch" if "update-ref" in argv else argv[0]
            )
            self.calls.append(list(argv))
            if action == self.ambiguous_action:
                raise decisions._AmbiguousCommand("connection ended without a result")
            if action == self.fail_action:
                return self.completed(argv, 1, err="denied")
            if argv[:3] in (["gh", "issue", "comment"], ["gh", "pr", "comment"]):
                self.touch()
            elif argv[:3] == ["gh", "issue", "edit"]:
                self.edit(self.issue, argv)
            elif argv[:3] == ["gh", "pr", "edit"]:
                self.edit(self.pr, argv)
            elif argv[:3] == ["gh", "issue", "close"]:
                self.issue["state"] = "CLOSED"
                self.touch()
            elif "worktree" in argv:
                if worktree.exists():
                    worktree.rmdir()
            elif "update-ref" in argv and "-d" in argv:
                self.branch = False
            if action == self.applied_then_nonzero_action:
                return self.completed(argv, 1, err="response lost")
            return self.completed(argv, out="ok")

    def touch(self) -> None:
        self.clock += 1
        self.issue["updatedAt"] = f"2026-10-09T00:00:{self.clock:02d}Z"

    def edit(self, target: dict, argv: list[str]) -> None:
        labels = {item["name"] for item in target["labels"]}
        for index, value in enumerate(argv):
            if value == "--add-label":
                labels.add(argv[index + 1])
            elif value == "--remove-label":
                labels.discard(argv[index + 1])
            elif value == "--add-assignee":
                self.issue["assignees"].append({"login": argv[index + 1]})
            elif value == "--remove-assignee":
                self.issue["assignees"] = [
                    item for item in self.issue["assignees"]
                    if item["login"].casefold() != argv[index + 1].casefold()
                ]
        target["labels"] = [{"name": label} for label in sorted(labels)]
        if target is self.issue:
            self.touch()
        else:
            self.clock += 1
            self.pr["updatedAt"] = f"2026-10-09T00:00:{self.clock:02d}Z"

    def mutations(self) -> list[str]:
        return [
            argv[2] if argv[0] == "gh" else "worktree" if "worktree" in argv else "branch"
            for argv in self.calls
        ]

    def proposal(self, requests: list[dict]) -> dict:
        return decisions.prepare(self.cfg, requests)

    def apply(self, preview: dict) -> dict:
        return decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])

    def test_closed_requests_actor_and_exact_target_drift_fail_before_intent(self) -> None:
        with self.assertRaises(decisions.DecisionError) as caught:
            self.proposal([{"op": "issue", "number": 7, "comment": "x", "argv": ["rm", "-rf", "/"]}])
        self.assertEqual(caught.exception.code, "invalid_request")

        for drift, code in (
            (lambda: setattr(self, "actor", "mallory"), "stale_actor"),
            (lambda: self.issue["labels"].append({"name": config.LABEL_AGENT}), "stale_target"),
        ):
            with self.subTest(code=code):
                self.actor = "alice"
                self.issue["labels"] = [{"name": config.LABEL_HUMAN}]
                preview = self.proposal([{"op": "issue", "number": 7, "add": [config.LABEL_AGENT]}])
                drift()
                with self.assertRaises(decisions.DecisionError) as changed:
                    self.apply(preview)
                self.assertEqual(changed.exception.code, code)
                self.assertEqual(self.calls, [])
                self.assertFalse((self.cfg.factory / "decisions" / f"intent-{preview['proposal_id']}.json").exists())

        self.actor = "alice"
        preview = self.proposal([{"op": "pr", "number": 17, "comment": "Hold exact head"}])
        self.pr["headRefOid"] = "b" * 40
        with self.assertRaises(decisions.DecisionError) as changed:
            self.apply(preview)
        self.assertEqual(changed.exception.code, "stale_target")
        self.assertEqual(self.calls, [])

    def test_factory_approval_requires_existing_exact_head_gate_and_independent_review(self) -> None:
        request = {"op": "pr", "number": 17, "add": [config.LABEL_APPROVED]}
        with self.assertRaises(decisions.DecisionError) as absent:
            self.proposal([request])
        self.assertEqual(absent.exception.code, "approval_evidence")
        self.assertNotIn(config.LABEL_APPROVED, {item["name"] for item in self.pr["labels"]})

        events = self.cfg.factory / "events.jsonl"
        lifecycle.append(events, {
            "event": "attempt", "ticket": 7, "gate": "PASS",
            "head": self.pr["headRefOid"], "actual_head": self.pr["headRefOid"],
        })
        lifecycle.append(events, {
            "event": "review", "ticket": 7, "verdict": "APPROVE", "accepted": True,
            "head": self.pr["headRefOid"], "actual_head": self.pr["headRefOid"],
        })
        preview = self.proposal([request])
        self.pr["headRefOid"] = "c" * 40
        with self.assertRaises(decisions.DecisionError) as stale:
            self.apply(preview)
        self.assertEqual(stale.exception.code, "stale_target")
        self.assertEqual(self.calls, [])

    def test_nonzero_mutations_are_uncertain_and_stop_later_steps(self) -> None:
        comment = "Factory human decision: Retry\n\nRationale: Corrected scope."
        request = {
            "op": "issue", "number": 7, "comment": comment,
            "add": [config.LABEL_AGENT], "close": "not planned",
        }
        self.fail_action = "comment"
        first = self.apply(self.proposal([request]))
        self.assertEqual((first["ok"], first["status"], self.mutations()), (False, "uncertain", ["comment"]))

        self.calls.clear()
        self.fail_action = "edit"
        second = self.apply(self.proposal([request]))
        self.assertEqual((second["ok"], second["status"], self.mutations()), (False, "uncertain", ["comment", "edit"]))
        self.assertEqual([step["status"] for step in second["steps"]], ["applied", "uncertain"])
        self.assertNotIn("close", self.mutations())
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        final = [row for row in rows if row.get("event") == "human-decision"][-1]
        self.assertEqual((final["actor"], final["request"]["comment"]), ("alice", comment))
        self.assertEqual(final["targets"][0]["number"], 7)
        self.assertEqual(final["receipt"]["status"], "uncertain")
        self.assertEqual(final["receipt"]["steps"][-1]["action"], "issue_edit")
        sources = briefing.sources_for(self.cfg, {
            "number": 7, "title": self.issue["title"], "url": self.issue["url"], "body": self.issue["body"],
        }, [])
        recorded = [source for source in sources if source["label"].startswith("Recorded human decision")]
        self.assertEqual(json.loads(recorded[-1]["text"])["receipt"]["status"], "uncertain")

    def test_nonzero_pr_comment_stops_label_mutation(self) -> None:
        self.fail_action = "comment"
        result = self.apply(self.proposal([{
            "op": "pr", "number": 17, "comment": "Human hold", "add": [config.LABEL_HUMAN],
        }]))
        self.assertEqual((result["status"], self.mutations()), ("uncertain", ["comment"]))
        self.assertNotIn(config.LABEL_HUMAN, {item["name"] for item in self.pr["labels"]})

    def test_unwritable_intent_journal_prevents_every_external_effect(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Do not bypass audit"}])
        with (
            mock.patch.object(lifecycle, "append", side_effect=OSError("read-only filesystem")),
            self.assertRaises(decisions.DecisionError) as caught,
        ):
            self.apply(preview)
        self.assertEqual(caught.exception.code, "intent_unavailable")
        self.assertEqual(self.calls, [])
        retained = decisions.receipt(self.cfg, preview["proposal_id"])
        self.assertEqual((retained["status"], retained["steps"]), ("failure", []))

    def test_failed_immutable_intent_write_prevents_every_external_effect(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Persist first"}])
        store = decisions._store_artifact

        def refuse_intent(directory: int, name: str, value: dict) -> None:
            if name.startswith("intent-"):
                raise decisions.DecisionError("disk full", "storage_unavailable")
            store(directory, name, value)

        with (
            mock.patch.object(decisions, "_store_artifact", side_effect=refuse_intent),
            self.assertRaises(decisions.DecisionError) as caught,
        ):
            self.apply(preview)
        self.assertEqual(caught.exception.code, "storage_unavailable")
        self.assertEqual(self.calls, [])
        self.assertEqual(decisions.receipt(self.cfg, preview["proposal_id"])["status"], "prepared")

    def test_duplicate_restart_and_concurrent_apply_execute_each_step_once(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Once"}])
        stored = json.loads(
            (self.cfg.factory / "decisions" / f"proposal-{preview['proposal_id']}.json").read_text()
        )
        self.assertEqual(stored["preview"], preview)
        first = self.apply(preview)
        replay = self.apply(preview)
        self.assertEqual(first, replay)
        self.assertEqual(self.mutations(), ["comment"])

        self.calls.clear()
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Concurrent once"}])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.apply(preview), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.mutations(), ["comment"])

    def test_crash_or_ambiguous_transport_after_intent_is_uncertain_and_never_retried(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "May have arrived"}])
        with (
            mock.patch.object(decisions, "_mutation", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.apply(preview)
        observed = decisions.receipt(self.cfg, preview["proposal_id"])
        self.assertEqual(observed["status"], "uncertain")
        replay = self.apply(preview)
        self.assertEqual(replay["status"], "uncertain")
        self.assertEqual(self.calls, [])

        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Transport uncertain"}])
        self.ambiguous_action = "comment"
        uncertain = self.apply(preview)
        self.assertEqual(uncertain["status"], "uncertain")
        call_count = len(self.calls)
        self.assertEqual(self.apply(preview), uncertain)
        self.assertEqual(len(self.calls), call_count)

    def test_applied_then_nonzero_is_uncertain_and_never_retried(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Response may be lost"}])
        self.applied_then_nonzero_action = "comment"
        result = self.apply(preview)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.issue["updatedAt"], "2026-10-09T00:00:02Z")
        call_count = len(self.calls)
        self.assertEqual(self.apply(preview), result)
        self.assertEqual(len(self.calls), call_count)

    def test_interrupted_receipt_write_never_publishes_a_partial_artifact(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Write atomically"}])
        store = decisions._store_artifact

        def interrupt_receipt(directory: int, name: str, value: dict) -> None:
            if not name.startswith("receipt-"):
                store(directory, name, value)
                return
            writes = 0

            def interrupted_write(fd: int, payload) -> int:
                nonlocal writes
                writes += 1
                if writes > 1:
                    raise KeyboardInterrupt
                return min(1, len(payload))

            with mock.patch.object(decisions.os, "write", side_effect=interrupted_write):
                store(directory, name, value)

        with (
            mock.patch.object(decisions, "_store_artifact", side_effect=interrupt_receipt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.apply(preview)
        artifacts = self.cfg.factory / "decisions"
        self.assertFalse((artifacts / f"receipt-{preview['proposal_id']}.json").exists())
        self.assertEqual(list(artifacts.glob(".tmp-*")), [])
        self.assertEqual(decisions.receipt(self.cfg, preview["proposal_id"])["status"], "uncertain")
        call_count = len(self.calls)
        self.assertEqual(self.apply(preview)["status"], "uncertain")
        self.assertEqual(len(self.calls), call_count)

    def test_interrupted_receipt_publication_remains_readable_and_nonretriable(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Publish once"}])
        rename = decisions._RENAME_NOREPLACE

        def publish_then_interrupt(source_fd, source, target_fd, target, flags):
            result = rename(source_fd, source, target_fd, target, flags)
            if result == 0 and target.startswith(b"receipt-"):
                raise KeyboardInterrupt
            return result

        with (
            mock.patch.object(decisions, "_RENAME_NOREPLACE", side_effect=publish_then_interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.apply(preview)
        call_count = len(self.calls)
        retained = decisions.receipt(self.cfg, preview["proposal_id"])
        self.assertEqual(retained["status"], "success")
        self.assertEqual(self.apply(preview), retained)
        self.assertEqual(len(self.calls), call_count)
        directory = decisions._state_dir(self.cfg, "decisions", create=False)
        try:
            with self.assertRaises(decisions.DecisionError) as caught:
                decisions._store_artifact(directory, f"receipt-{preview['proposal_id']}.json", {})
            self.assertEqual(caught.exception.code, "conflict")
        finally:
            os.close(directory)
        self.assertEqual(decisions.receipt(self.cfg, preview["proposal_id"]), retained)

    def test_sync_routing_and_live_cleanup_claims_are_refused_without_unlinking_lock(self) -> None:
        self.issue["title"] = "upstream sync: conflict at abc123"
        with self.assertRaises(decisions.DecisionError) as sync:
            self.proposal([{"op": "issue", "number": 7, "add": [config.LABEL_AGENT]}])
        self.assertEqual(sync.exception.code, "routing_refused")
        self.assertEqual(self.calls, [])

        self.issue.update(title="Merged ticket", state="CLOSED", labels=[], assignees=[])
        worktree = self.cfg.factory / "wt-7"
        worktree.mkdir(parents=True)
        self.branch = True
        lock = self.cfg.factory / "locks" / "7.lock"
        merge_lock = self.cfg.factory / "locks" / "merge.lock"
        lock.parent.mkdir(exist_ok=True)
        with merge_lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(decisions.DecisionError) as busy:
                self.proposal([{"op": "cleanup", "number": 7}])
            self.assertEqual(busy.exception.code, "target_busy")
        with lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(decisions.DecisionError) as busy:
                self.proposal([{"op": "cleanup", "number": 7}])
            self.assertEqual(busy.exception.code, "target_busy")
            self.assertTrue(worktree.is_dir())
            self.assertTrue(lock.exists())
        self.worktree_dirty = True
        with self.assertRaises(decisions.DecisionError) as dirty:
            self.proposal([{"op": "cleanup", "number": 7}])
        self.assertEqual(dirty.exception.code, "target_busy")
        self.worktree_dirty = False
        preview = self.proposal([{"op": "cleanup", "number": 7}])
        proposal = json.loads(
            (self.cfg.factory / "decisions" / f"proposal-{preview['proposal_id']}.json").read_text()
        )
        self.assertEqual(proposal["locks"], [{"kind": "merge"}, {"kind": "ticket", "number": 7}])
        cleanup_target = proposal["preview"]["targets"][0]
        self.assertEqual((cleanup_target["branch"], cleanup_target["worktree"]["head"]), ("a" * 40, "a" * 40))
        with merge_lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(decisions.DecisionError) as busy:
                self.apply(preview)
            self.assertEqual(busy.exception.code, "target_busy")
        with lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(decisions.DecisionError) as busy:
                self.apply(preview)
            self.assertEqual(busy.exception.code, "target_busy")
            self.assertTrue(worktree.is_dir())
            self.assertTrue(lock.exists())
        self.branch_sha = self.worktree_head = "b" * 40
        with self.assertRaises(decisions.DecisionError) as changed:
            self.apply(preview)
        self.assertEqual(changed.exception.code, "stale_target")
        self.branch_sha = self.worktree_head = "a" * 40
        self.worktree_dirty = True
        with self.assertRaises(decisions.DecisionError) as dirty:
            self.apply(preview)
        self.assertEqual(dirty.exception.code, "target_busy")
        self.worktree_dirty = False
        result = self.apply(preview)
        self.assertEqual(result["status"], "success")
        self.assertFalse(worktree.exists())
        self.assertTrue(lock.exists(), "cleanup must retain lock identity even after release")
        cleanup_rows = [
            row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
            if row.get("decision_id") == preview["proposal_id"]
        ]
        self.assertTrue(cleanup_rows)
        self.assertTrue(all(row.get("ticket") == 7 and "pr" not in row for row in cleanup_rows))

        self.issue["assignees"] = [{"login": "bob"}]
        worktree.mkdir()
        self.branch = True
        with self.assertRaises(decisions.DecisionError) as owned:
            self.proposal([{"op": "cleanup", "number": 7}])
        self.assertEqual(owned.exception.code, "target_busy")
        self.assertTrue(worktree.exists())

    def test_triage_uses_the_trusted_package_with_a_sanitized_import_path(self) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True, capture_output=True)
        shadow = self.root / "factory"
        shadow.mkdir()
        marker = self.root / "shadow-executed"
        (shadow / "__init__.py").write_text("")
        (shadow / "__main__.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        (self.root / ".factory.toml").write_text('[repo]\nslug="acme/widgets"\n[triage]\nendpoint="http://127.0.0.1:1"\n')
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text("#!/bin/sh\nprintf read > provider-read\nexit 1\n")
        gh.chmod(0o700)

        def transport(argv, **kwargs):
            return self.real_run(argv, **kwargs) if argv[0] == decisions.sys.executable else self.transport(argv, **kwargs)

        with (
            mock.patch.object(decisions, "_run", side_effect=transport),
            mock.patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                       "PYTHONPATH": str(self.root), "PYTHONHOME": str(shadow)}),
        ):
            result = self.apply(self.proposal([{"op": "triage", "number": 7}]))
        self.assertFalse(marker.exists())
        self.assertTrue((self.root / "provider-read").exists())
        self.assertEqual(result["status"], "uncertain")

    def test_cleanup_removes_the_confirmed_squash_merged_branch(self) -> None:
        def git(*args, cwd=None):
            return subprocess.run(["git", "-C", str(cwd or self.root), *args], check=True, capture_output=True, text=True)

        git("init", "-b", "main")
        git("config", "user.name", "Qualification")
        git("config", "user.email", "qualification@example.test")
        (self.root / "feature.txt").write_text("base\n")
        git("add", "feature.txt")
        git("commit", "-m", "base")
        worktree = self.cfg.factory / "wt-7"
        git("worktree", "add", "-b", "agent/7", str(worktree))
        (worktree / "feature.txt").write_text("delivered\n")
        git("add", "feature.txt", cwd=worktree)
        git("commit", "-m", "implementation", cwd=worktree)
        git("merge", "--squash", "agent/7")
        git("commit", "-m", "delivered outcome")

        def transport(argv, **kwargs):
            return self.real_run(argv, **kwargs) if argv[0] == "git" else self.transport(argv, **kwargs)

        with mock.patch.object(decisions, "_run", side_effect=transport):
            result = self.apply(self.proposal([{"op": "cleanup", "number": 7}]))
        self.assertEqual(result["status"], "success")
        self.assertFalse(worktree.exists())
        self.assertEqual(git("branch", "--list", "agent/7").stdout, "")
        self.assertEqual((self.root / "feature.txt").read_text(), "delivered\n")

    def test_expiry_confirmation_and_unsafe_storage_fail_closed(self) -> None:
        preview = self.proposal([{"op": "issue", "number": 7, "comment": "Bound token"}])
        with self.assertRaises(decisions.DecisionError) as token:
            decisions.apply(self.cfg, preview["proposal_id"], "wrong")
        self.assertEqual(token.exception.code, "confirmation_refused")
        with (
            mock.patch.object(decisions, "_now", return_value=decisions._now() + timedelta(days=1)),
            self.assertRaises(decisions.DecisionError) as expired,
        ):
            self.apply(preview)
        self.assertEqual(expired.exception.code, "expired")
        self.assertEqual(self.calls, [])

        other = Path(self.temp.name) / "outside"
        other.mkdir()
        unsafe_root = Path(self.temp.name) / "unsafe"
        unsafe_root.mkdir()
        (unsafe_root / ".factory").mkdir()
        os.symlink(other, unsafe_root / ".factory" / "decisions")
        unsafe = config.Config(root=unsafe_root, repo="acme/widgets")
        with self.assertRaises(decisions.DecisionError) as storage:
            decisions.prepare(unsafe, [{"op": "issue", "number": 7, "comment": "x"}])
        self.assertEqual(storage.exception.code, "unsafe_storage")

    def test_owner_outcome_prepares_applies_and_projects_without_claiming_verification(self) -> None:
        self.issue.update(
            labels=[{"name": "initiative"}],
            body="""**Status**
underway

**Outcome**
A useful result.

**Owner**
@alice

**Areas**
parser

**Boundaries**
No deployment authority.

**Plan**
Ship safely.

**Open decisions**
None.

**Success evidence**
Owner evidence linked to a source revision.

**Implementation links**
""",
        )
        request = {
            "op": "outcome",
            "number": 7,
            "kind": "healthy",
            "source_revision": "d" * 40,
            "evidence_url": "https://example.test/evidence/7",
            "summary": "Bounded health evidence.",
        }
        preview = self.proposal([request])
        self.assertEqual(self.calls, [])
        result = self.apply(preview)
        self.assertEqual(result["status"], "success")
        command = self.calls[-1]
        projected = outcomes.project(self.cfg, {**self.issue, "html_url": self.issue["url"]}, [{
            "id": 42,
            "body": command[command.index("--body") + 1],
            "user": {"login": "alice"},
            "created_at": "2026-10-09T12:00:00Z",
            "updated_at": "2026-10-09T12:00:00Z",
            "html_url": self.issue["url"] + "#issuecomment-42",
        }])
        self.assertEqual(projected["attributed"]["healthy"]["status"], "attested")
        self.assertEqual(projected["verified"]["health"]["status"], "unknown")
        self.assertEqual(projected["status"], "unknown")

    def test_observation_separates_fixed_state_from_execution_claims(self) -> None:
        preview = self.proposal([{
            "op": "issue", "number": 7, "comment": "Route it", "add": [config.LABEL_AGENT],
        }])
        result = self.apply(preview)
        self.assertEqual(result["status"], "success")
        observed = decisions.receipt(self.cfg, preview["proposal_id"], observe=True)["observation"]
        self.assertIn(config.LABEL_AGENT, observed["targets"][0]["labels"])
        self.assertEqual(observed["postconditions"][0]["status"], "partial")
        self.assertIn("do not establish that a worker ran", observed["interpretation"])


if __name__ == "__main__":
    unittest.main()
