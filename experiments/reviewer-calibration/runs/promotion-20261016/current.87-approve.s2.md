## Standards

Sources checked: no `AGENTS.md`/`CONTRIBUTING.md` in repo; README `## Bounded project evidence JSON (schema 1)` (README.md:1130–1300), `docs/c1-read-interface-ticket.md`, `docs/c1-read-interface-handoff.md`, `.factory.toml` gate.

**Required fixes:** none.

**Optional suggestions**

- `factory/evidence.py:374-397` — `reader_build()` duplicates the exact three-call provenance shape already at `factory/dashboard.py:936-940` (`rev-parse HEAD`, `ls-files --error-unmatch <own file>`, `status --porcelain -- .`, null on dirty/untracked). Judgement call (Duplicated Code). The new copy is the better one (timeout, `GIT_OPTIONAL_LOCKS=0`, `OSError` guard); not asking for a shared helper now, since it would be a net-new seam across dashboard/evidence that no criterion requires.
- `factory/evidence.py:420-421` + `README.md:1161,1216-1218` / `CHANGELOG.md:3` — `producers.reader` is a new public field in the documented `capabilities` contract; README's capabilities description and the `## Unreleased` changelog are not updated. No documented rule mandates it, so non-blocking; existing entries cite issue numbers (`CHANGELOG.md:7,9`), so a one-liner `(#87)` would match convention.
- `factory/evidence.py:382` — env is `{**os.environ, ...}`; an inherited `GIT_DIR`/`GIT_WORK_TREE` (e.g. running under a git hook of the operator repo) redirects all three calls. It fails closed (`ls-files --error-unmatch evidence.py` misses → `verified:false`), so correctness holds; `runtime_local.py:77-79` is the existing precedent for a scrubbed env if you want parity.
- `factory/evidence.py:376-377` docstring says "this module is a clean, tracked git file", but `status --porcelain -- .` (line 394) checks the whole package directory. Wording only.

## Spec

Issue #87 acceptance criteria vs diff:

1. *Build revision and supported schema identified separately from repository revision and service identity* — `factory/evidence.py:420` nests `reader:{revision,verified,evidence_schema,runtime_schema}` under `producers`, derived from `Path(__file__)` (line 381), not `--root`. Test `tests/test_evidence.py:306` asserts the engine revision differs from the selected repo head. **Met.**
2. *Unavailable provenance explicitly unknown; never fabricated from version strings or checkout HEAD* — `revision` is `None` unless full-SHA + tracked + clean (lines 395-397); no version string or `--root` HEAD is consulted anywhere in the hunk. Test lines 308-311 cover the locally-modified case → `(False, None)`. **Met.**
3. *Exercise actual JSON CLI for verified and unavailable provenance; bounded output; zero operational writes* — `reader_from` (test 275-284) runs `python -m factory.cli evidence` as a subprocess under the `sitecustomize` write guard and asserts no `EVIDENCE_FORBIDDEN:`; both verified (304-306) and modified (308-311) paths run through it. Output is a fixed four-key dict. Git side effects are suppressed by `GIT_OPTIONAL_LOCKS=0` (line 382); three bounded calls at `COMMAND_SECONDS` each (line 387). **Met.**

Scope creep: none. Touches only `capabilities()` and one test, per "reuse existing contracts".

Not verifiable from the diff: the issue's "record commands and observed results in the PR" — PR body, outside review scope.

**Required fixes:** none.

---

Summary — Standards: 0 required, 4 optional (worst: duplicated provenance logic vs `dashboard.py:936`). Spec: 0 required, 0 optional; all three acceptance criteria evidenced in the diff.

VERDICT: APPROVE
