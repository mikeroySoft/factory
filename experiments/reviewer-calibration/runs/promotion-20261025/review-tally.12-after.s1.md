Findings grounded in the diff, issue #12 brief, and `docs/manager-plan.md` T3.

**Spec trace**
- `KNOWN_KEYS["manager"]` gains `rounds`/`review` (review.diff:10); `HOST_TABLES` already contained `"manager"` at base (snapshot `factory/config.py:50`, unchanged), so host-layer handling via `host_filter`/`adopt` is in place.
- Loader: `cfg.manager`, `manager_rounds` (default 1), `manager_review` (default `"escalated"`, `"all"` allowed, anything else → `ConfigError`) (review.diff:70-74). Unset `command` → `cfg.manager is None` → doctor prints nothing (review.diff:110-111). Matches "unset = disabled".
- Drift: unknown `[manager]` keys flow through the pre-existing `unknown_keys` path; test asserts `manager.unknown` (review.diff:190,198).
- `dashboard --json` `config.manager` (review.diff:94-98) and template docs (review.diff:128-133) present.
- Loader test covers all three keys (review.diff:169-172,180-181); doctor row present/absent test (review.diff:224-232). Gate `test: PASS`.
- Running the command: not implemented (out of scope honored). `manager_settings` never executes argv; the existing boundary test is preserved (review.diff:146-157).
- Legacy string `command` now also becomes argv via `shlex.split` (review.diff:37-44) — stricter type validation than before but consistent with the brief.

**Plan drift**: plan not recorded. Files touched = issue "Touches" list + `tests/test_factory.py` + `tests/test_briefing.py` (rename fallout of `manager_model`→`manager_settings`). No unexplained files; no missing proof beyond the gate report.

**Standards**: no AGENTS.md/CONTRIBUTING.md in snapshot; code mirrors the existing `review`/`reviewer` pattern (loader, doctor `which`, dashboard join). No convention violations found.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"`int(manager.get(\"rounds\"))` surfaces a non-integer `rounds` as a bare ValueError instead of ConfigError, and accepts 0/negative; same shape as the pre-existing `gate.timeout` handling, so consistent but not validated."}]}
```
VERDICT: APPROVE
