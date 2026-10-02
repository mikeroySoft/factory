# Gate report

- conflict-markers: PASS
- test: FAIL
- leak-scan: PASS

## test failure
```
[dispatch] #31: handoff retention incomplete (retained result storage is unavailable or unsafe); worktree kept, operator resolution required before cleanup
.[dispatch] PR #200: merged into main (squash, ticket #99)
[dispatch] #99: handoff retention incomplete (retained result storage is unavailable or unsafe); worktree kept, operator resolution required before cleanup
.[dispatch] #7: escalating to human (gate failed)
[dispatch] #7: escalating to human (gate failed again)
.[dispatch] agent/7: existing PR #17 targets 'main', not 'stable'; not pushing
[dispatch] agent/7: existing PR #17 targets None, not 'stable'; not pushing
....[dispatch] #31: PR #100 merged onto current main, re-gated, and re-approved at 92ceaafcda6c
.[dispatch] PR #70: targets 'stable', not 'release'; not refreshing
[dispatch] PR #70: targets None, not 'release'; not refreshing
.[dispatch] #7: PR #100 rebased onto current main, re-gated, and re-approved at 2768079f5bc9
.....[dispatch] upstream sync: 1 commit(s) behind; waiting on human (#7)
..[dispatch] upstream sync: merged prefix through 90b894d1522a (1 commits) -> f7d687d4628a; 1 left, escalated https://x/issues/1
........................F....[dispatch] PR #90: refused (ticket #9 is an initiative record); not merging
......................[dispatch] #8: handoff request 8/1 published to unassigned
...................[dispatch] PR #80: frontier failed: Command '['gh', 'pr', 'close', '80', '--repo', 'example/project']' returned non-zero exit status 1.; leaving for human
.[dispatch] PR #80: not factory-owned (unverified); not in frontier
[dispatch] PR #80: not factory-owned (unverified); not in frontier
.[dispatch] PR #80: refused (ticket #79 is an initiative record)
.[dispatch] #79: escalating to human (PR #80: 3 unresolved feedback item(s) on head aaaaaaaaaaaa)
[dispatch] #79: escalating to human (PR #80: 1 unresolved feedback item(s) on head aaaaaaaaaaaa)
.[dispatch] PR #80: changed while the manager was thinking; decision discarded
..[dispatch] #79: escalating to human (PR #80: CI failed (unit))
[dispatch] PR #80: CI pending; waiting
[dispatch] #79: escalating to human (PR #80: no activity for 274 days (stale_days = 7))
..[dispatch] #79: gate and review passed at aaaaaaaaaaaa; waiting for manager approval (manager.review = all)
[dispatch] #79: handoff retention incomplete (retained result storage is unavailable or unsafe); worktree kept, operator resolution required before cleanup
[dispatch] #79: escalating to human (PR #80: manager rounds exhausted before approval)
.................[dispatch] #7: escalating to human (gate failed)
[dispatch] #7: escalating to human (gate failed again)
....[dispatch] #7: handoff request 7/1 published to @bob
.................................................................................................................[dispatch] issue #60: viability DEFER
[dispatch] issue #61: viability DEFER
[dispatch] issue #62: viability DEFER
[dispatch] issue #63: viability DEFER
[dispatch] issue #64: viability DEFER
.[dispatch] pr #43: would assess viability (needs-review)
[dispatch] issue #42: would assess viability (needs-viability)
.[dispatch] issue #44: viability BUILD
.[dispatch] issue #50: changed during viability assessment; leaving untouched
[dispatch] issue #51: changed during viability assessment; leaving untouched
[dispatch] pr #52: changed during viability assessment; leaving untouched
.[dispatch] pr #23: viability DEFER
[dispatch] issue #21: viability DONT_BUILD
[dispatch] issue #22: viability DEFER
.[dispatch] issue #23: viability failed: Command '['gh', 'issue', 'edit', '23', '--repo', 'acme/widgets', '--remove-label', 'needs-viability', '--add-label', 'needs-triage']' returned non-zero exit status 1.; inspect events.jsonl before re-arming
[dispatch] issue #24: viability failed: Command '['gh', 'issue', 'comment', '24', '--repo', 'acme/widgets', '--body', 'Factory manager: issue viability\n\nDirection is sound [S1]\n\nSources:\n- [S1] https://github.invalid/acme/widgets/issues/24\n\nBUILD queues needs-triage for deeper investigation, not implementation.\n\nVERDICT: BUILD']' returned non-zero exit status 1.; inspect events.jsonl before re-arming
[dispatch] issue #23: viability request already recorded; inspect events.jsonl before re-arming
[dispatch] issue #24: viability request already recorded; inspect events.jsonl before re-arming
.[dispatch] pr #24: viability BUILD
[dispatch] pr #24: viability request already recorded; inspect events.jsonl before re-arming
[dispatch] pr #24: viability DONT_BUILD
.[dispatch] pr #10: viability BUILD
[dispatch] issue #20: viability BUILD
....[dispatch] active tickets: 1, capacity: 0
[dispatch] at capacity, nothing to do
.[dispatch] PR #70: CI pending {'pending': 1}; waiting
[dispatch] PR #80: no passing CI checks reported; refusing to merge
.[dispatch] active tickets: 0, capacity: 1
[dispatch] frontier empty, nothing to do
[dispatch] #7: refused (incomplete-source: ticket body was not returned)
..[dispatch] sync + merge stage: skipped (another dispatcher holds the merge lock)
.[dispatch] #7: skipped (in flight, lock held on /tmp/tmpphxiajcb/.factory/locks/7.lock)
[dispatch] #7: skipped (lost lock race)
.[dispatch] #7: skipped (state changed since frontier query)
.
======================================================================
FAIL: test_selected_interpreter_launchers_and_validation (test_factory.HostConfigTest.test_selected_interpreter_launchers_and_validation)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/cal-gate-qze_iuhn/wt/tests/test_factory.py", line 655, in test_selected_interpreter_launchers_and_validation
    self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 1 != 0 : factory: service interpreter: /tmp/tmptohmnzz2/selected/bin/python space$HOME%name"quote\slash could not load Factory service commands: Could not find platform independent libraries <prefix> Could not find platform dependent libraries <exec_prefix> Python path configuration: PYTHONHOME = (not set) PYTHONPATH = (not set) program name = '/tmp/tmptohmnzz2/selected/bin/python space$HOME%name"quote\slash' isolated = 0 environment = 1 user site = 1 safe_path = 1 import site = 1 is in build tree = 0 stdlib dir = '/install/lib/python3.14' sys.path[0] = (not set) sys._base_executable = '/tmp/tmptohmnzz2/selected/bin/python' sys.base_pref


----------------------------------------------------------------------
Ran 375 tests in 97.893s

FAILED (failures=1, skipped=4)
```
