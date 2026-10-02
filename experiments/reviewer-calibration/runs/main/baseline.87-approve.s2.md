## Spec (issue #87)

- **Separate build identity** — `factory/evidence.py:420` adds `producers.reader` as its own object (`revision`, `verified`, `evidence_schema`, `runtime_schema`) beside the retained `evidence_schema`/`runtime_schema`/`runtime_reader` keys. Revision is derived from the engine's own source checkout (`factory/evidence.py:381`, `:392`), not `--root`; `tests/test_evidence.py:306` asserts it differs from the selected repository head. Criterion 1 met.
- **Unknown, never fabricated** — `factory/evidence.py:395-397`: `revision` is `None` unless `rev-parse` yields a full SHA, `evidence.py` is tracked (`:393`), and the package dir is clean (`:394`). No fallback to a version string or `--root` HEAD. `tests/test_evidence.py:308-311` proves the locally-modified case reports `(False, None)`. Matches the README's existing rule ("clean source-checkout Git revision, otherwise null, never a CLI version"). Criterion 2 met.
- **Actual JSON CLI, both branches, no writes** — `tests/test_evidence.py:275-283` runs `python -B -m factory.cli evidence --root …` with `op: capabilities`; `:295-306` for a clean committed engine, `:308-311` for a modified one. `:281` asserts the write guard didn't fire. `GIT_OPTIONAL_LOCKS=0` (`factory/evidence.py:382`) keeps the git probes from touching the index. Each git call is bounded by `COMMAND_SECONDS` (`:386`). Criterion 3 met.

## Standards

- README documents the feedback producer's revision with the same clean-checkout semantics; `factory/evidence.py:384-390` implements its own git wrapper rather than visibly reusing that helper. Whether a shared helper exists is outside the diff `[INFERENCE]`; semantics are identical, so non-blocking.
- Non-blocking: `status --porcelain -- .` (`factory/evidence.py:394`) scopes cleanliness to the `factory/` package dir only; consistent with "installed engine" identity, just noting the boundary.

No findings fail an acceptance criterion or a documented convention.

VERDICT: APPROVE
