## Standards

Sources: `README.md` (evidence contract §"Bounded project evidence JSON schema 1"), `pyproject.toml` `[tool.ruff] line-length = 100`, `.factory.toml` gate (`unittest` only), smell baseline.

No hard violations.

- `review.diff:45` (`factory/evidence.py` producers line) exceeds ruff's 100 columns. Not enforced by the gate or CI, and `evidence.py` already has 162 such lines → skipped, not a finding.
- Possible **Duplicated Code** (judgement call): `review.diff:27-30` re-implements `snapshot/factory/dashboard.py:936-940` (`rev-parse HEAD` / `ls-files --error-unmatch` / `status --porcelain -- .` on the imported source dir). The evidence copy adds `GIT_OPTIONAL_LOCKS=0` and a per-call timeout, which the dashboard copy lacks; `evidence` already imports `dashboard`, so the shared helper would have to live in `dashboard`. Optional.
- Audit-hook/no-write contract preserved: `review.diff:17` sets `GIT_OPTIONAL_LOCKS=0` (matches `github_read` at `evidence.py:321`), so `git status` does not refresh the index; all three git calls are read-only; failures collapse to `None` (`review.diff:23-25`) without leaking diagnostics, matching the module's "raw diagnostics withheld" convention.

## Spec (issue #87 + triage brief)

AC1 "identifies verifiable build revision and supported schema separately from repository revision and service identity" — met. `review.diff:31-32` emits `producers.reader = {revision, verified, evidence_schema, runtime_schema}` from the engine's own `Path(__file__)` checkout (`review.diff:16`), never from `--root`. Test `review.diff:88` asserts `reader.revision != self.head`.

AC2 "unavailable provenance is explicitly unknown; version strings or checkout HEAD never fabricate" — met. `verified` requires full-SHA HEAD ∧ tracked module ∧ clean `factory/` (`review.diff:30`); otherwise `revision: null, verified: false` (`review.diff:31`). No use of `factory.__version__`. Packaged/site-packages install → `rev-parse` fails or `ls-files` is unmatched → unknown. `review.diff:90-93` proves the locally-modified path yields `(False, None)`.

AC3 "exercise actual JSON CLI for verified and unavailable provenance; preserve bounded output and zero operational writes" — met through the real `factory.cli evidence` subprocess under the audit-hook guard (`review.diff:61-63`, `78-93`). Gate `test: PASS` on the target host.

Verification recording: issue requires commands/results recorded in the PR; not visible in the diff/gate report → unverifiable here, not a diff defect.

No scope creep: two files, one function, one producer field.

## Plan drift

Plan status: not recorded → no named file list or planned proof to diff against. Files changed: `factory/evidence.py`, `tests/test_evidence.py`. Nothing in the gate report is missing relative to the issue's required `python -m unittest discover -s tests` (gate `test: PASS`).

## Required fixes

None.

## Optional suggestions

1. `README.md:1216-1218` states `capabilities` "advertises **only** … evidence and runtime schema 1 support, the accepted escalation path, and an empty action menu"; `review.diff:44` now also advertises `producers.reader`. Add one clause there and a `CHANGELOG.md` Unreleased line `(#87)` to keep the documented contract accurate. No repo rule mandates it, so non-blocking.
2. `review.diff:57-65` `reader_from` skips the `before == self.state()` and `len(stdout) < 1_048_576` assertions that `invoke` (`tests/test_evidence.py:216-218`) applies; the engine checkout's own cleanliness after the read is also not asserted. Reusing `invoke` with an `env` override would cover AC3's "zero operational writes / bounded output" for the engine-copy runs too.
3. Share the source-revision check with `dashboard.py:936-940` (see Standards) so both surfaces agree on what "verified build" means; only worthwhile if the dashboard copy also wants the no-locks/timeout hardening.

VERDICT: APPROVE
