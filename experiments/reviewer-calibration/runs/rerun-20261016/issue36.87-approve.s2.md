# Review: #87 reader build and schema identity (`76a1a7a..19bd468`)

## Spec check

| Criterion | Evidence | Status |
|---|---|---|
| Build revision + schema reported separately from repo revision and service identity | `factory/evidence.py:374-398` builds `reader` from the engine's own `Path(__file__)` checkout, not `--root`; `factory/evidence.py:420` nests it under `producers.reader`; `tests/test_evidence.py:308` asserts `revision != self.head` through the actual CLI | Met |
| Unavailable provenance explicitly unknown; no fabrication from version string or checkout HEAD | `factory/evidence.py:393-396`: `revision` is `None` unless `rev-parse` yields a full SHA **and** the module is tracked **and** `status --porcelain -- .` is empty; failure of any git call returns `None` (`:387-389`). No reference to package version. `tests/test_evidence.py:310-313` exercises the modified-tree → `(False, None)` path | Met |
| Actual JSON CLI exercised for verified and unavailable provenance; bounded output; zero writes | `tests/test_evidence.py:275-283` runs `python -B -m factory.cli evidence` via subprocess for both states; guard sentinel checked at `:281`; `GIT_OPTIONAL_LOCKS=0` at `factory/evidence.py:382` prevents index/lock writes from `status`; reader payload is four fixed fields | Met |

Consistent with README's existing producer convention ("a clean source-checkout Git revision, otherwise null, never a CLI version") and the F03 `--no-optional-locks` discovery pattern. Existing `producers.evidence_schema` / `runtime_schema` keys preserved (`factory/evidence.py:420`), so current consumers are unaffected.

## Required fixes

None.

## Optional suggestions

- `factory/evidence.py:420-421` — `evidence_schema`/`runtime_schema` now appear twice (`producers` and `producers.reader`). Retaining the top-level keys is correct for contract stability; a one-line README note under "Bounded project evidence JSON → capabilities" documenting `producers.reader` (`revision`, `verified`, schemas) would make the new field discoverable. No documented rule requires it at this head.
- `factory/evidence.py:385-386` — three sequential `git` calls each bounded by `COMMAND_SECONDS`; worst case consumes a meaningful slice of the 90 s whole-read budget before any collection. `capabilities` performs no GitHub reads, so this is not a defect today; worth knowing if `reader_build()` is later reused in collecting ops.
- `tests/test_evidence.py:296` — `shutil.copytree` also copies any `__pycache__` present in the source tree into the engine repo; `git add .` commits it so `status` stays clean and the test remains deterministic, but `ignore=shutil.ignore_patterns("__pycache__")` would keep the fixture minimal.

No net-new abstractions beyond the brief; `reader_build()` is the single producer the acceptance criteria require.

VERDICT: APPROVE
