## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README is the only documented convention source. Nothing in the diff conflicts with it: the read stays non-persisting (`GIT_OPTIONAL_LOCKS=0`, `factory/evidence.py:382`), bounded per command (`timeout=COMMAND_SECONDS`, `factory/evidence.py:387`), and additive to the schema-1 `capabilities` envelope (`factory/evidence.py:420-421`).

## Spec (issue #87)

- **AC1 — build revision + supported schema separate from repo revision/service identity.** Satisfied. `producers.reader` carries `revision`, `verified`, `evidence_schema`, `runtime_schema` (`factory/evidence.py:395-396`) derived from the module's own checkout (`Path(__file__).resolve().parent`, `factory/evidence.py:380`), not `--root`. Test pins separation from the selected repo HEAD (`tests/test_evidence.py:306`).
- **AC2 — unavailable provenance explicitly unknown, never fabricated.** Satisfied. `verified` requires a full SHA, the module tracked by git (`ls-files --error-unmatch`), and a clean package directory (`factory/evidence.py:391-394`); otherwise `revision: null, verified: false`. No fallback to package version or `--root` HEAD. The locally-modified case is exercised (`tests/test_evidence.py:308-311`).
- **AC3 — actual JSON CLI, verified and unavailable paths, bounded output, zero writes.** Satisfied. Both paths go through `python -B -m factory.cli evidence` subprocess (`tests/test_evidence.py:279-280`); the `EVIDENCE_FORBIDDEN` guard assertion covers writes/network (`tests/test_evidence.py:281`). Worst-case added latency is 3×`COMMAND_SECONDS` git calls, within the 90 s whole-read ceiling.

Correctness checks: `git -c user.name/email` placed before `commit` is correct ordering (`tests/test_evidence.py:298-299`); copying `__pycache__` into the engine tree is harmless because `git add .` commits it and `-B` prevents new bytecode on the second run.

## Required fixes

None.

## Optional suggestions

- `evidence_schema`/`runtime_schema` are now emitted twice — inside `reader` (`factory/evidence.py:396`) and at the `producers` level (`factory/evidence.py:420`). Harmless for consumers, but one authoritative location would be clearer. Keeping the top-level pair preserves backward compatibility, so leaving as-is is defensible.
- README documents `producers.revision` semantics for the feedback collector ("clean source-checkout Git revision, otherwise null, never a CLI version") but does not mention the new `capabilities.producers.reader` object. A one-line addition under "Bounded project evidence JSON" would let consumers discover the field without reading source. Not a documented rule; non-blocking.
- The feedback producer already has a "clean source-checkout revision or null" concept per README; if `factory/feedback.py` exposes a reusable helper, `reader_build` could delegate to it rather than carrying a second git-probe implementation. Cannot verify from the diff alone; `[INFERENCE]`.

VERDICT: APPROVE
