**Inputs.** `./review.diff` and `./snapshot` are absent from the sandbox; reviewed `git diff origin/main..HEAD` (single commit `19bd468`, base `76a1a7a` = `origin/main`) against the working checkout. Issue #87 read via github.com (no `GH_TOKEN`). No `AGENTS.md`/`CONTRIBUTING.md` in repo; standards sources are `README.md` (evidence contract §"Bounded project evidence JSON") and `CHANGELOG.md` convention.

## Standards
- README 1132–1149 / 1246+ contract (one JSON in, one out, bounded, no writes): held. `reader_build` (`factory/evidence.py:381-395`) runs three git reads with `GIT_OPTIONAL_LOCKS=0` and `COMMAND_SECONDS` timeouts; `OSError`/timeout → `None` → `verified:false`. Worst case 60s < `READ_SECONDS`.
- Baseline smell, judgement call: `evidence_schema`/`runtime_schema` now emitted twice (`evidence.py:396-397` inside `reader`, `:420` at `producers`). Harmless duplication; kept presumably for contract stability.
- README 1216–1218 (`capabilities` description) and CHANGELOG `Unreleased` (every feature carries `(#N)`) not updated. Convention, not documented rule → nit.

## Spec (#87)
- AC1 "verifiable build revision and supported schema separately from repository revision and service identity": `producers.reader = {revision, verified, evidence_schema, runtime_schema}` (`evidence.py:396-397`); test asserts `revision != self.head` (`tests/test_evidence.py:306`). Met.
- AC2 "explicitly unknown; never fabricated from version string or checkout HEAD": `revision` is `None` unless HEAD is a full SHA, `evidence.py` is tracked, and package dir status is clean (`evidence.py:392-395`); `__version__` and `--root` are not consulted. Met.
- AC3 "exercise actual JSON CLI for verified and unavailable provenance; bounded output, zero writes": `reader_from` (`test_evidence.py:275-283`) runs the real CLI under the audit-hook guard for a clean committed engine copy and a locally modified one (`:296-311`). Guard covers writes; `reader_from` omits the `len(stdout) < 1_048_576` and `before == self.state()` checks that `invoke` applies (`:216-218`). Partial for the two provenance runs → nit.

## Plan drift
Plan not recorded → no named-file baseline. Files changed: `factory/evidence.py`, `tests/test_evidence.py`. Gate report covers conflict-markers/test/leak-scan; the issue's "record commands and observed results in the PR" is PR-body evidence outside this diff and not in the gate report. No defect exposed.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"tests/test_evidence.py","line":279,"summary":"reader_from exercises verified/unavailable provenance via the real CLI but, unlike invoke (:216-218), does not assert the output-size bound or unchanged --root checkout state; AC3 'preserve bounded output and zero operational writes' rests on the audit guard alone for these two runs."},
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":420,"summary":"evidence_schema/runtime_schema now emitted both at producers and producers.reader (:396-397); duplicated fact, pick one or document why both persist."},
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":420,"summary":"New producers.reader field is undocumented: README.md:1216-1218 capabilities description and CHANGELOG.md Unreleased (#87 entry per repo convention) not updated."}
]}
```
VERDICT: APPROVE
