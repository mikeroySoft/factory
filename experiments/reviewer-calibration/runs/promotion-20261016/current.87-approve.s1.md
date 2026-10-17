## Standards

Sources checked: `README.md` (capabilities contract §1161), `docs/c1-read-interface-handoff.md`, `docs/autonomy-execution-plan.md`, `CHANGELOG.md`. No `AGENTS.md`/`CONTRIBUTING.md` in repo.

No documented-rule violations. Observations (judgement calls, non-blocking):

- **Duplicated Code** — `tests/test_evidence.py:275-283` `reader_from` re-implements `invoke` (`tests/test_evidence.py:205-236`) minus its `self.state()` before/after no-write check and the `len(proc.stdout) < 1_048_576` bound. An `env=` kwarg on `invoke` would be a smaller diff and would carry the existing no-write/bounded-output assertions into the verified/unavailable CLI runs instead of only the audit-hook check.
- **Duplicated literal** — `factory/evidence.py:397` and `:420` both hard-code `"evidence_schema": 1, "runtime_schema": 1`; the parent `producers` keys are kept for compatibility, so the schema number now lives in four places. Harmless today; drifts if the schema bumps.
- `factory/evidence.py:375-380` docstring says "reported only when this module is a clean, tracked git file", but `:394` checks `git status --porcelain -- .` over the whole package directory. Behaviour is the stricter (better) one; docstring is slightly inaccurate.
- `CHANGELOG.md` `## Unreleased` records every accepted contract change with its issue number; the diff adds no entry for #87. Hand-maintained per `docs/autonomy-execution-plan.md:510`; not a stated hard rule, so optional.

## Spec

Issue #87 acceptance criteria vs. diff:

1. *Capability output identifies verifiable build revision and supported schema separately from repository revision and service identity* — **Met.** `factory/evidence.py:420` adds `producers.reader = {revision, verified, evidence_schema, runtime_schema}`; revision is derived from the engine's own `Path(__file__)` checkout (`:381`), never from `--root`. Test `tests/test_evidence.py:306` asserts engine revision ≠ selected-repo HEAD.
2. *Unavailable provenance is explicitly unknown; version strings or checkout HEAD never fabricate identity* — **Met.** `:395-396` requires full-SHA `rev-parse HEAD` **and** `ls-files --error-unmatch` on the module **and** clean package status; otherwise `revision: null, verified: false`. The tracked check correctly prevents a packaged install living under an unrelated repo (e.g. `.venv` inside the operator's project) from inheriting that repo's HEAD. No version string is consulted. Non-git install → `git` exits 128 → `None` (`:388-390`).
3. *Exercise actual JSON CLI for verified and unavailable provenance; preserve bounded output and zero operational writes* — **Met.** `tests/test_evidence.py:292-310` runs `factory.cli evidence` against a committed engine copy (verified) and the same tree locally modified (unavailable). `factory/evidence.py:382` sets `GIT_OPTIONAL_LOCKS=0`, matching the existing `github_read` convention at `:321`, so `git status` does not refresh the index. Output addition is a fixed four-key dict.

No scope creep: diff touches only `factory/evidence.py` and its focused test, per "Touches: existing evidence boundary". No new abstraction beyond the one `reader_build()` producer the brief asks for; the nested `git()` closure is local and single-use.

## Required fixes

None.

## Optional suggestions

- Route `reader_from` through `invoke(env=…)` so the verified/unavailable CLI runs inherit the existing no-write and output-bound assertions (`tests/test_evidence.py:275-283`).
- Add an `Unreleased` CHANGELOG line for #87.
- Align the `reader_build` docstring with the package-wide status check (`factory/evidence.py:377-378`).

Summary — Standards: 4 judgement-call observations, none hard; Spec: 0 findings, all three criteria satisfied.

VERDICT: APPROVE
