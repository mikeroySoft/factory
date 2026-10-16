## Review: #87 reader build and schema identity (`76a1a7a..19bd468`)

### Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Build revision + supported schema reported separately from repo revision / service identity | `factory/evidence.py:374-396` adds `reader_build()` → `{revision, verified, evidence_schema, runtime_schema}`; wired under `producers.reader` at `factory/evidence.py:420`. Revision derives from the engine's own `Path(__file__)` checkout (`:381`), never from `--root`. Test asserts `verified["revision"] != self.head` at `tests/test_evidence.py:305`. | Met |
| Unavailable provenance explicitly unknown; no fabrication from version strings or checkout HEAD | `factory/evidence.py:393-395`: `verified` requires full-SHA `rev-parse`, module tracked via `ls-files --error-unmatch`, and empty `status --porcelain -- .`; otherwise `revision: None, verified: False`. Package version string is never consulted. Dirty-tree case tested at `tests/test_evidence.py:307-310`. | Met |
| Actual JSON CLI exercised for verified and unavailable provenance; bounded output; zero operational writes | `tests/test_evidence.py:275-283` runs `python -B -m factory.cli evidence` via subprocess under the existing write guard (`EVIDENCE_FORBIDDEN` assertion `:281`); clean-commit path `:294-305`, modified path `:307-310`. `GIT_OPTIONAL_LOCKS=0` at `factory/evidence.py:382` prevents `git status` index refresh writes. Output adds four small fields. | Met |

### Standards

No AGENTS.md/CONTRIBUTING.md at this head. README conventions relevant here: capabilities does "no case or GitHub collection" — preserved; only local `git` subprocesses added. Subprocess invocation reuses the module's existing `COMMAND_SECONDS`/`subprocess.run(check=False)` pattern (`factory/evidence.py:385-386`). Gate (test/leak-scan) PASS.

### Required fixes

None.

### Optional suggestions

1. **Deadline not threaded** — `factory/evidence.py:385-386`: three sequential `git` calls, each bounded by `COMMAND_SECONDS` independently, not by the remaining whole-read deadline that `github_read(endpoint, deadline)` honors (`:371` signature). Worst case 3×`COMMAND_SECONDS` inside a `capabilities` read that previously spawned nothing. Local git is fast in practice; a single `deadline` parameter would make the bound consistent with the documented 90s whole-read ceiling. Non-blocking.
2. **README not updated** — README "Bounded project evidence JSON" documents `capabilities` as advertising "evidence and runtime schema 1 support"; the new `producers.reader` object (`factory/evidence.py:420`) and its unknown-provenance semantics (`revision: null`, `verified: false`) are undocumented. No documented rule in the packet mandates README updates, so non-blocking, but consumers have no contract for the new field.
3. **Possible duplicate producer-revision logic** — `[INFERENCE]` README states the feedback producer already computes "a clean source-checkout Git revision, otherwise null" (`factory/feedback.py`, `producer_revision`). The issue's "reuse existing evidence producers" suggests sharing that helper rather than a second git-probe in `reader_build()` (`factory/evidence.py:374-396`). Not visible in the diff; worth a look, not blocking.
4. **Scope of cleanliness check** — `factory/evidence.py:392` runs `status --porcelain -- .` from the package directory only. Modifications outside `factory/` (e.g. `pyproject.toml`) don't invalidate the revision. Defensible (the package is the engine), but the docstring at `:376-380` says "locally modified install" without stating the package-dir scope.
5. **Test copies `__pycache__`** — `tests/test_evidence.py:287` `copytree(ROOT / "factory", ...)` then `git add .` (`:290`) commits any stale `.pyc`. Harmless with `-B` (`:279`), but `ignore=shutil.ignore_patterns("__pycache__")` keeps the fixture minimal.

VERDICT: APPROVE
