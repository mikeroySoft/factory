## Review: #87 reader build and schema identity

### Standards (README.md; no AGENTS.md/CONTRIBUTING.md at this head)

- `factory/evidence.py:382` — `GIT_OPTIONAL_LOCKS=0` plus `rev-parse`/`ls-files`/`status --porcelain` matches the README's documented local-Git convention (`git --no-optional-locks rev-parse`, "no state directory, lock, event … or ownership file"). No index refresh, no writes. Consistent with the feedback producer rule "a clean source-checkout Git revision, otherwise null, never a CLI version."
- `factory/evidence.py:420-421` — change is additive inside the existing `producers` object; README says consumers must ignore additive keys. Existing contract preserved.

### Spec (issue #87 acceptance criteria)

1. **Separate build revision / schema from repo revision and service identity** — `factory/evidence.py:395-396` reports `reader.revision`, `reader.verified`, `reader.evidence_schema`, `reader.runtime_schema` scoped to `Path(__file__).resolve().parent`, not `--root`. `tests/test_evidence.py:306` asserts the engine revision differs from the selected repo `self.head`. Met.
2. **Unavailable provenance explicitly unknown; no fabrication from version string or checkout HEAD** — `factory/evidence.py:394-395`: `revision` is `None` unless `rev-parse` succeeds, the SHA parses, the module is tracked (`ls-files --error-unmatch`), and the package dir is clean. An installed wheel inside an unrelated enclosing repo fails the `ls-files` check, so the enclosing HEAD cannot leak in. Missing `git`/timeout → `OSError`/`SubprocessError` → `None`. Met.
3. **Actual JSON CLI for verified and unavailable provenance; bounded output; zero writes** — `tests/test_evidence.py:275-281` runs `python -B -m factory.cli evidence` with the write guard on `PYTHONPATH`; `:305,:311` cover clean-commit (verified) and locally-modified (unknown) engines. Gate `test: PASS`. Met.

### Required fixes

None.

### Optional suggestions

- `factory/evidence.py:395-396` — `evidence_schema`/`runtime_schema` now appear twice (`producers.*` and `producers.reader.*`). Harmless, but one of the two is dead weight; if the top-level keys are kept for old consumers, say so in the README contract.
- `factory/evidence.py:392` — `status --porcelain -- .` checks only the `factory/` package directory, so an edit elsewhere in the reader's checkout (e.g. `pyproject.toml`) still reports `verified: true` for that HEAD. Acceptable since only package code executes, but worth a sentence in the docstring.
- README "Bounded project evidence JSON" → `capabilities` row does not mention `producers.reader` or its `verified`/`revision` null semantics. Not a documented rule, but the rest of the evidence contract is specified there.
- `factory/evidence.py:385` — three sequential `git` calls each bounded by `COMMAND_SECONDS`; worst case adds up to 3× that to a `capabilities` read that the README describes as "No case or GitHub collection." Still inside the 90 s whole-read deadline; noting only.

VERDICT: APPROVE
