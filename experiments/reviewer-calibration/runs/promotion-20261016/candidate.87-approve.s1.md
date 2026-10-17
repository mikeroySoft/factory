## Scope

Diff: `git diff origin/main..HEAD`, one commit `19bd468` — `factory/evidence.py` (+28/−2), `tests/test_evidence.py` (+38). Issue #87 "[AS1] Expose reader build and schema identity" body and triage/brief comment read; no approved scope changes in comments. Gate (test/leak-scan/conflict-markers) PASS on target host — not re-run.

Standards sources checked: no `AGENTS.md`/`CONTRIBUTING.md` in repo; README evidence contract (§"Bounded project evidence JSON", README.md:1161, 1195-1218); `docs/c1-read-interface-ticket.md:33,35,37`; `pyproject.toml:36-37` (`ruff line-length = 100`, not CI-enforced — `.github/workflows/ci.yml` runs only unittest).

## Standards

**Required fixes:** none.

**Optional (judgement calls):**

- `factory/evidence.py:396` — `verified` is definitionally `revision is not None` (`revision if verified else None`); `tests/test_evidence.py:289` pins that equivalence. Repo convention for unknown facts is a nullable value (README.md:1207-1208; `dashboard.py:940` nullable `revision`). Redundant field; harmless.
- `factory/evidence.py:397` vs `:420` — `reader.evidence_schema/runtime_schema` restate the sibling `producers.evidence_schema/runtime_schema` in the same dict. Duplicated fact. If the nested copies are the intended long-term home, say so; otherwise drop one side.
- `factory/evidence.py:383-390` — possible Duplicated Code with `factory/dashboard.py:936-940` (same `rev-parse HEAD` / `ls-files --error-unmatch` / `status --porcelain -- .` shape on `Path(__file__).resolve().parent`). The new copy is justified as-is: it needs timeout (`COMMAND_SECONDS`), `GIT_OPTIONAL_LOCKS=0` for the no-write guarantee (docs/c1-read-interface-ticket.md:37), and must distinguish "clean = empty output" from failure, which neither `brief.git` (`brief.py:36-38`, returns `[]` for both) nor `dashboard.sh` (`dashboard.py:175-177`, returns `""` for both) can. Unifying would require touching dashboard.py; not requested.
- `factory/evidence.py:375-380` docstring says "when this module is a clean, tracked git file", but `:394` checks the whole package directory (`status --porcelain -- .`). Behaviour is stricter than the prose (any untracked non-ignored file under `factory/` flips `verified` to false). Conservative direction, so not a defect; align the docstring.
- `tests/test_evidence.py:275-283` — `reader_from` re-implements the subprocess half of `invoke` (`:205-236`) without its bounded-output (`:218`) and selected-root no-write (`:216`) assertions. An `env=` kwarg on `invoke` would give the two provenance runs those checks for free. The audit hook (`:27-38`, via guard `PYTHONPATH` at `:276`) still covers in-process writes for both runs.
- README.md:1216-1218 ("`capabilities` advertises only implemented operations and limits, evidence and runtime schema 1 support, the accepted escalation path, and an empty action menu") is now incomplete; `CHANGELOG.md` Unreleased has entries for most feature tickets (#53, #79, #67…) but not all (agent/4–9 commits skipped it). No documented rule requires either; one line each would be consistent.
- Over-length lines at `factory/evidence.py:421`, `tests/test_evidence.py:278-280,308` exceed `line-length = 100`; not CI-enforced and `evidence.py` already has many such lines on `origin/main` (e.g. `:1106`). Skip.

## Spec

Issue #87 acceptance criteria:

1. **"Existing evidence capability/inspection output identifies verifiable build revision and supported schema separately from repository revision and service identity."** — Met. `factory/evidence.py:420` adds `producers.reader = {revision, verified, evidence_schema, runtime_schema}` to `capabilities`, sourced from the engine's own package dir (`:381`), independent of `scope.root`/`--root` (`:1108-1113`) and of F03 runtime/service identity. `docs/c1-read-interface-ticket.md:33` names `capabilities` as the home for "producer/schema support", so this placement reuses the existing contract.
   *Ambiguity, non-blocking:* "capability/inspection output" could also be read as the `inspect` op; the diff adds identity to `capabilities` only (`:1127`). The same process answers both ops, so a consumer can obtain engine identity with one extra `capabilities` read. If the author intended `inspect` too, that is a scope question for the issue owner, not a defect.
2. **"Unavailable provenance is explicitly unknown; version strings or checkout HEAD never fabricate installed build identity."** — Met. `revision` is `null` unless `rev-parse HEAD` yields a full SHA (`SHA.fullmatch`, `:49,:395`), `evidence.py` is tracked (`:393`), and the package dir is clean (`:394`). `__version__` is never consulted; git failure/timeout/missing binary → `None` (`:387-390`). Selected-repo HEAD is never read. `tests/test_evidence.py:308` asserts engine revision ≠ selected repo head; `:310-313` asserts a locally modified engine reports `(False, None)`.
3. **"Exercise actual JSON CLI for verified and unavailable provenance; preserve bounded output and zero operational writes."** — Met. `tests/test_evidence.py:278-280` runs the real `factory.cli evidence` subprocess for a clean committed engine copy (`:297-307`) and for a modified one (`:310-313`), under the write-auditing guard (`:281`). Implementation side: `rev-parse`/`ls-files` are read-only; `status` with `GIT_OPTIONAL_LOCKS=0` (`:382`) skips the index refresh write; stderr captured (`:385`) so no raw diagnostics leak (README.md:1200); worst case 3×20 s stays under the 90 s deadline checked at `:1134`. Bounded-output assertion runs only on the environment-dependent first `invoke()` (`:286`), not on the two deterministic runs — see Standards optional item; the output path itself is unchanged.

Scope creep: none. "Record commands and observed results in the PR" is PR-body content, not visible in the diff.

**Required fixes:** none.

## Changed contracts and their consumers

- `capabilities()["producers"]` gains additive key `reader` (`factory/evidence.py:420`). Consumers outside the diff: `factory/evidence.py:1127-1128` (assigns and cites via `source()`, `:236-240`, 20 KB clip — still holds, addition is ~150 bytes); `tests/test_evidence.py:266-273` asserts `reads`/`actions` only — still holds. No `producers` consumer in `console/`, `docs/`, or README (grep). Existing keys unchanged.
- `reader_build()` is new, no prior callers. No flag, default, or CLI signature changed.

Net-new abstraction beyond the brief: none. The `git()` closure at `:383-390` is a local helper, not a reusable abstraction, and is justified above.

**Summary:** Standards — 0 required, 7 optional (worst: redundant `verified` + duplicated schema keys in `reader`). Spec — 0 required, 1 non-blocking interpretation note on "capability/inspection output".

VERDICT: APPROVE
