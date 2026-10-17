## Standards

Sources checked: no `AGENTS.md`/`CONTRIBUTING.md` in repo; `docs/c1-read-interface-ticket.md` (evidence CLI contract), `README.md:1130-1200`, `CHANGELOG.md` Unreleased convention.

- Bounded, read-only, dependency-free (`docs/c1-read-interface-ticket.md:37,39,50`): holds. Three fixed git queries, each `timeout=COMMAND_SECONDS` (`factory/evidence.py:386-387`), `GIT_OPTIONAL_LOCKS=0` (`:382`) matching `runtime_local.py:78` / `evidence.py:321`; stderr never surfaced (`:390`); only the SHA leaves the process (`:396`).
- No raw diagnostics/credentials in output (`ticket.md:39`): holds, `stdout` only on `returncode == 0` and then regex-validated (`:390,:395`).
- Cancellation contract (module docstring `evidence.py:5-7`): `except (OSError, subprocess.SubprocessError)` (`:388`) lets `SystemExit` from `_cancel_handler` propagate; `subprocess.run` kills the child on unwind. Holds.
- Smell, judgement call: **Duplicated data** — `evidence_schema`/`runtime_schema` now appear both in `producers.reader` (`:397`) and at `producers` top level (`:420`). No consumer reads the top-level keys (grep: `console/`, `README.md:1161` generic, tests only the new lines), so one copy would do. Optional.
- Convention by practice, not documented rule: every Unreleased feature has a `CHANGELOG.md` line with `(#N)`; this diff adds none. Optional.

## Spec

Issue #87 acceptance criteria vs diff:

1. *Verifiable build revision + supported schema, separate from repository revision and service identity* — `producers.reader = {revision, verified, evidence_schema, runtime_schema}` (`factory/evidence.py:396-397,420`). Revision is the engine's own package checkout (`Path(__file__).resolve().parent`, `:381`), never `--root`; test asserts `revision != self.head` (`tests/test_evidence.py:306`). Met.
2. *Unavailable provenance explicitly unknown; no fabrication from version strings or checkout HEAD* — `verified` requires full-SHA HEAD ∧ `ls-files --error-unmatch evidence.py` ∧ empty `status --porcelain -- .` (`:392-394`); otherwise `revision: null, verified: false` (`:396`). The `ls-files` gate correctly rejects a site-packages/`.venv` copy nested inside an unrelated repo (path untracked → `None`). Locally modified tree → unknown, exercised at `tests/test_evidence.py:308-311`. Met.
3. *Exercise actual JSON CLI for verified and unavailable provenance; bounded output, zero operational writes* — both states via `python -m factory.cli evidence` with engine on `PYTHONPATH` (`tests/test_evidence.py:275-283,304-311`); live-checkout path goes through `invoke()` which asserts selected-checkout state unchanged and `<1 MiB` (`tests/test_evidence.py:216-218`). `reader_from` itself omits those two assertions (`:279-283`) — the same code path is already covered by `invoke()` at `:286`. Met.

No scope creep: diff touches only `capabilities()` producers and one test.

### Changed contracts and consumers outside the diff

`capabilities()["producers"]` gains `reader`; existing keys retained.
- `factory/evidence.py:1127-1128` — pass-through into result/source; holds.
- `console/c0-prototype/check.py:79-81` — reads `actions`, `reads` only; holds.
- `README.md:1161` — lists `producers` generically; still accurate.
- `docs/c1-read-interface-handoff.md:44` — `exit 0, actions:[]`; holds.

## Required fixes

None.

## Optional suggestions

- `factory/evidence.py:397,420`: drop one copy of `evidence_schema`/`runtime_schema` (no external consumer of the top-level pair).
- `factory/evidence.py:382`: `git status` honours `core.fsmonitor`; if an operator's engine checkout enables it, `status` can start the fsmonitor daemon in the engine repo. `-c core.fsmonitor=false` (or `GIT_CONFIG_COUNT` equivalent) keeps the probe side-effect-free. Low likelihood; non-blocking.
- `factory/evidence.py:382`: inherited `GIT_DIR`/`GIT_WORK_TREE` (exported inside git hooks) redirects `rev-parse HEAD`; `ls-files` then fails so the result degrades to unknown, not fabrication. Scrubbing those vars would avoid a false unknown. Matches existing `runtime_local.py:77-79` behaviour, so purely optional.
- `tests/test_evidence.py:279-283`: `reader_from` could reuse `invoke(root=…)` with an env override instead of re-implementing the subprocess call, picking up the state/size assertions for free.
- `CHANGELOG.md` Unreleased: add a `(#87)` line.

VERDICT: APPROVE
