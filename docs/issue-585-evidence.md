# Issue #585: historical fifth failure

## Historical diagnosis

**[INFERENCE, corroborated by retained historical diagnosis]** The fifth failure is
`tests.test_tempdir_hygiene.TempdirHygieneTest.test_full_suite_leaves_no_tempdirs_behind`.
This test runs a second full suite and asserts that its subprocess exits zero
(`tests/test_tempdir_hygiene.py:61–74`, assertion at line 73). The child sets
`FACTORY_TEMPDIR_HYGIENE_CHILD=1`, skipping the recursive wrapper in that child.
The same four obsolete triage expectations fail in the child; its nonzero exit
therefore also fails the outer wrapper. This is not evidence of a separate
triage defect or of leaked temporary directories.

## Retained source and limits

Original source, relative to the main checkout's `.factory/transcripts/585/`:

- `2026-11-05T18-48-08-112Z_01a1ad0a-2eb0-701e-8f12-539320a28223.jsonl`,
  line 79, message `4595b04d`: retained original failing gate output, not a new
  run. Nested summary: `Ran 1470 tests in 318.274s` and
  `FAILED (failures=4, skipped=9)`. Outer summary:
  `Ran 1470 tests in 638.788s` and `FAILED (failures=5, skipped=8)`.
- `2026-11-05T23-55-04-539Z_01a1ae23-31db-77dc-bf29-b1fa4f2e1812/HistoricalFailure.jsonl`,
  lines 142–155: prior investigation's corrected wrapper diagnosis, withdrawing
  its earlier flaky-test speculation. Consulted through the historical-session
  reader rather than rerunning the old failure.
- `2026-11-07T09-28-44-352Z_01a1b556-c240-7160-80e9-cdd47aba5cf6/HistoricalFailureEvidence.jsonl`:
  additional historical investigation, consulted through the same reader.

The original wrapper `FAIL:` header and traceback were truncated from the
retained output. Only the child timeout failure header survives; all four outer
triage failure headers survive. The wrapper's ID is explicit in the later
historical diagnosis, not in recovered original stderr. **[INFERENCE]**
`AssertionError: 1 != 0` follows from the child failure and the line-73 assertion;
it is not a recovered traceback quote. Naming the wrapper explains the fifth
failure, but does not supply the missing verbatim historical traceback. No
current passing run substitutes for that missing evidence.

The original job completion at transcript line 74 (message `d0cc7cff`) retained
only the report path, `gate FAIL: test`, 639.42 seconds and exit 1. Its adjacent
session directory contains no bash log. The historical readers found no fuller
wrapper traceback in either named investigation, referenced logs, or available
artifact; a later incomplete suite capture is not the original run.

Verbatim surviving child traceback excerpt from message `4595b04d`
(embedded report lines 31–37):

```text
FAIL: test_timeout_skips_only_that_ticket (test_triage.TriagePass.test_timeout_skips_only_that_ticket)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/mike/.local/share/district/roots/factory/.factory/wt-585/tests/test_triage.py", line 82, in test_timeout_skips_only_that_ticket
    self.assertEqual(self.run_pass(), 1)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
AssertionError: 0 != 1
```

This is the nested triage failure, not the missing outer wrapper traceback.

## Current verification evidence

The inherited attempt gate at `ef8d25977280253571bb0d41eb74ecc27677afaa` reports
`test`, `ruff`, `conflict-markers` and `leak-scan` PASS; mutation is SKIP at attempt
tier. Its original content is preserved in
`.factory/gate-report-585-preserved.md`. The four owner-authorized rewrites are
already present; the obsolete claim that they remain forbidden is withdrawn.
Production behavior, existing tests, gate configuration and initiative baseline
are unchanged by this evidence repair.

The preserved PR-tier report `.factory/gate-report-585-pr.md` reports mutation
PASS: **61 killed, zero survivors, 109 incomplete** of 170 sampled mutants
(69 unstarted, 40 deadline-cut). This is partial, not exhaustive, mutation
coverage. Incomplete trials are nonblocking under `README.md:1224–1226`.
The final attempt's command/results belong in `.factory/handoff-585.md` and its
cited reports; they establish current verification, not historical diagnosis.
