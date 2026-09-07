## Review: #20 when-rules, ci-fix/conflict profiles (head ce5568e)

### Standards (README.md only; no AGENTS.md/CONTRIBUTING.md at this head)

- README updated in step with behavior: decision menu, table-form workers, FIX semantics, opt-in profiles (`README.md:88-102`). Consistent with `MENU` text (`factory/manage.py:19-29`).
- Existing patterns reused: `ConfigError` with `{path}: ...` prefix (`factory/config.py:293,295`), `dispatch.record("manage", ...)` before mutation retained (`factory/manage.py:219`), FIX errors surface via the existing `ValueError` → `mechanism_failure` path (`factory/manage.py:110,112,221-224`).
- Dashboard `worker_when` (`factory/dashboard.py:847`) is additive; README snapshot docs don't mention it, but README doesn't enumerate `config` snapshot keys either. Not a rule violation.

### Spec (issue #20 + brief)

| Criterion | Evidence | Status |
|---|---|---|
| `when` on `[workers.<label>]`; array form still loads | `factory/config.py:289-298`; test `tests/test_factory.py:147-165` | met |
| Prompt lists labels with `when` | `factory/manage.py:199-201` | met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54,63-66`; `parse` receives reserved-filtered map (`:213`) | met |
| ROUTE chore rejected+recorded without worker; label applied with one | `tests/test_factory.py:652-675` (HUMAN decision recorded, no `issue edit`; `--add-label chore` when configured) | met |
| FIX on red CI runs the selected argv | `factory/manage.py:104-131`; `tests/test_factory.py:677-731` | met |
| ci-fix + conflict profiles in template, human-applied | `factory/templates/factory.toml:32-47` (commented, opt-in) | met — matches "remain host-config, human-applied" |
| Out of scope: manager writing host config | no config writes in diff | met |

Correctness checks on the FIX path:
- Preconditions: kept worktree, OPEN PR on `agent/<n>`, no `CHANGES_REQUESTED`, worktree on ticket branch (`factory/manage.py:106-112`). Human veto respected; `reviewDecision` null compares false safely.
- Push only after gate PASS with `--force-with-lease` (`:121-124`); `factory-approved` only on fresh APPROVE (`:127-128`). Matches README text.
- Validation rejects non-string/reserved/unknown workers and empty guidance (`:63-67`); tests cover `default`, `ready-for-agent`, unconfigured `ci-fix`, list value (`tests/test_factory.py:733-743`).

### Required fixes

None.

### Optional suggestions

1. `factory/manage.py:122,130` — FIX failure calls `dispatch.escalate`, which [INFERENCE] writes a new escalation packet. If that packet is "untouched", the manager may consume another round on the next pass (FIX → escalate → FIX). Bounded by `[manager].rounds` per README, so not a defect; worth confirming the round counter is per-ticket rather than per-packet.
2. `factory/manage.py:54` — `- RESERVED_LABELS` is now redundant for the `manage_pass` caller (`:199,:213` pre-filters). Harmless defensive check since `parse` is a public seam; leave unless you want one source of truth.
3. `factory/config.py:296` — array-form entries store the TOML list by reference (previous code did `list(v)`). No observed aliasing consumer; cosmetic.

No unrequested abstractions introduced; `RESERVED_LABELS` (`factory/manage.py:32`) replaces an inline set literal used in two places.

VERDICT: APPROVE
