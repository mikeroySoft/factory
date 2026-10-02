## Review: #87 reader build and schema identity

### Spec coverage

- **AC1 (separate build revision + schema):** `factory/evidence.py:396-397` emits `producers.reader = {revision, verified, evidence_schema, runtime_schema}`; the revision is derived from the module's own checkout (`factory/evidence.py:381`, `:392`), never from `--root`. Test `tests/test_evidence.py:306` asserts the engine revision differs from the selected repo HEAD. Satisfied.
- **AC2 (unknown never fabricated):** `factory/evidence.py:395-396` requires full SHA match, tracked file, and clean package-dir status; otherwise `revision: null, verified: false`. No version-string or `--root` fallback exists. `tests/test_evidence.py:308-311` exercises the locally-modified → unavailable path. Satisfied.
- **AC3 (actual JSON CLI, bounded, zero writes):** `tests/test_evidence.py:279-282` runs `python -B -m factory.cli evidence` through the existing write guard (`EVIDENCE_FORBIDDEN` asserted absent) for both verified (`:304-305`) and unavailable (`:310-311`) provenance. `GIT_OPTIONAL_LOCKS=0` at `factory/evidence.py:382` prevents `git status` index refresh writes, consistent with the README's `--no-optional-locks` discovery convention. Output addition is a fixed small object. Satisfied.

### Required fixes

None.

### Optional suggestions

1. `factory/evidence.py:392-394` — three sequential `git` calls each bounded by `COMMAND_SECONDS` but not by the whole-read deadline passed to other collectors. Worst case ~3×20 s inside a `capabilities` read documented as "No case or GitHub collection". Local git is fast in practice; consider a shorter per-call timeout (the F03 path uses 0.5 s for local commands).
2. `factory/evidence.py:396-397` and `:420` — `evidence_schema`/`runtime_schema` now appear both inside `reader` and at `producers` level. Harmless duplication; one location would be clearer, but the top-level keys are an existing consumer contract so leaving them is the safe choice.
3. README "Bounded project evidence JSON" section does not mention `producers.reader` or its unknown semantics. Additive key, no documented rule requires it; a one-line note would help consumers interpret `revision: null`.
4. `tests/test_evidence.py:296` — `copytree` of the live package may copy `__pycache__` into the engine fixture. Committed with `git add .`, so status remains clean and `-B` prevents new writes; not a defect, but `ignore=shutil.ignore_patterns("__pycache__")` would make the fixture deterministic across hosts.

### Not reviewable from the diff

PR body must record the CLI commands and `python -m unittest discover -s tests` results per the issue; not visible in this packet.

VERDICT: APPROVE
