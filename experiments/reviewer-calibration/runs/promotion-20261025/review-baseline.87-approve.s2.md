## Review: #87 — Expose reader build and schema identity

### Scope of diff
`factory/evidence.py` (+`reader_build()`, `producers.reader`), `tests/test_evidence.py` (+`reader_from`, +1 test). Additive key only; no existing consumer of `producers` found in `factory/`, `console/`, `skills/`.

### 1. Standards
No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; conventions come from README and existing code.
- `review.diff:17,21-22` — reuses `COMMAND_SECONDS`, `GIT_OPTIONAL_LOCKS=0`, `SHA` (matches `github_read` env at `evidence.py:321`). `GIT_OPTIONAL_LOCKS=0` is what keeps `git status` from refreshing the engine checkout's index → no write to the installed engine's `.git`. Consistent with the "zero operational writes" contract.
- `review.diff:28` — `ls-files --error-unmatch` on this module guards the `.venv`-inside-operator-repo case: `rev-parse HEAD` would return the *enclosing* repo's HEAD, `ls-files` fails (ignored path) → `verified: false`. Correct non-fabrication guard.
- `review.diff:29-30` — `status --porcelain -- .` scoped to the package dir; `__pycache__/` is gitignored (`.gitignore:1-2`), so a normally-run install still verifies.
- Tests follow the file's existing pattern (real CLI under the audit-hook guard, `review.diff:57-65`).

### 2. Spec (issue #87 acceptance criteria)
| AC | Evidence | Status |
|---|---|---|
| Verifiable build revision + schema, separate from repo revision/service identity | `review.diff:31-32,44` — `producers.reader = {revision, verified, evidence_schema, runtime_schema}`; `review.diff:88` asserts `revision != self.head` (selected `--root` HEAD) | Met |
| Unavailable provenance explicitly unknown; never from version string/checkout HEAD | `review.diff:30-31` — `revision` is `None` unless `verified`; no `factory.__version__` reference; `review.diff:92-93` asserts `(False, None)` after local modification | Met |
| Exercise actual JSON CLI for verified and unavailable; bounded output; zero writes | `review.diff:61-65,86-93` — both states run through `python -m factory.cli evidence`; audit hook active (`:63`); bounded-output assert and `--root` state-unchanged assert come via `self.invoke()` at `:68` | Met |

### 3. Plan drift
Plan status: not recorded → no named file list to drift from. Files changed (`factory/evidence.py`, `tests/test_evidence.py`) both fall under the issue's "Touches: existing evidence boundary". Gate report does not record the issue's requested "actual changed CLI" commands/observed results; the new test performs that CLI exercise under `test: PASS`, so this exposes no defect.

### Required fixes
None.

### Optional suggestions
- `review.diff:44` — README `capabilities` contract (`README.md:1216-1218`) and `CHANGELOG.md` "Unreleased" (entries carry `(#N)`) not updated for the new `producers.reader` object. Convention, not a documented rule.
- `review.diff:57-65` — `reader_from` duplicates `invoke` minus the `len(proc.stdout) < 1_048_576` and `before == self.state()` checks. Adding an `env=` override to `invoke` would remove the copy and apply both asserts to the engine-swapped runs too.
- `review.diff:27-29` — the three `git` calls run unconditionally; when `tracked` is `None` the `status` scan of an unrelated enclosing repo is wasted (bounded by `COMMAND_SECONDS`, so not a defect). Short-circuit after a failed `ls-files` if latency matters.

VERDICT: APPROVE
