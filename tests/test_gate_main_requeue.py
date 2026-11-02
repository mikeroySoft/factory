"""#517: a main change to the sole failing test's file releases parked work."""
from __future__ import annotations

import fcntl
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests.test_refresh_conflict import FakeGitHub, build_fork, git

TEST = "tests.test_flaky.FlakyTest.test_status"
REPORT = f"""# Gate report
- conflict-markers: PASS
- test: FAIL
- ruff: PASS

## test failure
```
FAIL: test_status ({TEST})
Traceback (most recent call last):
  File "tests/test_flaky.py", line 4, in test_status
    self.fail("known failure")
AssertionError: known failure
Ran 1 test in 0.001s

FAILED (failures=1)
```
"""


class MainTestRequeueTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root, self.origin = build_fork(Path(temp.name))
        (self.origin / "tests").mkdir()
        (self.origin / "tests/test_flaky.py").write_text(
            "import unittest\nclass FlakyTest(unittest.TestCase):\n"
            "    def test_status(self):\n        self.fail('known failure')\n"
        )
        git(self.origin, "add", "tests/test_flaky.py")
        git(self.origin, "commit", "-qm", "known failing test")
        git(self.root, "fetch", "origin")
        self.baseline = git(self.root, "rev-parse", "origin/main")
        self.cfg = config.Config(root=self.root, repo="acme/widgets", main="main")
        dispatch.configure(self.cfg)
        self.github = FakeGitHub(self.origin, dispatch.run)
        self.github.issue_labels = {config.LABEL_HUMAN}
        self.github.assignees = []
        self.wt = dispatch.FACTORY / "wt-9"
        git(self.root, "worktree", "add", "-qb", "agent/9", str(self.wt), "origin/main")
        (self.wt / ".factory").mkdir()
        self.report = self.wt / ".factory/gate-report-9.md"

    def events(self, name):
        return [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == name]

    def park(self, report=REPORT, code="gate_failed", worker_exit=0):
        self.report.write_text(report)
        dispatch.record("attempt", ticket=9, attempt=3, gate="FAIL", worker_exit=worker_exit)
        with mock.patch.object(dispatch, "run", self.github.run):
            dispatch.escalate(9, "gate failed 3 times", None, code=code)

    def advance(self, path="tests/test_flaky.py", content="# fixed\n"):
        target = self.origin / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(self.origin, "add", path)
        git(self.origin, "commit", "-qm", "main change")
        git(self.root, "fetch", "origin")
        return git(self.origin, "rev-parse", "main")

    def dispatch_pass(self, dry_run=False):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(dispatch, "gh_json", self.github.gh_json))
            stack.enter_context(mock.patch.object(dispatch, "run", self.github.run))
            stack.enter_context(mock.patch.object(dispatch, "pin_root", return_value=[]))
            stack.enter_context(mock.patch.object(config, "load", return_value=self.cfg))
            for name in ("land_pass", "review_intake_pass", "ticket_pass"):
                stack.enter_context(mock.patch.object(dispatch, name))
            for name in ("factory.manage.manage_pass", "factory.learn.learn_pass"):
                stack.enter_context(mock.patch(name))
            stack.enter_context(mock.patch("factory.results.prune", return_value={"status": "complete"}))
            self.assertEqual(dispatch.main(["--dry-run"] if dry_run else []), 0)

    def test_only_test_failure_requeues_when_main_changes_its_file(self):
        self.park()
        self.advance("unrelated.txt")
        changed = self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        [parked] = self.events("escalate")
        self.assertEqual(parked["parked_test"], {
            "test": TEST, "file": "tests/test_flaky.py", "main": self.baseline,
        })
        comments = [c[c.index("--body") + 1] for c in self.github.calls
                    if c[1:3] == ["issue", "comment"]]
        self.assertIn(TEST, comments[-1])
        self.assertIn(changed, comments[-1])
        self.assertEqual(len(self.events("escalate")), 1)
        self.assertEqual(self.events("manage"), [])
        self.assertEqual(self.events("gate-requeue"), [])
        self.dispatch_pass()
        self.assertEqual(len([c for c in self.github.calls if c[1:3] == ["issue", "comment"]]), 2)

    def test_unrelated_main_change_and_unchanged_main_stay_parked(self):
        self.park()
        self.dispatch_pass()
        self.advance("unrelated.txt")
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})

    def test_other_gate_failure_blocks_requeue(self):
        self.park(REPORT.replace("- ruff: PASS", "- ruff: FAIL") + "\n## ruff failure\nerror\n")
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertNotIn("parked_test", self.events("escalate")[-1])

    def test_second_test_failure_in_same_file_blocks_requeue(self):
        self.park(REPORT.replace("FAILED (failures=1)",
                                f"FAIL: test_other ({TEST.replace('test_status', 'test_other')})\nFAILED (failures=2)"))
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertNotIn("parked_test", self.events("escalate")[-1])

    def test_truncated_failure_list_cannot_hide_second_failure(self):
        self.park(REPORT.replace("FAILED (failures=1)", "FAILED (failures=2)"))
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertNotIn("parked_test", self.events("escalate")[-1])

    def test_worker_failure_is_not_a_sole_test_failure(self):
        self.park(worker_exit=1)
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})

    def test_later_different_escalation_supersedes_test_parking(self):
        self.park()
        self.park(code="budget")
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})

    def test_dry_run_leaves_labels_and_journal_unchanged(self):
        self.park()
        self.advance()
        before = dispatch.EVENTS.read_bytes()
        self.dispatch_pass(dry_run=True)
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(dispatch.EVENTS.read_bytes(), before)

    def test_names_commit_touching_test_not_later_unrelated_tip(self):
        self.park()
        changed = self.advance()
        tip = self.advance("unrelated.txt")
        self.dispatch_pass()
        comments = [c[c.index("--body") + 1] for c in self.github.calls
                    if c[1:3] == ["issue", "comment"]]
        self.assertIn(changed, comments[-1])
        self.assertNotIn(tip, comments[-1])

    def test_changed_then_restored_file_still_counts_as_main_change(self):
        self.park()
        original = git(self.origin, "show", "main:tests/test_flaky.py") + "\n"
        self.advance()
        changed = self.advance(content=original)
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        comments = [c[c.index("--body") + 1] for c in self.github.calls
                    if c[1:3] == ["issue", "comment"]]
        self.assertIn(changed, comments[-1])
    def test_pytest_failure_records_its_source_file(self):
        report = REPORT.replace(
            f"FAIL: test_status ({TEST})",
            "FAILED tests/test_flaky.py::FlakyTest::test_status - AssertionError",
        ).replace("FAILED (failures=1)", "1 failed in 0.01s")
        self.park(report)
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})

    def test_rust_failure_records_its_source_file(self):
        self.advance("src/comfyui.rs", "#[test]\nfn status_reports_stopped() { assert!(false); }\n")
        git(self.wt, "rebase", "origin/main")
        report = REPORT.replace(
            f"FAIL: test_status ({TEST})",
            "---- comfyui::tests::status_reports_stopped stdout ----",
        ).replace("FAILED (failures=1)", "test result: FAILED. 0 passed; 1 failed; 0 ignored;")
        self.park(report)
        changed = self.advance("src/comfyui.rs", "#[test]\nfn status_reports_stopped() { assert!(true); }\n")
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertEqual(self.events("escalate")[-1]["parked_test"]["file"], "src/comfyui.rs")
        comments = [c[c.index("--body") + 1] for c in self.github.calls
                    if c[1:3] == ["issue", "comment"]]
        self.assertIn(changed, comments[-1])



    def test_timeout_after_test_failure_stays_parked(self):
        self.park(REPORT + "\ncheck timed out after 30s\n")
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertNotIn("parked_test", self.events("escalate")[-1])

    def test_unexpected_success_alongside_failure_stays_parked(self):
        self.park(REPORT.replace("failures=1", "failures=1, unexpected successes=1"))
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertNotIn("parked_test", self.events("escalate")[-1])


    def test_unittest_nonfailing_outcomes_preserve_sole_failure_recovery(self):
        self.park(REPORT.replace(
            "failures=1", "failures=1, skipped=3, expected failures=2",
        ))
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})

    def test_worker_gate_rejection_in_memory_blocks_parking(self):
        self.github.issue_labels = {config.LABEL_AGENT}
        self.github.assignees = []
        issue = self.github.issue()

        def dirty_gate(*_args, **_kwargs):
            self.report.write_text(REPORT)
            (self.wt / "tests/test_flaky.py").write_text("# dirty after gate\n")
            return True, REPORT

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(dispatch, "gh_json", self.github.gh_json))
            stack.enter_context(mock.patch.object(dispatch, "run", self.github.run))
            stack.enter_context(mock.patch.object(dispatch, "admit_plan", return_value=None))
            stack.enter_context(mock.patch.object(dispatch, "ensure_worktree", return_value=self.wt))
            stack.enter_context(mock.patch.object(dispatch, "agent_lease", return_value=""))
            stack.enter_context(mock.patch.object(dispatch, "refresh_requeue_context", return_value=""))
            stack.enter_context(mock.patch.object(dispatch, "sync_staged", return_value=None))
            stack.enter_context(mock.patch.object(dispatch, "build_prompt", return_value="prompt"))
            stack.enter_context(mock.patch.object(dispatch, "run_worker", return_value=(0, None)))
            stack.enter_context(mock.patch.object(dispatch, "commit_leftovers"))
            stack.enter_context(mock.patch.object(dispatch, "run_gate", side_effect=dirty_gate))
            stack.enter_context(mock.patch("factory.schedule.priority", return_value=(None, "test")))
            stack.enter_context(mock.patch("factory.results.source_metadata", return_value={}))
            dispatch.process_ticket(issue, 1, False, forced=True)

        self.assertNotIn("parked_test", self.events("escalate")[-1])
        self.assertEqual(self.events("test-requeue"), [])

    def test_refreshed_gate_rejection_in_memory_blocks_parking(self):
        (self.wt / "feature.txt").write_text("agent change\n")
        git(self.wt, "add", "feature.txt")
        git(self.wt, "commit", "-qm", "agent change")
        git(self.wt, "push", "-qu", "origin", "agent/9")
        self.advance("unrelated.txt")

        def dirty_gate(*_args, **_kwargs):
            self.report.write_text(REPORT)
            (self.wt / "tests/test_flaky.py").write_text("# dirty after refresh gate\n")
            return True, REPORT

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(dispatch, "gh_json", self.github.gh_json))
            stack.enter_context(mock.patch.object(dispatch, "run", self.github.run))
            stack.enter_context(mock.patch.object(dispatch, "ensure_worktree", return_value=self.wt))
            stack.enter_context(mock.patch.object(dispatch, "run_gate", side_effect=dirty_gate))
            self.assertFalse(dispatch.refresh_pr_branch(9, 102, False))

        self.assertNotIn("parked_test", self.events("escalate")[-1])
        self.assertEqual(self.events("test-requeue"), [])


    def test_manager_human_handoff_keeps_sole_test_recovery_eligible(self):
        self.park()
        dispatch.record("manage", ticket=9, decision="HUMAN", round=1)
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertEqual(len(self.events("manage")), 1)

    def test_later_worker_failure_invalidates_old_sole_test_marker(self):
        self.park()
        dispatch.record("attempt", ticket=9, gate="FAIL", worker_exit=1)
        self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})

    def test_parameterized_pytest_ids_requeue_after_source_file_changes(self):
        for parameter in ("1.0", "value::nested"):
            with self.subTest(parameter=parameter):
                self.github.issue_labels = {config.LABEL_HUMAN}
                test = f"tests/test_flaky.py::FlakyTest::test_status[{parameter}]"
                report = REPORT.replace(
                    f"FAIL: test_status ({TEST})", f"FAILED {test} - AssertionError",
                ).replace("FAILED (failures=1)", "1 failed in 0.01s")
                self.park(report)
                self.advance(content=f"# fixed {parameter}\n")
                self.dispatch_pass()
                self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
                self.assertEqual(self.events("test-requeue")[-1]["test"], test)

    def test_recovery_holds_ticket_lock_through_provider_mutations(self):
        self.park()
        self.advance()
        original_run = self.github.run
        contended = []

        def provider_run(args, **kwargs):
            if args[0] == "gh" and args[1:3] in (["issue", "comment"], ["issue", "edit"]):
                with dispatch.ticket_lock(9).open("w") as competitor:
                    try:
                        fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        contended.append(True)
                    else:
                        contended.append(False)
            return original_run(args, **kwargs)

        with mock.patch.object(self.github, "run", side_effect=provider_run):
            self.dispatch_pass()
        self.assertEqual(contended, [True, True])
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})

    def test_recovery_reloads_parking_evidence_after_acquiring_ticket_lock(self):
        self.park()
        self.advance()
        dispatch.ticket_lock(9).touch()
        real_flock = fcntl.flock
        superseded = False

        def acquire(lock, operation):
            nonlocal superseded
            result = real_flock(lock, operation)
            if operation & fcntl.LOCK_EX and not superseded:
                superseded = True
                dispatch.record("manage", ticket=9, decision="FIX", round=1)
                dispatch.record("attempt", ticket=9, gate="FAIL", worker_exit=1)
            return result

        with mock.patch.object(dispatch.fcntl, "flock", side_effect=acquire):
            self.dispatch_pass()
        self.assertTrue(superseded)
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("test-requeue"), [])

    def test_recovery_skips_ticket_locked_by_manager(self):
        self.park()
        self.advance()
        with dispatch.ticket_lock(9).open("w") as manager_lock:
            fcntl.flock(manager_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_HUMAN})
        self.assertEqual(self.events("test-requeue"), [])

    def test_no_pr_requeue_worker_rebases_kept_worktree_before_retrying_gate(self):
        (self.wt / "feature.txt").write_text("unpublished ticket work\n")
        git(self.wt, "add", "feature.txt")
        git(self.wt, "commit", "-qm", "ticket work")
        kept_head = git(self.wt, "rev-parse", "HEAD")
        self.park()
        fixed_source = "import unittest\nclass FlakyTest(unittest.TestCase):\n    def test_status(self):\n        pass\n"
        changed = self.advance(content=fixed_source)
        with mock.patch.object(dispatch, "run", self.github.run), \
                mock.patch.object(dispatch, "gh_json", self.github.gh_json):
            dispatch.requeue_parked_tests(False)
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), kept_head)
        self.assertNotIn("refs/heads/agent/9", git(self.origin, "show-ref"))
        self.cfg.idle_timeout = self.cfg.exit_grace = 0
        self.cfg.workers = {"default": [
            sys.executable, "-c",
            "from pathlib import Path; import subprocess; "
            "prompt = Path('.factory-prompt.md').read_text(); "
            "assert 'Rebase onto `origin/main`' in prompt, prompt; "
            "subprocess.run(['git', 'fetch', 'origin'], check=True); "
            "subprocess.run(['git', 'rebase', 'origin/main'], check=True)",
        ]}

        def gate(wt, *_args, **_kwargs):
            source = (wt / "tests/test_flaky.py").read_text()
            return source == fixed_source, "PASS" if source == fixed_source else REPORT

        dispatch.record("claimed", ticket=9)
        dispatch.LOGS.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(dispatch, "run", self.github.run), \
                mock.patch.object(dispatch, "gh_json", self.github.gh_json), \
                mock.patch.object(dispatch, "run_gate", side_effect=gate):
            ok, _report, _log, head = dispatch.worker_round(
                9, self.wt, {config.LABEL_AGENT}, "change",
                dispatch.refresh_requeue_context(9), 1, time.monotonic() + 30,
            )
        self.assertTrue(ok)
        self.assertEqual(git(self.wt, "merge-base", changed, head), changed)
        self.assertEqual((self.wt / "feature.txt").read_text(), "unpublished ticket work\n")
        self.assertEqual(len(self.events("escalate")), 1)
        self.assertEqual(self.events("manage"), [])
        self.assertEqual(dispatch.refresh_requeue_context(9), "")

    def test_pytest_parameter_with_spaces_retains_full_recovery_id(self):
        test = "tests/test_flaky.py::FlakyTest::test_status[hello world]"
        report = REPORT.replace(
            f"FAIL: test_status ({TEST})", f"FAILED {test} - AssertionError",
        ).replace("FAILED (failures=1)", "1 failed in 0.01s")
        self.park(report)
        changed = self.advance()
        self.dispatch_pass()
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})
        [requeue] = self.events("test-requeue")
        self.assertEqual((requeue["test"], requeue["main"]), (test, changed))


if __name__ == "__main__":
    unittest.main()
