## Review: issue #87 — reader build and schema identity

### Standards (repo conventions)
No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; README §evidence and `docs/` are the only documented contracts.
- `review.diff:17-25` — reuses the module's existing subprocess conventions (`COMMAND_SECONDS`, `GIT_OPTIONAL_LOCKS=0`, as in `github_read` at `evidence.py:321`). No new dependency, no new module, no new abstraction beyond one function. Consistent with README's "dependency-free" statement (README.md:1148-1149).
- `review.diff:44-45` — `producers` keeps `evidence_schema`/`runtime_schema`/`runtime_reader`/`escalation_packet`; existing consumers unaffected. README.md:1161 documents `capabilities` only at the field-group level (`reads`, `limits`, `producers`, …), so no README update is required by a documented rule.
- `review.diff:57-93` — test runs the real CLI under the existing read-only audit hook (`test_evidence.py:27-38`), matching the established harness convention rather than patching collectors.

### Spec (issue #87 acceptance criteria)
1. *Verifiable build revision and schema separate from repo revision and service identity* — `review.diff:27-32` reports the engine's own checkout HEAD from `Path(__file__)`, not `--root`; `review.diff:88` asserts `revision != self.head` (selected repo). Schema is in the same object. **Met.**
2. *Unavailable provenance explicitly unknown; version strings / checkout HEAD never fabricate* — `review.diff:30-31`: `revision` is `None` unless `rev-parse` yields a full SHA **and** this file is tracked **and** `status --porcelain -- .` is empty. The tracked-file guard specifically prevents the site-packages-inside-a-repo case from reporting the enclosing repo's HEAD. Nothing consults `factory.__version__`. `review.diff:90-93` covers the locally-modified case. **Met.**
3. *Exercise actual JSON CLI for verified and unavailable provenance; bounded output; zero operational writes* — `review.diff:61-65, 86-93` run `python -B -m factory.cli evidence` for both states. Bounded: only a regex-validated SHA or `null` reaches output; git stdout is never echoed; worst case 3×`COMMAND_SECONDS`=60s < `READ_SECONDS`=90 (`evidence.py:1102,1134`). Writes: `rev-parse`/`ls-files` don't write; `status` with `GIT_OPTIONAL_LOCKS=0` skips index refresh; the first invocation goes through `invoke()` which asserts `state()` unchanged and stdout < 1 MiB (`test_evidence.py:216-218`). **Met.**

### Plan drift
Plan status "not recorded" — no planned file list or planned proof to diff against. Files changed: `factory/evidence.py`, `tests/test_evidence.py`; both are within the issue's "Touches" (existing evidence boundary). Gate report shows `test: PASS`; the issue's "record commands and observed results in the PR" is a PR-body requirement not observable from the diff.

### Required fixes
None.

### Optional suggestions
- `review.diff:29` — `status --porcelain -- .` is scoped to `factory/` only; modifications elsewhere in the engine checkout (e.g. `pyproject.toml`) still report `verified: true`. Acceptable since the package dir is the executing code; widen to the repo root if "build" is meant to cover packaging metadata.
- `review.diff:44` — `evidence_schema`/`runtime_schema` now appear both under `producers` and `producers.reader`. Harmless duplication kept for compatibility; drop the top-level copies in a future schema bump if desired.
- `CHANGELOG.md:3` has an `## Unreleased` section where every other feature in this cycle is recorded with its issue number; this diff adds no entry. No documented rule mandates it, so non-blocking.
- `review.diff:58-62` — `reader_from` duplicates most of `invoke()` minus the `state()`/size assertions; passing an engine `PYTHONPATH` through `invoke()` would reuse the stronger checks for the clean/modified runs too.

VERDICT: APPROVE
