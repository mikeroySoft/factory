## Spec (issue #87)

- **Build revision / schema separate from repo & service identity** — `factory/evidence.py:374-397` derives identity from `Path(__file__)`'s own checkout, not `--root`; exposed under `producers.reader` (`evidence.py:420`). Test pins the separation: `tests/test_evidence.py:305` asserts engine revision ≠ selected-repo `self.head`. Satisfied.
- **Unknown, never fabricated** — `evidence.py:390-394`: `revision` is reported only when `rev-parse` yields a full SHA *and* `evidence.py` is a tracked file *and* the package dir is clean; otherwise `revision: null, verified: false`. The `ls-files --error-unmatch` gate (`evidence.py:391`) is what prevents a site-packages install nested under some unrelated repo from inheriting that repo's HEAD via upward `.git` discovery — that's the important case and it's covered. Version string is never consulted. Null-as-unknown matches the README's "nullable means unknown" convention. Satisfied.
- **Actual JSON CLI, verified + unavailable, bounded, zero writes** — `tests/test_evidence.py:275-283` drives `python -B -m factory.cli evidence` via stdin; verified path at `:296-305`, unavailable path at `:307-310`. `GIT_OPTIONAL_LOCKS=0` (`evidence.py:382`) keeps `git status` from refreshing the index, consistent with the README's `--no-optional-locks` discovery convention. Each git call bounded by `COMMAND_SECONDS` (`evidence.py:387`). Satisfied.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README's capabilities contract doesn't enumerate `producers` keys, so the additive `reader` object (`evidence.py:420`) breaks no documented contract.

## Non-blocking notes

- `evidence.py:420-421` now duplicates `evidence_schema`/`runtime_schema` at both `producers` and `producers.reader`. Harmless for compatibility; not a criterion.
- `capabilities` is documented as "no collection"; it now runs three local git subprocesses (`evidence.py:390-392`), worst case 3×`COMMAND_SECONDS` within the 90 s read deadline. Still bounded and local; no rule violated.
- `[INFERENCE]` `os`, `subprocess`, `SHA`, `COMMAND_SECONDS` in `evidence.py` and `shutil`, `ROOT`, `self.guard`, `self.head` in the test are assumed pre-existing (not visible in the diff); gate `test: PASS` is consistent with that.
- README's evidence section is not updated to mention `producers.reader`; no documented rule requires it, so not a REVISE condition.

VERDICT: APPROVE
