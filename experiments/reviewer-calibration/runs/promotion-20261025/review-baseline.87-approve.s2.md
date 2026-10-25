## Inputs

`./review.diff` and `./snapshot` are absent from the sandbox; reviewed `git diff origin/main...HEAD` (`76a1a7a..19bd468`, 2 files, +66/−2) in the checkout instead. Issue #87 fetched from GitHub (no `gh` token; web read). No AGENTS.md/CONTRIBUTING.md exist in the repo; standards sources are README `## Bounded project evidence JSON (schema 1)` and CHANGELOG.

## Standards

No documented-rule violations.

- `factory/evidence.py:381-390` — nested `git()` closure duplicates the shape of `config.git` (`factory/config.py:170-175`). Justified: `config.git` raises `ConfigError`, which the CLI maps to `invalid_scope`/exit 2 (`factory/evidence.py:1140-1143`); AC2 requires unavailable provenance to be *explicit unknown*, not a failed read. It also adds `timeout=COMMAND_SECONDS` and `GIT_OPTIONAL_LOCKS=0`, matching the existing subprocess env convention at `factory/evidence.py:321` and `factory/runtime_local.py:78`. Judgement call, not a smell to fix.
- `factory/evidence.py:396-397` — `evidence_schema`/`runtime_schema` now appear twice (`producers.reader.*` and `producers.*`, `evidence.py:420`). Duplicated data, not logic; keeping the top-level keys preserves the existing schema-1 contract for current consumers. Acceptable.

## Spec (#87)

- AC1 (build revision + supported schema separate from repo revision/service identity): met. `producers.reader` reports the engine's own checkout HEAD (`evidence.py:381,392`) and schema support (`evidence.py:397`); the selected `--root` revision is never consulted. Test asserts `revision != self.head` (`tests/test_evidence.py:306`).
- AC2 (unavailable = explicit unknown; no fabrication from version string or checkout HEAD): met. `verified` requires a full SHA, a tracked module (`ls-files --error-unmatch`, `evidence.py:393`), and clean package status (`evidence.py:394-395`); any git failure returns `None` (`evidence.py:388-390`) → `revision:null, verified:false`. A packaged install under an unrelated repo (e.g. gitignored venv) fails the tracked check. Version string is not referenced.
- AC3 (actual JSON CLI for verified and unavailable provenance; bounded output; zero writes): met. `reader_from` drives `python -B -m factory.cli evidence` (`tests/test_evidence.py:279`) against a committed engine copy and the same tree locally modified (`tests/test_evidence.py:296-310`), under the audit-hook write guard (`tests/test_evidence.py:281`). `GIT_OPTIONAL_LOCKS=0` (`evidence.py:382`) keeps `git status` from writing the index. Gate: test PASS.
- "Record commands and observed results in the PR": not verifiable from the diff; PR body not in evidence.

## Plan drift

Plan status not recorded; no named files or planned proof to compare. Changed files: `factory/evidence.py`, `tests/test_evidence.py` — both inside the issue's "existing owning CLI, evidence boundary and its consumers". No drift finding.

## Required fixes

None.

## Optional suggestions

1. `README.md:1216-1218` states `capabilities` "advertises **only** … evidence and runtime schema 1 support, the accepted escalation path, and an empty action menu." The new `producers.reader` object makes that sentence inaccurate; the diff does not touch README or `CHANGELOG.md` `## Unreleased` (every other user-visible change there carries an entry). No documented rule mandates it, so non-blocking — but the JSON contract doc is what consumers read.
2. `factory/evidence.py:392-394` — three sequential `subprocess.run` calls each bounded by `COMMAND_SECONDS=20` but not by the read `deadline`; `capabilities()` (`evidence.py:1127`) previously ran no subprocesses. Worst case 60 s < `READ_SECONDS=90`, and local git on a package directory is milliseconds, so no concrete defect.
3. `factory/evidence.py:394` — docstring says verified "only when this module is a clean, tracked git file," but `status --porcelain -- .` checks the whole package directory, so a modified sibling module also marks it unknown. Stricter than documented; arguably the right behavior (the engine is the package), but the docstring could say so.
4. `tests/test_evidence.py:279-283` — `reader_from` omits the `len(proc.stdout) < 1_048_576` bound that `invoke` asserts (`tests/test_evidence.py:218`); the first assertion path via `self.invoke()` (`:286`) still covers bounded output for the capabilities op.

VERDICT: APPROVE
