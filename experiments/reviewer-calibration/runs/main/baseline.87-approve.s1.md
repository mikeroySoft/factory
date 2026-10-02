## Findings

**Spec (issue #87)**

- Build identity separate from repo/service: `factory/evidence.py:374-397` adds `reader_build()` and `factory/evidence.py:420-421` nests it under `producers.reader`, distinct from the `--root` checkout identity. Test asserts the engine revision differs from the selected repo HEAD at `tests/test_evidence.py:305`. Satisfied.
- No fabrication: revision reported only when `rev-parse` succeeds, matches `SHA`, module is tracked, and `status --porcelain -- .` is empty (`factory/evidence.py:391-394`); otherwise `revision: null, verified: false`. Neither package version nor `--root` HEAD is consulted. Matches README's existing producer-revision convention ("clean source-checkout Git revision, otherwise null, never a CLI version"). Satisfied.
- Actual JSON CLI exercised for both verified and unavailable provenance: `tests/test_evidence.py:275-283` runs `python -B -m factory.cli evidence … capabilities` against a clean committed engine copy (`:294-303`) and again after a local modification (`:307-310`). Satisfied.
- Zero operational writes: `GIT_OPTIONAL_LOCKS=0` (`factory/evidence.py:382`) prevents index refresh writes from `status`; `check=False` with `COMMAND_SECONDS` timeout keeps it bounded (`:386-387`). Output remains a fixed four-key object. Satisfied.

**Standards (README)**

- No violation. README does not enumerate `producers` keys, so the additive `reader` field needs no doc change under documented conventions.

**Non-blocking notes**

- `evidence_schema`/`runtime_schema` now appear twice (`factory/evidence.py:396` and `:420`). Redundant but harmless; consumers of the old keys are unaffected.
- PR-body command/result recording required by the issue is outside the diff; not assessable here.

VERDICT: APPROVE
