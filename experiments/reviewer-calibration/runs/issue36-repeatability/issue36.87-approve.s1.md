# Review: #87 reader build and schema identity (`76a1a7a..19bd468`)

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Build revision + supported schema reported separately from repository revision and service identity | `factory/evidence.py:374-398` — `reader_build()` returns `{revision, verified, evidence_schema, runtime_schema}`; surfaced as a distinct `producers.reader` object at `factory/evidence.py:420-421`. Revision is derived from the module's own checkout (`Path(__file__).resolve().parent`, `:381`), not from `--root`. Selected-checkout identity stays in the existing `scope.root`. | Met |
| Unavailable provenance explicitly unknown; never fabricated from version string or checkout HEAD | `:391-396` — `verified` requires full-SHA match **and** `ls-files --error-unmatch` on this module **and** empty `status --porcelain -- .`; otherwise `revision: None, verified: False`. The tracked check (`:392`) is what defeats the "module sits inside some unrelated enclosing git repo" case (e.g. venv inside a project checkout). No reference to a version string or the `--root` HEAD anywhere in the function. Test `tests/test_evidence.py:289` asserts `verified == (revision is not None)`. | Met |
| Exercise actual JSON CLI for verified and unavailable provenance; bounded output; zero operational writes | `tests/test_evidence.py:275-283` runs `python -B -m factory.cli evidence` via subprocess; `:296-305` clean committed engine copy → `(True, <sha>)`; `:308-311` locally modified copy → `(False, None)`. `:281` asserts no `EVIDENCE_FORBIDDEN:` on stderr (existing write guard). `GIT_OPTIONAL_LOCKS=0` at `factory/evidence.py:382` prevents `git status` index refresh writes. | Met |

Gate: `test: PASS` is authoritative for suite status; not re-run here.

## Standards

No AGENTS.md / CONTRIBUTING.md at this head. README conventions relevant to this change:

- README "Source-versioned PR feedback": *"The producer revision is a clean source-checkout Git revision, otherwise null, never a CLI version."* — `reader_build()` applies the identical policy (`factory/evidence.py:394-396`). Consistent.
- README "Bounded runtime JSON": local git reads use `--no-optional-locks`. `GIT_OPTIONAL_LOCKS=0` env (`:382`) is the equivalent. Consistent.
- Existing `producers` contract is preserved additively (`:420-421`); consumers are documented to ignore additive keys.

## Required fixes

None.

## Optional suggestions (non-blocking)

1. **README not updated for the new field.** README "Result and source contract" describes `capabilities` as advertising "evidence and runtime schema 1 support" but does not mention `producers.reader` or its `verified`/`revision` null semantics. No documented rule mandates README updates, so not blocking; but this is the only contract doc for the JSON consumer. Evidence: `factory/evidence.py:420-421`.
2. **Redundant schema keys.** `evidence_schema`/`runtime_schema` now appear both at `producers.*` and `producers.reader.*` (`factory/evidence.py:397, 420`). Keeping the outer copies is defensible for compatibility; worth a one-line README note on which is canonical.
3. **Worst-case latency.** Three sequential git subprocesses, each bounded by `COMMAND_SECONDS` (`:386`), inside the `capabilities` op which the README describes as doing no collection. Under the 90 s whole-read deadline this cannot overflow, but a wedged `git` could make `capabilities` take up to ~3×`COMMAND_SECONDS`. A single shorter timeout for these local reads would be cheaper; not a correctness defect.
4. **`status --porcelain -- .` includes untracked files** (`:393`). An un-ignored `factory/__pycache__/` in a dev checkout (normal `python -m factory`, no `-B`) will always yield `verified: false`. Direction of error is conservative (unknown, not fabricated), so acceptable; `--untracked-files=no` would make dev-checkout provenance more useful. Note the test sidesteps this with `-B` (`tests/test_evidence.py:279`).
5. `[INFERENCE]` README documents a `producer_revision` input on `feedback.collect` computed elsewhere with the same "clean checkout, else null" policy. If that helper exists in a shared module, this is a second implementation of the same rule; the brief says "reuse existing evidence producers." Cannot confirm from the diff alone.

No net-new abstraction introduced; `reader_build()` is a single function wired into the existing `capabilities()` dict.

VERDICT: APPROVE
