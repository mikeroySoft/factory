# Gate report

- conflict-markers: PASS
- test: FAIL
- leak-scan: PASS

## test failure
```
.[dispatch] agent/7: existing PR #17 targets 'main', not 'stable'; not pushing
[dispatch] agent/7: existing PR #17 targets None, not 'stable'; not pushing
....[dispatch] #31: PR #100 merged onto current main, re-gated, and re-approved at 4cd40e75ad8b
.[dispatch] PR #70: targets 'stable', not 'release'; not refreshing
[dispatch] PR #70: targets None, not 'release'; not refreshing
.[dispatch] #7: PR #100 rebased onto current main, re-gated, and re-approved at 1ab45994af0a
......E.............................................................................................[dispatch] issue #60: viability DEFER
[dispatch] issue #61: viability DEFER
[dispatch] issue #62: viability DEFER
[dispatch] issue #63: viability DEFER
[dispatch] issue #64: viability DEFER
.[dispatch] pr #43: would assess viability (needs-review)
[dispatch] issue #42: would assess viability (needs-viability)
.[dispatch] issue #21: viability DONT_BUILD
[dispatch] issue #22: viability DEFER
.[dispatch] issue #44: viability BUILD
.[dispatch] issue #50: changed during viability assessment; leaving untouched
[dispatch] issue #51: changed during viability assessment; leaving untouched
[dispatch] pr #52: changed during viability assessment; leaving untouched
.[dispatch] issue #23: viability failed: Command '['gh', 'issue', 'edit', '23', '--repo', 'acme/widgets', '--remove-label', 'needs-viability', '--add-label', 'needs-triage']' returned non-zero exit status 1.; inspect events.jsonl before re-arming
[dispatch] issue #24: viability failed: Command '['gh', 'issue', 'comment', '24', '--repo', 'acme/widgets', '--body', 'Factory manager: issue viability\n\nDirection is sound [S1]\n\nSources:\n- [S1] https://github.invalid/acme/widgets/issues/24\n\nBUILD queues needs-triage for deeper investigation, not implementation.\n\nVERDICT: BUILD']' returned non-zero exit status 1.; inspect events.jsonl before re-arming
[dispatch] issue #23: viability request already recorded; inspect events.jsonl before re-arming
[dispatch] issue #24: viability request already recorded; inspect events.jsonl before re-arming
.[dispatch] pr #24: viability BUILD
[dispatch] pr #24: viability DONT_BUILD
.[dispatch] pr #10: viability BUILD
[dispatch] issue #20: viability BUILD
....[dispatch] active tickets: 1, capacity: 0
[dispatch] at capacity, nothing to do
.[dispatch] PR #70: CI pending {'pending': 1}; waiting
[dispatch] PR #80: no passing CI checks reported; refusing to merge
.[dispatch] active tickets: 0, capacity: 1
[dispatch] frontier empty, nothing to do
[dispatch] #7: would claim (assign @me), create worktree /tmp/tmp9cwp40st/.factory/wt-7 on branch agent/7, run omp worker, gate, push, open PR, review
..[dispatch] sync + merge stage: skipped (another dispatcher holds the merge lock)
.[dispatch] #7: skipped (in flight, lock held on /tmp/tmpcsui2jrc/.factory/locks/7.lock)
[dispatch] #7: skipped (lost lock race)
.[dispatch] #7: skipped (state changed since frontier query)
.
======================================================================
ERROR: test_full_snapshot_preserves_legacy_contract_and_attaches_feedback (test_factory.FeedbackSnapshotTest.test_full_snapshot_preserves_legacy_contract_and_attaches_feedback)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/cal-gate-mmg56jo8/wt/tests/test_factory.py", line 2431, in test_full_snapshot_preserves_legacy_contract_and_attaches_feedback
    snapshot = dashboard.snapshot()
  File "/tmp/cal-gate-mmg56jo8/wt/factory/dashboard.py", line 885, in snapshot
    prs[n]["feedback"] = feedback.collect(
                         ~~~~~~~~~~~~~~~~^
        github, repository={"id": repo.get("id"), "slug": REPO,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<4 lines>...
        producer_revision=revision,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/tmp/cal-gate-mmg56jo8/wt/factory/feedback.py", line 169, in collect
    initial = head()
  File "/tmp/cal-gate-mmg56jo8/wt/factory/feedback.py", line 132, in head
    data = request('pr', query=HEAD_QUERY, variables=variables)
  File "/tmp/cal-gate-mmg56jo8/wt/factory/feedback.py", line 119, in request
    value = read(**kwargs, timeout=remaining)
  File "/home/mike/.local/share/mise/installs/python/3.14.8/lib/python3.14/unittest/mock.py", line 1176, in __call__
    return self._mock_call(*args, **kwargs)
           ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "/home/mike/.local/share/mise/installs/python/3.14.8/lib/python3.14/unittest/mock.py", line 1180, in _mock_call
    return self._execute_mock_call(*args, **kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "/home/mike/.local/share/mise/installs/python/3.14.8/lib/python3.14/unittest/mock.py", line 1247, in _execute_mock_call
    result = effect(*args, **kwargs)
  File "/tmp/cal-gate-mmg56jo8/wt/tests/test_factory.py", line 2421, in read
    return provider(**kwargs)
  File "/home/mike/dev/mikeroysoft/factory-calibration-corpus/tests/test_feedback.py", line 36, in __call__
    feedback.REVIEW_QUERY: 'reviews'}.get(query, 'checks'))
    ^^^^^^^^^^^^^^^^^^^^^
AttributeError: module 'factory.feedback' has no attribute 'REVIEW_QUERY'

----------------------------------------------------------------------
Ran 201 tests in 65.360s

FAILED (errors=1, skipped=4)
```
