## Review: #87 — Expose reader build and schema identity

Scope of evidence: `review.diff` (2 files), snapshot of `factory/evidence.py`, `factory/dashboard.py`, `tests/test_evidence.py`, `README.md`, `CHANGELOG.md`, `docs/*`. Issue #87 body + triage comment fetched from the GitHub API. No `AGENTS.md`/`CONTRIBUTING.md` exists in the snapshot; the repo's documented conventions live in `README.md`, `CHANGELOG.md`, and `docs/`.

### 1. Standards

- **Follows the existing producer-revision rule.** README.md:824-825 (B2/#79 contract): "The producer revision is a clean source-checkout Git revision, otherwise null, never a CLI version." `reader_build()` (review.diff:9-32) implements exactly that: `rev-parse HEAD` + `ls-files --error-unmatch` on its own module + `status --porcelain -- .`, else `revision: None`. It never reads `factory.__version__` or the `--root` HEAD.
- **Read-only/bounded conventions preserved.** `GIT_OPTIONAL_LOCKS=0` (review.diff:17) matches `github_read` at evidence.py:321 and `runtime_local.py:63`; each git call is capped at `COMMAND_SECONDS` (review.diff:22); three sequential calls (≤60 s) stay inside the 90 s `READ_SECONDS` deadline checked at evidence.py:1134; output still passes through `bounded_output` (evidence.py:1157). Failures collapse to `None` → `verified: False`, never an exception leaking into the envelope.
- **Test conventions.** New test drives the real CLI via subprocess under the audit-hook guard and asserts no `EVIDENCE_FORBIDDEN` (review.diff:57-65), matching the file's stated approach (test_evidence.py:25-26). Git fixture commands inherit the process env exactly as the existing `self.git` helper does (test_evidence.py:172-174).
- **Smell (judgement call, non-blocking): Duplicated Code.** dashboard.py:936-940 already runs the same three-command clean-checkout probe for the feedback producer. Two copies now exist. Extracting a shared helper would be a net-new cross-module seam that the brief did not ask for; I am not requesting it.

### 2. Spec (issue #87 acceptance criteria)

| Criterion | Status | Evidence |
|---|---|---|
| Capability output identifies verifiable build revision and supported schema separately from repository revision and service identity | Met | `producers.reader = {revision, verified, evidence_schema, runtime_schema}` (review.diff:31-32, 44-45). Revision is derived from the engine's own `__file__` location (review.diff:16), not `--root`. Test asserts engine revision ≠ selected repo HEAD (review.diff:88). |
| Unavailable provenance explicitly unknown; version strings / checkout HEAD never fabricate | Met | `revision if verified else None` + boolean `verified` (review.diff:30-31). Locally modified tree → `(False, None)` proven at review.diff:90-93. No version string referenced anywhere in `reader_build()`. |
| Exercise actual JSON CLI for verified and unavailable provenance; bounded output; zero operational writes | Met | Both states exercised through `python -m factory.cli evidence` subprocess (review.diff:61-65, 86-93) under the write-forbidding audit hook (review.diff:63). Gate `test: PASS` on target host. |
| Reuse existing producers/contracts; no deployment/permission changes | Met | Only `capabilities()` producers map touched (review.diff:42-45); existing `evidence_schema`/`runtime_schema`/`runtime_reader`/`escalation_packet` keys retained. |
| "Record commands and observed results in the PR" | Not verifiable from diff | PR body not in scope; not a code defect. |

No scope creep observed.

### 3. Plan drift

Plan status: not recorded, so no planned file list or proof list to diff against. Files changed: `factory/evidence.py`, `tests/test_evidence.py` — both squarely within the issue's "Touches" (owning evidence boundary + tests). Gate report covers conflict-markers/test/leak-scan; the issue's stated verification (`python -m unittest discover -s tests`) is the gate's `test: PASS`. No drift exposing a defect.

### Required fixes (blocking)

None.

### Optional suggestions (non-blocking)

1. **CHANGELOG.md `## Unreleased` entry.** Every other public-contract change in that section carries a one-line entry with its issue number (CHANGELOG.md:5-14). `producers.reader` is a new field in the schema-1 capabilities output and has no entry. Not a documented rule, so not blocking.
2. **README evidence contract.** README.md:1161 lists `producers` without fields; a half-sentence noting `producers.reader` follows the README.md:824 "clean source-checkout revision, otherwise null" rule would keep the two producer-revision contracts visibly aligned.
3. **Duplicate probe with dashboard.py:936-940.** If a third consumer appears, fold into one helper then; two call sites don't justify a new seam now.

VERDICT: APPROVE
