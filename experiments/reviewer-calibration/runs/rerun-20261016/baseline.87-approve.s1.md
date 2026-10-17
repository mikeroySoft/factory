## Spec (issue #87)

**AC1 — build revision and schema separate from repo revision/service identity.** Satisfied. `factory/evidence.py:374-396` derives identity from `Path(__file__).resolve().parent` (the engine's own tree), not from `--root`; output lands under a dedicated `producers.reader` key (`factory/evidence.py:420-421`) with `revision`, `verified`, `evidence_schema`, `runtime_schema`. Test `tests/test_evidence.py:306` asserts the engine revision ≠ the selected repo HEAD.

**AC2 — unavailable provenance explicitly unknown, never fabricated.** Satisfied. `verified` requires a full-SHA `rev-parse`, `ls-files --error-unmatch` on the module, and empty `status --porcelain` (`factory/evidence.py:391-394`); any failure yields `{"revision": None, "verified": False}`. No version string is consulted. Consistent with the README's existing rule for `producer_revision` ("clean source-checkout Git revision, otherwise null, never a CLI version"). The dirty-tree case is exercised at `tests/test_evidence.py:308-311`.

**AC3 — actual JSON CLI, both paths, bounded output, zero writes.** Satisfied. `reader_from` (`tests/test_evidence.py:275-283`) invokes `python -B -m factory.cli evidence --root …` for both a clean committed copy and a locally modified copy. `GIT_OPTIONAL_LOCKS=0` (`factory/evidence.py:382`) prevents `git status` index refresh writes; every git call is bounded by `COMMAND_SECONDS` and swallows `OSError`/`SubprocessError` (`factory/evidence.py:384-390`). Output size is three short scalars plus two ints.

## Standards (README)

No violation found. Capabilities shape stays additive (`factory/evidence.py:420-421` preserves prior `evidence_schema`/`runtime_schema`/`runtime_reader`/`escalation_packet` keys).

Optional, non-blocking:
- README "Bounded project evidence JSON" documents what `capabilities` advertises but is not updated for `producers.reader` (`factory/evidence.py:420`). No documented rule mandates the README edit, so not required.
- Untracked files under `factory/` make `status --porcelain -- .` nonempty (`factory/evidence.py:393`), reporting an otherwise clean committed build as unverified. Conservative, within AC2; noting only as behavior.
- `[INFERENCE]` Issue says "reuse existing evidence producers"; if `factory/feedback.py` already has a clean-checkout producer-revision helper, `reader_build` duplicates it. Not in packet; cannot cite.

Issue also requires recorded commands/results in the PR body — outside the diff, not assessable here.

VERDICT: APPROVE
